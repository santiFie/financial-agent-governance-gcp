import logging
from typing import Dict, Any, Optional
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from app.graph.state import AgentState
from app.graph.plan_utils import get_active_step, complete_active_step
from app.graph.tools.banking_read import get_account_balances, get_recent_transactions
from app.core.llm import get_chat_model
from app.core.prompt_manager import prompt_manager

logger = logging.getLogger(__name__)

READONLY_TOOLS = [get_account_balances, get_recent_transactions]


def _build_executor():
    """ReAct executor over the read tools. Built per call so importing the graph needs no LLM credentials."""
    llm = get_chat_model(temperature=0.0)
    if llm is None:
        raise RuntimeError("Read-Only Agent: no hay credenciales LLM configuradas.")
    return create_react_agent(model=llm, tools=READONLY_TOOLS)


def _extract_text(content: Any) -> str:
    if isinstance(content, list):
        return "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
    return content or ""


def readonly_agent_node(state: AgentState, config: Optional[RunnableConfig] = None) -> Dict[str, Any]:
    """
    Ejecuta el paso activo del plan mediante un sub-agente ReAct.
    Inyecta user_id y preserva trazabilidad (LangSmith) vía RunnableConfig.
    """
    user_id = state.get("user_id", "user_default_01")
    active_step = get_active_step(state)
    
    task_description = (
        active_step.get("description")
        if isinstance(active_step, dict)
        else state.get("sanitized_input", "No se pudo obtener la descripción de la tarea")
    )

    system_prompt = prompt_manager.render('readonly_agent', user_id=user_id)

    # Combinar RunnableConfig entrante con configurable.user_id para preservar trazabilidad
    exec_config = dict(config) if config else {}
    configurable = dict(exec_config.get("configurable", {}))
    configurable["user_id"] = user_id
    exec_config["configurable"] = configurable

    result = _build_executor().invoke(
        {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task_description}
            ]
        },
        config=exec_config
    )

    # El último mensaje devuelto por el sub-agente es la síntesis final
    last_message = result["messages"][-1]
    step_output = _extract_text(last_message.content)

    return {
        "final_response": step_output,
        "status": "COMPLETED",
        "execution_plan": complete_active_step(state, step_output),
        "messages": [AIMessage(content=f"[Read-Only Agent]: {step_output}")],
    }