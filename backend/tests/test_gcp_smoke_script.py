"""Offline tests for scripts/gcp_smoke_test.py: every check is exercised with mocked GCP clients (no network)."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.core.service_status import GCP, LOCAL_FALLBACK, service_status
from app.models.api_schemas import DetectedPII
from tests.fakes_firestore import FakeFirestoreClient

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "gcp_smoke_test.py"


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("gcp_smoke_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_status():
    service_status.reset()
    yield
    service_status.reset()


# --- runner ------------------------------------------------------------------------------------------------------


def test_run_checks_turns_an_unexpected_exception_into_a_failure(smoke):
    def boom():
        raise RuntimeError("kaput")

    results = smoke.run_checks(["a", "b"], {"a": boom, "b": lambda: smoke.CheckResult("b", smoke.OK)})

    assert [r.status for r in results] == [smoke.FAIL, smoke.OK]
    assert "kaput" in results[0].detail


def test_exit_code_fails_only_on_failures_not_on_warnings_or_skips(smoke):
    ok, warn, skip, fail = (smoke.CheckResult("x", s) for s in (smoke.OK, smoke.WARN, smoke.SKIP, smoke.FAIL))
    assert smoke.exit_code([ok, warn, skip]) == 0
    assert smoke.exit_code([ok, fail]) == 1


def test_bootstrap_forces_gcp_configuration_and_uses_explicit_project(smoke, monkeypatch):
    monkeypatch.setattr(os, "environ", {"SECURITY_MODE": "local_mock", "LLM_PROVIDER": "groq"})
    monkeypatch.chdir(Path.cwd())

    project = smoke.bootstrap_environment("mi-proyecto", env_file=str(Path("/nonexistent/.env.gcp")))

    assert project == "mi-proyecto"
    env = os.environ
    assert env["GCP_PROJECT_ID"] == "mi-proyecto"
    assert (env["SECURITY_MODE"], env["LLM_PROVIDER"], env["CHECKPOINTER_TYPE"]) == (
        "gcp_live",
        "vertexai",
        "firestore",
    )
    assert env["USE_GCP_MODEL_ARMOR"] == "true" and env["MODEL_ARMOR_TEMPLATE_ID"] == "aegisbank-template"


def test_resolve_project_ignores_placeholders_and_falls_back_to_gcloud(smoke, monkeypatch):
    monkeypatch.setattr(os, "environ", {"GCP_PROJECT_ID": "your-gcp-project-id"})
    monkeypatch.setattr(smoke, "_gcloud", lambda *a: "proyecto-gcloud")

    assert smoke.resolve_project(None) == "proyecto-gcloud"


# --- DLP ---------------------------------------------------------------------------------------------------------


def _fake_dlp(monkeypatch, *, backend, sanitized, types):
    from app.core import dlp_sanitizer as module

    def sanitize(text, session_id):
        service_status.record("dlp", backend, "403 denied" if backend == LOCAL_FALLBACK else None)
        return sanitized, [DetectedPII(info_type=t, token_assigned=f"[{t}_1]") for t in types]

    monkeypatch.setattr(module.dlp_sanitizer, "sanitize", sanitize)


def test_dlp_passes_when_cloud_dlp_answers_and_pii_is_tokenized(smoke, monkeypatch):
    _fake_dlp(
        monkeypatch,
        backend=GCP,
        sanitized="CBU [CBU_BANCARIO_1] mail [EMAIL_ADDRESS_1]",
        types=["CBU_BANCARIO", "EMAIL_ADDRESS", "CREDIT_CARD_NUMBER"],
    )
    assert smoke.check_dlp().status == smoke.OK


def test_dlp_fails_when_it_silently_fell_back_to_the_local_mock(smoke, monkeypatch):
    _fake_dlp(monkeypatch, backend=LOCAL_FALLBACK, sanitized="[CBU_BANCARIO_1]", types=["CBU_BANCARIO"])
    result = smoke.check_dlp()
    assert result.status == smoke.FAIL and any("403" in h for h in result.hints)


def test_dlp_fails_when_pii_leaks_or_expected_types_are_missing(smoke, monkeypatch):
    _fake_dlp(monkeypatch, backend=GCP, sanitized="CBU 0720412088000007788336", types=["CBU_BANCARIO", "EMAIL_ADDRESS"])
    assert smoke.check_dlp().status == smoke.FAIL
    _fake_dlp(monkeypatch, backend=GCP, sanitized="todo tokenizado", types=["CBU_BANCARIO"])
    assert smoke.check_dlp().status == smoke.FAIL


# --- Model Armor -------------------------------------------------------------------------------------------------


class _FakeGuard:
    """benign: pass, jailbreak: block (configurable); every call is attributed to the given backend."""

    backend = GCP
    block_jailbreak = True
    block_document = False

    def inspect(self, text):
        service_status.record("model_armor", self.backend, "404 template not found")
        if "DAN" in text:
            return (True, "MODEL_ARMOR_VIOLATION", "unsafe") if self.block_jailbreak else (False, None, None)
        if "COMPROBANTE DE TRANSFERENCIA" in text.upper():
            return (True, "MODEL_ARMOR_VIOLATION", "unsafe") if self.block_document else (False, None, None)
        return False, None, None


def _install_guard(monkeypatch, **attrs):
    from app.core import model_armor as module

    guard = type("G", (_FakeGuard,), attrs)
    monkeypatch.setattr(module, "HybridModelArmorGuard", guard)


def test_model_armor_passes_with_real_service_blocking_jailbreak(smoke, monkeypatch):
    _install_guard(monkeypatch)
    assert smoke.check_model_armor().status == smoke.OK


def test_model_armor_fails_on_local_fallback_and_on_unblocked_jailbreak(smoke, monkeypatch):
    _install_guard(monkeypatch, backend=LOCAL_FALLBACK)
    failed = smoke.check_model_armor()
    assert failed.status == smoke.FAIL and any("404" in h for h in failed.hints)

    _install_guard(monkeypatch, block_jailbreak=False)
    assert smoke.check_model_armor().status == smoke.FAIL


def test_model_armor_warns_when_the_attack_document_is_blocked(smoke, monkeypatch):
    _install_guard(monkeypatch, block_document=True)
    result = smoke.check_model_armor()
    assert result.status == smoke.WARN and result.data["comprobante_blocked"] is True


# --- LLM model probe ---------------------------------------------------------------------------------------------


def test_llm_picks_the_first_candidate_that_answers_with_tool_calling(smoke, monkeypatch):
    calls = []

    def attempt(model, location):
        calls.append((model, location))
        if model == "retired-model":
            raise RuntimeError("404 model not found")
        return smoke.CheckResult("llm", smoke.OK, f"{model}@{location}", data={"model": model})

    monkeypatch.setattr(smoke, "_llm_attempt", attempt)
    monkeypatch.setattr(settings, "vertex_location", None)
    monkeypatch.setattr(settings, "gcp_location", "us-central1")

    result = smoke.check_llm(["retired-model", "good-model", "never-tried"])

    assert result.status == smoke.OK and result.data["model"] == "good-model"
    assert ("retired-model", "us-central1") in calls and ("retired-model", "global") in calls
    assert not any(m == "never-tried" for m, _ in calls)
    assert any("VERTEX_MODEL_NAME=good-model" in h for h in result.hints)


def test_llm_tries_the_global_location_when_the_regional_one_fails(smoke, monkeypatch):
    def attempt(model, location):
        if location != "global":
            raise RuntimeError("404 not in this region")
        return smoke.CheckResult("llm", smoke.OK, "ok", data={"model": model})

    monkeypatch.setattr(smoke, "_llm_attempt", attempt)
    monkeypatch.setattr(settings, "vertex_location", None)
    monkeypatch.setattr(settings, "gcp_location", "us-central1")

    result = smoke.check_llm(["m"])

    assert result.status == smoke.OK and any("VERTEX_LOCATION=global" in h for h in result.hints)


def test_llm_fails_listing_every_error_when_no_candidate_works(smoke, monkeypatch):
    monkeypatch.setattr(smoke, "_llm_attempt", MagicMock(side_effect=RuntimeError("403 denied")))
    result = smoke.check_llm(["a", "b"])
    assert result.status == smoke.FAIL and sum("403" in h for h in result.hints) == 4  # 2 models x 2 locations


def test_llm_attempt_requires_a_tool_call(smoke, monkeypatch):
    plain = SimpleNamespace(usage_metadata={"input_tokens": 5, "output_tokens": 1}, tool_calls=[])
    llm = MagicMock()
    llm.invoke.return_value = plain
    llm.bind_tools.return_value.invoke.return_value = plain
    import app.core.llm as llm_module

    monkeypatch.setattr(llm_module, "get_chat_model", lambda **kw: llm)

    assert smoke._llm_attempt("m", "global").status == smoke.FAIL


# --- Firestore / GCS / OPA ---------------------------------------------------------------------------------------


def _patch_firestore(monkeypatch, client, graph_saver):
    from google.cloud import firestore

    from app.db import checkpointer as checkpointer_module

    monkeypatch.setattr(firestore, "Client", lambda project=None: client)
    monkeypatch.setattr(checkpointer_module, "checkpointer", graph_saver)


def test_firestore_passes_with_document_and_checkpoint_round_trip(smoke, monkeypatch):
    from app.db.firestore_saver import FirestoreSaver

    client = FakeFirestoreClient()
    _patch_firestore(monkeypatch, client, FirestoreSaver(client, "c"))

    result = smoke.check_firestore()

    assert result.status == smoke.OK, result.detail
    assert client.store.docs == {}  # the smoke test cleans up after itself


def test_firestore_fails_when_the_graph_checkpointer_fell_back_to_memory(smoke, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    _patch_firestore(monkeypatch, FakeFirestoreClient(), MemorySaver())

    result = smoke.check_firestore()

    assert result.status == smoke.FAIL and "MemorySaver" in result.detail


def test_firestore_fails_with_setup_hints_when_the_api_errors(smoke, monkeypatch):
    from app.db.firestore_saver import FirestoreSaver

    client = FakeFirestoreClient()
    client.store.fail_with = PermissionError("403 Cloud Firestore API has not been used")
    _patch_firestore(monkeypatch, client, FirestoreSaver(client, "c"))

    result = smoke.check_firestore()

    assert result.status == smoke.FAIL and any("databases create" in h for h in result.hints)


def test_gcs_is_skipped_unless_enabled_and_fails_when_the_object_is_unreadable(smoke, monkeypatch):
    monkeypatch.setattr(settings, "use_gcs_ingestion", False)
    assert smoke.check_gcs().status == smoke.SKIP

    from app.core import document_loader

    monkeypatch.setattr(settings, "use_gcs_ingestion", True)
    monkeypatch.setattr(settings, "gcs_bucket_name", "bucket-x")
    monkeypatch.setattr(
        document_loader.DocumentIngestionService, "_load_from_gcs", MagicMock(side_effect=RuntimeError("404"))
    )
    assert smoke.check_gcs().status == smoke.FAIL

    monkeypatch.setattr(document_loader.DocumentIngestionService, "_load_from_gcs", lambda self, name: "contenido")
    assert smoke.check_gcs().status == smoke.OK


def test_opa_down_is_a_warning_not_a_failure(smoke, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", MagicMock(side_effect=httpx.ConnectError("refused")))

    result = smoke.check_opa()

    assert result.status == smoke.WARN and smoke.exit_code([result]) == 0
