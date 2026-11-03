"""Marker for integration tests that call a real LLM through the full graph."""
import pytest

from app.core.llm import get_chat_model


def _live_llm_available() -> bool:
    try:
        return get_chat_model(temperature=0.0) is not None
    except Exception:
        return False


requires_live_llm = pytest.mark.skipif(
    not _live_llm_available(), reason="Requiere credenciales LLM válidas para el proveedor configurado"
)
