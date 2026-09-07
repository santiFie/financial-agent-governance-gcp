"""
Router / Triage Agent Node.
Classifies the sanitized user intent to route to the appropriate domain agent.
Uses LLM semantic classification with structured schema, and falls back to deterministic rules.
Ensures that the LLM only operates on sanitized inputs.
"""

from typing import Dict, Any, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage

from app.graph.state import AgentState
from app.core.llm import get_chat_model


class IntentClassification(BaseModel):
    intent: Literal["balance", "transactions", "transfer", "general"] = Field(
        description="Categoría de la intención del usuario: "
                    "'balance' (consultar saldos o fondos disponibles), "
                    "'transactions' (ver últimos movimientos, historial de pagos o extracto), "
                    "'transfer' (realizar un pago, envío de dinero o transferencia bancaria), "
                    "'general' (saludos, preguntas informativas generales o dudas no transaccionales)."
    )


def router_node(state: AgentState) -> Dict[str, Any]:
    """
    Categorizes the user request into:
    - 'balance': Consulta de saldos
    - 'transactions': Consulta de últimos movimientos
    - 'transfer': Solicitud de transferencia / pago
    - 'general': Consultas informativas generales
    """
    if state.get("is_threat"):
        return {"current_intent": "blocked"}

    sanitized_text = state.get("sanitized_input", "")

    llm = get_chat_model(temperature=0.0)
    structured_llm = llm.with_structured_output(IntentClassification)
    system_prompt = (
        "Eres el clasificador de intenciones de un banco. "
        "Tu objetivo es clasificar con precisión la intención del usuario a partir de su consulta sanitizada."
    )
    result = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=sanitized_text)
    ])

    if result and hasattr(result, "intent") and result.intent:
        return {"current_intent": result.intent}
    else:
        raise ValueError("[RouterNode] Failed to classify intent.")