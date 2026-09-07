"""
Multi-Provider LLM Factory.
Supports Groq (Free Tier), NVIDIA NIM (Free Tier), Google Vertex AI,
Google AI Studio, and Deterministic Mock mode for offline development and tests.
"""
import os
import logging
from typing import Optional, Any
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings

logger = logging.getLogger(__name__)


def get_chat_model(temperature: float = 0.0) -> Optional[BaseChatModel]:
    """
    Instantiates and returns a LangChain chat model based on `settings.llm_provider`.
    Returns None if provider is 'mock' or if required credentials are unavailable.
    """
    provider = settings.llm_provider.lower() if settings.llm_provider else "mock"

    if provider == "mock":
        return None

    try:
        if provider == "groq":
            api_key = settings.groq_api_key or os.getenv("GROQ_API_KEY")
            if not api_key:
                logger.warning("[LLM] GROQ_API_KEY not configured. Falling back to deterministic mode.")
                return None
            from langchain_groq import ChatGroq
            model_name = settings.llm_model or "llama-3.3-70b-versatile"
            return ChatGroq(
                model_name=model_name,
                api_key=api_key,
                temperature=temperature
            )

        elif provider == "nvidia":
            api_key = settings.nvidia_api_key or os.getenv("NVIDIA_API_KEY")
            if not api_key:
                logger.warning("[LLM] NVIDIA_API_KEY not configured. Falling back to deterministic mode.")
                return None
            from langchain_openai import ChatOpenAI
            model_name = settings.llm_model or "meta/llama-3.3-70b-instruct"
            return ChatOpenAI(
                model=model_name,
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=api_key,
                temperature=temperature
            )

        elif provider == "vertexai":
            from langchain_google_vertexai import ChatVertexAI
            model_name = settings.llm_model or settings.vertex_model_name
            return ChatVertexAI(
                model_name=model_name,
                project=settings.gcp_project_id,
                location=settings.gcp_location,
                temperature=temperature
            )

        elif provider == "google_genai":
            api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.warning("[LLM] GEMINI_API_KEY not configured. Falling back to deterministic mode.")
                return None
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                model_name = settings.llm_model or "gemini-1.5-flash"
                return ChatGoogleGenerativeAI(
                    model=model_name,
                    google_api_key=api_key,
                    temperature=temperature
                )
            except ImportError:
                # Fallback to OpenAI-compatible endpoint if available
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(
                    model=settings.llm_model or "gemini-1.5-flash",
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    api_key=api_key,
                    temperature=temperature
                )

        else:
            logger.warning(f"[LLM] Unknown provider '{provider}'. Falling back to deterministic mode.")
            return None

    except Exception as e:
        logger.error(f"[LLM] Error initializing model for provider '{provider}': {e}. Falling back to deterministic mode.")
        return None
