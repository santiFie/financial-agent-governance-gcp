"""
LangGraph Multi-Agent Financial Assistant Graph Definition.
Orchestrates Defense-in-Depth layers with Planner + Replanner Architecture:
Sanitizer Node -> Planner Node -> [Executor (Read-Only | Action HITL)] -> Replanner Node -> [Next Executor | END].
"""
from typing import Literal
from langgraph.graph import StateGraph, START, END

from app.graph.state import AgentState
from app.graph.nodes.sanitizer import sanitizer_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.replanner import replanner_node
from app.graph.nodes.readonly_agent import readonly_agent_node
from app.graph.nodes.action_agent import action_agent_node
from app.db.checkpointer import checkpointer


def route_after_sanitizer(state: AgentState) -> Literal["planner", "__end__"]:
    """If prompt injection is detected, terminate graph early with security rejection."""
    if state.get("is_threat"):
        return END
    return "planner"


def route_to_executor(state: AgentState) -> Literal["readonly_agent", "action_agent", "__end__"]:
    """
    Evaluates current task in plan and delegates to the designated domain agent.
    Terminates graph if plan is complete, empty, or flagged as a threat.
    """
    if state.get("is_threat") or state.get("plan_completed", False):
        return END

    plan = state.get("plan", [])
    idx = state.get("current_task_idx", state.get("current_step_index", 0))

    if not plan or idx >= len(plan):
        return END

    task = plan[idx]
    tool = str(task.get("assigned_tool", "")).lower()

    if any(kw in tool for kw in ["transfer", "write", "action", "pago"]):
        return "action_agent"
    elif any(kw in tool for kw in ["db", "read", "query", "crm", "analytics", "balance", "transact", "general"]):
        return "readonly_agent"
    else:
        return "readonly_agent"


def route_after_replanner(state: AgentState) -> Literal["readonly_agent", "action_agent", "__end__"]:
    """
    Routes from Replanner: if finished, terminates to END; otherwise routes to next executor.
    """
    if state.get("is_threat") or state.get("plan_completed", False):
        return END
    return route_to_executor(state)


def build_financial_graph():
    """
    Constructs and compiles the multi-agent graph with persistent fault-tolerant checkpointer.
    Orchestrates Planner (initial plan) and Replanner (continuous feedback evaluation) loop.
    """
    workflow = StateGraph(AgentState)

    # 1. Add nodes
    workflow.add_node("sanitizer", sanitizer_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("readonly_agent", readonly_agent_node)
    workflow.add_node("action_agent", action_agent_node)
    workflow.add_node("replanner", replanner_node)

    # 2. Add edges
    workflow.add_edge(START, "sanitizer")

    workflow.add_conditional_edges(
        "sanitizer",
        route_after_sanitizer,
        {
            "planner": "planner",
            END: END
        }
    )

    workflow.add_conditional_edges(
        "planner",
        route_to_executor,
        {
            "readonly_agent": "readonly_agent",
            "action_agent": "action_agent",
            END: END
        }
    )

    # Executors feed their outputs to the Replanner
    workflow.add_edge("readonly_agent", "replanner")
    workflow.add_edge("action_agent", "replanner")

    # Replanner either finishes or executes the next step/re-planned task
    workflow.add_conditional_edges(
        "replanner",
        route_after_replanner,
        {
            "readonly_agent": "readonly_agent",
            "action_agent": "action_agent",
            END: END
        }
    )

    # 3. Compile with persistent Checkpointer
    return workflow.compile(checkpointer=checkpointer)


# Global compiled graph instance
financial_graph = build_financial_graph()
