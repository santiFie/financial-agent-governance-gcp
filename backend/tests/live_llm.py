"""Single gate for every test that calls a real LLM.

A live test carries two marks (see `requires_live_llm`):
- `requires_live_llm`: lets `pytest -m "not requires_live_llm"` deselect it (what `make test` does);
- a `skipif` that skips it when no provider has credentials, so a plain `pytest` never fails for lack of keys.

Vertex AI only counts as available when it is requested on purpose (`LLM_PROVIDER=vertexai`). Having gcloud
Application Default Credentials on the machine is not enough: otherwise an "offline" run would silently call a
real model with whatever project those credentials point to.
"""

from typing import Callable, TypeVar

import pytest

from app.config import settings
from app.core.llm import LLMConfigurationError, get_available_deployments  # noqa: F401 (re-exported for tests)

F = TypeVar("F", bound=Callable)


def live_llm_available() -> bool:
    """True when a provider with valid credentials is configured for this run."""
    try:
        if (settings.llm_provider or "").lower() == "vertexai":
            return bool(get_available_deployments(provider="vertexai"))
        return any(d["model_name"] != "vertexai" for d in get_available_deployments())
    except Exception:
        return False


_skip_without_llm = pytest.mark.skipif(
    not live_llm_available(),
    reason="Requiere credenciales LLM válidas (Vertex AI solo con LLM_PROVIDER=vertexai)",
)

# For module-level `pytestmark = live_llm_marks`.
live_llm_marks = [pytest.mark.requires_live_llm, _skip_without_llm]


def requires_live_llm(test: F) -> F:
    """Decorator for a test or class that calls a real LLM."""
    for mark in live_llm_marks:
        test = mark(test)
    return test
