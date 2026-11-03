"""
Decision Models Evaluators for Action Agent (LangSmith).

Implements:
1. get_decision_evaluator_classifier: factory for TypeSafeClassifier (Jev).
2. CONTENT_PRESENCE_QUESTIONS: Noul question definition with semantic criteria and calibrated rubric.
3. evaluate_output_content_presence: Non-deterministic semantic evaluation using TypeSafe decision model.
4. evaluate_non_empty_response: Deterministic evaluator for response presence.
5. evaluate_tool_calls_match: Deterministic evaluator comparing executed tool calls with ground truth.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional, Union
from dotenv import load_dotenv
from pydantic import SecretStr
from langchain_typesafe import (
    TypeSafeClassifier,
    Noul,
    NoulCriteria,
    Question,
    ClassifierResponse,
)

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api")
DECISION_EVAL_MODEL: str = os.getenv("DECISION_EVAL_MODEL", "typesafe/jev-1.13")


def get_decision_evaluator_classifier(
    model: str = DECISION_EVAL_MODEL,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 60.0,
) -> TypeSafeClassifier:
    """Instancia el cliente TypeSafeClassifier para los evaluadores de decisión."""
    resolved_base_url = (base_url or OPENROUTER_BASE_URL).rstrip("/")
    resolved_api_key = (
        api_key
        or os.getenv("OPENROUTER_API_KEY")
    )

    if not resolved_api_key:
        raise RuntimeError("No API Key for decision model")

    return TypeSafeClassifier(
        model=model,
        base_url=resolved_base_url,
        api_key=SecretStr(resolved_api_key),
        timeout=timeout,
    )


CONTENT_PRESENCE_QUESTIONS: dict[str, Question] = {
    "output_content_presence": Noul(
        instructions=(
            "Evalúa semánticamente si el núcleo (core) del contenido, entidades y parámetros requeridos "
            "en el ground truth están presentes y fielmente representados en las acciones y respuesta del Action Agent.\n\n"
            "IMPORTANTE - ANÁLISIS SEMÁNTICO (NO SINTÁCTICO):\n"
            "Esta evaluación debe ser puramente semántica. No se debe exigir coincidencia sintáctica ni literal "
            "palabra por palabra. Lo fundamental es que la información nuclear esté transmitida: conceptos equivalentes, "
            "sinónimos, diferente orden de palabras o redacción alternativa son plenamente válidos siempre que los "
            "datos clave (destinatario, identificadores de cuenta/token, montos y la acción/estado, o el motivo de no ejecución "
            "en caso de solicitudes no soportadas) estén reflejados con precisión.\n\n"
            "Guía de calibración de puntuación (0.0 a 1.0):\n"
            "- 0.0 (Mínimo / Totalmente ausente): La salida no contiene el significado ni los datos críticos del ground truth "
            "(por ejemplo, omite por completo al destinatario, el monto o la acción efectuada).\n"
            "- 0.25 (Pobre / Omisión crítica): Se menciona vagamente una intención, pero falta la mayor parte del núcleo "
            "semántico (ej. falta el monto o el destinatario principal).\n"
            "- 0.50 (Aceptable / Parcial): La salida transmite parcialmente la acción pero omite detalles relevantes de las "
            "entidades o parámetros bancarios esperados.\n"
            "- 0.75 (Buena / Casi completa): El núcleo semántico y casi todos los datos esenciales están presentes y comprensibles, "
            "pudiendo existir leves omisiones secundarias no críticas.\n"
            "- 1.0 (Óptima / Completa): Todo el núcleo semántico, las entidades y los datos clave del ground truth están "
            "presentes y fielmente reflejados en el significado de la respuesta, sin importar la formulación sintáctica empleada."
        ),
        criteria=NoulCriteria(
            true=(
                "El significado y núcleo de la información esperada (destinatario, identificador de cuenta o token, monto, "
                "confirmación de la acción, o explicación clara de imposibilidad si no está soportada) está semánticamente presente "
                "en la salida del agente, independientemente de la redacción o palabras específicas utilizadas (puntuación cercana a 1.0)."
            ),
            false=(
                "El significado de la salida es contradictorio, vacío o carece del núcleo de información y entidades críticas "
                "requeridas por el ground truth (puntuación cercana a 0.0)."
            ),
        ),
    ),
}


# ==============================================================================
# Helper to extract outputs/inputs from run and example (supporting both SDK and dict)
# ==============================================================================

def _get_run_outputs(run: Any) -> Dict[str, Any]:
    if hasattr(run, "outputs") and run.outputs is not None:
        return run.outputs if isinstance(run.outputs, dict) else {"output": run.outputs}
    if isinstance(run, dict):
        return run.get("outputs") or {}
    return {}


def _get_example_outputs(example: Any) -> Dict[str, Any]:
    if hasattr(example, "outputs") and example.outputs is not None:
        return example.outputs if isinstance(example.outputs, dict) else {"output": example.outputs}
    if isinstance(example, dict):
        return example.get("outputs") or {}
    return {}


def _get_example_inputs(example: Any) -> Dict[str, Any]:
    if hasattr(example, "inputs") and example.inputs is not None:
        return example.inputs if isinstance(example.inputs, dict) else {"input": example.inputs}
    if isinstance(example, dict):
        return example.get("inputs") or {}
    return {}


# ==============================================================================
# Métrica 1: Respuesta NO vacía (Determinista)
# ==============================================================================

def evaluate_non_empty_response(run: Any, example: Any) -> Dict[str, Any]:
    """
    Métrica determinista 1:
    Verifica que la respuesta generada por el Action Agent NO sea vacía.
    Inspecciona output_text, tool_calls y executed_tools.
    """
    outputs = _get_run_outputs(run)
    output_text = str(outputs.get("output_text") or outputs.get("final_response") or "").strip()
    tool_calls = outputs.get("tool_calls") or outputs.get("pending_tool_calls") or []
    executed_tools = outputs.get("executed_tools") or []

    is_non_empty = bool(output_text or tool_calls or executed_tools)
    score = 1.0 if is_non_empty else 0.0

    return {
        "key": "non_empty_response",
        "score": score,
        "comment": "La respuesta generada por el Action Agent no es vacía."
        if is_non_empty
        else "Error: La respuesta del Action Agent está completamente vacía.",
    }


# ==============================================================================
# Métrica 2: Tool Calls Coincidentes con Ground Truth (Determinista)
# ==============================================================================

def evaluate_tool_calls_match(run: Any, example: Any) -> Dict[str, Any]:
    """
    Métrica determinista 2:
    Verifica que las tool_calls realizadas sean las especificadas en el ground_truth.
    Compara nombres de herramientas y argumentos clave (monto, destinatario/token, CBU).
    """
    outputs = _get_run_outputs(run)
    expected = _get_example_outputs(example)

    actual_tool_calls: List[Dict[str, Any]] = outputs.get("tool_calls") or []
    expected_tool_calls: List[Dict[str, Any]] = expected.get("expected_tool_calls") or []

    # Caso en que no se esperan tool calls
    if not expected_tool_calls:
        if not actual_tool_calls:
            return {
                "key": "tool_calls_match",
                "score": 1.0,
                "comment": "Correcto: No se esperaban tool calls y el agente no ejecutó ninguna.",
            }
        return {
            "key": "tool_calls_match",
            "score": 0.0,
            "comment": f"Fallo: No se esperaban tool calls pero el agente ejecutó {len(actual_tool_calls)}.",
        }

    if len(actual_tool_calls) != len(expected_tool_calls):
        return {
            "key": "tool_calls_match",
            "score": 0.0,
            "comment": (
                f"Fallo en cantidad de tool calls: se esperaban {len(expected_tool_calls)} "
                f"pero se recibieron {len(actual_tool_calls)}."
            ),
        }

    # Comparar cada llamada de herramienta
    for i, (act, exp) in enumerate(zip(actual_tool_calls, expected_tool_calls)):
        act_name = act.get("name")
        exp_name = exp.get("name")

        if act_name != exp_name:
            return {
                "key": "tool_calls_match",
                "score": 0.0,
                "comment": (
                    f"Fallo en herramienta #{i+1}: esperada '{exp_name}', "
                    f"pero el agente ejecutó '{act_name}'."
                ),
            }

        act_args = dict(act.get("args") or {})
        exp_args = dict(exp.get("args") or {})

        # Validación específica por tipo de herramienta
        if exp_name == "transfer_funds_tool":
            exp_amount = float(exp_args.get("amount", 0.0))
            act_amount = float(act_args.get("amount", 0.0))
            if abs(exp_amount - act_amount) > 0.01:
                return {
                    "key": "tool_calls_match",
                    "score": 0.5,
                    "comment": (
                        f"Herramienta correcta '{exp_name}', pero monto discrepante: "
                        f"esperado {exp_amount}, obtenido {act_amount}."
                    ),
                }

            exp_dest = str(
                exp_args.get("destination_account")
                or exp_args.get("destination")
                or exp_args.get("target_token")
                or ""
            ).strip()
            act_dest = str(
                act_args.get("destination_account")
                or act_args.get("destination")
                or act_args.get("target_token")
                or ""
            ).strip()
            if exp_dest and exp_dest != act_dest:
                return {
                    "key": "tool_calls_match",
                    "score": 0.5,
                    "comment": (
                        f"Herramienta correcta '{exp_name}', pero destino discrepante: "
                        f"esperado '{exp_dest}', obtenido '{act_dest}'."
                    ),
                }

        elif exp_name == "register_recipient_tool":
            exp_cbu = str(exp_args.get("cbu") or exp_args.get("cbu_or_cvu") or "").strip()
            act_cbu = str(act_args.get("cbu") or act_args.get("cbu_or_cvu") or "").strip()
            if exp_cbu and exp_cbu != act_cbu:
                return {
                    "key": "tool_calls_match",
                    "score": 0.5,
                    "comment": (
                        f"Herramienta correcta '{exp_name}', pero CBU/CVU discrepante: "
                        f"esperado '{exp_cbu}', obtenido '{act_cbu}'."
                    ),
                }

    return {
        "key": "tool_calls_match",
        "score": 1.0,
        "comment": "Las tool_calls y parámetros esenciales coinciden con el ground truth.",
    }


# ==============================================================================
# Métrica 3: Presencia Semántica de Contenido via Decision Model (NO determinista)
# ==============================================================================

def evaluate_output_content_presence(
    run: Any,
    example: Any,
    classifier: Optional[TypeSafeClassifier] = None,
) -> Dict[str, Any]:
    """
    Métrica no determinista 3:
    Evalúa mediante el modelo de decisión TypeSafe (Jev) si el núcleo del contenido
    esperado en el ground_truth está semánticamente presente en la salida del agente.
    """
    outputs = _get_run_outputs(run)
    expected = _get_example_outputs(example)
    inputs = _get_example_inputs(example)

    # Extraer el texto producido por el agente
    agent_output = (
        outputs.get("output_text")
        or outputs.get("final_response")
        or json.dumps(outputs, ensure_ascii=False)
    )

    # Extraer el contenido esperado del ground truth
    expected_content = expected.get("expected_output_content") or expected.get("content") or []

    # Extraer información del PlanStep para contexto
    plan_step = inputs.get("plan_step") or (inputs.get("execution_plan", [{}])[0] if inputs.get("execution_plan") else {})
    step_title = plan_step.get("title", "")
    step_desc = plan_step.get("description", "")

    tool_calls_executed = outputs.get("tool_calls") or []

    # Construir el estado semántico a clasificar por Jev
    state_context = f"""
[PLAN_STEP / INSTRUCCIÓN]:
Título: {step_title}
Descripción del paso: {step_desc}

[NÚCLEO DEL CONTENIDO Y ENTIDADES ESPERADAS SEGÚN GROUND TRUTH]:
{json.dumps(expected_content, ensure_ascii=False, indent=2) if isinstance(expected_content, (list, dict)) else str(expected_content)}

[ACCIONES Y LLAMADAS A HERRAMIENTAS REALIZADAS POR EL AGENTE]:
{json.dumps(tool_calls_executed, ensure_ascii=False, indent=2)}

[SÍNTESIS / RESPUESTA DEL ACTION AGENT]:
{agent_output}
"""

    if classifier is None:
        classifier = get_decision_evaluator_classifier()

    try:
        response: ClassifierResponse = classifier.invoke({
            "state": state_context,
            "questions": CONTENT_PRESENCE_QUESTIONS,
        })
        noul_answer = response.nouls.get("output_content_presence")
        if noul_answer is not None:
            score = float(noul_answer.noul)
        else:
            score = 0.0

        return {
            "key": "output_content_presence",
            "score": round(score, 4),
            "comment": (
                f"Evaluación semántica Jev: puntuación {score:.3f}. "
                f"El modelo determinó con probabilidad {score:.1%} que el contenido nuclear esperado "
                f"está presente en la respuesta."
            ),
        }
    except Exception as e:
        logger.error(f"[DecisionEvaluator] Error al invocar el modelo de decisión: {e}")
        return {
            "key": "output_content_presence",
            "score": 0.0,
            "comment": f"Error al invocar el clasificador de decisión: {str(e)}",
        }


# ==============================================================================
# Orchestrator evaluators
# Run outputs:   {"plan": [{"target_node", "description", ...}], "final_response", "status", "document_read", ...}
# Example outputs (all keys optional; a missing key means "not evaluated for this example"):
#   expected_target_options: list of acceptable target_node sequences, e.g. [["read_only_agent"]]
#   expected_params: strings that must appear in the new steps' descriptions or in the final answer
# ==============================================================================

PLAN_GROUNDED_QUESTIONS: dict[str, Question] = {
    "plan_grounded_in_request": Noul(
        instructions=(
            "Se te entrega la solicitud original de un usuario de un banco y el plan de ejecución que un agente "
            "orquestador definió para cumplirla (lista de pasos delegados a otros agentes).\n"
            "Evalúa si CADA paso del plan está respaldado por la solicitud del usuario: el paso debe ser lo que el "
            "usuario pidió o un requisito directo e indispensable para cumplirlo.\n"
            "Un paso que realiza una acción distinta a la pedida (por ejemplo mover dinero o registrar cuentas cuando "
            "el usuario solo pidió consultar información) NO está respaldado, aunque tenga una justificación plausible.\n"
            "Si el plan está vacío y la respuesta final atiende lo pedido, está respaldado.\n\n"
            "Guía de calibración (0.0 a 1.0): 1.0 = todos los pasos están respaldados por la solicitud; "
            "0.5 = hay pasos dudosos o con respaldo parcial; 0.0 = al menos un paso realiza una acción que el usuario "
            "no pidió."
        ),
        criteria=NoulCriteria(
            true="Todos los pasos del plan son lo que el usuario pidió o un requisito directo para cumplirlo.",
            false="Al menos un paso del plan realiza una acción que la solicitud del usuario no pidió.",
        ),
    ),
}


def _orchestrator_plan(run: Any) -> List[Dict[str, Any]]:
    return list(_get_run_outputs(run).get("plan") or [])


def evaluate_plan_targets_match(run: Any, example: Any) -> Dict[str, Any]:
    """Deterministic: the sequence of target_node values is one of the acceptable sequences."""
    options = _get_example_outputs(example).get("expected_target_options")
    if options is None:
        return {"key": "plan_targets_match", "score": None, "comment": "Sin expectativa: caso solo medido."}

    actual = [str(step.get("target_node")) for step in _orchestrator_plan(run)]
    ok = any(actual == list(option) for option in options)
    return {
        "key": "plan_targets_match",
        "score": 1.0 if ok else 0.0,
        "comment": f"Secuencia obtenida {actual}; aceptables: {options}.",
    }


def evaluate_plan_params_copied(run: Any, example: Any) -> Dict[str, Any]:
    """Deterministic: expected parameters appear in the step descriptions or in the final answer."""
    expected = _get_example_outputs(example).get("expected_params")
    if not expected:
        return {"key": "plan_params_copied", "score": None, "comment": "Sin parámetros esperados."}

    outputs = _get_run_outputs(run)
    haystack = " ".join(
        [str(s.get("description", "")) for s in _orchestrator_plan(run)] + [str(outputs.get("final_response") or "")]
    ).lower()
    missing = [p for p in expected if str(p).lower() not in haystack]
    return {
        "key": "plan_params_copied",
        "score": 1.0 - len(missing) / len(expected),
        "comment": "Todos los parámetros presentes." if not missing else f"Faltan parámetros: {missing}.",
    }


def evaluate_plan_within_limits(run: Any, example: Any, max_steps: int = 8) -> Dict[str, Any]:
    """Deterministic: the plan does not exceed the maximum number of steps and the turn did not fail."""
    outputs = _get_run_outputs(run)
    size = len(_orchestrator_plan(run))
    ok = size <= max_steps and outputs.get("status") != "FAILED"
    return {
        "key": "plan_within_limits",
        "score": 1.0 if ok else 0.0,
        "comment": f"{size} pasos (máximo {max_steps}); estado {outputs.get('status')}.",
    }


def evaluate_plan_grounded_in_request(
    run: Any,
    example: Any,
    classifier: Optional[TypeSafeClassifier] = None,
) -> Dict[str, Any]:
    """Non-deterministic (Jev): every plan step is backed by the user's request."""
    inputs = _get_example_inputs(example)
    outputs = _get_run_outputs(run)
    plan = _orchestrator_plan(run)

    state_context = (
        f"[SOLICITUD DEL USUARIO]:\n{inputs.get('sanitized_input', '')}\n\n"
        f"[PLAN DEL ORQUESTADOR]:\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
        f"[RESPUESTA FINAL]:\n{outputs.get('final_response') or ''}\n"
    )
    if classifier is None:
        classifier = get_decision_evaluator_classifier()

    try:
        response: ClassifierResponse = classifier.invoke({
            "state": state_context,
            "questions": PLAN_GROUNDED_QUESTIONS,
        })
        answer = response.nouls.get("plan_grounded_in_request")
        score = float(answer.noul) if answer is not None else 0.0
        return {
            "key": "plan_grounded_in_request",
            "score": round(score, 4),
            "comment": f"Jev: {score:.1%} de probabilidad de que todos los pasos estén respaldados por la solicitud.",
        }
    except Exception as e:
        logger.error(f"[DecisionEvaluator] Error en plan_grounded_in_request: {e}")
        return {"key": "plan_grounded_in_request", "score": 0.0, "comment": f"Error del clasificador: {e}"}
