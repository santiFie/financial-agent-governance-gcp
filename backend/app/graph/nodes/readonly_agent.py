"""
Read-Only DB Agent Node.
Executes read queries on banking data (balances, statements).
Binds read-only tools to the LLM (Principle of Least Privilege).
Has ZERO write permissions to the database or transfer systems.
"""
import json
import logging
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from app.graph.state import AgentState
from app.graph.tools.banking_read import get_account_balances, get_recent_transactions
from app.core.llm import get_chat_model

logger = logging.getLogger(__name__)

READONLY_TOOLS = [get_account_balances, get_recent_transactions]
TOOL_MAP = {t.name: t for t in READONLY_TOOLS}


def _execute_readonly_mock(intent: str, user_id: str) -> str:
    """Deterministic fallback formatting."""
    if intent == "balance":
        accounts = get_account_balances.invoke({"user_id": user_id})
        acc_details = "\n".join([
            f"• **Cuenta {acc['account_number']}** ({acc['owner_name']}): "
            f"**${acc['balance']:,.2f} {acc['currency']}**"
            for acc in accounts
        ])
        return (
            f"🏦 **Estado de Cuentas y Saldos Disponibles:**\n\n"
            f"{acc_details}\n\n"
            "¿Deseas realizar alguna otra consulta o ver tus últimos movimientos?"
        )
    elif intent == "transactions":
        txs = get_recent_transactions.invoke({"user_id": user_id, "limit": 5})
        tx_details = "\n".join([
            f"• [{tx['timestamp'][:10]}] **{tx['type']}**: ${tx['amount']:,.2f} - *{tx['description']}*"
            for tx in txs
        ])
        return (
            f"📜 **Últimos 5 Movimientos Bancarios:**\n\n"
            f"{tx_details}\n\n"
            "Todos tus movimientos se encuentran conciliados y protegidos."
        )
    else:
        return (
            "ℹ️ Bienvenido al Asistente Financiero Seguro. "
            "Puedes consultarme tus saldos, tus últimos movimientos o solicitar una transferencia bancaria."
        )


def readonly_agent_node(state: AgentState) -> Dict[str, Any]:
    """
    Handles read-only requests (balance or transaction history)
    adhering to the Principle of Least Privilege by binding only read tools.
    """
    intent = state.get("current_intent", "balance")
    user_id = state.get("user_id", "user_default_01")
    sanitized_text = state.get("sanitized_input", "")

    llm = get_chat_model(temperature=0.0)

    if llm is not None:
        try:
            llm_with_tools = llm.bind_tools(READONLY_TOOLS)
            system_prompt = (
                "Eres un asistente bancario de solo lectura. Tienes acceso a herramientas para consultar saldos "
                "y últimos movimientos bancarios. Tu identificador de usuario es '{user_id}'. "
                "Siempre usa las herramientas disponibles para responder con datos precisos. "
                "Al informar saldos de cuentas, titula la sección con '**Estado de Cuentas y Saldos Disponibles**'. "
                "No tienes permisos de escritura ni para realizar transferencias directas."
            ).format(user_id=user_id)

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=sanitized_text or f"Consultar {intent}")
            ]

            ai_msg = llm_with_tools.invoke(messages)

            # Check if LLM requested tool execution
            if hasattr(ai_msg, "tool_calls") and ai_msg.tool_calls:
                tool_messages = []
                for tool_call in ai_msg.tool_calls:
                    fn_name = tool_call["name"]
                    fn_args = tool_call.get("args", {})
                    # Ensure user_id is injected
                    if "user_id" not in fn_args:
                        fn_args["user_id"] = user_id

                    if fn_name in TOOL_MAP:
                        tool_result = TOOL_MAP[fn_name].invoke(fn_args)
                    else:
                        tool_result = {"error": f"Tool {fn_name} no disponible."}

                    tool_messages.append(
                        ToolMessage(
                            content=json.dumps(tool_result, ensure_ascii=False),
                            tool_call_id=tool_call["id"]
                        )
                    )

                # Feed tool results back to LLM to formulate synthesized final response
                final_ai_msg = llm.invoke(messages + [ai_msg] + tool_messages)
                content = final_ai_msg.content
                if isinstance(content, list):
                    content = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])

                return {
                    "final_response": content,
                    "status": "COMPLETED",
                    "messages": [AIMessage(content=content)]
                }

            elif ai_msg.content:
                # LLM responded directly without tool call
                content = ai_msg.content
                if isinstance(content, list):
                    content = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
                return {
                    "final_response": content,
                    "status": "COMPLETED",
                    "messages": [AIMessage(content=content)]
                }

        except Exception as e:
            logger.warning(f"[ReadOnlyAgent] Error invoking LLM with tools: {e}. Using deterministic fallback.")

    # Fallback to deterministic read
    content = _execute_readonly_mock(intent, user_id)
    return {
        "final_response": content,
        "status": "COMPLETED",
        "messages": [AIMessage(content=content)]
    }
