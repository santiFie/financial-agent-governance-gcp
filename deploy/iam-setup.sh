#!/usr/bin/env bash
# ==============================================================================
# GUÍA Y SCRIPT DE CONFIGURACIÓN IAM (LEAST PRIVILEGE) PARA CLOUD RUN
# ==============================================================================
#
# OBJETIVO DE APRENDIZAJE:
# Configurar una Service Account (SA) dedicada bajo el Principio de Menor Privilegio
# (Least Privilege) para el contenedor de Cloud Run que ejecuta el sistema multi-agente.
#
# REGLA DE ORO DE GOBERNANZA EN AGENTES:
# El agente NUNCA debe usar la Service Account por defecto de Compute Engine
# (que suele tener rol 'Editor'). Solo debe tener permisos estrictos para:
#  1. Invocación de Cloud DLP (de-identificación de PII).
#  2. Invocación de Vertex AI (Gemini Flash).
#  3. Lectura sobre Firestore (si se usa para checkpointer o base de datos de lectura).
#
# ==============================================================================

set -euo pipefail

# 1. Configura el ID de tu proyecto en GCP
PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"

if [ -z "$PROJECT_ID" ]; then
  echo "❌ Error: Variable GCP_PROJECT_ID no configurada o gcloud no autenticado."
  echo "Ejecuta: export GCP_PROJECT_ID='tu-id-de-proyecto'"
  exit 1
fi

echo "========================================================"
echo "🏦 Configurando IAM para Proyecto: $PROJECT_ID"
echo "========================================================"

# Nombre de la Service Account dedicada
SA_NAME="sa-financial-assistant"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# ------------------------------------------------------------------------------
# PASO 1: CREAR LA SERVICE ACCOUNT DEDICADA
# ------------------------------------------------------------------------------
gcloud iam service-accounts create $SA_NAME \
  --description="Service Account para el asistente financiero multi-agente con permisos mínimos" \
  --display-name="SA Financial Assistant" \
  --project="$PROJECT_ID"

echo "[PASO 1] Service Account ya creada..."


# ------------------------------------------------------------------------------
# PASO 2: ASIGNAR ROL PARA CLOUD DLP (SENSITIVE DATA PROTECTION)
# ------------------------------------------------------------------------------
# El Sanitizer Agent necesita desidentificar datos mediante Cloud DLP.
echo "[PASO 2] Asignando rol roles/dlp.user..."

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/dlp.user"

# ------------------------------------------------------------------------------
# PASO 3: ASIGNAR ROL PARA VERTEX AI (INVOCACIÓN DE MODELOS GEMINI)
# ------------------------------------------------------------------------------
# Los agentes que requieran razonamiento con LLM deben poder invocar endpoints de Vertex AI.
echo "[PASO 3] Asignando rol roles/aiplatform.user..."

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/aiplatform.user"

# ------------------------------------------------------------------------------
# PASO 4: ASIGNAR ROL PARA FIRESTORE (CHECKPOINTER / LECTURA SEGURA)
# ------------------------------------------------------------------------------
# Para persistir checkpoints de LangGraph en Firestore.

echo "[PASO 4] Asignando rol roles/datastore.user..."

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/datastore.user"

# ------------------------------------------------------------------------------
# PASO 5: VERIFICACIÓN FINAL
# ------------------------------------------------------------------------------
echo "========================================================"
echo "✅ Para verificar las políticas asignadas a tu Service Account, ejecutar en la terminal:"
echo "   gcloud projects get-iam-policy $PROJECT_ID --flatten=\"bindings[].members\" --filter=\"bindings.members:$SA_EMAIL\" --format=\"table(bindings.role)\""
echo "========================================================"
