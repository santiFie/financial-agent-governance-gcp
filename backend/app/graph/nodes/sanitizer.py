"""
Sanitizer Agent (Deterministic Middleware Node).
Does NOT invoke an LLM. Executes Cloud DLP de-identification and Model Armor
prompt injection filtering before passing data into downstream nodes.
"""
from typing import Dict, Any
from langchain_core.messages import AIMessage
from app.graph.state import AgentState
from app.core.dlp_sanitizer import dlp_sanitizer
from app.core.model_armor import model_armor


def sanitizer_node(state: AgentState) -> Dict[str, Any]:
    """
    Evaluates input for security threats and tokenizes sensitive data (PII).
    Short-circuits immediately if an injection or jailbreak is detected.
    """
    raw_input = state.get("raw_input", "")
    session_id = state.get("session_id", "default")

    # Step 1: Model Armor Inspection (Prompt Injection / Adversarial Defense)
    is_threat, threat_cat, threat_reason = model_armor.inspect(raw_input)
    if is_threat:
        blocked_msg = (
            "⚠️ [SEGURIDAD BANCARIA] Tu solicitud ha sido bloqueada por las políticas de seguridad "
            "y prevención de inyecciones (Model Armor). "
            f"Motivo: Detección de patrón adverso ({threat_cat})."
        )
        return {
            "is_threat": True,
            "threat_category": threat_cat,
            "threat_reason": threat_reason,
            "defense_action": "BLOCKED",
            "current_intent": "blocked",
            "status": "BLOCKED",
            "final_response": blocked_msg,
            "messages": [AIMessage(content=blocked_msg)]
        }

    # Step 2: Cloud DLP De-identification (Sensitive Data Protection)
    sanitized_input, detected_pii = dlp_sanitizer.sanitize(raw_input, session_id=session_id)
    defense_action = "DEIDENTIFIED" if detected_pii else "PASSED"

    return {
        "sanitized_input": sanitized_input,
        "detected_pii": detected_pii,
        "is_threat": False,
        "threat_category": None,
        "threat_reason": None,
        "defense_action": defense_action,
        "status": "IN_PROGRESS"
    }
