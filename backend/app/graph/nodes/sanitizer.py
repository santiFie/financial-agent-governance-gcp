"""
Sanitizer Agent (Deterministic Middleware Node).
Does NOT invoke an LLM. Executes Cloud DLP de-identification and Model Armor
prompt injection filtering on user inputs and attached documents before passing data into downstream nodes.
"""
import logging
from typing import Dict, Any, Optional
from langchain_core.messages import AIMessage
from app.graph.state import AgentState
from app.core.dlp_sanitizer import dlp_sanitizer
from app.core.model_armor import model_armor
from app.core.prompt_manager import prompt_manager
from app.core.document_loader import document_ingestion_service

logger = logging.getLogger(__name__)


def sanitizer_node(state: AgentState) -> Dict[str, Any]:
    """
    Evaluates input and attached documents for security threats and tokenizes sensitive data (PII).
    Short-circuits immediately if an injection or jailbreak is detected.
    """
    raw_input = state.get("raw_input", "")
    session_id = state.get("session_id", "default")
    document_name = state.get("document_name")
    raw_document = state.get("raw_document")

    # Ingest document if document_name is present and raw_document not yet loaded
    if document_name and not raw_document:
        try:
            raw_document = document_ingestion_service.load_document(document_name)
        except Exception as e:
            logger.warning(f"Error cargando documento '{document_name}': {e}")
            raw_document = None

    # Step 1: Model Armor Inspection (Prompt Injection / Adversarial Defense)
    # Check user prompt
    is_threat, threat_cat, threat_reason = model_armor.inspect(raw_input)
    if is_threat:
        blocked_msg = prompt_manager.render("sanitizer_responses", threat_cat=threat_cat)
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

    # Check attached document if present
    if raw_document:
        doc_is_threat, doc_threat_cat, doc_threat_reason = model_armor.inspect(raw_document)
        if doc_is_threat:
            blocked_msg = prompt_manager.render("sanitizer_responses", threat_cat=doc_threat_cat)
            return {
                "raw_document": raw_document,
                "sanitized_document": None,
                "is_threat": True,
                "threat_category": doc_threat_cat,
                "threat_reason": doc_threat_reason,
                "defense_action": "BLOCKED",
                "current_intent": "blocked",
                "status": "BLOCKED",
                "final_response": blocked_msg,
                "messages": [AIMessage(content=blocked_msg)]
            }

    # Step 2: Cloud DLP De-identification (Sensitive Data Protection)
    sanitized_input, detected_pii = dlp_sanitizer.sanitize(raw_input, session_id=session_id)
    all_detected_pii = list(detected_pii)

    sanitized_document: Optional[str] = None
    if raw_document:
        sanitized_document, doc_detected_pii = dlp_sanitizer.sanitize(raw_document, session_id=session_id)
        all_detected_pii.extend(doc_detected_pii)

    defense_action = "DEIDENTIFIED" if all_detected_pii else "PASSED"

    return {
        "raw_document": raw_document,
        "sanitized_document": sanitized_document,
        "sanitized_input": sanitized_input,
        "detected_pii": all_detected_pii,
        "is_threat": False,
        "threat_category": None,
        "threat_reason": None,
        "defense_action": defense_action,
        "status": "IN_PROGRESS"
    }
