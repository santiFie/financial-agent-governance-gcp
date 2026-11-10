"""DLPSanitizer in gcp_live mode against a fake Cloud DLP client (no network, no credentials)."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.core.dlp_sanitizer import DLPSanitizer
from app.core.security_context import token_vault
from app.core.service_status import service_status

CBU_A = "0720412088000007788336"
CBU_B = "0290021110000045678912"


def _finding(text: str, value: str, info_type: str, *, codepoints: bool = True, occurrence: int = 0):
    """A DLP finding for the `occurrence`-th appearance of `value`; codepoint_range can be omitted (bytes only)."""
    idx = -1
    for _ in range(occurrence + 1):
        idx = text.index(value, idx + 1)
    start, end = idx, idx + len(value)
    byte_start = len(text[:start].encode("utf-8"))
    byte_end = byte_start + len(value.encode("utf-8"))
    return SimpleNamespace(
        info_type=SimpleNamespace(name=info_type),
        location=SimpleNamespace(
            byte_range=SimpleNamespace(start=byte_start, end=byte_end),
            codepoint_range=SimpleNamespace(start=start if codepoints else 0, end=end if codepoints else 0),
        ),
    )


@pytest.fixture
def gcp_sanitizer(monkeypatch):
    service_status.reset()
    sanitizer = DLPSanitizer()  # local_mock at construction: no real client is built
    client = MagicMock(name="dlp_client")
    sanitizer._dlp_client = client
    monkeypatch.setattr(settings, "security_mode", "gcp_live")
    sessions = []

    def session(name: str) -> str:
        sessions.append(name)
        return name

    sanitizer.session = session
    yield sanitizer
    for name in sessions:
        token_vault.clear_session(name)
    service_status.reset()


def _respond(sanitizer, *findings):
    sanitizer._dlp_client.inspect_content.return_value = SimpleNamespace(
        result=SimpleNamespace(findings=list(findings))
    )


def test_offsets_are_code_points_so_accents_do_not_shift_the_replacement(gcp_sanitizer):
    sid = gcp_sanitizer.session("dlp_accents")
    text = f"Titular: GÓMEZ, LÓPEZ MARTÍN. CBU {CBU_A} y mail ñandú@banco.com.ar"
    _respond(
        gcp_sanitizer,
        _finding(text, CBU_A, "CBU_BANCARIO"),
        _finding(text, "ñandú@banco.com.ar", "EMAIL_ADDRESS"),
    )

    sanitized, detected = gcp_sanitizer.sanitize(text, sid)

    assert sanitized == "Titular: GÓMEZ, LÓPEZ MARTÍN. CBU [CBU_BANCARIO_1] y mail [EMAIL_1]"
    assert token_vault.resolve_token(sid, "[CBU_BANCARIO_1]") == CBU_A
    assert token_vault.resolve_token(sid, "[EMAIL_1]") == "ñandú@banco.com.ar"
    assert {d.info_type for d in detected} == {"CBU_BANCARIO", "EMAIL_ADDRESS"}


def test_byte_range_is_converted_when_codepoint_range_is_missing(gcp_sanitizer):
    sid = gcp_sanitizer.session("dlp_bytes")
    text = f"GÓMEZ CBU {CBU_A}"
    _respond(gcp_sanitizer, _finding(text, CBU_A, "CBU_BANCARIO", codepoints=False))

    sanitized, _ = gcp_sanitizer.sanitize(text, sid)

    assert sanitized == "GÓMEZ CBU [CBU_BANCARIO_1]"


def test_tokens_do_not_collide_between_message_and_document_in_the_same_session(gcp_sanitizer):
    sid = gcp_sanitizer.session("dlp_session_stable")

    msg = f"Transferí a {CBU_A}"
    _respond(gcp_sanitizer, _finding(msg, CBU_A, "CBU_BANCARIO"))
    gcp_sanitizer.sanitize(msg, sid)

    doc = f"Destinatario {CBU_B}, origen {CBU_A}"
    _respond(gcp_sanitizer, _finding(doc, CBU_B, "CBU_BANCARIO"), _finding(doc, CBU_A, "CBU_BANCARIO"))
    sanitized_doc, _ = gcp_sanitizer.sanitize(doc, sid)

    assert sanitized_doc == "Destinatario [CBU_BANCARIO_2], origen [CBU_BANCARIO_1]"
    assert token_vault.resolve_token(sid, "[CBU_BANCARIO_1]") == CBU_A
    assert token_vault.resolve_token(sid, "[CBU_BANCARIO_2]") == CBU_B


def test_tokens_are_numbered_by_position(gcp_sanitizer):
    sid = gcp_sanitizer.session("dlp_order")
    text = f"{CBU_A} luego {CBU_B}"
    _respond(gcp_sanitizer, _finding(text, CBU_B, "CBU_BANCARIO"), _finding(text, CBU_A, "CBU_BANCARIO"))

    sanitized, _ = gcp_sanitizer.sanitize(text, sid)

    assert sanitized == "[CBU_BANCARIO_1] luego [CBU_BANCARIO_2]"


def test_overlapping_findings_keep_the_longest_one(gcp_sanitizer):
    sid = gcp_sanitizer.session("dlp_overlap")
    text = f"cuenta {CBU_A} fin"
    _respond(
        gcp_sanitizer,
        _finding(text, "72041208", "ARGENTINA_DNI"),  # 8 digits inside the CBU
        _finding(text, CBU_A, "CBU_BANCARIO"),
    )

    sanitized, detected = gcp_sanitizer.sanitize(text, sid)

    assert sanitized == "cuenta [CBU_BANCARIO_1] fin"
    assert [d.info_type for d in detected] == ["CBU_BANCARIO"]


def test_card_and_dni_use_the_same_token_names_as_the_local_mock(gcp_sanitizer):
    sid = gcp_sanitizer.session("dlp_names")
    text = "tarjeta 4545 1234 5678 9010 dni 38123456"
    _respond(
        gcp_sanitizer,
        _finding(text, "4545 1234 5678 9010", "CREDIT_CARD_NUMBER"),
        _finding(text, "38123456", "ARGENTINA_DNI"),
    )

    sanitized, _ = gcp_sanitizer.sanitize(text, sid)

    assert sanitized == "tarjeta [TARJETA_CREDITO_1] dni [DNI_1]"
    assert token_vault.resolve_token(sid, "[TARJETA_CREDITO_1]") == "4545123456789010"


def test_success_is_recorded_as_gcp(gcp_sanitizer):
    sid = gcp_sanitizer.session("dlp_ok")
    _respond(gcp_sanitizer, _finding(f"x {CBU_A}", CBU_A, "CBU_BANCARIO"))

    gcp_sanitizer.sanitize(f"x {CBU_A}", sid)

    snap = service_status.snapshot()["dlp"]
    assert snap["gcp"] == 1 and snap["local_fallback"] == 0


def test_no_findings_is_a_valid_gcp_answer_and_does_not_fall_back_to_local(gcp_sanitizer):
    sid = gcp_sanitizer.session("dlp_none")
    _respond(gcp_sanitizer)
    gcp_sanitizer._sanitize_local_mock = MagicMock()

    sanitized, detected = gcp_sanitizer.sanitize("consulta de saldo", sid)

    assert (sanitized, detected) == ("consulta de saldo", [])
    gcp_sanitizer._sanitize_local_mock.assert_not_called()
    snap = service_status.snapshot()["dlp"]
    assert snap["gcp"] == 1 and snap["local_fallback"] == 0


def test_api_error_falls_back_to_local_mock_with_log_and_record(gcp_sanitizer, caplog):
    sid = gcp_sanitizer.session("dlp_error")
    gcp_sanitizer._dlp_client.inspect_content.side_effect = RuntimeError("403 PERMISSION_DENIED")

    with caplog.at_level(logging.WARNING, logger="app.core.dlp_sanitizer"):
        sanitized, _ = gcp_sanitizer.sanitize(f"CBU {CBU_A}", sid)

    assert CBU_A not in sanitized and "[CBU_BANCARIO_1]" in sanitized  # the local mock still protects the PII
    assert any("sanitizador LOCAL" in r.message and "403" in r.message for r in caplog.records)
    snap = service_status.snapshot()["dlp"]
    assert snap["local_fallback"] == 1 and snap["gcp"] == 0
    assert "403" in snap["last_fallback_reason"]


def test_missing_client_in_gcp_live_falls_back_with_log_and_record(gcp_sanitizer, caplog):
    sid = gcp_sanitizer.session("dlp_noclient")
    gcp_sanitizer._dlp_client = None
    gcp_sanitizer._init_error = "no credentials"

    with caplog.at_level(logging.WARNING, logger="app.core.dlp_sanitizer"):
        sanitized, _ = gcp_sanitizer.sanitize(f"CBU {CBU_A}", sid)

    assert CBU_A not in sanitized
    assert any("sanitizador LOCAL" in r.message for r in caplog.records)
    assert service_status.snapshot()["dlp"]["local_fallback"] == 1


def test_local_mock_mode_is_recorded_as_local(monkeypatch):
    service_status.reset()
    monkeypatch.setattr(settings, "security_mode", "local_mock")

    DLPSanitizer().sanitize("hola", "dlp_local_mode")

    snap = service_status.snapshot()["dlp"]
    assert snap["local"] == 1 and snap["local_fallback"] == 0
    service_status.reset()
