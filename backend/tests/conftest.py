"""Shared pytest fixtures."""
from pathlib import Path

import pytest

from app.core.document_loader import document_ingestion_service

FIXTURES_DOCS_DIR = Path(__file__).parent / "fixtures" / "documents"


@pytest.fixture(autouse=True)
def fixture_documents_dir(monkeypatch):
    """Test documents (including adversarial payloads) live under tests/fixtures, never in app data."""
    monkeypatch.setattr(document_ingestion_service, "base_dir", FIXTURES_DOCS_DIR.resolve())
    yield
