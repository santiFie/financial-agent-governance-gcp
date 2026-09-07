# Despliegue en Google Cloud Platform (GCP) y Guía de Aprendizaje IAM

Esta guía explica cómo desplegar **AegisBank AI** en GCP maximizando la preservación del crédito inicial de $300 USD y detalla el ejercicio práctico de configuración de **IAM bajo el Principio de Menor Privilegio (Least Privilege)**.

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
> Este módulo está reservado para ser completado por ti en el archivo [`deploy/iam-setup.sh`](file:///home/santi/Documentos/Curso%20Ciberseguridad/gcp-projects/financial_asistent/deploy/iam-setup.sh), permitiéndote afianzar las competencias de gobernanza y seguridad en agentes de IA.

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
  firestore.googleapis.com
```

### Paso 2: Crear base de datos Firestore en Modo Nativo
```bash
gcloud firestore databases create --location=us-central1 --type=firestore-native
```

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
gcloud builds submit backend \
  --tag us-central1-docker.pkg.dev/$PROJECT_ID/fintech-repo/backend:v1
```

### Paso 5: Desplegar en Cloud Run con Menor Privilegio
```bash
gcloud run deploy financial-assistant-backend \
  --image us-central1-docker.pkg.dev/$PROJECT_ID/fintech-repo/backend:v1 \
  --platform managed \
  --region us-central1 \
  --service-account sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com \
  --min-instances 0 \
  --max-instances 2 \
  --memory 512Mi \
  --cpu 1 \
  --allow-unauthenticated \
  --set-env-vars ENVIRONMENT=production,SECURITY_MODE=gcp_live,GCP_PROJECT_ID=$PROJECT_ID,CHECKPOINTER_TYPE=firestore
```

---

## 4. Verificación y Auditoría en Cloud Logging

Para comprobar que el contenedor está ejecutando las llamadas de sanitización con la Service Account correcta:
```bash
# Ver logs de auditoría en tiempo real
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=financial-assistant-backend" \
  --limit 30 \
  --format "table(timestamp, textPayload)"
```
