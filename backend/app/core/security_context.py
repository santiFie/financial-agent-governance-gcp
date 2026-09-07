"""
Security Context and Token Vault.
Ensures strict Defense-in-Depth by isolating raw sensitive PII in a secure vault
so that LLM context windows only receive surrogate anonymized tokens.
"""
from typing import Dict, Optional, Tuple
import threading


class TokenVault:
    """
    In-memory isolated vault that manages mapping between surrogate tokens and raw PII.
    In enterprise production, this can be backed by GCP Cloud KMS / Secret Manager.
    """
    def __init__(self):
        # thread-safe storage: session_id -> {token: raw_value}
        self._vault: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()

    def store_token(self, session_id: str, token: str, raw_value: str) -> None:
        with self._lock:
            if session_id not in self._vault:
                self._vault[session_id] = {}
            self._vault[session_id][token] = raw_value

    def resolve_token(self, session_id: str, token: str) -> Optional[str]:
        with self._lock:
            return self._vault.get(session_id, {}).get(token)

    def get_all_session_tokens(self, session_id: str) -> Dict[str, str]:
        with self._lock:
            return dict(self._vault.get(session_id, {}))

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._vault:
                del self._vault[session_id]


# Global instance of the token vault
token_vault = TokenVault()
