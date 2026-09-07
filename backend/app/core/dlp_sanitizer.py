"""
Google Cloud DLP (Sensitive Data Protection) Integration and Local Fallback Sanitizer.
Implements de-identification of PII before text enters the LangGraph agent flow.
"""
import re
import time
from typing import List, Tuple, Dict
from app.config import settings
from app.core.security_context import token_vault
from app.models.api_schemas import DetectedPII, SecurityMetadata


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
            "[TARJETA_CREDITO_{id}]"
        ),
        "CBU": (
            r"\b\d{22}\b",
            lambda s: s if len(s) == 22 else None,
            "[CBU_BANCARIO_{id}]"
        ),
        "DNI": (
            r"\b(?:DNI|dni|Documento|documento)?\s*(\d{7,8})\b",
            lambda s: re.search(r"\d{7,8}", s).group(0) if re.search(r"\d{7,8}", s) else None,
            "[DNI_{id}]"
        ),
        "EMAIL": (
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b",
            lambda s: s,
            "[EMAIL_{id}]"
        ),
    }

    def __init__(self):
        self._dlp_client = None
        if settings.security_mode == "gcp_live":
            try:
                from google.cloud import dlp_v2
                self._dlp_client = dlp_v2.DlpServiceClient()
            except Exception as e:
                print(f"[WARN] Cloud DLP client could not be initialized: {e}. Falling back to deterministic local mock.")

    def sanitize(self, text: str, session_id: str) -> Tuple[str, List[DetectedPII]]:
        """
        De-identifies text using Cloud DLP if live, otherwise uses local rule-based tokenizer.
        Stores the mapping securely in TokenVault.
        """
        if self._dlp_client and settings.security_mode == "gcp_live":
            return self._sanitize_with_gcp_dlp(text, session_id)
        else:
            return self._sanitize_local_mock(text, session_id)

    def _sanitize_local_mock(self, text: str, session_id: str) -> Tuple[str, List[DetectedPII]]:
        sanitized_text = text
        detected: List[DetectedPII] = []
        counters: Dict[str, int] = {}

        # 1. CBU Check (exact 22 digits)
        cbu_matches = list(re.finditer(r"\b\d{22}\b", sanitized_text))
        for match in sorted(cbu_matches, key=lambda m: m.start(), reverse=True):
            raw_cbu = match.group(0)
            counters["CBU"] = counters.get("CBU", 0) + 1
            token = f"[CBU_BANCARIO_{counters['CBU']}]"
            token_vault.store_token(session_id, token, raw_cbu)
            sanitized_text = sanitized_text[:match.start()] + token + sanitized_text[match.end():]
            detected.append(DetectedPII(
                info_type="CBU_BANCARIO",
                token_assigned=token,
                start_offset=match.start(),
                end_offset=match.end()
            ))

        # 2. Credit Card Check (13 to 16 digits with optional spaces or dashes)
        card_matches = list(re.finditer(r"\b(?:\d[ -]*?){13,16}\b", sanitized_text))
        for match in sorted(card_matches, key=lambda m: m.start(), reverse=True):
            raw_matched = match.group(0)
            clean_digits = re.sub(r"[\s-]", "", raw_matched)
            if 13 <= len(clean_digits) <= 16 and not raw_matched.startswith("["):
                counters["CARD"] = counters.get("CARD", 0) + 1
                token = f"[TARJETA_CREDITO_{counters['CARD']}]"
                token_vault.store_token(session_id, token, clean_digits)
                sanitized_text = sanitized_text[:match.start()] + token + sanitized_text[match.end():]
                detected.append(DetectedPII(
                    info_type="CREDIT_CARD_NUMBER",
                    token_assigned=token,
                    start_offset=match.start(),
                    end_offset=match.end()
                ))

        # 3. DNI Check (7 or 8 digits, optionally preceded by DNI keyword)
        dni_matches = list(re.finditer(r"(?i)\b(?:dni\s*[:#]?\s*|\b)(\d{7,8})\b", sanitized_text))
        for match in sorted(dni_matches, key=lambda m: m.start(), reverse=True):
            raw_dni = match.group(1)
            # Avoid replacing already replaced tokens
            if not match.group(0).startswith("["):
                counters["DNI"] = counters.get("DNI", 0) + 1
                token = f"[DNI_{counters['DNI']}]"
                token_vault.store_token(session_id, token, raw_dni)
                sanitized_text = sanitized_text[:match.start(1)] + token + sanitized_text[match.end(1):]
                detected.append(DetectedPII(
                    info_type="ARGENTINA_DNI",
                    token_assigned=token,
                    start_offset=match.start(1),
                    end_offset=match.end(1)
                ))

        # 4. Email check
        email_matches = list(re.finditer(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b", sanitized_text))
        for match in sorted(email_matches, key=lambda m: m.start(), reverse=True):
            raw_email = match.group(0)
            counters["EMAIL"] = counters.get("EMAIL", 0) + 1
            token = f"[EMAIL_{counters['EMAIL']}]"
            token_vault.store_token(session_id, token, raw_email)
            sanitized_text = sanitized_text[:match.start()] + token + sanitized_text[match.end():]
            detected.append(DetectedPII(
                info_type="EMAIL_ADDRESS",
                token_assigned=token,
                start_offset=match.start(),
                end_offset=match.end()
            ))

        return sanitized_text, detected

    def _sanitize_with_gcp_dlp(self, text: str, session_id: str) -> Tuple[str, List[DetectedPII]]:
        """
        Calls Google Cloud DLP inspectContent and deidentifyContent APIs.
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
                {
                    "info_type": {"name": "CBU_BANCARIO"},
                    "regex": {"pattern": r"\b\d{22}\b"}
                },
                {
                    "info_type": {"name": "ARGENTINA_DNI"},
                    "regex": {"pattern": r"\b\d{7,8}\b"}
                }
            ],
            "min_likelihood": dlp_v2.Likelihood.POSSIBLE,
        }

        # Inspect first to capture tokens in TokenVault
        response = self._dlp_client.inspect_content(
            request={"parent": parent, "inspect_config": inspect_config, "item": item}
        )

        # Fallback to local mock if response has no findings but patterns match, or process findings
        detected: List[DetectedPII] = []
        if not response.result.findings:
            return self._sanitize_local_mock(text, session_id)

        # Process findings in reverse order to replace with tokens
        findings = sorted(response.result.findings, key=lambda f: f.location.byte_range.start, reverse=True)
        sanitized_text = text
        counter = 1

        for f in findings:
            info_type = f.info_type.name
            start = f.location.byte_range.start
            end = f.location.byte_range.end
            raw_val = text[start:end]
            token = f"[{info_type}_{counter}]"
            counter += 1

            token_vault.store_token(session_id, token, raw_val)
            sanitized_text = sanitized_text[:start] + token + sanitized_text[end:]
            detected.append(DetectedPII(
                info_type=info_type,
                token_assigned=token,
                start_offset=start,
                end_offset=end
            ))

        return sanitized_text, detected


dlp_sanitizer = DLPSanitizer()
