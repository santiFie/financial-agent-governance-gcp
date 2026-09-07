"""
Model Armor / Prompt Injection Guardrail.
Inspects incoming user queries for adversarial attacks, jailbreaks,
system prompt overrides, and goal hijacking before passing them to the agent graph.
"""
import re
from typing import Tuple, Optional
from app.config import settings


class ModelArmorGuard:
    """
    Simulates Google Cloud Model Armor / Guardrail defense.
    Evaluates adversarial inputs deterministically to prevent prompt injection.
    """

    # Signatures of prompt injection and adversarial manipulation
    INJECTION_SIGNATURES = [
        (r"(?i)\b(ignore|ignora|desobedece|olvida)\b.*?\b(all|todas|previas|anteriores|instrucciones|instructions|reglas|rules)\b", "INSTRUCTION_OVERRIDE"),
        (r"(?i)\b(system override|override system|modo desarrollador|developer mode|dan mode|jailbreak)\b", "JAILBREAK_ATTEMPT"),
        (r"(?i)\b(act as|actúa como|simula ser)\b.*?\b(root|admin|superadmin|hacker|unrestricted)\b", "ROLEPLAY_HIJACKING"),
        (r"(?i)\b(transfer|transfiera|transferir)\b.*?\b(sin confirmación|without approval|bypass|sin autorización|directo)\b", "SECURITY_BYPASS"),
        (r"(?i)(<system>|\[SYSTEM\]|\{\{system\}\}|```system)", "DELIMITER_INJECTION"),
        (r"(?i)\b(muestra|revela|print|leak|exfiltrate)\b.*?\b(system prompt|prompt inicial|instrucciones del sistema)\b", "PROMPT_LEAK"),
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


model_armor = ModelArmorGuard()
