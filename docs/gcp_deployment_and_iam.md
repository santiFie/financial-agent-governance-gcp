# Despliegue en Google Cloud Platform (GCP) y Guía de Aprendizaje IAM

Esta guía explica cómo desplegar este sistema (PoC) en GCP maximizando la preservación del crédito inicial de $300 USD y detalla el ejercicio práctico de configuración de **IAM bajo el Principio de Menor Privilegio (Least Privilege)**. Para consultar las decisiones sobre Firestore, Artifact Registry, cuentas de servicio de CI/CD (`sa-cloud-build`) y resolución de incidencias en Cloud Build, consulta [`docs/gcp_build_and_service_accounts_decisions.md`](gcp_build_and_service_accounts_decisions.md).

---

## 1. Estrategia de Minimización de Costos en GCP

Para operar este proyecto sin consumir de forma prematura tus $300 de crédito, hemos diseñado la infraestructura para apoyarse en los **Niveles Gratuitos Perpetuos (Free Tier)** de Google Cloud:

| Servicio GCP | Rol en el Proyecto | Estrategia de Ahorro / Free Tier |
| :--- | :--- | :--- |
| **Cloud Run** | Ejecución del backend FastAPI | **2 millones de peticiones/mes gratis**. Escalado a cero (`--min-instances=0`) y CPU throttling activo (`--no-cpu-throttling=false`), consumiendo cómputo únicamente durante peticiones activas. |
| **Cloud Firestore** | Checkpointer y persistencia de LangGraph | **1 GB de almacenamiento gratis y 50.000 lecturas/día**. Costo $0 vs los ~$25/mes que cuesta una base de datos Cloud SQL PostgreSQL activa. |
| **Cloud DLP** | De-identificación de datos sensibles (PII) | Primer **1 GB de inspección de texto al mes gratuito**. |
| **Vertex AI (Gemini 1.5 Flash)** | Razonamiento de agentes | Modelo de alta eficiencia con costo ultra-reducido (~$0.075 por 1 millón de tokens de entrada). |
| **Artifact Registry** | Almacenamiento de imágenes Docker | **0.5 GB/mes de almacenamiento gratuito**. |

---

## 2. Ejercicio Práctico: Configuración de IAM (Least Privilege)

> [!NOTE]
> Este módulo está reservado para ser completado por ti en el archivo [`deploy/iam-setup.sh`](../deploy/iam-setup.sh), permitiéndote afianzar las competencias de gobernanza y seguridad en agentes de IA.

### ¿Por qué NO usar la Service Account por defecto de Compute Engine?
Por defecto, Cloud Run puede ejecutarse con la Service Account `[PROJECT_NUMBER]-compute@developer.gserviceaccount.com`, la cual posee el rol **Editor** a nivel de proyecto. Si un atacante comprometiera el contenedor o lograra ejecución remota, tendría control total sobre el proyecto GCP.

Bajo **Defense-in-Depth**, creamos una Service Account dedicada:
`sa-financial-assistant@[PROJECT_ID].iam.gserviceaccount.com` con **exclusivamente 3 roles mínimos**:

1. **`roles/dlp.user`**:
   - *Propósito:* Permite invocar la API de Cloud DLP para inspeccionar y de-identificar textos con InfoTypes estándar y personalizados.
   - *Comando a completar en `deploy/iam-setup.sh`:*
     ```bash
     gcloud projects add-iam-policy-binding "$PROJECT_ID" \
       --member="serviceAccount:sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com" \
       --role="roles/dlp.user"
     ```

2. **`roles/aiplatform.user`**:
   - *Propósito:* Permite a la aplicación invocar modelos fundacionales (Gemini) a través de Vertex AI.
   - *Comando a completar:*
     ```bash
     gcloud projects add-iam-policy-binding "$PROJECT_ID" \
       --member="serviceAccount:sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com" \
       --role="roles/aiplatform.user"
     ```

3. **`roles/datastore.user`**:
   - *Propósito:* Permite leer y escribir documentos de estado en Cloud Firestore para el Checkpointer de LangGraph.
   - *Comando a completar:*
     ```bash
     gcloud projects add-iam-policy-binding "$PROJECT_ID" \
       --member="serviceAccount:sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com" \
       --role="roles/datastore.user"
     ```

---

## 3. Pasos de Despliegue en Cloud Run

Una vez completado el script de IAM, sigue estos pasos para desplegar en GCP:

### Paso 1: Habilitar APIs requeridas
```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  dlp.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  modelarmor.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com
```

### Paso 2: Crear base de datos Firestore en Modo Nativo
```bash
gcloud firestore databases create --location=us-central1 --type=firestore-native
```

### Paso 2.b: Crear el secreto del Token Vault
Con `ENVIRONMENT=production` el backend **no arranca** si `TOKEN_VAULT_SECRET` conserva el valor por defecto.
```bash
openssl rand -base64 32 | gcloud secrets create token-vault-secret --data-file=-
```
`deploy/iam-setup.sh` (paso 4c) concede a la Service Account `roles/secretmanager.secretAccessor` sobre ese secreto, por eso el secreto se crea antes.

### Paso 3: Ejecutar tu script de configuración IAM
```bash
chmod +x deploy/iam-setup.sh
./deploy/iam-setup.sh
```

### Paso 4: Construir y Subir la Imagen Docker
```bash
# Crear repositorio en Artifact Registry (si no existe)
gcloud artifacts repositories create fintech-repo \
  --repository-format=docker \
  --location=us-central1 \
  --description="Repositorio Docker para Asistente Financiero"

# Construir y subir imagen del Backend
gcloud builds submit . \
  --config deploy/cloudbuild-backend.yaml \
  --substitutions _IMAGE=us-central1-docker.pkg.dev/$PROJECT_ID/fintech-repo/backend:v1
```

### Paso 5: Desplegar en Cloud Run con Menor Privilegio
```bash
gcloud run deploy financial-assistant-backend \
  --image us-central1-docker.pkg.dev/$PROJECT_ID/fintech-repo/backend:v1 \
  --platform managed \
  --region us-central1 \
  --service-account sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com \
  --min-instances 0 \
  --max-instances 1 \
  --memory 512Mi \
  --cpu 1 \
  --no-allow-unauthenticated \
  --set-secrets TOKEN_VAULT_SECRET=token-vault-secret:latest \
  --set-env-vars ENVIRONMENT=production,SECURITY_MODE=gcp_live,GCP_PROJECT_ID=$PROJECT_ID,GCP_LOCATION=us-central1,LLM_PROVIDER=vertexai,VERTEX_MODEL_NAME=$VERTEX_MODEL,USE_GCP_MODEL_ARMOR=true,MODEL_ARMOR_TEMPLATE_ID=fin-template,PDP_URL=$PDP_URL,PDP_FALLBACK_MODE=deny
```

Puntos que el comando anterior corrige respecto de la versión previa de esta guía (sin ellos el servicio no arranca o no puede ejecutar transferencias):

- **`TOKEN_VAULT_SECRET`** es obligatorio en producción (ver Paso 2.b).
- **`LLM_PROVIDER=vertexai`**: con el valor por defecto (`litellm`) se intenta primero Groq y NVIDIA, que no tienen credenciales en Cloud Run.
- **`VERTEX_MODEL_NAME`**: el valor por defecto del repo (`gemini-1.5-flash-002`) corresponde a una generación de modelos que Google fue retirando; elegí un modelo vigente en Vertex AI Model Garden (`export VERTEX_MODEL=...`) y verificá que esté disponible en tu región.
- **`PDP_URL`**: tiene que apuntar a una instancia de OPA con `policies/` cargada (ver la nota al final). Sin PDP, con `PDP_FALLBACK_MODE=deny` toda alta de destinatario y toda transferencia termina `FAILED`.
- **`--max-instances 1`**: la agenda de destinatarios, el Token Vault y el checkpointer viven en memoria del proceso. Con más de una instancia, un `POST /api/approve` o una transferencia posterior pueden caer en otra instancia que no conoce el estado.
- **`--no-allow-unauthenticated`**: la PoC es deliberadamente vulnerable y el CORS del backend es `*`. No la expongas públicamente; invocala con un token de identidad (`gcloud auth print-identity-token`) o por `gcloud run services proxy`.

El manifiesto `deploy/cloudrun-service.yaml` es el equivalente declarativo: reemplazá `${PROJECT_ID}`, `${MODEL_ARMOR_TEMPLATE_ID}` y `${PDP_URL}` (p. ej. con `envsubst`) antes de `gcloud run services replace`. Nota: `deploy/dlp-template.json` documenta una plantilla de desidentificación, pero el código de DLP (`core/dlp_sanitizer.py`) hace `inspect_content` en línea y no la usa.

---

## 3.b Model Armor real (`gcp_live`)

Por defecto el guardrail es `LocalModelArmorGuard` (regex léxicas, `backend/app/core/model_armor.py`). Para usar el servicio real:

1. Habilitar la API y crear una plantilla en la misma región que `GCP_LOCATION` (los nombres de flags pueden variar con la versión de `gcloud`; revisá `gcloud model-armor templates create --help`):
   ```bash
   gcloud services enable modelarmor.googleapis.com
   gcloud model-armor templates create fin-template --location=$REGION \
     --pi-and-jailbreak-filter-settings-enforcement=enabled \
     --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above
   ```
2. Dar a la Service Account del backend permiso de uso:
   ```bash
   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:$SA_EMAIL" --role="roles/modelarmor.user"
   ```
   (`deploy/iam-setup.sh` ya lo hace en el paso 4b.)
3. Configurar `SECURITY_MODE=gcp_live`, `USE_GCP_MODEL_ARMOR=true`, `MODEL_ARMOR_TEMPLATE_ID=fin-template`, `GCP_PROJECT_ID` y `GCP_LOCATION` (ver `.env.production.example`; con `make up-prod`).
4. Para leer el comprobante desde un bucket: `USE_GCS_INGESTION=true`, `GCS_BUCKET_NAME=<bucket>` y el rol `roles/storage.objectViewer` sobre ese bucket. Si el objeto no existe se usa `backend/local_storage/comprobante.txt`.

**Comportamiento a tener en cuenta**

- El mismo `ModelArmorGuard` inspecciona el mensaje del usuario y el contenido del documento que lee `read_document_tool`.
- Cuando Model Armor responde (seguro o amenaza), su veredicto es **final**: el guardrail local no se consulta. Solo se usa el local si `USE_GCP_MODEL_ARMOR` está apagado, o si el cliente no se pudo crear o la llamada falla. En ese caso se registra una advertencia y queda anotado en `service_status` (la PoC no falla cerrado ante un error del servicio). Cada corrida guardada en `docs/evidence/runs/` indica en `services.used` qué servicios sirvió GCP y cuáles cayeron a local.
- En la PoC el comprobante está escrito para parecer un documento legítimo, de modo que **se espera que pase** Model Armor: el ataque 3.1 es semántico y no contiene firmas léxicas de jailbreak. Si en tu plantilla lo bloquea, la corrida termina en `BLOCKED` desde el sanitizer y eso es un resultado válido de medición.

**Estado de la evidencia:** las corridas de `make attack-gcp` guardadas en `docs/evidence/runs/` (campo `pure_gcp: true`) usaron Model Armor, Cloud DLP, Firestore, el bucket y Vertex AI reales, sin fallbacks a local, y el comprobante pasó Model Armor (`document_read: true`). Los resultados por modelo están en [`docs/analisis_modelos_ataque.md`](analisis_modelos_ataque.md) (sección 3c) y en [`docs/modelos_probados.md`](modelos_probados.md). Con los modelos de Vertex AI probados, ninguna de esas corridas terminó en transferencia.

---

## 3.c Modo GCP real desde tu máquina (`make build-gcp` → `smoke-gcp` → `attack-gcp`)

Ejecuta el ataque 3.1 en local contra servicios reales de GCP (sin desplegar Cloud Run). Todo se hace con las credenciales de **tu** usuario y en **tu** proyecto.

**Requisitos:** `gcloud` instalado, `gcloud auth application-default login`, un proyecto con facturación vinculada, rol Owner/Editor (o, como mínimo, Service Usage Admin, Datastore Owner, Model Armor Admin y Storage Admin) y Docker para levantar OPA.

| Comando | Qué hace |
| :--- | :--- |
| `make build-gcp PROJECT=<id>` | Idempotente. Habilita las APIs (DLP, Vertex AI, Firestore, Model Armor, Storage), crea la base Firestore, la plantilla de Model Armor `fin-template` y un bucket con el comprobante, y escribe `.env.gcp` (ignorado por git, con un `TOKEN_VAULT_SECRET` propio). Termina con los smoke tests. Opciones por `ARGS`: `--dry-run`, `--yes`, `--region`, `--no-gcs`, `--skip-smoke`, `--with-sa`. |
| `make smoke-gcp` | Prueba DLP, Model Armor, Vertex AI, Firestore y GCS reales antes de gastar una corrida. |
| `make attack-gcp` | Escenario 3.1 con `.env.gcp` cargado. `GCS=false` lee el comprobante de `backend/local_storage/`. Usa `--require-gcp`: se niega a correr (código de salida 3) si algún servicio, **incluido el LLM**, no es GCP real, y termina con código 3 si una corrida usó un fallback local (no es una medición 100% GCP). |
| `make attack-gcp-n N=5` | Lo mismo `N` veces, con tasas. |
| `make destroy-gcp` | Borra lo que creó `build-gcp` (bucket, plantilla, checkpoints); pide confirmación. |

`.env.gcp` fija `LLM_PROVIDER=vertexai`, así que todos los agentes usan el mismo modelo de Vertex AI (`VERTEX_MODEL_NAME`, `VERTEX_LOCATION`) y no hay proveedores de respaldo. Cada corrida se guarda en `docs/evidence/runs/` con `services.used` y `pure_gcp`. No medí el costo de estas corridas.

---

## 4. Verificación y Auditoría en Cloud Logging

Para comprobar que el contenedor está ejecutando las llamadas de sanitización con la Service Account correcta:
```bash
# Ver logs de auditoría en tiempo real
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=financial-assistant-backend" \
  --limit 30 \
  --format "table(timestamp, textPayload)"
```


## Nota: política OPA (PDP) en el despliegue

El `pep_node` consulta la política OPA en `PDP_URL` (en `docker-compose.yml` apunta al servicio `opa`). `deploy/cloudrun-service.yaml` **no despliega OPA** (solo define `PDP_URL`): sin PDP accesible se aplica `PDP_FALLBACK_MODE` (por defecto `deny`) y cada alta de destinatario y cada transferencia termina `FAILED`. Para un despliegue funcional hay que publicar OPA (p. ej. un segundo servicio de Cloud Run, con una imagen `openpolicyagent/opa` que incluya `policies/` y ejecute `opa run --server --addr=0.0.0.0:$PORT /policies`; ese paso no está automatizado ni probado en este repo) y configurar `PDP_URL=https://<servicio-opa>/v1/data/transaction/decision`. Si el servicio OPA exige autenticación, el backend tendría que enviar un token de identidad, y `pep_node` hoy no lo hace: dejá OPA accesible solo desde el backend por red interna o ingress restringido. Con `PDP_FALLBACK_MODE=hitl` las transferencias pasan a aprobación humana, pero las altas siguen fallando cerradas. El despliegue en GCP no forma parte de la demo local de la PoC.

`CHECKPOINTER_TYPE=firestore` persiste los checkpoints en Cloud Firestore; si el servicio no responde al iniciar, el backend cae a `MemorySaver` y lo registra (ver `docs/architecture.md`, sección 4).
