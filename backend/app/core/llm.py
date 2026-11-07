"""
Multi-Provider LLM Factory with LiteLLM Fallback Router.
Configures dynamic fallbacks in the order: groq -> nvidia -> vertexai -> openrouter.
Verifies API keys and GCP parameters before including each provider in the fallback chain.
Fails fast with LLMConfigurationError if no provider has valid credentials.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings

logger = logging.getLogger(__name__)

KNOWN_PROVIDERS = {"litellm", "groq", "nvidia", "vertexai", "openrouter", "google_genai", ""}


class LLMConfigurationError(RuntimeError):
    """Raised when the configured LLM provider cannot be instantiated or credentials are missing."""


def _check_groq_deployment(temperature: float, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Verifies Groq API key and returns LiteLLM deployment config if valid."""
    api_key = settings.groq_api_key or os.getenv("GROQ_API_KEY")
    if not api_key or not api_key.strip() or api_key.startswith("your-"):
        return None
    model = model or getattr(settings, "groq_model", None) or "llama-3.3-70b-versatile"
    return {
        "model_name": "groq",
        "litellm_params": {
            "model": f"groq/{model}",
            "api_key": api_key.strip(),
            "temperature": temperature,
        },
    }


def _check_nvidia_deployment(temperature: float, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Verifies NVIDIA API key and returns LiteLLM deployment config if valid."""
    api_key = settings.nvidia_api_key or os.getenv("NVIDIA_API_KEY")
    if not api_key or not api_key.strip() or api_key.startswith("your-"):
        return None
    model = model or getattr(settings, "nvidia_model", None) or "meta/llama-3.3-70b-instruct"
    return {
        "model_name": "nvidia",
        "litellm_params": {
            "model": f"nvidia_nim/{model}",
            "api_key": api_key.strip(),
            "temperature": temperature,
        },
    }


def _check_vertexai_deployment(temperature: float, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Verifies GCP project, location and credentials for Vertex AI."""
    project = (
        settings.gcp_project_id
        or os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEXAI_PROJECT")
    )
    location = (
        settings.gcp_location
        or os.getenv("GCP_LOCATION")
        or os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("VERTEXAI_LOCATION")
        or "us-central1"
    )

    has_creds = False
    cred_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_file and os.path.isfile(cred_file):
        has_creds = True
    else:
        try:
            import google.auth
            from google.auth.exceptions import DefaultCredentialsError

            try:
                creds, auth_project = google.auth.default()
                if creds:
                    has_creds = True
                    if not project or project in ("your-gcp-project-id", "demo-fintech-project"):
                        if auth_project:
                            project = auth_project
            except (DefaultCredentialsError, Exception):
                has_creds = False
        except ImportError:
            has_creds = False

    # Also accept Gemini / Google API key if ADC is not set up
    gemini_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not has_creds and gemini_key and not gemini_key.startswith("your-"):
        has_creds = True

    if not project or not has_creds:
        return None

    model = model or getattr(settings, "vertex_model_name", None) or "gemini-1.5-flash-002"
    litellm_params: Dict[str, Any] = {
        "model": f"vertex_ai/{model}",
        "vertex_project": project,
        "vertex_location": location,
        "temperature": temperature,
    }
    if cred_file and os.path.isfile(cred_file):
        litellm_params["vertex_credentials"] = cred_file

    return {
        "model_name": "vertexai",
        "litellm_params": litellm_params,
    }


def _check_openrouter_deployment(temperature: float, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Verifies OpenRouter API key and returns LiteLLM deployment config if valid."""
    api_key = getattr(settings, "openrouter_api_key", None) or os.getenv("OPENROUTER_API_KEY")
    if not api_key or not api_key.strip() or api_key.startswith("your-"):
        return None
    model = model or getattr(settings, "openrouter_model", None) or "meta-llama/llama-3.3-70b-instruct"
    return {
        "model_name": "openrouter",
        "litellm_params": {
            "model": f"openrouter/{model}",
            "api_key": api_key.strip(),
            "temperature": temperature,
            # "reasoning_effort": "high"  # OPCIONAL: pruebas con/sin reasoning
        },
    }


def get_available_deployments(
    temperature: float = 0.0,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluates providers in priority order: groq -> nvidia -> vertexai -> openrouter.
    Returns list of deployment configurations for providers that have valid credentials.

    If `provider` is given, that provider is placed first in the list. `model` overrides
    the provider's default model (it only applies when `provider` is given).
    """
    providers_order = [
        ("groq", _check_groq_deployment),
        ("nvidia", _check_nvidia_deployment),
        ("vertexai", _check_vertexai_deployment),
        ("openrouter", _check_openrouter_deployment),
    ]

    provider = provider.lower() if provider else None
    if provider and provider not in {n for n, _ in providers_order}:
        raise LLMConfigurationError(f"Provider LLM desconocido: '{provider}'.")

    deployments: List[Dict[str, Any]] = []
    for name, check_fn in providers_order:
        if name == provider:
            dep = check_fn(temperature, model)
            if dep is None:
                raise LLMConfigurationError(
                    f"El provider '{provider}' fue solicitado pero no tiene credenciales válidas."
                )
            deployments.insert(0, dep)
        else:
            dep = check_fn(temperature)
            if dep is not None:
                deployments.append(dep)

    return deployments


def get_chat_model(
    temperature: float = 0.0,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> BaseChatModel:
    """
    Instantiates and returns a LangChain chat model with LiteLLM Router fallbacks.
    Order of fallbacks: groq -> nvidia -> vertexai -> openrouter.
    Only providers with verified credentials/parameters are included in the fallback chain.
    If `provider` is given it is placed first in the chain; `model` overrides that provider's
    default model (if `model` is None the provider's default model is used).
    Raises LLMConfigurationError if no provider is configured with valid credentials.
    """
    configured_provider = (settings.llm_provider or "").lower()
    if configured_provider and configured_provider not in KNOWN_PROVIDERS:
        raise LLMConfigurationError(f"Provider LLM desconocido: '{settings.llm_provider}'.")

    model_list = get_available_deployments(temperature=temperature, provider=provider, model=model)

    if not model_list:
        raise LLMConfigurationError(
            "No se encontraron credenciales válidas para ninguno de los proveedores de LLM "
            "(groq, nvidia, vertexai, openrouter)."
        )

    primary_name = model_list[0]["model_name"]
    fallback_targets = [m["model_name"] for m in model_list[1:]]

    fallbacks = [{primary_name: fallback_targets}] if fallback_targets else None

    logger.info(
        f"[LLM Factory] LiteLLM Router inicializado con primario='{primary_name}' y fallbacks={fallback_targets}"
    )

    from langchain_litellm import ChatLiteLLMRouter
    from litellm import Router

    router = Router(
        model_list=model_list,
        fallbacks=fallbacks,
    )

    return ChatLiteLLMRouter(
        router=router,
        model_name=primary_name,
        temperature=temperature,
    )
