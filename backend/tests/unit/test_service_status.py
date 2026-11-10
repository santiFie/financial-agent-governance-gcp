"""service_status registry, snapshot deltas and the effective LLM label used in run evidence."""

import pytest

from app.config import settings
from app.core.llm import describe_llm, effective_provider
from app.core.service_status import GCP, LOCAL, LOCAL_FALLBACK, ServiceStatus, snapshot_delta


def test_record_rejects_unknown_backends_and_keeps_the_last_fallback_reason():
    status = ServiceStatus()
    with pytest.raises(ValueError):
        status.record("dlp", "cloud")

    status.record("dlp", LOCAL_FALLBACK, "first")
    status.record("dlp", LOCAL_FALLBACK, "second")
    status.record("dlp", GCP)

    snap = status.snapshot()["dlp"]
    assert (snap["gcp"], snap["local_fallback"], snap["last_fallback_reason"]) == (1, 2, "second")
    assert status.has_fallback()
    status.reset()
    assert status.snapshot() == {} and not status.has_fallback()


def test_snapshot_delta_only_reports_what_changed_between_two_snapshots():
    status = ServiceStatus()
    status.record("dlp", GCP)
    status.record("model_armor", GCP)
    before = status.snapshot()

    status.record("dlp", GCP)
    status.record("dlp", LOCAL_FALLBACK, "403")
    status.record("gcs", LOCAL)
    delta = snapshot_delta(before, status.snapshot())

    assert set(delta) == {"dlp", "gcs"}  # model_armor did not change
    assert (delta["dlp"]["gcp"], delta["dlp"]["local_fallback"], delta["dlp"]["last_fallback_reason"]) == (1, 1, "403")
    assert delta["gcs"]["local"] == 1 and delta["gcs"]["last_fallback_reason"] is None


def test_describe_llm_names_the_real_vertex_model_and_location(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "vertexai")
    monkeypatch.setattr(settings, "vertex_model_name", "gemini-3.1-flash-lite")
    monkeypatch.setattr(settings, "vertex_location", "global")

    assert effective_provider(None) == "vertexai"
    assert describe_llm() == "vertexai/gemini-3.1-flash-lite@global"
    assert (
        describe_llm(None, "deepseek-ai/deepseek-v4.1-flash-maas")
        == "vertexai/deepseek-ai/deepseek-v4.1-flash-maas@global"
    )


def test_describe_llm_for_other_providers(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "litellm")
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(settings, "nvidia_api_key", None)
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(settings, "openrouter_model", "openai/gpt-oss-120b")

    assert effective_provider(None) is None
    assert describe_llm("openrouter", "nvidia/nemotron") == "openrouter/nvidia/nemotron"
    assert describe_llm("openrouter") == "openrouter/openai/gpt-oss-120b"
