"""
LangGraph Multi-Agent Financial Assistant Graph Definition.
Orchestrates Defense-in-Depth layers:
Sanitizer Node -> Router Node -> (Read-Only DB Agent | Action Agent HITL) -> END.
"""

from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.db.checkpointer import checkpointer
from app.graph.agent_registry import AGENT_REGISTRY, TargetNode
from app.graph.nodes.action_agent import action_agent_node
from app.graph.nodes.pep import pep_node
from app.graph.nodes.readonly_agent import readonly_agent_node
from app.graph.nodes.router import router_node
from app.graph.nodes.sanitizer import sanitizer_node
from app.graph.plan_utils import get_active_step
from app.graph.state import AgentState
from app.graph.tools.document_reader import read_document_tool


def route_after_sanitizer(state: AgentState) -> Literal["router", "__end__"]:
    """If prompt injection is detected, terminate graph early with security rejection."""
    if state.get("is_threat"):
        return END
    return "router"


def route_after_router(state: AgentState) -> str:
    """
    Orchestrator dispatcher:
    - A pending read_document_tool call in the scratchpad goes to the orchestrator tool node.
    - Otherwise the first plan step without a result is routed to its executor agent (via the agent registry).
    - When no step is pending (plan finished, empty or blocked), the graph ends.
    """
    if state.get("is_threat") or state.get("status") in ("BLOCKED", "FAILED"):
        return END

    scratchpad = state.get("orchestrator_scratchpad") or []
    if scratchpad and isinstance(scratchpad[-1], AIMessage) and scratchpad[-1].tool_calls:
        return "orchestrator_tools"

    next_step = get_active_step(state)
    if next_step is None:
        return END

    try:
        return AGENT_REGISTRY[TargetNode(next_step.get("target_node"))].graph_node
    except ValueError:
        return END


def build_financial_graph():
    """
    Constructs and compiles the multi-agent graph with persistent checkpointer.
    """
    workflow = StateGraph(AgentState)

    # 1. Add nodes
    workflow.add_node("sanitizer", sanitizer_node)
    workflow.add_node("router", router_node)
    workflow.add_node("readonly_agent", readonly_agent_node)
    workflow.add_node("action_agent", action_agent_node)
    workflow.add_node("pep_node", pep_node)
    workflow.add_node("orchestrator_tools", ToolNode([read_document_tool], messages_key="orchestrator_scratchpad"))

    # 2. Add edges
    workflow.add_edge(START, "sanitizer")

    workflow.add_conditional_edges("sanitizer", route_after_sanitizer, {"router": "router", END: END})

    workflow.add_conditional_edges(
        "router",
        route_after_router,
        {
            "orchestrator_tools": "orchestrator_tools",
            "readonly_agent": "readonly_agent",
            "action_agent": "action_agent",
            END: END,
        },
    )

    # Orchestrator loop edges: return control to router after each step execution
    workflow.add_edge("orchestrator_tools", "router")
    workflow.add_edge("readonly_agent", "router")
    workflow.add_edge("action_agent", "pep_node")
    workflow.add_edge("pep_node", "router")

    # 3. Compile with Checkpointer
    return workflow.compile(checkpointer=checkpointer)


# Global compiled graph instance
financial_graph = build_financial_graph()
