"""
Execution plan helpers shared by the Orchestrator, the executor agents, the PEP and the graph router.
"""

from typing import Any, Dict, List, Optional

from app.graph.state import AgentState


def get_active_step(state: AgentState) -> Optional[Dict[str, Any]]:
    """Returns the first plan step that has not produced a result yet."""
    for step in state.get("execution_plan") or []:
        if isinstance(step, dict) and step.get("result") is None:
            return step
    return None


def complete_active_step(state: AgentState, result: str) -> List[Dict[str, Any]]:
    """Returns a copy of the execution plan with the active step marked as completed."""
    plan = [dict(s) for s in (state.get("execution_plan") or [])]
    for step in plan:
        if step.get("result") is None:
            step["result"] = result
            break
    return plan
