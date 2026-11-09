"""Offline tests for DocumentIngestionService (local_storage vs GCS with local fallback)."""

from unittest.mock import MagicMock, patch

import pytest

from app.core.document_loader import DocumentIngestionService


@pytest.fixture
def local_doc(tmp_path):
    path = tmp_path / "local.txt"
    path.write_text("contenido local", encoding="utf-8")
    return str(path)


def test_without_gcs_loads_local_document_and_ignores_filename(local_doc):
    service = DocumentIngestionService(local_document_path=local_doc, use_gcs=False)

    assert service.load_document("cualquier_cosa.txt") == "contenido local"
    assert service.load_document("") == "contenido local"


def test_default_local_document_exists():
    assert DocumentIngestionService(use_gcs=False).load_document("x").strip()


def test_missing_local_document_raises(tmp_path):
    service = DocumentIngestionService(local_document_path=str(tmp_path / "nope.txt"), use_gcs=False)

    with pytest.raises(FileNotFoundError):
        service.load_document("x")


def test_gcs_enabled_uses_gcs_content(local_doc):
    service = DocumentIngestionService(local_document_path=local_doc, use_gcs=True, gcs_bucket="b")

    with patch.object(service, "_load_from_gcs", return_value="contenido gcs") as gcs:
        assert service.load_document("doc.txt") == "contenido gcs"
    gcs.assert_called_once_with("doc.txt")


def test_gcs_missing_object_falls_back_to_local(local_doc):
    service = DocumentIngestionService(local_document_path=local_doc, use_gcs=True, gcs_bucket="b")

    with patch.object(service, "_load_from_gcs", side_effect=RuntimeError("404")):
        assert service.load_document("doc.txt") == "contenido local"


def test_gcs_empty_object_falls_back_to_local(local_doc):
    service = DocumentIngestionService(local_document_path=local_doc, use_gcs=True, gcs_bucket="b")

    with patch.object(service, "_load_from_gcs", return_value=""):
        assert service.load_document("doc.txt") == "contenido local"


@pytest.mark.parametrize("name", ["../secrets.env", "/etc/passwd", "", "   "])
def test_gcs_mode_rejects_unsafe_object_names(local_doc, name):
    service = DocumentIngestionService(local_document_path=local_doc, use_gcs=True, gcs_bucket="b")

    with pytest.raises(ValueError):
        service.load_document(name)


def test_gcs_flag_without_bucket_uses_local(local_doc):
    service = DocumentIngestionService(local_document_path=local_doc, use_gcs=True, gcs_bucket=None)
    with patch("app.core.document_loader.settings") as s:
        s.gcs_bucket_name = None
        assert service.load_document("doc.txt") == "contenido local"


# --- GCSFileLoader end to end with a fake google-cloud-storage client ----------------------------------------------


class _FakeBlob:
    def __init__(self, content):
        self.content, self.metadata = content, None

    def download_to_filename(self, path):
        from pathlib import Path

        Path(path).write_text(self.content, encoding="utf-8")


class _FakeBucket:
    def __init__(self, objects):
        self.objects = objects

    def blob(self, name):
        return _FakeBlob(self.objects[name])

    def get_blob(self, name):
        return _FakeBlob(self.objects[name]) if name in self.objects else None


def _fake_storage(monkeypatch, objects):
    from google.cloud import storage

    client = MagicMock()
    client.get_bucket.return_value = _FakeBucket(objects)
    monkeypatch.setattr(storage, "Client", lambda *args, **kwargs: client)


def test_gcs_object_is_read_as_plain_text_without_langchain_community(monkeypatch, local_doc):
    """Regression: GCSFileLoader's default loader needs langchain-community, which is not installed."""
    from app.core.service_status import service_status

    service_status.reset()
    _fake_storage(monkeypatch, {"comprobante.txt": "contenido del bucket con tildes: GÓMEZ"})
    service = DocumentIngestionService(local_document_path=local_doc, use_gcs=True, gcs_bucket="b")

    assert service.load_document("comprobante.txt") == "contenido del bucket con tildes: GÓMEZ"
    assert service_status.snapshot()["gcs"]["gcp"] == 1
    service_status.reset()


def test_gcs_failure_falls_back_to_local_with_log_and_record(monkeypatch, local_doc, caplog):
    import logging

    from app.core.service_status import service_status

    service_status.reset()
    _fake_storage(monkeypatch, {})
    service = DocumentIngestionService(local_document_path=local_doc, use_gcs=True, gcs_bucket="b")

    with caplog.at_level(logging.WARNING, logger="app.core.document_loader"):
        assert service.load_document("falta.txt") == "contenido local"

    assert any("documento LOCAL" in r.message for r in caplog.records)
    assert service_status.snapshot()["gcs"]["local_fallback"] == 1
    service_status.reset()


def test_local_ingestion_is_recorded_as_local(local_doc):
    from app.core.service_status import service_status

    service_status.reset()
    DocumentIngestionService(local_document_path=local_doc, use_gcs=False).load_document("x.txt")
    assert service_status.snapshot()["gcs"]["local"] == 1
    service_status.reset()
