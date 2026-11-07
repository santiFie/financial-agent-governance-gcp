# Modelos probados para el Orquestador

El éxito del ataque 3.1 depende del comportamiento del modelo del Orquestador (`ORCHESTRATOR_PROVIDER` / `ORCHESTRATOR_MODEL`, ver `.env.example`). Si no se definen, el Orquestador usa `LLM_PROVIDER` / `LLM_MODEL`.

Observaciones del autor (corridas manuales, sin fecha exacta registrada; completar con fecha al re-probar):

| Modelo (OpenRouter) | Resultado observado |
|---|---|
| `nvidia/nemotron-3.5-super-vl:free` | **Único que funcionó** (planifica y replanifica correctamente). Default sugerido en `.env.example`. |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | No funcionó |
| `openai/gpt-oss-20b` | No funcionó |
| `openai/gpt-oss-120b` | No funcionó |

"Funcionó" = el Orquestador emite planes válidos con `PlanUpdate`; no implica que adopte la directiva del documento.
