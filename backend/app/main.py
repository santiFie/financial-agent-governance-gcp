"""
FastAPI Main Application.
Exposes endpoints for chat, Human-in-the-Loop approvals, telemetry, and pending transfers.
"""
import logging
import time
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.config import settings
from app.models.api_schemas import (
    ChatRequest,
    ChatResponse,
    ApproveRequest,
    ApproveResponse,
    SecurityMetadata,
    PendingTransferInfo,
    DetectedPII
)
from app.graph.graph import financial_graph
from app.db.mock_banking import mock_banking_db
from app.core.mcp_action_client import mcp_action_client
from app.core.telemetry import build_trace_config, get_telemetry_status

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Backend API para el Asistente Financiero Multi-Agente con Defense-in-Depth en GCP"
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    """Health check endpoint for Cloud Run and Docker Compose."""
    return {
        "status": "healthy",
        "environment": settings.environment,
        "security_mode": settings.security_mode,
        "checkpointer": settings.checkpointer_type,
        "gcp_project": settings.gcp_project_id,
        "telemetry": get_telemetry_status()
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Main chat endpoint.
    Orchestrates execution of the LangGraph multi-agent defense pipeline.
    """
    start_time = time.time()
    session_id = request.session_id
    user_id = request.user_id or "user_default_01"

    config, trace_id = build_trace_config(
        session_id=session_id,
        user_id=user_id,
        endpoint="/api/chat",
        run_name="financial_assistant_chat_turn"
    )

    initial_state = {
        "messages": [HumanMessage(content=request.message)],
        "raw_input": request.message,
        "document_name": request.document_name,
        "session_id": session_id,
        "user_id": user_id,
        "status": "IN_PROGRESS"
    }

    try:
        # Runs until completion or until interrupt(); a HITL pause is read back from the checkpointed state below
        financial_graph.invoke(initial_state, config=config)
    except Exception as e:
        logger.exception("Error ejecutando el grafo para la sesión %s", session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error ejecutando el grafo: {str(e)}"
        )

    # Inspect current state from checkpointer
    graph_state = financial_graph.get_state(config)
    state_values = graph_state.values if graph_state else {}
    elapsed_ms = round((time.time() - start_time) * 1000, 2)

    # Prepare Security Metadata
    detected_pii_raw = state_values.get("detected_pii", [])
    detected_pii_objs = [
        p if isinstance(p, DetectedPII) else DetectedPII(**p)
        for p in detected_pii_raw
    ]

    security_audit = SecurityMetadata(
        sanitized_input=state_values.get("sanitized_input", request.message),
        pii_detected=detected_pii_objs,
        prompt_injection_flagged=state_values.get("is_threat", False),
        threat_category=state_values.get("threat_category"),
        defense_action=state_values.get("defense_action", "PASSED"),
        processing_time_ms=elapsed_ms,
        trace_id=trace_id
    )

    # Check if graph paused at a Human-in-the-Loop breakpoint
    if graph_state and graph_state.next:
        # Graph is currently interrupted awaiting human input
        # Retrieve staged transfer details
        pending_list = [
            t for t in mock_banking_db.get_pending_transfers()
            if t.get("session_id") == session_id
        ]
        pending_info = None
        if pending_list:
            t = pending_list[-1]
            pending_info = PendingTransferInfo(
                transaction_id=t["transaction_id"],
                session_id=session_id,
                user_id=user_id,
                amount=t["amount"],
                currency=t["currency"],
                source_account=t["source_account"],
                target_token=t["target_token"],
                status=t["status"],
                created_at=t["created_at"]
            )

        return ChatResponse(
            session_id=session_id,
            response=(
                f"⏸️ **Solicitud de Transferencia en Pausa por Gobernanza de Seguridad (HITL)**\n\n"
                f"Hemos encolado tu transferencia por **${pending_info.amount if pending_info else 0:,.2f}** "
                f"hacia el destinatario protegido `{pending_info.target_token if pending_info else ''}`.\n\n"
                "Por política de seguridad bancaria, esta operación requiere autorización humana previa "
                "por parte del oficial de cumplimiento antes de impactar los fondos."
            ),
            status="PENDING_APPROVAL",
            security_audit=security_audit,
            pending_transfer=pending_info,
            document_name=state_values.get("document_name", request.document_name),
            execution_plan=state_values.get("execution_plan")
        )

    # If graph finished normally or was blocked
    is_blocked = state_values.get("is_threat", False)
    final_resp = state_values.get("final_response", "Operación procesada con éxito.")

    return ChatResponse(
        session_id=session_id,
        response=final_resp,
        status="BLOCKED" if is_blocked else ("FAILED" if state_values.get("status") == "FAILED" else "COMPLETED"),
        security_audit=security_audit,
        pending_transfer=None,
        document_name=state_values.get("document_name", request.document_name),
        execution_plan=state_values.get("execution_plan")
    )


@app.post("/api/approve", response_model=ApproveResponse)
async def approve_endpoint(request: ApproveRequest):
    """
    Human-in-the-Loop Resumption Endpoint.
    Authorizes or rejects a paused transfer and resumes the LangGraph execution.
    """
    config, trace_id = build_trace_config(
        session_id=request.session_id,
        user_id=request.admin_user,
        endpoint="/api/approve",
        run_name="financial_assistant_hitl_resumption",
        extra_metadata={
            "transaction_id": request.transaction_id,
            "decision": request.decision,
            "admin_user": request.admin_user
        },
        extra_tags=["hitl-approval"]
    )
    graph_state = financial_graph.get_state(config)

    if not graph_state or not graph_state.next:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La sesión {request.session_id} no tiene una ejecución en pausa esperando aprobación."
        )

    staged = mock_banking_db.pending_transfers.get(request.transaction_id)
    if (
        staged is None
        or staged.get("session_id") != request.session_id
        or staged.get("status") != "PENDING_APPROVAL"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La transacción {request.transaction_id} no está pendiente de aprobación en la sesión {request.session_id}."
        )

    # Resume graph execution using Command(resume=...)
    try:
        resumed_result = financial_graph.invoke(
            Command(resume={
                "decision": request.decision,
                "admin_user": request.admin_user,
                "reason": request.reason
            }),
            config=config
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reanudando la ejecución del grafo: {str(e)}"
        )

    # Check updated balance if executed
    accounts = mcp_action_client.read_account_balances(staged["user_id"])
    primary_balance = accounts[0]["balance"] if accounts else None

    return ApproveResponse(
        session_id=request.session_id,
        transaction_id=request.transaction_id,
        decision=request.decision,
        status="EXECUTED" if request.decision == "APPROVED" else "REJECTED",
        message=resumed_result.get("final_response", "Operación resuelta."),
        final_balance=primary_balance
    )


@app.get("/api/pending-transfers", response_model=List[PendingTransferInfo])
def get_pending_transfers_endpoint():
    """Returns all transfers waiting for human review."""
    pending = mock_banking_db.get_pending_transfers()
    return [
        PendingTransferInfo(
            transaction_id=p["transaction_id"],
            session_id=p["session_id"],
            user_id=p["user_id"],
            amount=p["amount"],
            currency=p["currency"],
            source_account=p["source_account"],
            target_token=p["target_token"],
            status=p["status"],
            created_at=p["created_at"]
        )
        for p in pending
    ]


@app.get("/api/accounts/{user_id}")
def get_user_accounts(user_id: str = "user_default_01"):
    """Returns accounts and balances for user profile dashboard."""
    return {
        "user_id": user_id,
        "accounts": mcp_action_client.read_account_balances(user_id),
        "recent_transactions": mcp_action_client.read_recent_transactions(user_id, limit=5)
    }
