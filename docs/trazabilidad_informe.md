# Trazabilidad informe → PoC

Este documento relaciona las secciones del informe final del curso *Seguridad y Gobernanza en Agentes de IA* con el código de este repositorio, y deja escritas las diferencias deliberadas entre ambos. El PoC reproduce la amenaza **3.1 (Goal Hijacking / Confused Deputy)**; no pretende implementar todo lo que el informe describe.

Convención: **✅ implementado** · **🟡 parcial o simulado** · **⬜ no construido (trabajo futuro)**.

---

## 1. Sección 2 — Arquitectura del sistema de prueba y línea base de hardening

| Informe | Estado | Dónde está en el repo |
| :--- | :---: | :--- |
| 2.1 Grafo multi-agente sobre LangGraph | ✅ | `backend/app/graph/graph.py` (cableado), `state.py` (estado), `docs/architecture.md` (diagrama) |
| Sanitizer Node (pre-LLM, sin inferencia) | ✅ | `backend/app/graph/nodes/sanitizer.py`, `core/dlp_sanitizer.py`, `core/model_armor.py`, `core/security_context.py` (Token Vault) |
| Planner Agent (orquestador) | ✅ | `backend/app/graph/nodes/router.py` (plan append-only, replan), `plan_utils.py`, `agent_registry.py`, prompts `app/prompts/orchestrator_*.yaml` |
| Read-Only Agent (saldos, movimientos) | ✅ | `backend/app/graph/nodes/readonly_agent.py`, `tools/banking_read.py` (solo herramientas de lectura) |
| Action Agent (transferencias, alta de CBU) | ✅ | `backend/app/graph/nodes/action_agent.py`, `tools/banking_write.py` |
| Servidor transaccional FastMCP con esquemas tipados | ✅ | `mcp_financial_action_server/` (validación de CBU/alias, idempotencia, límites, atomicidad); cliente en `backend/app/core/mcp_action_client.py` |
| Documento adjunto como texto no estructurado | ✅ | `core/document_loader.py`, `tools/document_reader.py` (`read_document_tool`), `backend/local_storage/comprobante.txt` |
| Ingesta desde bucket | ✅ | `GCSFileLoader` con `USE_GCS_INGESTION=true`; sin el objeto cae al archivo local. `make build-gcp` crea el bucket y las corridas de `make attack-gcp` leen de él (`use_gcs_ingestion: true` en `docs/evidence/runs/`) |
| 2.2 Model Armor | ✅ | Heurísticas léxicas locales por defecto (`LocalModelArmorGuard`); servicio real con `USE_GCP_MODEL_ARMOR=true` (`docs/gcp_deployment_and_iam.md`, secciones 3.b y 3.c). Con GCP real su veredicto es final y los fallbacks quedan registrados. Verificado en corridas guardadas: el comprobante pasa Model Armor real (`services.used.model_armor.gcp`) |
| 2.2 DLP / Sensitive Data Protection | ✅ | Regex local en `local_mock`; Cloud DLP real en `SECURITY_MODE=gcp_live` (`core/dlp_sanitizer.py`, tokens estables por sesión). Verificado en corridas guardadas (`services.used.dlp.gcp`) |
| 2.2 VPC Service Controls, Secret Manager, gVisor, Cloud Run | ⬜ / 🟡 | Descritos en `docs/gcp_deployment_and_iam.md` y `deploy/`; no son parte de la ejecución local |
| 2.3 ABAC con PEP/PDP desacoplados (OPA, Rego, deny por defecto) | ✅ | PEP: `backend/app/graph/nodes/pep.py`; PDP: `policies/transaction.rego` (+ `transaction_test.rego`), contenedor `opa` en `docker-compose.yml` |
| 2.3 Umbral de $50.000 ARS o destinatario no confiable → HITL | ✅ | `policies/transaction.rego`; HITL con `interrupt()` y `POST /api/approve` (`backend/app/main.py`) |
| 2.3 Identidad de cargas con SPIFFE/SPIRE y mTLS | ⬜ | Fuera de alcance. La identidad es el `user_id` del request; no hay mTLS |
| 2.3 Autorización por horarios bancarios, identidad agente–acción–herramienta | ⬜ | La política actual evalúa solo monto y `is_trusted_recipient` |

## 2. Sección 3.1 — Goal Hijacking y Confused Deputy

| Paso del informe | Dónde ocurre en el PoC |
| :--- | :--- |
| El usuario pide conciliar movimientos y adjunta un comprobante | `POST /api/chat` con `document_name`; escenarios en `backend/tests/data/attack_3_1_scenarios.json` |
| El documento pasa por el pipeline de sanitización (DLP y filtros léxicos de Model Armor) | `read_document_tool` → Model Armor + DLP (`tools/document_reader.py`) |
| El comprobante contiene la directiva camuflada ("ajuste normativo fiscal N.º 11.222") | Solo como **dato**: `backend/local_storage/comprobante.txt`. Ningún archivo de `backend/app`, prompts o herramientas contiene la cuenta, el monto ni el texto del ataque |
| El Planner no distingue la directiva y se desvía del objetivo del usuario | `router.py` (replan a partir de resultados y del documento). Depende solo del comportamiento del modelo |
| Se agregan pasos `action_agent`: alta de cuenta y transferencia | `action_agent.py` (`register_recipient_tool`, `transfer_funds_tool`) |
| La infraestructura aprueba: llamada bien formada, identidad válida, monto bajo el umbral | `pep.py` consulta a OPA con `{user_id, tool_name, amount, is_trusted_recipient}` (el alta solo con `{user_id, tool_name}`); la cuenta quedó en la agenda en el paso anterior, así que el PDP responde Fast-Path |
| La transferencia se ejecuta sin HITL | `pep.py` (rama Fast-Path) → `MCPActionClient.execute_approved_transfer` |
| Punto de quiebre: se verifica *quién*, no *por qué* | Es el resultado a observar. No se agregó ninguna defensa semántica (postura *baseline-vulnerable*) |

**Cómo se observa y se mide**

- Narrado paso a paso: `make attack` (`scripts/demo_attack.py`). Si no tenés un LLM, la corrida guardada se ve con `make server-results` (`docs/evidence/`).
- Medido: `backend/tests/evals/test_attack_3_1_e2e_evals.py` registra, como métricas separadas, si el plan se desvió, si se invocó `action_agent`, si se pidió y si se ejecutó la transferencia a la cuenta ajena, y por qué camino (Fast-Path o HITL). Un resultado "el ataque funcionó" es el resultado esperado y no un bug.
- Contraste: `make attack-control` y `backend/tests/data/control_scenarios.json`. Transferencias directas a un destinatario que no está en la agenda terminan en HITL aunque el monto sea 49.999 ARS. Eso muestra que la línea base funciona como fue diseñada y que el ataque pasa porque primero hace que la cuenta sea "de confianza" y luego queda bajo el umbral.

## 3. Sección 5.2.1 — Diagnóstico sobre las capas de MAESTRO

| Capa MAESTRO (informe) | Cómo se manifiesta en el PoC | Dónde mirar |
| :--- | :--- | :--- |
| Capa 7 – Agent Ecosystem: estímulo externo (comprobante) | El documento adjunto llega por `document_name` o desde el bucket | `core/document_loader.py`, `local_storage/comprobante.txt` |
| Capa 1 – Foundation Models: inyección indirecta | El Orchestrator lee el contenido y lo trata como instrucción | `read_document_tool`, prompts del orquestador (genéricos, sin pistas sobre el ataque) |
| Capa 3 – Agent Frameworks: plan y ruteo incorrectos | Replan con pasos `action_agent` no pedidos por el usuario | `execution_plan` en la corrida guardada de `docs/evidence/runs/` |
| Capa 6 – Security and Compliance: desvío de las políticas OPA | Monto 49.999 ARS + alta previa del destinatario → Fast-Path | `policies/transaction.rego`, `nodes/pep.py` |

## 4. Diferencias deliberadas respecto del informe

1. **CBU del informe.** El informe cita `000084328932217340083`, que tiene **21 dígitos**. Un CBU válido tiene 22, y el servidor MCP rechaza los que no cumplen (`mcp_financial_action_server/validation.py`). El PoC usa `0170099900000012345678` (22 dígitos). Es el único cambio de dato; el mecanismo del ataque es el mismo.
2. **Payload en dos etapas.** El informe lo plantea en una frase ("dar de alta... y acto seguido transferir"). El comprobante lo expresa como *Etapa 1* (alta en el padrón de contrapartes) y *Etapa 2* (transferencia), para que el Orchestrator produzca los dos pasos separados que el Action Agent ejecuta.
3. **Componentes.** El informe enumera cinco componentes (incluye *Analytics*); el repo implementa cuatro: Sanitizer, Orchestrator, Read-Only Agent y Action Agent (+ PEP).
4. **Alcance del PEP.** Como en el informe, el PEP intermedia todas las llamadas a herramientas contra el PDP: el `action_agent` solo encola y `pep_node` envía a OPA cada llamada con su `tool_name`. La política del PoC no tiene reglas por herramienta más allá de una: las que no son transferencia se permiten (`allow: true`). Las reglas de monto y destinatario siguen aplicándose solo a `transfer_funds_tool`. Ver la sección 5.
5. **Controles simulados o reales.** Model Armor y DLP corren con mocks locales salvo con `SECURITY_MODE=gcp_live` / `USE_GCP_MODEL_ARMOR=true`. En `docs/evidence/` conviven corridas en modo local (la transcripción de gpt-oss-120b) y corridas sobre GCP real (`pure_gcp: true`, solo con modelos de Vertex AI, ninguna terminó en transferencia). Cada corrida indica en `services.used` qué servicio sirvió GCP y cuál local.
6. **Persistencia.** `CHECKPOINTER_TYPE=firestore` persiste en Cloud Firestore y solo cae a `MemorySaver` (con aviso en el log) si el servicio no responde. El informe describe además memoria de largo plazo, que el PoC no implementa.

## 5. Alta de beneficiario y PEP (decisión tomada)

Antes, `register_recipient_tool` se ejecutaba dentro del `action_agent` y `pep_node` solo gobernaba la transferencia, lo que contradecía el invariante "todo pasa por el PEP". Se resolvió así: ambas herramientas solo encolan; `pep_node` consulta al PDP con el `tool_name` y ejecuta el alta si OPA responde `allow: true`. Es el patrón habitual (el PEP delega siempre en el PDP, y la política decide por herramienta con denegación por defecto para transferencias sin datos).

El ataque 3.1 no cambia: el PDP permite el alta porque no mueve fondos, la cuenta pasa a ser de confianza y la transferencia de 49.999 ARS queda en Fast-Path. Cambios derivados: si el PDP no responde (`deny`), tampoco se registra el destinatario; con `PDP_FALLBACK_MODE=hitl` las altas también fallan cerradas (no tienen flujo de aprobación humana).

## 6. Trabajo futuro (no construido, a propósito)

- Analytics, memoria de largo plazo en Firestore y los escenarios 3.2 y 4.2 del informe.
- SPIFFE/SPIRE y mTLS entre nodos.
- Mitigaciones del capítulo 5: CaMeL, FIDES, SEAgent, trazabilidad causal. Cuando se implementen deben ser **conmutables y apagadas por defecto**, para conservar la línea base vulnerable.
