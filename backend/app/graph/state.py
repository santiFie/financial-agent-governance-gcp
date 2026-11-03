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

    # Attached document reference (name only; content is read by the Orchestrator through read_document_tool)
    document_name: Optional[str] = None

    # Orchestrator Agent: append-only execution plan and tool-calling scratchpad.
    # The scratchpad uses the add_messages reducer so ToolNode can append ToolMessages;
    # it is reset every turn by the sanitizer with RemoveMessage(REMOVE_ALL_MESSAGES).
    execution_plan: Optional[List[Dict[str, Any]]] = None
    orchestrator_scratchpad: Annotated[List[BaseMessage], add_messages]
    orchestrator_tool_rounds: Optional[int] = 0

    # Action / HITL Staging & Governance
    pending_transfer: Optional[Dict[str, Any]]
    approval_status: Optional[str]  # "NONE" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED"
    pending_tool_calls: Optional[List[Dict[str, Any]]] = None
    executed_tools: Optional[List[Dict[str, Any]]] = None

    # Final Output to user
    final_response: Optional[str]
    status: str  # "COMPLETED" | "BLOCKED" | "PENDING_APPROVAL"
