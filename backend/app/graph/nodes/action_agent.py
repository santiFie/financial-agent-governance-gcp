"""
Action Agent Node (Transactional execution & tool orchestration).
Executes exactly the active plan step with a single tool call:
Every tool call is only staged into pending_tool_calls (transfers also into pending_transfer): the Policy
Enforcement Point (pep_node) asks the PDP about each one, executes the allowed ones and completes the step.
Decoupled from interrupt(), which is delegated to pep_node.
"""
import logging
from langchain_core.runnables import RunnableConfig
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolNode, create_react_agent
from app.graph.state import AgentState
from app.graph.plan_utils import get_active_step, complete_active_step
from app.graph.tools.banking_write import (
    register_recipient_tool,
    transfer_funds_tool,
    ACTION_TOOLS,
)
from app.core.llm import get_chat_model
from app.core.prompt_manager import prompt_manager
from app.core.security_context import normalize_account_reference

logger = logging.getLogger(__name__)

SOURCE_ACCOUNT = "acc_001"


def _build_executor():
    """ReAct executor over the write tools. Built per call so the LLM factory can be patched in tests."""
    llm = get_chat_model(temperature=0.0)
    if llm is None:
        raise RuntimeError("Action Agent: no hay credenciales LLM configuradas.")
    # Tool errors go back to the model as tool messages so the ReAct loop can correct itself.
    return create_react_agent(model=llm, tools=ToolNode(ACTION_TOOLS, handle_tool_errors=True))

def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
    return content or ""


def action_agent_node(state: AgentState, config: RunnableConfig = None) -> Dict[str, Any]:
    """
    Executes the active plan step. The step description carries every parameter the agent needs
    (extracted by the Orchestrator); the LLM maps it to exactly one tool call.
    """
    session_id = state.get("session_id", "default")
    user_id = state.get("user_id", "user_default_01")
    active_step = get_active_step(state)
    
    task = (active_step or {}).get("description") or state.get("sanitized_input", "")

    # Combinar RunnableConfig entrante con configurable.user_id para preservar trazabilidad
    exec_config = dict(config) if config else {}
    configurable = dict(exec_config.get("configurable", {}))
    configurable["user_id"] = user_id
    exec_config["configurable"] = configurable


    system_prompt = prompt_manager.render(
        "action_agent",
        session_id=session_id,
        user_id=user_id,
        source_acc=SOURCE_ACCOUNT,
    )
    result = _build_executor().invoke(
    {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task}
        ]
    },
    config=exec_config
)

    messages = result["messages"]
    step_output = _text(messages[-1].content)

    # The tools only stage requests: collect them so the PEP governs each one.
    staged_calls = _collect_staged_calls(messages)
    staged_transfers = [c for c in staged_calls if c["name"] == transfer_funds_tool.name]
    update: Dict[str, Any] = {
        "final_response": step_output,
        "messages": [AIMessage(content=f"[Action Agent]: {step_output}")],
    }
    if staged_calls:
        # The step is completed by pep_node once the transfer is resolved.
        update["pending_tool_calls"] = staged_calls
        if staged_transfers:
            update["pending_transfer"] = _to_pending_transfer(staged_transfers[-1]["args"], session_id, user_id)
        update["status"] = "PENDING_PEP"
    else:
        update["status"] = "COMPLETED"
        update["execution_plan"] = complete_active_step(state, step_output)
    return update


def _collect_staged_calls(messages: List[Any]) -> List[Dict[str, Any]]:
    """Returns the tool calls the ReAct loop issued (and the tools accepted), as pending tool calls."""
    failed = {m.tool_call_id for m in messages if isinstance(m, ToolMessage) and m.status == "error"}
    return [
        {"name": call["name"], "args": dict(call["args"]), "id": call.get("id")}
        for msg in messages
        if isinstance(msg, AIMessage)
        for call in (msg.tool_calls or [])
        if call["name"] in {t.name for t in ACTION_TOOLS} and call.get("id") not in failed
    ]


def _to_pending_transfer(args: Dict[str, Any], session_id: str, user_id: str) -> Dict[str, Any]:
    destination = normalize_account_reference(args.get("destination_account") or args.get("destination"))
    source = str(args.get("source_account_id") or args.get("source_account") or SOURCE_ACCOUNT)
    return {
        "session_id": session_id,
        "user_id": user_id,
        "amount": float(args.get("amount", 0.0)),
        "source_account": source,
        "destination": destination,
        "concept": args.get("concept", "transfer"),
        "status": "PENDING_PEP",
    }
