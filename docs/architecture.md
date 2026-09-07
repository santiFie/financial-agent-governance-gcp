# Documento de Arquitectura Técnica - AegisBank AI

Este documento detalla los componentes, decisiones de diseño, contratos de datos y patrones arquitectónicos adoptados en la implementación del **Sistema Multi-Agente Bancario con Defense-in-Depth**.

---

## 1. Visión General y Filosofía de Diseño

Los sistemas agénticos que delegan operaciones financieras a modelos de lenguaje grandes (LLMs) enfrentan riesgos críticos:
- **No determinismo:** Un LLM no puede garantizar probabilísticamente que jamás filtrará un dato o ejecutará una acción no deseada.
- **Vulnerabilidad a Prompt Injections:** Atacantes pueden manipular el contexto conversacional para forzar acciones arbitrarias.
- **Fuga de Información Personal (PII):** Enviar datos bancarios sin procesar a nubes de terceros o a modelos viola regulaciones como GDPR, PCI-DSS y normativas bancarias locales.

Para solucionar estos problemas, AegisBank AI adopta el principio de **Defensa en Profundidad (Defense-in-Depth)**: la seguridad reside en la **arquitectura perimetral y determinística del software**, no en las instrucciones del prompt del modelo.

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
[router]
   ├──────► intent == "balance" | "transactions" | "general" ────► [readonly_agent] ──► [END]
   │
   └──────► intent == "transfer" ───────────────────────────────► [action_agent]
                                                                        │ (interrupt)
                                                                        ▼
                                                             [Estado: Aprobación Pendiente]
                                                                        │
                                                                        │ POST /api/approve
                                                                        ▼
                                                             [Reanudación y Débito DB] ──► [END]
```

### 2.1. Nodo `sanitizer` (Middleware Determinístico)
- **Tipo:** Determinístico (Código Python estricto, sin llamadas a LLM).
- **Entrada:** `raw_input` del usuario.
- **Responsabilidades:**
  1. Ejecuta el filtro de **Model Armor**: Compara el texto contra expresiones regulares de patrones de Jailbreak, System Override y Goal Hijacking. Si hay coincidencia, marca `is_threat = True` y short-circuita el grafo.
  2. Ejecuta **Google Cloud DLP (Sensitive Data Protection)**: De-identifica números de tarjetas de crédito (13-16 dígitos), CBUs (22 dígitos), DNIs (7-8 dígitos) y correos electrónicos, sustituyéndolos por tokens opacos (`[TARJETA_CREDITO_1]`, `[CBU_BANCARIO_1]`).
  3. Almacena la correspondencia de tokens en el **`TokenVault`**, un almacén criptográficamente aislado que reside en la memoria del backend y nunca se comparte con los prompts de los agentes.

### 2.2. Nodo `router` (Triage Agent)
- **Tipo:** Clasificador de Intenciones.
- **Entrada:** `sanitized_input` (solo texto desidentificado).
- **Responsabilidades:** Clasifica la intención en una de las siguientes categorías de negocio:
  - `balance`: Consultas de saldos de cajas de ahorro o cuentas corrientes.
  - `transactions`: Consultas de movimientos históricos y extractos.
  - `transfer`: Solicitudes de envío o giro de fondos.
  - `general`: Preguntas institucionales, horarios, preguntas frecuentes.

### 2.3. Nodo `readonly_agent` (Read-Only DB Agent)
- **Tipo:** Agente de Consulta Restringido.
- **Herramientas asignadas:** Únicamente `get_account_balances` y `get_recent_transactions`.
- **Gobernanza:** Este agente tiene **cero herramientas de escritura o mutación**. Incluso si el modelo sufriera una alucinación o un ataque de inyección indirecto, carece de cualquier capacidad sintáctica o técnica para alterar saldos o ejecutar transferencias.

### 2.4. Nodo `action_agent` (Action Agent con Human-in-the-Loop)
- **Tipo:** Agente Transaccional con Breakpoint.
- **Herramientas asignadas:** `stage_transfer_request`.
- **Mecanismo HITL:**
  1. Extrae los parámetros de la transferencia (monto y token del destinatario).
  2. Llama al core bancario para **encolar** la transferencia en estado `PENDING_APPROVAL`.
  3. Invoca la función nativa `interrupt({...})` de LangGraph.
  4. LangGraph congela el hilo de ejecución, guarda el checkpoint en la base de datos y retorna el control al cliente con el estado `PENDING_APPROVAL`.
  5. Cuando el oficial de cumplimiento aprueba la operación vía `POST /api/approve`, el grafo se reanuda mediante `Command(resume=...)`, resolviendo el CBU o cuenta real desde el `TokenVault` y ejecutando el débito de forma atómica.

---

## 3. Estado del Grafo (`AgentState`)

El estado compartido a lo largo de las transiciones del grafo está fuertemente tipado en [`app/graph/state.py`](file:///home/santi/Documentos/Curso%20Ciberseguridad/gcp-projects/financial_asistent/backend/app/graph/state.py):

| Campo | Tipo | Propósito |
| :--- | :--- | :--- |
| `messages` | `Annotated[List[BaseMessage], add_messages]` | Historial conversacional de LangChain. |
| `raw_input` | `str` | Texto sin filtrar recibido del cliente (aislado en auditoría). |
| `sanitized_input` | `str` | Texto desidentificado que reciben los prompts. |
| `detected_pii` | `List[DetectedPII]` | Lista de InfoTypes detectados y sustituidos por DLP. |
| `is_threat` | `bool` | Flag de Model Armor ante inyecciones adversarias. |
| `current_intent` | `str` | Clasificación de negocio asignada por el Router. |
| `pending_transfer` | `Optional[Dict[str, Any]]` | Metadatos de la transferencia encolada para HITL. |
| `approval_status` | `Optional[str]` | Decisión humana (`PENDING_APPROVAL`, `APPROVED`, `REJECTED`). |
| `final_response` | `Optional[str]` | Mensaje devuelto al cliente. |

---

## 4. Persistencia y Checkpointing Seguro

Para garantizar que las conversaciones y los puntos de interrupción (`interrupt`) puedan pausarse y reanudarse en arquitecturas sin estado (Serverless / Cloud Run), se implementa un Checkpointer:

- **Modo Desarrollo / Local:** `MemorySaver`, que almacena los checkpoints en la memoria del proceso, ideal para testing unitario y ejecución sin dependencias de infraestructura.
- **Modo Producción en GCP:** **Cloud Firestore (Native Mode)**.
  - **Ventaja de Costo:** Firestore tiene un nivel gratuito permanente (1 GB y 50.000 lecturas/día), permitiendo operar el asistente sin gastar el crédito de $300 en instancias activas como Cloud SQL.
  - **Cifrado en Reposo:** Todos los datos en Firestore están encriptados por defecto con llaves administradas por Google (CMEK o Google-managed keys).
