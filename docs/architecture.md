# Documento de Arquitectura Técnica - Sistema Multi-Agente Bancario

Este documento detalla los componentes, decisiones de diseño, contratos de datos y patrones arquitectónicos adoptados en la implementación del **Sistema Multi-Agente Bancario con Políticas de Seguridad y Gobernanza**.

---

## 1. Visión General y Filosofía de Diseño

Los sistemas agénticos que delegan operaciones financieras a modelos de lenguaje grandes (LLMs) enfrentan riesgos críticos:
- **No determinismo:** Un LLM no puede garantizar probabilísticamente que jamás filtrará un dato o ejecutará una acción no deseada.
- **Vulnerabilidad a Prompt Injections:** Atacantes pueden manipular el contexto conversacional para forzar acciones arbitrarias.
- **Fuga de Información Personal (PII):** Enviar datos bancarios sin procesar a nubes de terceros o a modelos viola regulaciones como GDPR, PCI-DSS y normativas bancarias locales.

Para solucionar estos problemas, se busca que la seguridad resida en la **arquitectura perimetral y determinística del software**, no en las instrucciones del prompt del modelo.

---

## 2. Nodos del Grafo en LangGraph

El flujo conversacional se modela como un grafo de estado dirigido usando **LangGraph v0.2+**, donde cada nodo tiene un propósito único y un alcance de privilegios aislado:

```
[START]
   │
   ▼
[sanitizer] ──(¿is_threat?)──► [END: Bloqueo Inmediato]
   │ (input anonimizado; plan y scratchpad reiniciados)
   ▼
[router / Orchestrator] ◄───────────────┐
   ├─ read_document_tool ─► [orchestrator_tools] ──┤  (Model Armor + DLP sobre el documento)
   ├─ paso pendiente: read_only_agent ──► [readonly_agent] ──┤
   ├─ paso pendiente: action_agent ────► [action_agent] ─► [pep_node] ─┤ (Fast-Path | interrupt HITL)
   └─ sin pasos pendientes ──────────► [END]  (final_response compuesta por el Orchestrator)
```

### 2.1. Nodo `sanitizer` (Middleware Determinístico)
- **Tipo:** Determinístico (Código Python estricto, sin llamadas a LLM).
- **Entrada:** `raw_input` del usuario.
- **Responsabilidades:**
  1. Ejecuta el filtro de **Model Armor** sobre el input. Si hay coincidencia, marca `is_threat = True` y short-circuita el grafo.
  2. Ejecuta **Google Cloud DLP (Sensitive Data Protection)**: de-identifica tarjetas, CBUs, DNIs y correos, sustituyéndolos por tokens opacos (`[CBU_BANCARIO_1]`), guardados en el **`TokenVault`** que nunca ve el LLM.
  3. Reinicia el plan (`execution_plan`) y el `orchestrator_scratchpad` del turno.
- **No lee documentos adjuntos**: los lee el Orchestrator a demanda con `read_document_tool`.

### 2.2. Nodo `router` (Orchestrator Agent)
- **Tipo:** Agente planificador (LLM con tool calling).
- **Entrada:** `sanitized_input`, si hay documento adjunto (`<attached_document available=... filename=.../>`) y el progreso del plan.
- **Plan:** lista append-only de `PlanStep` (`step_id`, `title`, `target_node`, `description`, `result`). Si queda un paso sin `result` no se invoca al LLM: el grafo despacha el paso. Si no, un turno LLM (`orchestrator_initial` con plan vacío, `orchestrator_replan` en otro caso) llama a `read_document_tool` o emite un `PlanUpdate` (`new_steps`, `final_answer`).
- **Cierre del turno:** con `new_steps` vacío el Orchestrator debe entregar `final_answer`; esa es la respuesta final. Errores del Orchestrator y límites excedidos (`orchestrator_max_plan_steps`, `orchestrator_max_tool_iterations`) terminan el turno con estado `FAILED`.
- **Agentes disponibles:** definidos en `agent_registry.py` (fuente única); los ejecutores solo ven la `description` del paso.
- **`read_document_tool(filename)`:** con GCS desactivado carga el documento de `backend/local_storage` (ignora `filename`); con GCS activado busca el objeto en el bucket y cae al documento local si no existe. Siempre pasa por Model Armor y DLP antes de llegar al LLM.

### 2.3. Nodo `readonly_agent` (Read-Only DB Agent)
- **Tipo:** Agente de Consulta Restringido.
- **Herramientas asignadas:** Únicamente `get_account_balances` y `get_recent_transactions`.
- **Gobernanza:** Este agente tiene **cero herramientas de escritura o mutación**. Incluso si el modelo sufriera una alucinación o un ataque de inyección indirecto, carece de cualquier capacidad sintáctica o técnica para alterar saldos o ejecutar transferencias.

### 2.4. Nodo `action_agent` y `pep_node`
- **Tipo:** Agente ReAct transaccional + Policy Enforcement Point.
- **Herramientas del `action_agent`:** `register_recipient_tool` (alta en la agenda de destinatarios) y `transfer_funds_tool`. Ambas **solo encolan** la solicitud en `pending_tool_calls` (la transferencia también en `pending_transfer`): el agente nunca modifica la agenda ni mueve fondos. Una llamada con argumentos inválidos (p. ej. un CBU mal formado) no se encola y el error vuelve al modelo.
- **`pep_node`:** toda llamada encolada pasa por el PEP, que consulta al PDP enviando el nombre de la herramienta (`tool_name`). Para `register_recipient_tool` la política responde `allow: true` (herramienta sin movimiento de fondos) y el PEP ejecuta el alta (`execute_register_recipient`); si el PDP deniega o no responde, no se registra nada y el paso termina `FAILED`. Para `transfer_funds_tool`: verifica si el destinatario es confiable, consulta al PDP (`settings.pdp_url`, política OPA: destinatario confiable y monto < 50.000 ARS → Fast-Path, caso contrario HITL). Si el PDP no responde, `settings.pdp_fallback_mode` decide: `deny` (por defecto, la transferencia termina `FAILED`) o `hitl` (todo pasa a aprobación humana); nunca Fast-Path y completa el paso. Fast-Path debita directamente; HITL llama a `interrupt()` y se reanuda con `POST /api/approve` (`Command(resume=...)`).

---

## 3. Estado del Grafo (`AgentState`)

El estado compartido a lo largo de las transiciones del grafo está fuertemente tipado en [`app/graph/state.py`](../backend/app/graph/state.py):

| Campo | Tipo | Propósito |
| :--- | :--- | :--- |
| `messages` | `Annotated[List[BaseMessage], add_messages]` | Historial conversacional de LangChain. |
| `raw_input` | `str` | Texto sin filtrar recibido del cliente (aislado en auditoría). |
| `sanitized_input` | `str` | Texto desidentificado que reciben los prompts. |
| `detected_pii` | `List[DetectedPII]` | Lista de InfoTypes detectados y sustituidos por DLP. |
| `is_threat` | `bool` | Flag de Model Armor ante inyecciones adversarias. |
| `document_name` | `Optional[str]` | Filename del documento adjunto a la solicitud (el contenido se lee con `read_document_tool`). |
| `execution_plan` | `List[Dict]` | Plan append-only del Orchestrator (`PlanStep`). |
| `orchestrator_scratchpad` | `List[BaseMessage]` | Mensajes de tool calling del Orchestrator (se reinicia cada turno). |
| `orchestrator_tool_rounds` | `int` | Rondas consecutivas de herramientas del Orchestrator. |
| `pending_tool_calls` | `Optional[List[Dict]]` | Tool calls de transferencia encoladas por el Action Agent para el PEP. |
| `pending_transfer` | `Optional[Dict[str, Any]]` | Metadatos de la transferencia encolada para HITL. |
| `approval_status` | `Optional[str]` | Decisión humana (`PENDING_APPROVAL`, `APPROVED`, `REJECTED`). |
| `final_response` | `Optional[str]` | Mensaje devuelto al cliente. |

---

## 4. Persistencia y Checkpointing Seguro

Para garantizar que las conversaciones y los puntos de interrupción (`interrupt`) puedan pausarse y reanudarse en arquitecturas sin estado (Serverless / Cloud Run), se implementa un Checkpointer:

- **Modo Desarrollo / Local:** `MemorySaver`, que almacena los checkpoints en la memoria del proceso, ideal para testing unitario y ejecución sin dependencias de infraestructura.
- **Modo Producción en GCP (previsto, no implementado):** **Cloud Firestore (Native Mode)**. Hoy `get_checkpointer()` (`app/db/checkpointer.py`) devuelve `MemorySaver` también con `CHECKPOINTER_TYPE=firestore`; el estado no sobrevive a reinicios del proceso.
  - **Ventaja de Costo:** Firestore tiene un nivel gratuito permanente (1 GB y 50.000 lecturas/día), permitiendo operar el asistente sin gastar el crédito de $300 en instancias activas como Cloud SQL.
  - **Cifrado en Reposo:** Todos los datos en Firestore están encriptados por defecto con llaves administradas por Google (CMEK o Google-managed keys).
