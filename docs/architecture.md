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
   │ (Seguro & Anonimizado)
   ▼
[planner] ◄───────────────────────────────────────────────┐
   │ (Formulación inicial: List[Task])                     │
   ▼                                                       │
┌───────────────────┐                                      │
│ route_to_executor │                                      │
└─────────┬─────────┘                                      │
          ├──────► tool: "DB_Query" / "banking_read" ──► [readonly_agent]
          │                                                    │
          └──────► tool: "Transfer_API" / "banking_write" ─► [action_agent]
                                                               │ (interrupt)
                                                               ▼
                                                    [Estado: Aprobación Pendiente]
                                                               │
                                                               │ POST /api/approve
                                                               ▼
                                                    [Reanudación y Débito DB]
                                                               │
          ┌────────────────────────────────────────────────────┘
          ▼
     [replanner] ◄─────────────────────────────────────────────┘
          │ (Evalúa resultados de past_steps + optimiza ventana de contexto)
          ├──────► decision == "CONTINUE" ──► [route_after_replanner] ──► (siguiente tarea)
          ├──────► decision == "REPLAN"   ──► [route_after_replanner] ──► (nueva tarea)
          │
          └──────► decision == "FINISH" (o rechazo HITL) ──► [END: Respuesta Final]
```

### 2.1. Nodo `sanitizer` (Middleware Determinístico)
- **Tipo:** Determinístico (Código Python estricto, sin llamadas a LLM).
- **Entrada:** `raw_input` del usuario.
- **Responsabilidades:**
  1. Ejecuta el filtro de **Model Armor**: Compara el texto contra expresiones regulares de patrones de Jailbreak, System Override y Goal Hijacking. Si hay coincidencia, marca `is_threat = True` y short-circuita el grafo.
  2. Ejecuta **Google Cloud DLP (Sensitive Data Protection)**: De-identifica números de tarjetas de crédito (13-16 dígitos), CBUs (22 dígitos), DNIs (7-8 dígitos) y correos electrónicos, sustituyéndolos por tokens opacos (`[TARJETA_CREDITO_1]`, `[CBU_BANCARIO_1]`).
  3. Almacena la correspondencia de tokens en el **`TokenVault`**, un almacén criptográficamente aislado que reside en la memoria del backend y nunca se comparte con los prompts de los agentes.

### 2.2. Nodos `planner` y `replanner` (Orquestación Adaptativa Multi-Paso)

En reemplazo del antiguo clasificador estático (`router.py`), el sistema implementa una arquitectura desacoplada y orientada a metas basada en dos agentes coordinados:

#### A. Nodo `planner` (Planner Agent)
- **Tipo:** Planificador Semántico con LLM Estructurado.
- **Entrada:** `sanitized_input` (texto anonimizado por DLP y auditado por Model Armor).
- **Responsabilidad:** Formula el plan inicial estructurado (`Plan`) mediante `llm.with_structured_output(Plan)`.
- **Contrato de Datos (`app/graph/schemas.py`):**
  - **`Task`**:
    - `id: int`: Identificador numérico secuencial del paso (1, 2, ...).
    - `dependencies: List[int]`: IDs de tareas que deben completarse previamente.
    - `description: str`: Instrucción explícita y directa para el agente ejecutor.
    - `assigned_tool: str`: Herramienta corporativa asignada (`'DB_Query'`, `'banking_read'`, `'CRM_API'`, `'Transfer_API'`, `'Analytics_API'`).
  - **`Plan`**:
    - `steps: List[Task]`: Secuencia ordenada de tareas no redundantes requeridas para cumplir el objetivo corporativo.
- **Gobernanza y Cero Fallback Determinístico:** No utiliza heurísticas cableadas ni listas de palabras clave para adivinar intenciones. Si el proveedor de LLM no está disponible o falla, interrumpe la ejecución con `ValueError` para preservar la integridad operacional del banco y evitar ejecuciones espurias.

#### B. Nodo `replanner` (Replanner Agent)
- **Tipo:** Evaluador Dinámico y Replanificador con LLM Estructurado.
- **Entrada:** `input` original, `plan` activo, `current_task_idx`, historial `past_steps` y resultado de la última tarea ejecutada (`final_response`, `approval_status`).
- **Responsabilidad:** Supervisa el progreso de ejecución tras cada paso completado por los agentes ejecutores y toma decisiones estructuradas (`ReplannerDecision`):
  - **`CONTINUE`**: El plan continúa vigente; incrementa `current_task_idx` y envía el flujo al siguiente ejecutor.
  - **`REPLAN`**: Si los datos obtenidos requieren cambiar de estrategia (ej. saldo insuficiente o cuenta destino ambigua), genera un `new_plan` actualizado y reinicia el puntero de ejecución.
  - **`FINISH`**: El objetivo se ha cumplido o el plan concluyó; sintetiza la respuesta final consolidada (`final_response`) para el usuario corporativo y transiciona al nodo `END`.
- **Manejo de Rechazo HITL:** Si el oficial de cumplimiento rechaza la operación (`approval_status == "REJECTED"`), el Replanner corta de inmediato la ejecución sin evaluar nuevos pasos, garantizando que ninguna orden no autorizada continúe.
- **Optimizador de Contexto (`ContextOptimizer`):**
  - Implementado en [`app/graph/context_optimizer.py`](file:///home/santi/Documentos/Curso%20Ciberseguridad/gcp-projects/financial_asistent/backend/app/graph/context_optimizer.py).
  - Trunca determinísticamente resultados extensos (`max_chars_per_result`) para no agotar la ventana de contexto.
  - Aplica una ventana deslizante (`recent_window_size=4`): las tareas previas más antiguas se compactan en resúmenes informativos de una sola línea, mientras que los pasos recientes conservan sus detalles operacionales completos.

### 2.3. Nodo `readonly_agent` (Read-Only DB Agent)
- **Tipo:** Agente de Consulta Restringido.
- **Herramientas asignadas:** Únicamente `get_account_balances` y `get_recent_transactions`.
- **Gobernanza:** Este agente tiene **cero herramientas de escritura o mutación**. Incluso si el modelo sufriera una alucinación o un ataque de inyección indirecto, carece de cualquier capacidad sintáctica o técnica para alterar saldos o ejecutar transferencias. Una vez ejecutada la consulta, transiciona de retorno al `replanner` para evaluar el paso siguiente.

### 2.4. Nodo `action_agent` (Action Agent con Human-in-the-Loop)
- **Tipo:** Agente Transaccional con Breakpoint.
- **Herramientas asignadas:** `stage_transfer_request`.
- **Mecanismo HITL:**
  1. Extrae los parámetros de la transferencia (monto y token del destinatario).
  2. Llama al core bancario para **encolar** la transferencia en estado `PENDING_APPROVAL`.
  3. Invoca la función nativa `interrupt({...})` de LangGraph.
  4. LangGraph congela el hilo de ejecución, guarda el checkpoint en la base de datos y retorna el control al cliente con el estado `PENDING_APPROVAL`.
  5. Cuando el oficial de cumplimiento aprueba la operación vía `POST /api/approve`, el grafo se reanuda mediante `Command(resume=...)`, resolviendo el CBU o cuenta real desde el `TokenVault` y ejecutando el débito de forma atómica.
  6. Finalizada la ejecución, cede el control al `replanner` para la consolidación de la respuesta o la continuación del plan.

---

## 3. Estado del Grafo (`AgentState` / `PlanState`)

El estado compartido a lo largo de las transiciones del grafo está fuertemente tipado en [`app/graph/state.py`](file:///home/santi/Documentos/Curso%20Ciberseguridad/gcp-projects/financial_asistent/backend/app/graph/state.py) e integra el contrato de `PlanState`:

| Campo | Tipo | Propósito |
| :--- | :--- | :--- |
| `input` | `str` | Petición inicial del usuario corporativo (`PlanState`). |
| `plan` | `List[Dict[str, Any]]` | Secuencia ordenada de tareas (`Task`) que componen el plan de trabajo actual. |
| `past_steps` | `List[Dict[str, Any]]` | Historial de tareas ejecutadas con sus resultados, herramientas y estados (`COMPLETED`, `REJECTED`). |
| `current_task_idx` | `int` | Índice de control de la tarea en ejecución dentro del plan. |
| `current_step_index`| `int` | Alias de compatibilidad hacia `current_task_idx`. |
| `plan_completed` | `bool` | Flag que indica si el plan de trabajo ha concluido exitosamente o ha sido abortado. |
| `plan_summary` | `Optional[str]` | Resumen legible de la formulación o replanificación del plan. |
| `current_intent` | `Optional[str]` | Alias de intención derivado dinámicamente de la herramienta en ejecución (`balance`, `transactions`, `transfer`, `general`, `blocked`). |
| `messages` | `Annotated[List[BaseMessage], add_messages]` | Historial conversacional de LangChain. |
| `raw_input` | `str` | Texto sin filtrar recibido del cliente (aislado en auditoría). |
| `sanitized_input` | `str` | Texto desidentificado que reciben los prompts. |
| `detected_pii` | `List[DetectedPII]` | Lista de InfoTypes detectados y sustituidos por DLP. |
| `is_threat` | `bool` | Flag de Model Armor ante inyecciones adversarias. |
| `threat_category` | `Optional[str]` | Categoría de la amenaza detectada si `is_threat` es True. |
| `threat_reason` | `Optional[str]` | Detalle heurístico del bloqueo de seguridad. |
| `defense_action` | `str` | Resultado de la capa de sanitización (`PASSED`, `DEIDENTIFIED`, `BLOCKED`). |
| `pending_transfer` | `Optional[Dict[str, Any]]` | Metadatos de la transferencia encolada para HITL. |
| `approval_status` | `Optional[str]` | Decisión humana (`NONE`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`). |
| `status` | `str` | Estado global del ciclo (`IN_PROGRESS`, `PENDING_APPROVAL`, `COMPLETED`, `BLOCKED`). |
| `final_response` | `Optional[str]` | Mensaje final formateado devuelto al cliente (sintetizado por el Replanner o agente ejecutor). |

---

## 4. Persistencia y Checkpointing Seguro

Para garantizar que las conversaciones, la memoria multi-paso y los puntos de interrupción (`interrupt`) puedan pausarse y reanudarse con tolerancia a fallos en entornos serverless y contenedores distribuidos, el sistema ofrece persistencia multi-backend ([`app/db/checkpointer.py`](file:///home/santi/Documentos/Curso%20Ciberseguridad/gcp-projects/financial_asistent/backend/app/db/checkpointer.py)):

1. **PostgreSQL (`PostgresSaver`) con Pool de Conexiones:**
   - Emplea `psycopg-pool` (`ConnectionPool`) para gestión robusta y de alta concurrencia de conexiones hacia Cloud SQL o PostgreSQL corporativo.
   - Inicializa automáticamente el esquema de tablas mediante `saver.setup()`.
   - Garantiza aislamiento ACID por sesión/hilo de conversación (`thread_id`).

2. **Degradación Elegante y Tolerante a Fallos (Fallback a `MemorySaver`):**
   - Si la instancia de PostgreSQL no se encuentra accesible al iniciar o durante pruebas offline, el checkpointer captura la excepción, registra un aviso en los logs y activa de forma transparente un `MemorySaver` en memoria para no interrumpir la operatividad.

3. **Modo Producción en GCP Serverless (Cloud Firestore Native):**
   - Aprovecha el nivel gratuito permanente de Firestore (1 GB y 50.000 lecturas/día), permitiendo operar el asistente sin incurrir en costos de instancias inactivas de bases de datos.
   - Datos cifrados en reposo con llaves administradas por Google o CMEK.
