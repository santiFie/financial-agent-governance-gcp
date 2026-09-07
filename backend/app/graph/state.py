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

    # Routing & Business Logic
    current_intent: Optional[str]  # "general" | "balance" | "transactions" | "transfer" | "blocked"

    # Action / HITL Staging
    pending_transfer: Optional[Dict[str, Any]]
    approval_status: Optional[str]  # "NONE" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED"

    # Final Output to user
    final_response: Optional[str]
    status: str  # "COMPLETED" | "BLOCKED" | "PENDING_APPROVAL"
