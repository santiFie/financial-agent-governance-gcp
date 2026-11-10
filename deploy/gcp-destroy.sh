#!/usr/bin/env bash
# ==============================================================================
# LIMPIEZA DE LO QUE CREÓ deploy/gcp-bootstrap.sh (make destroy-gcp)
# ==============================================================================
#
# Borra (solo si existen): el bucket con el comprobante, la plantilla de Model Armor, los checkpoints y documentos
# de prueba de Firestore y, si existe, la Service Account sa-financial-assistant.
#
# NO borra: la base Firestore (default), las APIs habilitadas ni el archivo .env.gcp (contiene el secreto del
# token vault). Esos pasos son manuales a propósito porque no cuestan dinero en reposo y pueden afectar a otros usos
# del proyecto:
#   gcloud firestore databases delete --database='(default)' --project=<ID>
#   gcloud services disable dlp.googleapis.com modelarmor.googleapis.com --project=<ID>
#
# Uso:
#   deploy/gcp-destroy.sh [--project ID] [--env-file .env.gcp] [--yes] [--dry-run]
# ==============================================================================

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROJECT_ID=""
ENV_FILE=".env.gcp"
ASSUME_YES=0
DRY_RUN=0
SA_NAME="sa-financial-assistant"

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT_ID="${2:?--project necesita un valor}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?--env-file necesita un valor}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "❌ Opción desconocida: $1 (ver --help)" >&2; exit 2 ;;
  esac
done

# shellcheck source=deploy/lib-common.sh
source "$ROOT_DIR/deploy/lib-common.sh"

# Como `exists`, pero en --dry-run responde "existe" para mostrar todo lo que se borraría.
present() {
  [ "$DRY_RUN" -eq 1 ] && return 0
  "$@" >/dev/null 2>&1
}

# Valores por defecto tomados de lo que escribió el bootstrap.
[ -n "$PROJECT_ID" ] || PROJECT_ID="$(get_env_var GCP_PROJECT_ID)"
TEMPLATE_ID="$(get_env_var MODEL_ARMOR_TEMPLATE_ID)"; TEMPLATE_ID="${TEMPLATE_ID:-fin-template}"
REGION="$(get_env_var GCP_LOCATION)"; REGION="${REGION:-us-central1}"
COLLECTION="$(get_env_var FIRESTORE_COLLECTION)"; COLLECTION="${COLLECTION:-langgraph_checkpoints}"
BUCKET="$(get_env_var GCS_BUCKET_NAME)"
[ -n "$PROJECT_ID" ] || die "Indicá el proyecto: --project <ID> (o ejecutá antes make build-gcp para generar $ENV_FILE)."
BUCKET="${BUCKET:-${PROJECT_ID}-fin-docs}"

PY="${PYTHON_BIN:-backend/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

step 0 "Recursos a borrar en el proyecto $PROJECT_ID"
info "Bucket            : gs://$BUCKET (y su contenido)"
info "Plantilla Armor   : $TEMPLATE_ID ($REGION)"
info "Firestore         : colecciones $COLLECTION y ${COLLECTION}_smoke (la base (default) NO se borra)"
info "Service Account   : $SA_NAME (si existe)"
confirm "¿Borrar estos recursos de $PROJECT_ID? Esta acción no se puede deshacer." || die "Cancelado."

step 1 "Bucket GCS"
if present gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT_ID"; then
  run gcloud storage rm --recursive "gs://$BUCKET" --project="$PROJECT_ID"
  ok "Bucket gs://$BUCKET borrado"
else
  info "No existe gs://$BUCKET"
fi

step 2 "Plantilla Model Armor"
if present gcloud model-armor templates describe "$TEMPLATE_ID" --location="$REGION" --project="$PROJECT_ID"; then
  run gcloud model-armor templates delete "$TEMPLATE_ID" --location="$REGION" --project="$PROJECT_ID" --quiet
  ok "Plantilla $TEMPLATE_ID borrada"
else
  info "No existe la plantilla $TEMPLATE_ID"
fi

step 3 "Firestore: checkpoints y documentos de prueba"
if [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] recursive_delete de $COLLECTION y ${COLLECTION}_smoke"
else
  "$PY" - "$PROJECT_ID" "$COLLECTION" <<'PYEOF'
import sys

from google.cloud import firestore

project, collection = sys.argv[1], sys.argv[2]
client = firestore.Client(project=project)
for name in (collection, f"{collection}_smoke"):
    deleted = client.recursive_delete(client.collection(name))
    print(f"   {name}: {deleted} documentos borrados")
PYEOF
  ok "Colecciones de Firestore vaciadas"
fi

step 4 "Service Account"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
if present gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID"; then
  run gcloud iam service-accounts delete "$SA_EMAIL" --project="$PROJECT_ID" --quiet
  ok "Service Account $SA_EMAIL borrada (sus bindings de IAM del proyecto quedan huérfanos y se limpian solos)"
else
  info "No existe $SA_EMAIL"
fi

printf '\n\033[1m✅ Limpieza terminada.\033[0m  Para volver a crear todo:  make build-gcp\n'
