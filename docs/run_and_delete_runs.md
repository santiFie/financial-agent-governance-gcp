Aunque Cloud Run tiene escala a cero (``minScale:0``, es decir, $0 si nadie entra), el peligro real con el tráfico de bots o escaneos automáticos de internet en endpoints con --allow-unauthenticated no es solo Cloud Run, sino los servicios downstream que se disparan en cadena:
1. **Vertex AI (Gemini):** Si un bot envía peticiones POST al chat, cada llamada consume tokens de IA.
2. **Cloud DLP:** Escanea texto en cada mensaje.
3. **Firestore:** Escribe y lee checkpoints por cada interacción.

Para prevenir esto, hay 2 opciones:

1. **Quitar el acceso público sin borrar el servicio y acceder de manera local para las pruebas:**
```bash
SERVICE_NAME=financial-assistant-backend
GCP_LOCATION=us-central1


# Eliminar el permiso de "All Users" para evitar acceso no autenticado
gcloud run services remove-iam-policy-binding $SERVICE_NAME \
    --region=$GCP_LOCATION \
    --member="allUsers" \
    --role="roles/run.invoker"

# Opcional: Para volver a habilitarlo después:
# gcloud run services add-iam-policy-binding $SERVICE_NAME \
#     --region=$GCP_LOCATION \
#     --member="allUsers" \
#     --role="roles/run.invoker"
```

```bash
# Acceder via proxy (local)
gcloud run services proxy $SERVICE_NAME --region=$GCP_LOCATION --port=8080

# Luego acceder a la app desde: http://localhost:8080/
```

2. **Eliminar el servicio al terminar de usarlo:**

```bash
# 1. Borrar el servicio de Cloud Run
gcloud run services delete $SERVICE_NAME --region=$GCP_LOCATION --quiet

# 2. Borrar el repositorio de Artifact Registry (opcional)
GCP_LOCATION=us-central1
REPO_NAME=financial-assistant
IMAGE_URI=gcr.io/$PROJECT_ID/$REPO_NAME:latest

# Eliminar imagen
gcloud artifacts images delete $IMAGE_URI --quiet
```

y para volver a levantarlo:
```bash
# 1. Re-deploy con el YAML
gcloud run services replace deploy/cloudrun-service.yaml

# 2. O simplemente volver a ejecutar el deploy inicial si no has cambiado el YAML
gcloud run deploy financial-assistant-backend \
  --source . \
  --region $GCP_LOCATION \
  --project <Project ID> \
  --allow-unauthenticated