"""
Safety limit for the executor agents (readonly_agent, action_agent).

Both run a ReAct loop (model -> tools -> model ...). LangGraph's default recursion limit is ~10,000 supersteps, so a model
that keeps repeating a failing tool call (e.g. malformed arguments) would loop, and spend tokens, until someone
interrupts it. `settings.executor_max_tool_rounds` bounds the number of tool rounds per plan step.
"""

import logging
from typing import Any, Dict

from langgraph.errors import GraphRecursionError

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["GraphRecursionError", "bound_executor_config", "limit_reached_message"]


def bound_executor_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Copy of `config` whose recursion_limit allows `executor_max_tool_rounds` tool rounds.
    One round is two supersteps (model, tools) plus the final model turn that answers.
    """
    rounds = max(int(settings.executor_max_tool_rounds), 1)
    return {**config, "recursion_limit": 2 * rounds + 1}


def limit_reached_message(agent: str, outcome: str) -> str:
    """Result text for a step that was cut off by the limit. Nothing was staged or executed."""
    rounds = settings.executor_max_tool_rounds
    logger.warning(
        "[%s] El agente alcanzó el máximo de %s rondas de herramientas sin terminar el paso: se corta.", agent, rounds
    )
    return (
        f"No pude completar este paso: el agente no logró terminar tras {rounds} rondas de herramientas "
        f"(por ejemplo, llamadas con argumentos inválidos). {outcome}"
    )
