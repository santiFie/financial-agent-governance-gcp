"""
Secure LangGraph Checkpointer.
Provides session state persistence with support for MemorySaver and GCP Cloud Firestore.
Ensures checkpoint data is isolated and safely stored per thread/session.
"""

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from app.config import settings
from app.core.service_status import GCP, LOCAL, LOCAL_FALLBACK, service_status

logger = logging.getLogger(__name__)


def build_firestore_checkpointer() -> Any:
    """Builds the Firestore saver and verifies it with a real round trip. Raises if Firestore is not usable."""
    from google.cloud import firestore

    from app.db.firestore_saver import FirestoreSaver

    client = firestore.Client(project=settings.gcp_project_id)
    saver = FirestoreSaver(
        client,
        settings.firestore_collection,
        ttl_seconds=settings.firestore_checkpoint_ttl_seconds,
    )
    saver.verify_connection()
    return saver


def get_checkpointer() -> Any:
    """
    Returns the configured LangGraph checkpointer.
    - MemorySaver for local zero-dependency testing and development.
    - FirestoreSaver (CHECKPOINTER_TYPE=firestore) for persistent, serverless state storage in GCP.
      If Firestore cannot be reached at startup, the fallback to MemorySaver is logged and recorded in service_status.
    """
    if settings.checkpointer_type == "firestore":
        try:
            saver = build_firestore_checkpointer()
        except Exception as e:
            logger.warning(
                "[CHECKPOINTER] No se pudo usar Cloud Firestore (%s): se usa MemorySaver LOCAL "
                "(el estado se pierde al reiniciar el proceso).",
                e,
            )
            service_status.record("checkpointer", LOCAL_FALLBACK, str(e))
            return MemorySaver()
        logger.info(
            "[CHECKPOINTER] Cloud Firestore (proyecto %s, colección %s).",
            settings.gcp_project_id,
            settings.firestore_collection,
        )
        service_status.record("checkpointer", GCP)
        return saver

    service_status.record("checkpointer", LOCAL)
    return MemorySaver()


# Global checkpointer instance shared across graph runs
checkpointer = get_checkpointer()
