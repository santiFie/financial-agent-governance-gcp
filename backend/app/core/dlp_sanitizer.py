"""
Google Cloud DLP (Sensitive Data Protection) Integration and Local Fallback Sanitizer.
Implements de-identification of PII before text enters the LangGraph agent flow.
"""

import logging
import re
from typing import Dict, List, Tuple

from app.config import settings
from app.core.security_context import token_vault
from app.core.service_status import GCP, LOCAL, LOCAL_FALLBACK, service_status
from app.models.api_schemas import DetectedPII

logger = logging.getLogger(__name__)

# Cloud DLP infoType -> token prefix. Same token names as the local mock so the rest of the system sees one format.
_DLP_TOKEN_PREFIX = {
    "CBU_BANCARIO": "CBU_BANCARIO",
    "CREDIT_CARD_NUMBER": "TARJETA_CREDITO",
    "ARGENTINA_DNI": "DNI",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "TELEFONO",
}
_TOKEN_NAME_RE = re.compile(r"^\[([A-Z_]+?)_(\d+)\]$")


class DLPSanitizer:
    """
    Sanitizes user input by identifying sensitive PII (CBU, Credit Cards, DNI, Email)
    and replacing them with surrogate tokens ([CBU_1], [TARJETA_CREDITO_1], etc.)
    """

    # Regex patterns for local/mock deterministic mode & fallback
    PATTERNS = {
        "CREDIT_CARD": (
            r"\b(?:\d[ -]*?){13,16}\b",
            lambda s: re.sub(r"[\s-]", "", s) if 13 <= len(re.sub(r"[\s-]", "", s)) <= 16 else None,
            "[TARJETA_CREDITO_{id}]",
        ),
        "CBU": (r"\b\d{22}\b", lambda s: s if len(s) == 22 else None, "[CBU_BANCARIO_{id}]"),
        "DNI": (
            r"\b(?:DNI|dni|Documento|documento)?\s*(\d{7,8})\b",
            lambda s: re.search(r"\d{7,8}", s).group(0) if re.search(r"\d{7,8}", s) else None,
            "[DNI_{id}]",
        ),
        "EMAIL": (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b", lambda s: s, "[EMAIL_{id}]"),
    }

    def __init__(self):
        self._dlp_client = None
        self._init_error = None
        if settings.security_mode == "gcp_live":
            try:
                from google.cloud import dlp_v2

                self._dlp_client = dlp_v2.DlpServiceClient()
            except Exception as e:
                self._init_error = str(e)
                logger.warning(
                    "[DLP] No se pudo inicializar el cliente de Cloud DLP (%s): se usa el sanitizador LOCAL.", e
                )

    def sanitize(self, text: str, session_id: str) -> Tuple[str, List[DetectedPII]]:
        """
        De-identifies text using Cloud DLP if live, otherwise uses local rule-based tokenizer.
        Stores the mapping securely in TokenVault.
        On any Cloud DLP failure the local sanitizer is used, the fallback is logged and recorded in service_status.
        """
        if settings.security_mode != "gcp_live":
            service_status.record("dlp", LOCAL)
            return self._sanitize_local_mock(text, session_id)

        if self._dlp_client is None:
            reason = f"client not initialized: {self._init_error}"
            logger.warning("[DLP] Cliente de Cloud DLP no disponible (%s): se usa el sanitizador LOCAL.", reason)
            service_status.record("dlp", LOCAL_FALLBACK, reason)
            return self._sanitize_local_mock(text, session_id)

        try:
            result = self._sanitize_with_gcp_dlp(text, session_id)
        except Exception as e:
            logger.warning("[DLP] Falló la llamada a Cloud DLP (%s): se usa el sanitizador LOCAL.", e)
            service_status.record("dlp", LOCAL_FALLBACK, f"inspect: {e}")
            return self._sanitize_local_mock(text, session_id)

        service_status.record("dlp", GCP)
        return result

    def _sanitize_local_mock(self, text: str, session_id: str) -> Tuple[str, List[DetectedPII]]:
        sanitized_text = text
        detected: List[DetectedPII] = []
        counters: Dict[str, int] = {}

        # Pre-populate seen mappings from session's TokenVault if present
        session_tokens = token_vault.get_all_session_tokens(session_id)
        seen_cbus: Dict[str, str] = {
            val: tok for tok, val in session_tokens.items() if tok.startswith("[CBU_BANCARIO_")
        }
        for tok in seen_cbus.values():
            m_num = re.search(r"\d+", tok)
            if m_num:
                counters["CBU"] = max(counters.get("CBU", 0), int(m_num.group(0)))

        seen_cards: Dict[str, str] = {
            val: tok for tok, val in session_tokens.items() if tok.startswith("[TARJETA_CREDITO_")
        }
        for tok in seen_cards.values():
            m_num = re.search(r"\d+", tok)
            if m_num:
                counters["CARD"] = max(counters.get("CARD", 0), int(m_num.group(0)))

        seen_dnis: Dict[str, str] = {val: tok for tok, val in session_tokens.items() if tok.startswith("[DNI_")}
        for tok in seen_dnis.values():
            m_num = re.search(r"\d+", tok)
            if m_num:
                counters["DNI"] = max(counters.get("DNI", 0), int(m_num.group(0)))

        seen_emails: Dict[str, str] = {
            val.lower(): tok for tok, val in session_tokens.items() if tok.startswith("[EMAIL_")
        }
        for tok in seen_emails.values():
            m_num = re.search(r"\d+", tok)
            if m_num:
                counters["EMAIL"] = max(counters.get("EMAIL", 0), int(m_num.group(0)))

        # 1. CBU Check (exact 22 digits)
        cbu_matches = list(re.finditer(r"\b\d{22}\b", sanitized_text))
        for m in sorted(cbu_matches, key=lambda m: m.start()):
            raw_cbu = m.group(0)
            if raw_cbu not in seen_cbus:
                counters["CBU"] = counters.get("CBU", 0) + 1
                token = f"[CBU_BANCARIO_{counters['CBU']}]"
                token_vault.store_token(session_id, token, raw_cbu)
                seen_cbus[raw_cbu] = token

        for match in sorted(cbu_matches, key=lambda m: m.start(), reverse=True):
            raw_cbu = match.group(0)
            token = seen_cbus[raw_cbu]
            sanitized_text = sanitized_text[: match.start()] + token + sanitized_text[match.end() :]
            detected.append(
                DetectedPII(
                    info_type="CBU_BANCARIO", token_assigned=token, start_offset=match.start(), end_offset=match.end()
                )
            )

        # 2. Credit Card Check (13 to 16 digits with optional spaces or dashes)
        card_matches = list(re.finditer(r"\b(?:\d[ -]*?){13,16}\b", sanitized_text))
        for m in sorted(card_matches, key=lambda m: m.start()):
            raw_matched = m.group(0)
            clean_digits = re.sub(r"[\s-]", "", raw_matched)
            if 13 <= len(clean_digits) <= 16 and not raw_matched.startswith("["):
                if clean_digits not in seen_cards:
                    counters["CARD"] = counters.get("CARD", 0) + 1
                    token = f"[TARJETA_CREDITO_{counters['CARD']}]"
                    token_vault.store_token(session_id, token, clean_digits)
                    seen_cards[clean_digits] = token

        for match in sorted(card_matches, key=lambda m: m.start(), reverse=True):
            raw_matched = match.group(0)
            clean_digits = re.sub(r"[\s-]", "", raw_matched)
            if 13 <= len(clean_digits) <= 16 and not raw_matched.startswith("[") and clean_digits in seen_cards:
                token = seen_cards[clean_digits]
                sanitized_text = sanitized_text[: match.start()] + token + sanitized_text[match.end() :]
                detected.append(
                    DetectedPII(
                        info_type="CREDIT_CARD_NUMBER",
                        token_assigned=token,
                        start_offset=match.start(),
                        end_offset=match.end(),
                    )
                )

        # 3. DNI Check (7 or 8 digits, optionally preceded by DNI keyword)
        dni_matches = list(re.finditer(r"(?i)\b(?:dni\s*[:#]?\s*|\b)(\d{7,8})\b", sanitized_text))
        for m in sorted(dni_matches, key=lambda m: m.start()):
            raw_dni = m.group(1)
            if not m.group(0).startswith("["):
                if raw_dni not in seen_dnis:
                    counters["DNI"] = counters.get("DNI", 0) + 1
                    token = f"[DNI_{counters['DNI']}]"
                    token_vault.store_token(session_id, token, raw_dni)
                    seen_dnis[raw_dni] = token

        for match in sorted(dni_matches, key=lambda m: m.start(), reverse=True):
            raw_dni = match.group(1)
            if not match.group(0).startswith("[") and raw_dni in seen_dnis:
                token = seen_dnis[raw_dni]
                sanitized_text = sanitized_text[: match.start(1)] + token + sanitized_text[match.end(1) :]
                detected.append(
                    DetectedPII(
                        info_type="ARGENTINA_DNI",
                        token_assigned=token,
                        start_offset=match.start(1),
                        end_offset=match.end(1),
                    )
                )

        # 4. Email check
        email_matches = list(re.finditer(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b", sanitized_text))
        for m in sorted(email_matches, key=lambda m: m.start()):
            raw_email = m.group(0)
            email_key = raw_email.lower()
            if email_key not in seen_emails:
                counters["EMAIL"] = counters.get("EMAIL", 0) + 1
                token = f"[EMAIL_{counters['EMAIL']}]"
                token_vault.store_token(session_id, token, raw_email)
                seen_emails[email_key] = token

        for match in sorted(email_matches, key=lambda m: m.start(), reverse=True):
            raw_email = match.group(0)
            email_key = raw_email.lower()
            if email_key in seen_emails:
                token = seen_emails[email_key]
                sanitized_text = sanitized_text[: match.start()] + token + sanitized_text[match.end() :]
                detected.append(
                    DetectedPII(
                        info_type="EMAIL_ADDRESS",
                        token_assigned=token,
                        start_offset=match.start(),
                        end_offset=match.end(),
                    )
                )

        return sanitized_text, detected

    @staticmethod
    def _finding_span(finding, text: str) -> Tuple[int, int]:
        """Start/end of a DLP finding as indexes into the Python `str` (code points, not UTF-8 bytes)."""
        codepoints = finding.location.codepoint_range
        if codepoints.end > codepoints.start:
            return codepoints.start, codepoints.end
        # Fallback: convert the UTF-8 byte range into code point indexes.
        raw = text.encode("utf-8")
        byte_range = finding.location.byte_range
        return len(raw[: byte_range.start].decode("utf-8", "ignore")), len(
            raw[: byte_range.end].decode("utf-8", "ignore")
        )

    @staticmethod
    def _drop_overlaps(spans: List[Tuple[int, int, str]]) -> List[Tuple[int, int, str]]:
        """Keeps non-overlapping spans, preferring the longest one (e.g. a 22-digit CBU over an 8-digit DNI inside it)."""
        kept: List[Tuple[int, int, str]] = []
        for span in sorted(spans, key=lambda sp: (-(sp[1] - sp[0]), sp[0])):
            if all(span[1] <= k[0] or span[0] >= k[1] for k in kept):
                kept.append(span)
        return sorted(kept, key=lambda sp: sp[0])

    def _sanitize_with_gcp_dlp(self, text: str, session_id: str) -> Tuple[str, List[DetectedPII]]:
        """
        Calls Google Cloud DLP inspectContent and replaces every finding with a session-stable surrogate token.
        Raises on API errors (the caller logs and falls back to the local sanitizer).
        """
        from google.cloud import dlp_v2

        parent = f"projects/{settings.gcp_project_id}/locations/{settings.gcp_location}"
        item = {"value": text}

        inspect_config = {
            "info_types": [
                {"name": "CREDIT_CARD_NUMBER"},
                {"name": "EMAIL_ADDRESS"},
                {"name": "PHONE_NUMBER"},
            ],
            "custom_info_types": [
                {"info_type": {"name": "CBU_BANCARIO"}, "regex": {"pattern": r"\b\d{22}\b"}},
                {"info_type": {"name": "ARGENTINA_DNI"}, "regex": {"pattern": r"\b\d{7,8}\b"}},
            ],
            "min_likelihood": dlp_v2.Likelihood.POSSIBLE,
        }

        response = self._dlp_client.inspect_content(
            request={"parent": parent, "inspect_config": inspect_config, "item": item}
        )

        spans: List[Tuple[int, int, str]] = []
        for f in response.result.findings:
            start, end = self._finding_span(f, text)
            if end > start:
                spans.append((start, end, f.info_type.name))
        spans = self._drop_overlaps(spans)
        if not spans:
            return text, []

        # Session-stable tokens: reuse the token of a value already seen and continue each counter where the vault left it.
        session_tokens = token_vault.get_all_session_tokens(session_id)
        counters: Dict[str, int] = {}
        known: Dict[Tuple[str, str], str] = {}
        for token, value in session_tokens.items():
            m = _TOKEN_NAME_RE.match(token)
            if m:
                counters[m.group(1)] = max(counters.get(m.group(1), 0), int(m.group(2)))
                known[(m.group(1), value.lower() if m.group(1) == "EMAIL" else value)] = token

        detected: List[DetectedPII] = []
        replacements: List[Tuple[int, int, str]] = []
        for start, end, info_type in spans:  # ascending order: tokens are numbered by position
            prefix = _DLP_TOKEN_PREFIX.get(info_type, info_type)
            raw_val = text[start:end]
            if info_type == "CREDIT_CARD_NUMBER":
                raw_val = re.sub(r"[\s-]", "", raw_val)
            key = (prefix, raw_val.lower() if prefix == "EMAIL" else raw_val)
            token = known.get(key)
            if token is None:
                counters[prefix] = counters.get(prefix, 0) + 1
                token = f"[{prefix}_{counters[prefix]}]"
                known[key] = token
                token_vault.store_token(session_id, token, raw_val)
            replacements.append((start, end, token))
            detected.append(DetectedPII(info_type=info_type, token_assigned=token, start_offset=start, end_offset=end))

        sanitized_text = text
        for start, end, token in reversed(replacements):
            sanitized_text = sanitized_text[:start] + token + sanitized_text[end:]

        return sanitized_text, detected


dlp_sanitizer = DLPSanitizer()
