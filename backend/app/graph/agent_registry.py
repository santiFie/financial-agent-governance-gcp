"""
Agent Registry.
Single source of truth for the executable agents the Orchestrator can delegate plan steps to.
Adding a new agent only requires registering it here (and in the graph); prompts and routing adapt automatically.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class TargetNode(str, Enum):
    READ_ONLY_AGENT = "read_only_agent"
    ACTION_AGENT = "action_agent"


@dataclass(frozen=True)
class AgentSpec:
    graph_node: str
    description: str


AGENT_REGISTRY: Dict[TargetNode, AgentSpec] = {
    TargetNode.READ_ONLY_AGENT: AgentSpec(
        graph_node="readonly_agent",
        description=(
            "Agente de solo lectura. Solamente consulta saldos de cuentas y últimos movimientos bancarios del usuario. "
            "No puede modificar datos ni mover fondos."
        ),
    ),
    TargetNode.ACTION_AGENT: AgentSpec(
        graph_node="action_agent",
        description=(
            "Agente transaccional. Registra destinatarios en la agenda de cuentas y emite transferencias de fondos. "
            "Registrar un destinatario y transferir son acciones independientes: una transferencia no requiere que el "
            "destinatario esté registrado antes. Ejecuta una única acción por paso."
        ),
    ),
}


def render_agents_catalog() -> str:
    """Renders the agent catalog injected into the Orchestrator prompts."""
    return "\n".join(f"- '{target.value}': {spec.description}" for target, spec in AGENT_REGISTRY.items())
