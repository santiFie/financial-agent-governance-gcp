"""Offline tests for DocumentIngestionService (local_storage vs GCS with local fallback)."""

from unittest.mock import patch

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
