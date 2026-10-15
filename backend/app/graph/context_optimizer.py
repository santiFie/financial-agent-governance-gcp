"""
Context Optimizer for Replanner Agent.
Prevents context window saturation by compacting, truncating, and windowing past execution steps.
"""
from typing import List, Dict, Any


class ContextOptimizer:
    """
    Manages and compresses past execution history to fit safely within the LLM context window.
    Applies sliding-window detailing and deterministic content truncation.
    """
    def __init__(self, max_chars_per_result: int = 400, recent_window_size: int = 4):
        self.max_chars_per_result = max_chars_per_result
        self.recent_window_size = recent_window_size

    def truncate_result(self, raw_result: Any) -> str:
        """Truncates long results or serializes objects safely."""
        if raw_result is None:
            return "Sin resultado explícito"
        text = str(raw_result).strip()
        if len(text) <= self.max_chars_per_result:
            return text
        half = self.max_chars_per_result // 2
        return f"{text[:half]}... [truncado por límite de contexto] ...{text[-half:]}"

    def format_past_steps(self, past_steps: List[Dict[str, Any]]) -> str:
        """
        Formats past_steps into a clean, compact markdown representation.
        Older steps are summarized; recent steps retain actionable details.
        """
        if not past_steps:
            return "No hay tareas ejecutadas previas."

        total_steps = len(past_steps)
        formatted_lines: List[str] = []

        # If history exceeds window, summarize earlier steps concisely
        if total_steps > self.recent_window_size:
            earlier_count = total_steps - self.recent_window_size
            formatted_lines.append(f"ℹ️ [{earlier_count} pasos anteriores completados satisfactoriamente]")
            steps_to_detail = past_steps[-self.recent_window_size:]
        else:
            steps_to_detail = past_steps

        for step in steps_to_detail:
            task_id = step.get("task_id", step.get("id", "?"))
            desc = step.get("description", "Sin descripción")
            tool = step.get("assigned_tool", step.get("tool", "Desconocida"))
            status = step.get("status", "COMPLETED")
            result_snippet = self.truncate_result(step.get("result", ""))

            formatted_lines.append(
                f"- **Tarea #{task_id}** [{tool}] ({status}): {desc}\n"
                f"  *Resultado*: {result_snippet}"
            )

        return "\n".join(formatted_lines)


# Default singleton instance
context_optimizer = ContextOptimizer()
