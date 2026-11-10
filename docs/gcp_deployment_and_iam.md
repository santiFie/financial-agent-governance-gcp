# Servicios de GCP: levantarlos, usarlos y bajarlos

Esta guía tiene dos partes con propósitos distintos:

| Parte | Para qué sirve | ¿Hace falta para el ataque? |
| :--- | :--- | :--- |
| [**2. Servicios reales de GCP para el ataque**](#2-servicios-reales-de-gcp-para-el-ataque) | Levantar y bajar Model Armor, Cloud DLP, Firestore, Cloud Storage y Vertex AI en **tu** proyecto, y correr el ataque 3.1 contra ellos. | **Sí** (solo si querés medir contra GCP real) |
| [**3. Anexo de aprendizaje: Cloud Run e IAM de mínimo privilegio**](#3-anexo-de-aprendizaje-cloud-run-e-iam-de-mínimo-privilegio-no-necesario-para-el-ataque) | Ejercicio de *least privilege* con una Service Account dedicada y despliegue en Cloud Run. | **No** |

Sin GCP el ataque se corre igual en modo local (`make attack`, ver el [README](../README.md)).

---

## 1. Costos

Los servicios del ataque usan, en lo posible, los niveles gratuitos de Google Cloud, pero **el proyecto necesita facturación vinculada** (DLP, Vertex AI y Model Armor la exigen). Una corrida del ataque hace pocas llamadas. El costo de estas corridas no se midió, así que revisá la facturación de tu proyecto. Detalle del anexo (Cloud Run, Artifact Registry) en la sección 3.

---

## 2. Servicios reales de GCP para el ataque

Ejecuta el ataque 3.1 en tu máquina contra servicios reales de GCP, **sin desplegar Cloud Run**. Todo se hace con las credenciales de **tu** usuario y en **tu** proyecto.

### 2.1 Qué se levanta

| Servicio | Recurso que se crea | Para qué lo usa el sistema |
| :--- | :--- | :--- |
| APIs | DLP, Vertex AI, Firestore, Model Armor y Cloud Storage habilitadas | Requisito de los servicios siguientes |
| **Cloud DLP** | (solo la API; no hay recurso que crear) | Reemplaza PII por tokens (`[CBU_BANCARIO_1]`) antes de que llegue al LLM |
| **Model Armor** | Plantilla `fin-template` (filtro de prompt injection y jailbreak, confianza `medium-and-above`) | Inspecciona el mensaje del usuario y el contenido de los documentos |
| **Firestore** | Base `(default)` en modo nativo | Checkpointer de LangGraph (`CHECKPOINTER_TYPE=firestore`) |
| **Cloud Storage** | Bucket `<proyecto>-fin-docs` (acceso uniforme, sin acceso público) con `comprobante.txt` | Variante "documento leído desde un bucket" |
| **Vertex AI** | (solo la API) | LLM de todos los agentes (`LLM_PROVIDER=vertexai`) |
| Local | `.env.gcp` (ignorado por git, con un `TOKEN_VAULT_SECRET` propio) | Configuración que carga `make attack-gcp` |

No se despliega Cloud Run, Cloud Build ni Artifact Registry. OPA (el PDP) corre en un contenedor local que levanta el propio `make attack-gcp`.

### 2.2 Qué necesitás (credenciales y permisos)

- **Sin API keys de LLM.** El modelo es de Vertex AI y se autentica con tus credenciales de gcloud; no hace falta Groq, NVIDIA ni OpenRouter.
- **`gcloud` instalado** y credenciales de aplicación: `gcloud auth application-default login` (si faltan, `make build-gcp` abre el login).
- **Un proyecto GCP con facturación vinculada.**
- **Permisos de tu usuario sobre el proyecto:** Owner o Editor o, como mínimo, *Service Usage Admin* (habilitar APIs), *Datastore Owner* (Firestore), *Model Armor Admin* y *Storage Admin*. Los servicios se usan después con esas mismas credenciales: no se crea ninguna Service Account ni se descarga ninguna clave.
- **Docker**, para levantar OPA.

### 2.3 Levantar, probar, atacar y bajar

| Comando | Qué hace |
| :--- | :--- |
| `make build-gcp PROJECT=<id>` | Idempotente. Habilita las APIs, crea Firestore, la plantilla de Model Armor y el bucket con el comprobante, y escribe `.env.gcp`. Termina con los smoke tests. Opciones por `ARGS`: `--dry-run`, `--yes`, `--region`, `--no-gcs`, `--skip-smoke`, `--with-sa` (crea además la Service Account del anexo). |
| `make smoke-gcp` | Prueba DLP, Model Armor, Vertex AI, Firestore y GCS reales antes de gastar una corrida. También elige el modelo de Vertex AI que sostiene tool calling. |
| `make attack-gcp` | Escenario 3.1 con `.env.gcp` cargado. `GCS=false` lee el comprobante de `backend/local_storage/`. Usa `--require-gcp`: se niega a correr (código de salida 3) si algún servicio, **incluido el LLM**, no es GCP real, y termina con 3 si una corrida usó un fallback local (no es una medición 100% GCP). |
| `make attack-gcp-n N=5` | Lo mismo `N` veces, con tasas. |
| `make destroy-gcp` | Baja lo que creó `build-gcp`: bucket, plantilla de Model Armor, checkpoints y documentos de prueba de Firestore y, si existe, la Service Account. Pide confirmación. |

`make destroy-gcp` **no** borra la base Firestore, las APIs habilitadas ni `.env.gcp` (tiene el secreto del token vault), porque no cuestan en reposo y pueden afectar otros usos del proyecto. Si querés limpiarlos:

```bash
gcloud firestore databases delete --database='(default)' --project=<ID>
gcloud services disable dlp.googleapis.com modelarmor.googleapis.com --project=<ID>
```

`.env.gcp` fija `LLM_PROVIDER=vertexai`: todos los agentes usan el mismo modelo (`VERTEX_MODEL_NAME`, `VERTEX_LOCATION`) y no hay proveedores de respaldo. Cada corrida se guarda en `docs/evidence/runs/` con `services.used` y `pure_gcp`. Para probar otro modelo sin tocar `.env.gcp`, copiá el archivo, editá esas dos variables y ejecutá `make attack-gcp-n N=5 GCP_ENV_FILE=<copia>`. Los modelos probados están en [`modelos_probados.md`](modelos_probados.md).

### 2.4 Model Armor real: comportamiento a tener en cuenta

Por defecto el guardrail es `LocalModelArmorGuard` (regex léxicas, `backend/app/core/model_armor.py`). Con `SECURITY_MODE=gcp_live`, `USE_GCP_MODEL_ARMOR=true` y `MODEL_ARMOR_TEMPLATE_ID=fin-template` se usa el servicio real (`make build-gcp` deja todo esto en `.env.gcp`).

- El mismo `ModelArmorGuard` inspecciona el mensaje del usuario y el contenido del documento que lee `read_document_tool`.
- Cuando Model Armor responde (seguro o amenaza), su veredicto es **final**: el guardrail local no se consulta. Solo se usa el local si `USE_GCP_MODEL_ARMOR` está apagado, o si el cliente no se pudo crear o la llamada falla. En ese caso se registra una advertencia y queda anotado en `service_status` (la PoC no falla cerrado ante un error del servicio). Cada corrida guardada indica en `services.used` qué servicios sirvió GCP y cuáles cayeron a local.
- El comprobante está escrito para parecer un documento legítimo, de modo que **se espera que pase** Model Armor: el ataque 3.1 es semántico y no contiene firmas léxicas de jailbreak. Si en tu plantilla lo bloquea, la corrida termina en `BLOCKED` desde el sanitizer y eso es un resultado válido de medición.
- Para leer el comprobante del bucket: `USE_GCS_INGESTION=true` y `GCS_BUCKET_NAME=<bucket>`. Si el objeto no existe se usa `backend/local_storage/comprobante.txt`.

**Equivalente manual de Model Armor** (lo que hace `build-gcp`; los flags pueden variar con la versión de `gcloud`, revisá `gcloud model-armor templates create --help`):

```bash
gcloud services enable modelarmor.googleapis.com
gcloud model-armor templates create fin-template --location=$REGION \
  --pi-and-jailbreak-filter-settings-enforcement=enabled \
  --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above
```

**Estado de la evidencia:** las corridas de `make attack-gcp` guardadas en `docs/evidence/runs/` (campo `pure_gcp: true`) usaron Model Armor, Cloud DLP, Firestore, el bucket y Vertex AI reales, sin fallbacks a local, y el comprobante pasó Model Armor (`document_read: true`). Con los modelos de Vertex AI probados, ninguna de esas corridas terminó en transferencia (ver [`modelos_probados.md`](modelos_probados.md)).

---

## 3. Anexo de aprendizaje: Cloud Run e IAM de mínimo privilegio (no necesario para el ataque)

> [!NOTE]
> Esta parte **no** hace falta para reproducir el ataque 3.1. Es un ejercicio de gobernanza y seguridad (**Principio de Menor Privilegio**) y un despliegue en Cloud Run que no forma parte de la demo. El módulo de IAM queda para completarse en [`deploy/iam-setup.sh`](../deploy/iam-setup.sh). Para las decisiones sobre Firestore, Artifact Registry, la cuenta de CI/CD (`sa-cloud-build`) y los incidentes de Cloud Build, ver [`gcp_build_and_service_accounts_decisions.md`](gcp_build_and_service_accounts_decisions.md).

### 3.1 Estrategia de costos del despliegue

| Servicio GCP | Rol en el proyecto | Estrategia de ahorro / Free Tier |
| :--- | :--- | :--- |
| **Cloud Run** | Ejecución del backend FastAPI | **2 millones de peticiones/mes gratis**. Escalado a cero (`--min-instances=0`), cómputo solo durante peticiones activas. |
| **Cloud Firestore** | Checkpointer y persistencia de LangGraph | **1 GB de almacenamiento gratis y 50.000 lecturas/día**. |
| **Cloud DLP** | De-identificación de datos sensibles (PII) | Primer **1 GB de inspección de texto al mes gratuito**. |
| **Vertex AI** | Razonamiento de agentes | Modelos de bajo costo por token. |
| **Artifact Registry** | Imágenes Docker | **0,5 GB/mes de almacenamiento gratuito**. |

### 3.2 IAM de menor privilegio

#### ¿Por qué NO usar la Service Account por defecto de Compute Engine?
Por defecto, Cloud Run puede ejecutarse con la Service Account `[PROJECT_NUMBER]-compute@developer.gserviceaccount.com`, que tiene el rol **Editor** a nivel de proyecto. Si un atacante comprometiera el contenedor o lograra ejecución remota, tendría control total sobre el proyecto GCP.

Bajo **Defense-in-Depth** se crea una Service Account dedicada, `sa-financial-assistant@[PROJECT_ID].iam.gserviceaccount.com`, con **solo estos roles mínimos**:

1. **`roles/dlp.user`**: invocar Cloud DLP para inspeccionar y de-identificar textos.
   ```bash
   gcloud projects add-iam-policy-binding "$PROJECT_ID" \
     --member="serviceAccount:sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com" \
     --role="roles/dlp.user"
   ```
2. **`roles/aiplatform.user`**: invocar modelos a través de Vertex AI.
   ```bash
   gcloud projects add-iam-policy-binding "$PROJECT_ID" \
     --member="serviceAccount:sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com" \
     --role="roles/aiplatform.user"
   ```
3. **`roles/datastore.user`**: leer y escribir el estado del Checkpointer en Firestore.
   ```bash
   gcloud projects add-iam-policy-binding "$PROJECT_ID" \
     --member="serviceAccount:sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com" \
     --role="roles/datastore.user"
   ```

`deploy/iam-setup.sh` además concede `roles/modelarmor.user` (paso 4b) y `roles/secretmanager.secretAccessor` sobre el secreto del Token Vault (paso 4c). Para leer el comprobante de un bucket, la SA necesita `roles/storage.objectViewer` **sobre ese bucket** (no a nivel de proyecto). `make build-gcp ARGS=--with-sa` ejecuta el script y concede ese último rol.

### 3.3 Pasos de despliegue en Cloud Run

Una vez completado el script de IAM:

**Paso 1: habilitar APIs**
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

**Paso 2: crear Firestore en modo nativo**
```bash
gcloud firestore databases create --location=us-central1 --type=firestore-native
```

**Paso 2.b: crear el secreto del Token Vault.** Con `ENVIRONMENT=production` el backend **no arranca** si `TOKEN_VAULT_SECRET` conserva el valor por defecto.
```bash
openssl rand -base64 32 | gcloud secrets create token-vault-secret --data-file=-
```
`deploy/iam-setup.sh` (paso 4c) concede a la Service Account `roles/secretmanager.secretAccessor` sobre ese secreto, por eso el secreto se crea antes.

**Paso 3: ejecutar tu script de IAM**
```bash
chmod +x deploy/iam-setup.sh
./deploy/iam-setup.sh
```

**Paso 4: construir y subir la imagen**
```bash
gcloud artifacts repositories create fintech-repo \
  --repository-format=docker \
  --location=us-central1 \
  --description="Repositorio Docker para Asistente Financiero"

gcloud builds submit . \
  --config deploy/cloudbuild-backend.yaml \
  --substitutions _IMAGE=us-central1-docker.pkg.dev/$PROJECT_ID/fintech-repo/backend:v1
```

**Paso 5: desplegar con menor privilegio**
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

Puntos del comando (sin ellos el servicio no arranca o no puede ejecutar transferencias):

- **`TOKEN_VAULT_SECRET`** es obligatorio en producción (Paso 2.b).
- **`LLM_PROVIDER=vertexai`**: con el valor por defecto (`litellm`) se intenta primero Groq y NVIDIA, que no tienen credenciales en Cloud Run.
- **`VERTEX_MODEL_NAME`**: elegí un modelo vigente en Vertex AI Model Garden (`export VERTEX_MODEL=...`) y verificá que esté disponible en tu región. El valor por defecto del repo puede estar retirado.
- **`PDP_URL`**: tiene que apuntar a una instancia de OPA con `policies/` cargada (ver la nota al final). Sin PDP, con `PDP_FALLBACK_MODE=deny` toda alta de destinatario y toda transferencia termina `FAILED`.
- **`--max-instances 1`**: la agenda de destinatarios, el Token Vault y el checkpointer viven en memoria del proceso. Con más de una instancia, un `POST /api/approve` o una transferencia posterior pueden caer en otra instancia que no conoce el estado.
- **`--no-allow-unauthenticated`**: la PoC es deliberadamente vulnerable y el CORS del backend es `*`. No la expongas públicamente; invocala con un token de identidad (`gcloud auth print-identity-token`) o por `gcloud run services proxy`.

El manifiesto `deploy/cloudrun-service.yaml` es el equivalente declarativo: reemplazá `${PROJECT_ID}`, `${MODEL_ARMOR_TEMPLATE_ID}` y `${PDP_URL}` (p. ej. con `envsubst`) antes de `gcloud run services replace`. `deploy/dlp-template.json` documenta una plantilla de desidentificación, pero el código de DLP (`core/dlp_sanitizer.py`) hace `inspect_content` en línea y no la usa.

### 3.4 Verificación en Cloud Logging

```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=financial-assistant-backend" \
  --limit 30 \
  --format "table(timestamp, textPayload)"
```

### 3.5 Nota: política OPA (PDP) en el despliegue

El `pep_node` consulta la política OPA en `PDP_URL` (en `docker-compose.yml` apunta al servicio `opa`). `deploy/cloudrun-service.yaml` **no despliega OPA** (solo define `PDP_URL`): sin PDP accesible se aplica `PDP_FALLBACK_MODE` (por defecto `deny`) y cada alta de destinatario y cada transferencia termina `FAILED`. Para un despliegue funcional hay que publicar OPA (p. ej. un segundo servicio de Cloud Run, con una imagen `openpolicyagent/opa` que incluya `policies/` y ejecute `opa run --server --addr=0.0.0.0:$PORT /policies`; ese paso no está automatizado ni probado en este repo) y configurar `PDP_URL=https://<servicio-opa>/v1/data/transaction/decision`. Si el servicio OPA exige autenticación, el backend tendría que enviar un token de identidad, y `pep_node` hoy no lo hace: dejá OPA accesible solo desde el backend por red interna o ingress restringido. Con `PDP_FALLBACK_MODE=hitl` las transferencias pasan a aprobación humana, pero las altas siguen fallando cerradas.

`CHECKPOINTER_TYPE=firestore` persiste los checkpoints en Cloud Firestore; si el servicio no responde al iniciar, el backend cae a `MemorySaver` y lo registra (ver `docs/architecture.md`, sección 4).
