"""
Document Ingestion Service.
Supports local filesystem loading with path traversal defenses,
and Google Cloud Storage (GCS) ingestion with graceful local fallback.
"""
import os
import logging
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class DocumentIngestionService:
    """
    Ingests documents from local storage or Google Cloud Storage.
    Enforces strict path containment to prevent path traversal vulnerabilities.
    """

    def __init__(
        self,
        local_docs_dir: Optional[str] = None,
        use_gcs: Optional[bool] = None,
        gcs_bucket: Optional[str] = None,
    ):
        # Resolve base directory
        backend_dir = Path(__file__).resolve().parent.parent.parent
        custom_dir = local_docs_dir or settings.local_docs_dir

        candidates = [
            Path(custom_dir),
            backend_dir / custom_dir,
            backend_dir / "data" / "documents",
            Path.cwd() / custom_dir,
            Path.cwd() / "backend" / custom_dir,
        ]

        resolved_dir = None
        for cand in candidates:
            if cand.exists() and cand.is_dir():
                resolved_dir = cand.resolve()
                break

        if resolved_dir is None:
            resolved_dir = (backend_dir / "data" / "documents").resolve()

        self.base_dir = resolved_dir
        self.use_gcs = use_gcs if use_gcs is not None else settings.use_gcs_ingestion
        self.gcs_bucket = gcs_bucket or settings.gcs_bucket_name

    def load_document(self, document_name: str) -> str:
        """
        Loads document content by name.
        If GCS ingestion is enabled, attempts GCS loading first and falls back to local.
        Validates local path against directory traversal attacks.
        """
        if not document_name or not document_name.strip():
            raise ValueError("El nombre del documento no puede estar vacío.")

        # Attempt GCS load if configured
        if self.use_gcs and self.gcs_bucket:
            try:
                content = self._load_from_gcs(document_name)
                if content:
                    return content
            except Exception as e:
                logger.warning(
                    f"Error al cargar documento '{document_name}' desde GCS: {e}. "
                    "Haciendo fallback a almacenamiento local."
                )

        # Local loading with path traversal protection
        return self._load_from_local(document_name)

    def _load_from_local(self, document_name: str) -> str:
        """
        Loads document from local directory with strict path traversal checks.
        """
        # Reject obvious path traversal patterns
        if ".." in document_name:
            raise ValueError(
                f"Intento de path traversal detectado para el documento: '{document_name}'"
            )

        if os.path.isabs(document_name):
            target_path = Path(document_name).resolve()
        else:
            target_path = (self.base_dir / document_name).resolve()


        if not target_path.is_relative_to(self.base_dir):
            raise ValueError(
                f"Intento de path traversal detectado para el documento: '{document_name}'"
            )

        if not target_path.exists():
            raise FileNotFoundError(f"Documento no encontrado: {document_name} en {self.base_dir}")

        if not target_path.is_file():
            raise ValueError(f"La ruta especificada no es un archivo válido: {document_name}")

        return target_path.read_text(encoding="utf-8")

    def _load_from_gcs(self, document_name: str) -> str:
        """
        Loads document from Google Cloud Storage using GCSFileLoader or google-cloud-storage.
        """
        try:
            from langchain_google_community import GCSFileLoader
            loader = GCSFileLoader(
                project_name=settings.gcp_project_id,
                bucket=self.gcs_bucket,
                blob=document_name,
            )
            docs = loader.load()
            if docs:
                return docs[0].page_content
        except ImportError:
            pass

        try:
            from google.cloud import storage
            client = storage.Client(project=settings.gcp_project_id)
            bucket = client.bucket(self.gcs_bucket)
            blob = bucket.blob(document_name)
            return blob.download_as_text()
        except Exception as e:
            raise RuntimeError(f"Fallo al descargar blob '{document_name}' desde GCS: {e}")


document_ingestion_service = DocumentIngestionService()
