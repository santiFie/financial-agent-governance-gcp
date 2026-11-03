"""
Document reader tool for the Orchestrator Agent.
Pipeline (no LLM involved): load document -> Model Armor inspection -> Cloud DLP de-identification.
The LLM only supplies the filename and receives the sanitized content.
"""
from typing import Annotated, Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.core.document_loader import document_ingestion_service
from app.core.dlp_sanitizer import dlp_sanitizer
from app.core.model_armor import model_armor


def process_document(filename: str, session_id: str) -> Dict[str, Any]:
    """
    Loads and sanitizes a document. Returns a dict with:
    status ("OK" | "BLOCKED" | "NOT_FOUND"), document_name, content, threat_category, threat_reason, detected_pii.
    """
    base: Dict[str, Any] = {
        "status": "OK",
        "document_name": filename,
        "content": None,
        "threat_category": None,
        "threat_reason": None,
        "detected_pii": [],
    }

    try:
        raw_content = document_ingestion_service.load_document(filename)
    except ValueError as e:
        return {
            **base,
            "status": "BLOCKED",
            "threat_category": "PATH_TRAVERSAL",
            "threat_reason": f"Path traversal attempt in filename: {filename} ({e})",
        }
    except FileNotFoundError:
        return {**base, "status": "NOT_FOUND"}

    is_threat, threat_cat, threat_reason = model_armor.inspect(raw_content)
    if is_threat:
        return {
            **base,
            "status": "BLOCKED",
            "threat_category": threat_cat,
            "threat_reason": threat_reason,
        }

    sanitized, detected_pii = dlp_sanitizer.sanitize(raw_content, session_id=session_id)
    return {
        **base,
        "content": sanitized,
        "detected_pii": [p.model_dump() if hasattr(p, "model_dump") else p for p in detected_pii],
    }


@tool(response_format="content_and_artifact")
def read_document_tool(
    filename: str,
    session_id: Annotated[str, InjectedState("session_id")],
) -> Tuple[str, Dict[str, Any]]:
    """Lee el documento adjunto a la solicitud, indicando su filename, y devuelve su contenido ya saneado (sin datos sensibles)."""
    result = process_document(filename, session_id)
    if result["status"] == "OK":
        content = result["content"] or ""
    elif result["status"] == "NOT_FOUND":
        content = "Documento no encontrado."
    else:
        content = "Documento bloqueado por las políticas de seguridad."
    return content, result
