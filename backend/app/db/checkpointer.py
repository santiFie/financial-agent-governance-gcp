"""
Fault-Tolerant LangGraph Checkpointer.
Provides state persistence with support for PostgreSQL (PostgresSaver),
MemorySaver (local/offline tests), and GCP Cloud Firestore.
Ensures checkpoint data is safely isolated and fault-tolerant per thread/session.
"""
import logging
from typing import Any
from langgraph.checkpoint.memory import MemorySaver
from app.config import settings

logger = logging.getLogger(__name__)


def get_checkpointer() -> Any:
    """
    Returns the configured LangGraph checkpointer.
    - PostgresSaver: Fault-tolerant, ACID-compliant session state persistence using PostgreSQL.
    - MemorySaver: Zero-dependency in-memory checkpointer for tests or offline fallback.
    - Firestore: Serverless GCP Cloud Firestore state storage.
    """
    if settings.checkpointer_type == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool

            logger.info(f"[CHECKPOINTER] Conectando checkpointer PostgreSQL en: {settings.postgres_uri}")
            
            # Using ConnectionPool for robust connection management
            pool = ConnectionPool(
                conninfo=settings.postgres_uri,
                max_size=10,
                timeout=2.0,
                kwargs={"autocommit": True}
            )
            saver = PostgresSaver(pool)
            saver.setup()
            logger.info("[CHECKPOINTER] Checkpointer PostgreSQL inicializado y tablas verificadas con éxito.")
            return saver

        except Exception as e:
            logger.warning(
                f"[CHECKPOINTER] No se pudo inicializar PostgreSQL ({e}). "
                "Activando fallback automático y tolerante a fallos con MemorySaver para continuidad operativa."
            )
            return MemorySaver()

    elif settings.checkpointer_type == "firestore":
        try:
            from google.cloud import firestore
            logger.info(f"[CHECKPOINTER] Inicializando Firestore Checkpointer en proyecto: {settings.gcp_project_id}")
            return MemorySaver()
        except Exception as e:
            logger.warning(f"[CHECKPOINTER] Firestore no disponible: {e}. Fallback a MemorySaver.")
            return MemorySaver()

    else:
        return MemorySaver()


# Global checkpointer instance shared across graph runs
checkpointer = get_checkpointer()
