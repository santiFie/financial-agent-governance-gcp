"""
Sanitizer Agent (Deterministic Middleware Node).
Does NOT invoke an LLM. Executes Model Armor prompt injection filtering and Cloud DLP
de-identification on the user input before passing data into downstream nodes.
Attached documents are NOT read here: the Orchestrator reads them on demand through
read_document_tool, which applies the same Model Armor + DLP pipeline.
"""
import logging
from typing import Dict, Any
from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from app.graph.state import AgentState
from app.core.dlp_sanitizer import dlp_sanitizer
from app.core.model_armor import model_armor
from app.core.prompt_manager import prompt_manager

logger = logging.getLogger(__name__)


def sanitizer_node(state: AgentState) -> Dict[str, Any]:
    """
    Evaluates the user input for security threats and tokenizes sensitive data (PII).
    Short-circuits immediately if an injection or jailbreak is detected.
    Resets the per-turn Orchestrator state (plan and scratchpad).
    """
    raw_input = state.get("raw_input", "")
    session_id = state.get("session_id", "default")

    turn_reset: Dict[str, Any] = {
        "execution_plan": [],
        "orchestrator_scratchpad": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
        "orchestrator_tool_rounds": 0,
    }

    # Step 1: Model Armor Inspection (Prompt Injection / Adversarial Defense)
    is_threat, threat_cat, threat_reason = model_armor.inspect(raw_input)
    if is_threat:
        blocked_msg = prompt_manager.render("sanitizer_responses", threat_cat=threat_cat)
        return {
            **turn_reset,
            "is_threat": True,
            "threat_category": threat_cat,
            "threat_reason": threat_reason,
            "defense_action": "BLOCKED",
            "status": "BLOCKED",
            "final_response": blocked_msg,
            "messages": [AIMessage(content=blocked_msg)],
        }

    # Step 2: Cloud DLP De-identification (Sensitive Data Protection)
    sanitized_input, detected_pii = dlp_sanitizer.sanitize(raw_input, session_id=session_id)

    return {
        **turn_reset,
        "sanitized_input": sanitized_input,
        "detected_pii": list(detected_pii),
        "is_threat": False,
        "threat_category": None,
        "threat_reason": None,
        "defense_action": "DEIDENTIFIED" if detected_pii else "PASSED",
        "status": "IN_PROGRESS",
    }
