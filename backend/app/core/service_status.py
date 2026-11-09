"""
Registry of which backend served each security/infrastructure service during the process lifetime.

Every GCP-backed component (Model Armor, Cloud DLP, LLM, checkpointer, GCS) records here whether a call was served
by the real GCP service, by the deliberately local implementation, or by a local *fallback* after a GCP failure.
This lets scripts report whether a run was pure GCP or silently degraded.
"""

import threading
from typing import Dict, Optional

GCP = "gcp"
LOCAL = "local"
LOCAL_FALLBACK = "local_fallback"

SERVICES = ("model_armor", "dlp", "llm", "checkpointer", "gcs")
BACKENDS = (GCP, LOCAL, LOCAL_FALLBACK)


class ServiceStatus:
    """Thread-safe counters per (service, backend) plus the last fallback reason per service."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Dict[str, Dict[str, int]] = {}
        self._last_reason: Dict[str, str] = {}

    def record(self, service: str, backend: str, reason: Optional[str] = None) -> None:
        if backend not in BACKENDS:
            raise ValueError(f"Backend desconocido: '{backend}'. Válidos: {BACKENDS}")
        with self._lock:
            counts = self._counts.setdefault(service, {})
            counts[backend] = counts.get(backend, 0) + 1
            if reason and backend == LOCAL_FALLBACK:
                self._last_reason[service] = reason

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        """Per service: {"gcp": n, "local": n, "local_fallback": n, "last_fallback_reason": str | None}."""
        with self._lock:
            return {
                service: {
                    **{b: counts.get(b, 0) for b in BACKENDS},
                    "last_fallback_reason": self._last_reason.get(service),
                }
                for service, counts in self._counts.items()
            }

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._last_reason.clear()

    def has_fallback(self) -> bool:
        with self._lock:
            return any(counts.get(LOCAL_FALLBACK, 0) for counts in self._counts.values())


service_status = ServiceStatus()
