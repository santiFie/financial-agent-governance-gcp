"""
Data models and schemas for API requests, responses, and security telemetry.
"""
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
import uuid


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Raw input text from user")
    session_id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), description="Session/thread ID for conversation state")
    user_id: Optional[str] = Field(default="user_default_01", description="Identifier for the authenticated user")


class DetectedPII(BaseModel):
    info_type: str = Field(..., description="Type of sensitive data (e.g. CREDIT_CARD, CBU, DNI)")
    token_assigned: str = Field(..., description="Surrogate token replacing the sensitive data (e.g. [TARJETA_CREDITO_1])")
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None


class SecurityMetadata(BaseModel):
    sanitized_input: str = Field(..., description="Text after DLP de-identification that enters the LLM")
    pii_detected: List[DetectedPII] = Field(default_factory=list)
    prompt_injection_flagged: bool = Field(default=False)
    threat_category: Optional[str] = None
    defense_action: Literal["PASSED", "DEIDENTIFIED", "BLOCKED"] = "PASSED"
    processing_time_ms: float = 0.0


class PendingTransferInfo(BaseModel):
    transaction_id: str
    session_id: str
    user_id: str
    amount: float
    currency: str = "ARS"
    source_account: str
    target_token: str
    status: Literal["PENDING_APPROVAL", "APPROVED", "REJECTED", "EXECUTED"] = "PENDING_APPROVAL"
    created_at: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
    intent: Optional[str] = "unknown"
    status: Literal["COMPLETED", "BLOCKED", "PENDING_APPROVAL"] = "COMPLETED"
    security_audit: SecurityMetadata
    pending_transfer: Optional[PendingTransferInfo] = None


class ApproveRequest(BaseModel):
    session_id: str = Field(..., description="LangGraph thread_id corresponding to paused state")
    transaction_id: str = Field(..., description="Unique ID of the pending transaction")
    decision: Literal["APPROVED", "REJECTED"] = Field(..., description="Human-in-the-Loop decision")
    admin_user: str = Field(default="admin_compliance_01", description="Admin or operator approving the request")
    reason: Optional[str] = Field(default=None, description="Optional note or explanation")


class ApproveResponse(BaseModel):
    session_id: str
    transaction_id: str
    decision: str
    status: Literal["EXECUTED", "REJECTED", "ERROR"]
    message: str
    final_balance: Optional[float] = None
