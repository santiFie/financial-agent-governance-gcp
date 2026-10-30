"""
State Definition for the Multi-Agent Financial Assistant Graph.
Enforces typed transitions and preserves isolated audit trails.
"""
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from app.models.api_schemas import DetectedPII


class AgentState(TypedDict):
    """
    Core state passed through all nodes of the LangGraph architecture.
    """
    # LangChain message history
    messages: Annotated[List[BaseMessage], add_messages]

    # Session & Identity
    session_id: str
    user_id: str

    # Security & Sanitization context
    raw_input: str
    sanitized_input: str
    detected_pii: List[DetectedPII]
    is_threat: bool
    threat_category: Optional[str]
    threat_reason: Optional[str]
    defense_action: str  # "PASSED" | "DEIDENTIFIED" | "BLOCKED"

    # Multimodal / Document Ingestion Context (Stream A)
    document_name: Optional[str] = None
    raw_document: Optional[str] = None
    sanitized_document: Optional[str] = None

    # Routing, Planning & Business Logic (Stream A)
    current_intent: Optional[str]  # "general" | "balance" | "transactions" | "transfer" | "blocked"
    execution_plan: Optional[List[Dict[str, Any]]] = None
    delegated_instructions: Optional[str] = None

    # Action / HITL Staging & Governance (Stream B)
    pending_transfer: Optional[Dict[str, Any]]
    approval_status: Optional[str]  # "NONE" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED"
    pending_tool_calls: Optional[List[Dict[str, Any]]] = None
    executed_tools: Optional[List[Dict[str, Any]]] = None

    # Final Output to user
    final_response: Optional[str]
    status: str  # "COMPLETED" | "BLOCKED" | "PENDING_APPROVAL"
