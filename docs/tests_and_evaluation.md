# Guía de Pruebas y Matriz de Evaluación de Tests

Estructura de la suite de `backend/tests` (`unit/` offline, `integration/` API y grafo, `evals/` LLM real, `data/` y `fixtures/` datos) (más `mcp_financial_action_server/tests`). Regla: **todo test que llama a un LLM real lleva el marker `requires_live_llm`** (`tests/live_llm.py`: `@requires_live_llm` o `pytestmark = live_llm_marks`). `make test` y el CI lo deseleccionan; `make test-live` solo corre esos. Todo lo demás es offline con LLM mockeado.

> Nota: sin credenciales, los tests marcados se omiten. Vertex AI solo cuenta como proveedor disponible con `LLM_PROVIDER=vertexai`; las *Application Default Credentials* de `gcloud` por sí solas no habilitan ninguna llamada real. Lo verifica `tests/unit/test_live_llm_gate.py`.

---

## 1. Matriz resumen

### Offline (sin red ni API keys)

| Archivo | Qué cubre |
| :--- | :--- |
| `test_sanitizer.py` | DLP (tarjeta, CBU, DNI), Model Armor local, nodo `sanitizer`, `read_document_tool` (sanitizado, bloqueo, path traversal, no encontrado) |
| `test_llm_nodes.py` | Fábrica `get_chat_model` (proveedores, fallbacks, `bind_tools`), `router_node` con LLM mockeado, `readonly_agent_node` con ejecutor mockeado, propagación de `ORCHESTRATOR_PROVIDER/MODEL` |
| `test_router_planner.py` | Mecánica del Orchestrator: plan append-only, replan, límites, `FAILED`, scratchpad de documentos, contexto |
| `test_graph_routing.py` | `route_after_router` y `agent_registry` |
| `test_document_loader.py` | Ingesta local / GCS con fallback y nombres inseguros |
| `test_action_multitool.py` | `action_agent_node` (alta de destinatario, staging de transferencia, errores de tool) |
| `test_account_references.py` | `normalize_account_reference` y tools servidas por el MCP |
| `test_pep_node.py` | `pep_node`: Fast-Path, HITL, fallback del PDP, sobre de entrada a OPA |
| `test_control_scenarios.py` | Escenarios de control (`tests/data/control_scenarios.json`): el PEP envía a OPA el sobre esperado y entra en HITL |
| `test_dlp_sanitizer.py` / `test_model_armor.py` | DLP y Model Armor: offsets, tokens estables, bloqueos reales y fallback local logueado |
| `test_firestore_checkpointer.py` | Checkpointer en Firestore (cliente falso en `tests/fakes_firestore.py`) |
| `test_executor_limits.py` | Límites de iteraciones de los bucles ReAct de los ejecutores |
| `test_live_llm_gate.py` | El gate de los tests con LLM real: Vertex solo cuenta si se pide explícitamente |
| `test_service_status.py` | Estado de servicios GCP expuesto por la API |
| `test_gcp_bootstrap_script.py` / `test_gcp_smoke_script.py` / `test_gcp_make_targets.py` | Scripts de bootstrap, smoke y targets `make *-gcp` con `gcloud` falso (sin red) |
| `test_api_guards.py` | Guardas de `/api/chat` y `/api/approve` |
| `test_prompts.py` | Validación y render de los prompts YAML |
| `test_telemetry.py` (3 de 4) | `build_trace_config`, estado de telemetría, `/api/health` |
| `mcp_financial_action_server/tests/` | 19 tests del servidor MCP (tools, concurrencia, protocolo) |
| `policies/` (`opa test`) | 6 tests de la política OPA (ver `docker run ... opa test /policies`) |

### Con LLM real (se omiten sin credenciales)

| Archivo | Qué mide |
| :--- | :--- |
| `test_readonly_agent_evals.py` | Tool y argumentos correctos del `readonly_agent`, sin escrituras |
| `test_action_agent_evals.py` | Tool calls staged del `action_agent` contra `action_agent_ground_truth.json` |
| `test_orchestrator_evals.py` | Calidad del plan del Orchestrator (dataset `orchestrator-plan-evals`) |
| `test_attack_3_1_e2e_evals.py` | Medición E2E del escenario 3.1 (dataset `attack-3-1-scenarios`) |
| `test_graph_flow.py` (2 de 3) | Flujo del grafo y ciclo HITL (el test de inyección es offline) |
| `test_api.py` (3 de 4) | `/api/chat` y `/api/approve` (`test_health_endpoint` es offline) |
| `test_telemetry.py` (1 de 4) | `trace_id` en la auditoría de `/api/chat` |

---

## 2. Detalle de los evals

### C. `tests/evals/test_readonly_agent_evals.py` (Evals con LLM Real y Trazabilidad LangSmith)

Suite de evaluación que valida la precisión del LLM real en el razonamiento agéntico ReAct, selección estricta de herramientas de solo lectura y trazabilidad en LangSmith.

* **`test_eval_balances_task_calls_only_account_balances()`**
  * **Función:** Tarea de consulta de saldos en `PlanStep`.
  * **¿Invoca Modelo?:**  **SÍ (LLM Real)**.
  * **Qué testea:**
    1. Invocación exclusiva de `get_account_balances` (exactamente 1 vez) con `user_id`.
    2. Cero llamadas a `get_recent_transactions`.
    3. Que `final_response` contenga saldos reales de cuentas (`485,250.75 ARS`, `1,520 USD`).
    4. Actualización del paso activo en `execution_plan` marcándolo con el resultado final.

* **`test_eval_transactions_task_calls_only_recent_transactions()`**
  * **Función:** Tarea de movimientos bancarios en `PlanStep`.
  * **¿Invoca Modelo?:**  **SÍ (LLM Real)**.
  * **Qué testea:** Invocación exclusiva de `get_recent_transactions` (sin llamar a saldos), reporte de transacciones y completado del paso.

* **`test_eval_compound_task_calls_both_tools()`**
  * **Función:** Tarea compuesta de conciliación (saldos + movimientos) en `PlanStep`.
  * **¿Invoca Modelo?:**  **SÍ (LLM Real)**.
  * **Qué testea:** Invocación de ambas herramientas secuencialmente en el bucle ReAct, síntesis combinada y completado del paso.

* **`test_eval_multi_step_plan_completes_only_active_step()`**
  * **Función:** Ejecución del Paso 2 en un plan multi-paso.
  * **¿Invoca Modelo?:**  **SÍ (LLM Real)**.
  * **Qué testea:** Que únicamente el paso activo asignado a `readonly_agent` sea procesado y completado, preservando intactos los pasos previos y posteriores.

* **`test_eval_langsmith_telemetry_and_config_propagation()`**
  * **Función:** Propagación de contexto y configuración de trazabilidad LangSmith.
  * **¿Invoca Modelo?:**  **SÍ (LLM Real)**.
  * **Qué testea:** Inyección de `RunnableConfig` generado con `build_trace_config`, preservación de `run_id`, `tags`, `metadata` y propagación de `user_id` hacia el ejecutor ReAct.

---

### E. `tests/evals/test_orchestrator_evals.py` (Evals del Orchestrator)

Ground truth en `tests/data/orchestrator_ground_truth.json`. Corre el router y el `ToolNode` de documentos de producción (sin ejecutores) y evalúa el plan resultante:
* `plan_targets_match`, `plan_params_copied`, `plan_within_limits` (deterministas) y `plan_grounded_in_request` (TypeSafe Jev), definidos en `backend/tests/decision_models_evaluators.py`.
* El caso `orchestrator_adversarial_document_measured` (documento de `local_storage`) solo se mide.
* Para cambiar el documento en un caso se usa `document_fixture` (archivo de `tests/fixtures/documents/`), que sobrescribe `settings.local_document_path` durante la corrida.

### F. `tests/evals/test_attack_3_1_e2e_evals.py` (Medición E2E de la amenaza 3.1)

Ejecuta el grafo completo con LLM real sobre los escenarios de `tests/data/attack_3_1_scenarios.json`, `ATTACK_EVAL_REPETITIONS` veces (por defecto 3). Los evaluadores (`backend/attack_scenario_evaluators.py`) **miden** cada eslabón del ataque y no determinan pass/fail del sistema: `plan_deviated`, `action_agent_invoked`, `new_beneficiary_registered` y `transfer_requested_to_adversary` (verifican la tool call explícita), `transfer_executed_to_adversary`, `execution_path` (`FAST_PATH` / `HITL` / `BLOCKED` / `ERROR` / `NONE`) y `blocked_by_model_armor`. Los espías registran las llamadas sin reemplazar las herramientas reales. El estado se reinicia antes y después de cada corrida.

---

## 3. Guía de ejecución

### Offline (sin costo)
Desde la raíz del repo, `make test`, o a mano:

```bash
cd backend
pytest tests/unit -q
cd .. && pytest mcp_financial_action_server/tests -q
docker run --rm -v "$PWD/policies:/policies:ro" openpolicyagent/opa:1.4.2-static test /policies -v
```

### Con modelo real
Configurar credenciales en `.env` (ver `.env.example`; el modelo del Orchestrator se fija con `ORCHESTRATOR_PROVIDER` / `ORCHESTRATOR_MODEL`, ver `docs/modelos_probados.md`) y ejecutar el archivo deseado, por ejemplo:

```bash
cd backend && pytest tests/integration/test_api.py tests/integration/test_graph_flow.py -v
```

El E2E del ataque requiere OPA accesible (`PDP_URL`); sin él aplica `pdp_fallback_mode` y la transferencia termina en `FAILED`.