"""
Model Armor / Prompt Injection Guardrail.
Inspects incoming user queries and attached documents for adversarial attacks, jailbreaks,
system prompt overrides, and goal hijacking before passing them to the agent graph.
"""

import logging
import re
from typing import Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


class LocalModelArmorGuard:
    """
    Simulates Google Cloud Model Armor / Guardrail defense.
    Evaluates adversarial inputs deterministically to prevent prompt injection.
    """

    # Signatures of prompt injection and adversarial manipulation
    INJECTION_SIGNATURES = [
        (
            r"(?i)\b(ignore|ignora|desobedece|olvida)\b.*?\b(all|todas|previas|anteriores|instrucciones|instructions|reglas|rules)\b",
            "INSTRUCTION_OVERRIDE",
        ),
        (
            r"(?i)\b(system override|override system|modo desarrollador|developer mode|dan mode|jailbreak)\b",
            "JAILBREAK_ATTEMPT",
        ),
        (
            r"(?i)\b(act as|actúa como|simula ser)\b.*?\b(root|admin|superadmin|hacker|unrestricted)\b",
            "ROLEPLAY_HIJACKING",
        ),
        (
            r"(?i)\b(transfer|transfiera|transferir)\b.*?\b(sin confirmación|without approval|bypass|sin autorización|directo)\b",
            "SECURITY_BYPASS",
        ),
        (r"(?i)(<system>|\[SYSTEM\]|\{\{system\}\}|```system)", "DELIMITER_INJECTION"),
        (
            r"(?i)\b(muestra|revela|print|leak|exfiltrate)\b.*?\b(system prompt|prompt inicial|instrucciones del sistema)\b",
            "PROMPT_LEAK",
        ),
    ]

    def inspect(self, text: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Inspects the input text.
        Returns:
            - is_threat: bool (True if malicious pattern detected)
            - threat_type: str (Identifier of threat category)
            - reason: str (Human-readable explanation for security logging)
        """
        for pattern, threat_type in self.INJECTION_SIGNATURES:
            match = re.search(pattern, text)
            if match:
                matched_snippet = match.group(0)
                reason = f"Patrón sospechoso detectado ({threat_type}): '{matched_snippet}'"
                return True, threat_type, reason

        return False, None, None

    def inspect_prompt(self, text: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Unified method for prompt inspection."""
        return self.inspect(text)


class HybridModelArmorGuard:
    """
    Hybrid Model Armor wrapper:
    - Uses Google Cloud Model Armor via ModelArmorSanitizePromptRunnable when enabled.
    - Falls back to LocalModelArmorGuard when disabled, offline, or upon client error.
    """

    def __init__(self):
        self.local_guard = LocalModelArmorGuard()
        self._gcp_runnable = None
        self._initialized = False

    def _get_runnable(self):
        if not self._initialized:
            self._initialized = True
            if settings.use_gcp_model_armor and settings.model_armor_template_id:
                try:
                    from langchain_google_community.model_armor import ModelArmorSanitizePromptRunnable

                    self._gcp_runnable = ModelArmorSanitizePromptRunnable(
                        project=settings.gcp_project_id,
                        location=settings.gcp_location,
                        template_id=settings.model_armor_template_id,
                    )
                except Exception as e:
                    logger.warning(f"No se pudo inicializar GCP Model Armor: {e}. Usando guardrail local.")
                    self._gcp_runnable = None
        return self._gcp_runnable

    def inspect(self, text: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Inspects input text for security threats using GCP Model Armor or local guardrail.
        """
        runnable = self._get_runnable()
        if runnable is not None:
            try:
                res = runnable.invoke(text)
                if isinstance(res, dict) and res.get("is_threat"):
                    return (
                        True,
                        res.get("threat_category", "MODEL_ARMOR_VIOLATION"),
                        res.get("threat_reason", "GCP Model Armor detected threat"),
                    )
                return self.local_guard.inspect(text)
            except Exception as e:
                err_msg = str(e).lower()
                if "violation" in err_msg or "blocked" in err_msg or "threat" in err_msg:
                    return True, "MODEL_ARMOR_VIOLATION", str(e)
                logger.warning(f"Error invocando GCP Model Armor: {e}. Realizando fallback local.")
                return self.local_guard.inspect(text)

        return self.local_guard.inspect(text)

    def inspect_prompt(self, text: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Unified method alias for prompt and document inspection."""
        return self.inspect(text)


ModelArmorGuard = HybridModelArmorGuard
model_armor = HybridModelArmorGuard()
