"""
Telemetry and Observability Module.
Provides helper utilities to instrument LangGraph multi-agent runs,
enriching execution traces with contextual tags, user/session metadata,
and unique trace IDs for LangSmith and Langfuse auditing.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


def build_trace_config(
    session_id: str,
    user_id: str,
    endpoint: str,
    run_name: str = "financial_assistant_pipeline",
    extra_metadata: Optional[Dict[str, Any]] = None,
    extra_tags: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Constructs an execution config dictionary for LangGraph invocations,
    generating a unique trace_id UUID that serves as the root run ID.

    Returns:
        Tuple of (config_dict, trace_id_str)
    """
    trace_id_uuid = uuid.uuid4()
    trace_id_str = str(trace_id_uuid)

    tags = [
        "financial-assistant",
        f"env:{settings.environment}",
        f"security:{settings.security_mode}",
        f"llm:{settings.llm_provider}",
    ]
    if extra_tags:
        tags.extend(extra_tags)

    metadata: Dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "endpoint": endpoint,
        "environment": settings.environment,
        "security_mode": settings.security_mode,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model or "default",
        "trace_id": trace_id_str,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    config: Dict[str, Any] = {
        "configurable": {"thread_id": session_id},
        "tags": tags,
        "metadata": metadata,
        "run_name": run_name,
        "run_id": trace_id_uuid,
    }

    return config, trace_id_str


def get_telemetry_status() -> Dict[str, Any]:
    """
    Returns the current status of the tracing/observability subsystem.
    """
    is_active = bool(settings.langchain_tracing_v2 and settings.langchain_api_key)
    return {
        "tracing_enabled": is_active,
        "provider": "LangSmith",
        "project": settings.langchain_project,
        "endpoint": settings.langchain_endpoint,
    }
