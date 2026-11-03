"""
Document Ingestion Service.

- GCS ingestion disabled: the single local document (`settings.local_document_path`) is loaded and the
  requested filename is ignored.
- GCS ingestion enabled: the object `filename` is looked up in the configured bucket
  (langchain-google-community `GCSFileLoader`); if it cannot be found or read, the local document is used.
"""
import logging
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class DocumentIngestionService:
    """Loads the document attached to a request, from GCS or from local storage."""

    def __init__(
        self,
        local_document_path: Optional[str] = None,
        use_gcs: Optional[bool] = None,
        gcs_bucket: Optional[str] = None,
    ):
        self._local_document_path = local_document_path
        self._use_gcs = use_gcs
        self._gcs_bucket = gcs_bucket

    # Settings are resolved on every call so they can be overridden at runtime (tests, evals).
    @property
    def local_path(self) -> Path:
        path = Path(self._local_document_path or settings.local_document_path)
        return path if path.is_absolute() else BACKEND_DIR / path

    @property
    def use_gcs(self) -> bool:
        return self._use_gcs if self._use_gcs is not None else settings.use_gcs_ingestion

    @property
    def gcs_bucket(self) -> Optional[str]:
        return self._gcs_bucket or settings.gcs_bucket_name

    def load_document(self, filename: str) -> str:
        """
        Returns the document content. `filename` is only used with GCS; otherwise the local document is loaded.
        Raises ValueError for unsafe object names and FileNotFoundError if no document is available.
        """
        if self.use_gcs and self.gcs_bucket:
            self._validate_object_name(filename)
            try:
                content = self._load_from_gcs(filename)
                if content:
                    return content
            except Exception as e:
                logger.warning(
                    f"Documento '{filename}' no disponible en GCS ({e}). Usando el documento de local_storage."
                )
        return self._load_from_local()

    @staticmethod
    def _validate_object_name(filename: str) -> None:
        if not filename or not filename.strip():
            raise ValueError("El nombre del documento no puede estar vacío.")
        if ".." in filename or filename.startswith("/"):
            raise ValueError(f"Intento de path traversal detectado para el documento: '{filename}'")

    def _load_from_local(self) -> str:
        path = self.local_path
        if not path.is_file():
            raise FileNotFoundError(f"Documento local no encontrado: {path}")
        return path.read_text(encoding="utf-8")

    def _load_from_gcs(self, filename: str) -> str:
        from langchain_google_community import GCSFileLoader

        loader = GCSFileLoader(project_name=settings.gcp_project_id, bucket=self.gcs_bucket, blob=filename)
        docs = loader.load()
        return docs[0].page_content if docs else ""


document_ingestion_service = DocumentIngestionService()
