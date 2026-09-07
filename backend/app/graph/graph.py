"""
LangGraph Multi-Agent Financial Assistant Graph Definition.
Orchestrates Defense-in-Depth layers:
Sanitizer Node -> Router Node -> (Read-Only DB Agent | Action Agent HITL) -> END.
"""
from typing import Literal
from langgraph.graph import StateGraph, START, END
from app.graph.state import AgentState
from app.graph.nodes.sanitizer import sanitizer_node
from app.graph.nodes.router import router_node
from app.graph.nodes.readonly_agent import readonly_agent_node
from app.graph.nodes.action_agent import action_agent_node
from app.db.checkpointer import checkpointer


def route_after_sanitizer(state: AgentState) -> Literal["router", "__end__"]:
    """If prompt injection is detected, terminate graph early with security rejection."""
    if state.get("is_threat"):
        return END
    return "router"


def route_after_router(state: AgentState) -> Literal["readonly_agent", "action_agent", "__end__"]:
    """Routes based on classified user intent."""
    intent = state.get("current_intent", "general")
    if intent == "transfer":
        return "action_agent"
    elif intent in ["balance", "transactions", "general"]:
        return "readonly_agent"
    else:
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

    # 2. Add edges
    workflow.add_edge(START, "sanitizer")

    workflow.add_conditional_edges(
        "sanitizer",
        route_after_sanitizer,
        {
            "router": "router",
            END: END
        }
    )

    workflow.add_conditional_edges(
        "router",
        route_after_router,
        {
            "readonly_agent": "readonly_agent",
            "action_agent": "action_agent",
            END: END
        }
    )

    workflow.add_edge("readonly_agent", END)
    workflow.add_edge("action_agent", END)

    # 3. Compile with Checkpointer
    return workflow.compile(checkpointer=checkpointer)


# Global compiled graph instance
financial_graph = build_financial_graph()
