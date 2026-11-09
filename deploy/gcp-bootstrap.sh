#!/usr/bin/env bash
# ==============================================================================
# BOOTSTRAP IDEMPOTENTE DE GCP PARA EL ATAQUE 3.1 (make build-gcp)
# ==============================================================================
#
# Crea e inicializa en el proyecto GCP de QUIEN LO EJECUTA (con su usuario, vía Application Default
# Credentials) todo lo que necesita `make attack-gcp`:
#
#   1. APIs: Cloud DLP, Vertex AI, Firestore, Model Armor, Cloud Storage
#   2. Base Firestore (default) en modo nativo           -> checkpointer de LangGraph
#   3. Plantilla Model Armor "aegisbank-template"        -> filtro de prompt injection / jailbreak
#   4. Bucket GCS con el comprobante del ataque          -> variante "documento desde un bucket"
#   5. (opcional, --with-sa) Service Account de mínimo privilegio para un futuro Cloud Run
#   6. .env.gcp (gitignored) con toda la configuración y un TOKEN_VAULT_SECRET propio
#   7. Smoke tests de los servicios reales (scripts/gcp_smoke_test.py)
#
# Es idempotente: cada recurso se comprueba antes de crearlo y se puede ejecutar las veces que haga falta.
# NO despliega Cloud Run, Cloud Build ni Artifact Registry: el ataque corre en local contra estos servicios.
#
# Permisos necesarios: Owner/Editor del proyecto (o, como mínimo, Service Usage Admin, Datastore Owner,
# Model Armor Admin y Storage Admin) y facturación vinculada al proyecto.
#
# Uso:
#   deploy/gcp-bootstrap.sh [--project ID] [--region us-central1] [--yes] [--dry-run]
#                           [--with-sa] [--no-gcs] [--skip-smoke] [--env-file .env.gcp]
# ==============================================================================

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROJECT_ID=""
REGION="us-central1"
TEMPLATE_ID="aegisbank-template"
ENV_FILE=".env.gcp"
ASSUME_YES=0
DRY_RUN=0
WITH_SA=0
WITH_GCS=1
SKIP_SMOKE=0
DOC_FILE="backend/local_storage/comprobante.txt"
DOC_OBJECT="comprobante.txt"

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT_ID="${2:?--project necesita un valor}"; shift 2 ;;
    --region) REGION="${2:?--region necesita un valor}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?--env-file necesita un valor}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --with-sa) WITH_SA=1; shift ;;
    --no-gcs) WITH_GCS=0; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "❌ Opción desconocida: $1 (ver --help)" >&2; exit 2 ;;
  esac
done

# --- utilidades ---------------------------------------------------------------------------------------------------

step() { printf '\n\033[1m[%s]\033[0m %s\n' "$1" "$2"; }
info() { printf '   %s\n' "$*"; }
ok()   { if [ "$DRY_RUN" -eq 1 ]; then printf '   🔸 (simulado) %s\n' "$*"; else printf '   ✅ %s\n' "$*"; fi; }
warn() { printf '   ⚠️  %s\n' "$*"; }
die()  { printf '❌ %s\n' "$*" >&2; exit 1; }

# Ejecuta un comando que MODIFICA recursos (en --dry-run solo lo muestra).
run() {
  if [ "$DRY_RUN" -eq 1 ]; then printf '   [dry-run] %s\n' "$*"; return 0; fi
  "$@"
}

# Como run, pero reintenta: tras habilitar una API Google tarda unos segundos en propagarla.
run_retry() {
  if [ "$DRY_RUN" -eq 1 ]; then printf '   [dry-run] %s\n' "$*"; return 0; fi
  local attempt
  for attempt in 1 2 3 4; do
    if "$@"; then return 0; fi
    [ "$attempt" -lt 4 ] && { warn "Falló (intento $attempt/4). Reintento en 20 s por si la API recién se habilitó..."; sleep 20; }
  done
  return 1
}

# Comprueba si un recurso existe (en --dry-run siempre responde "no existe" para mostrar qué crearía).
exists() {
  [ "$DRY_RUN" -eq 1 ] && return 1
  "$@" >/dev/null 2>&1
}

confirm() {
  [ "$ASSUME_YES" -eq 1 ] && return 0
  [ -t 0 ] || die "Sin terminal interactiva: usá --yes para confirmar."
  read -r -p "$1 [s/N] " answer
  case "$answer" in s|S|y|Y|si|SI|Si) return 0 ;; *) return 1 ;; esac
}

hint_permissions() {
  warn "Si el error fue de permisos (403), tu usuario necesita ser Owner/Editor del proyecto $PROJECT_ID."
}
trap 'hint_permissions' ERR

# Archivo de entorno: set_env_var sobrescribe la clave, ensure_env_var solo la crea si falta.
get_env_var() { [ -f "$ENV_FILE" ] && grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; }
set_env_var() {
  local key="$1" value="$2" tmp
  [ "$DRY_RUN" -eq 1 ] && { info "[dry-run] $ENV_FILE: $key=$value"; return 0; }
  tmp="$(mktemp)"
  if grep -q "^${key}=" "$ENV_FILE"; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in "${key}="*) printf '%s=%s\n' "$key" "$value" ;; *) printf '%s\n' "$line" ;; esac
    done < "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}
ensure_env_var() { [ -n "$(get_env_var "$1")" ] || set_env_var "$1" "$2"; }

# --- 0. prerrequisitos y proyecto ---------------------------------------------------------------------------------

step 0 "Prerrequisitos y proyecto"
if [ "$DRY_RUN" -eq 0 ]; then
  command -v gcloud >/dev/null 2>&1 || die "gcloud no está instalado (https://cloud.google.com/sdk/docs/install)."
  ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1)"
  if [ -z "$ACCOUNT" ]; then
    info "No hay una cuenta de gcloud activa: se abre el login en el navegador."
    gcloud auth login
    ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n1)"
  fi
else
  ACCOUNT="(dry-run)"
fi

if [ -z "$PROJECT_ID" ]; then
  PROJECT_ID="${GCP_PROJECT_ID:-}"
  case "$PROJECT_ID" in your-gcp-project-id|demo-fintech-project) PROJECT_ID="" ;; esac
fi
if [ -z "$PROJECT_ID" ] && [ "$DRY_RUN" -eq 0 ]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
[ -n "$PROJECT_ID" ] && [ "$PROJECT_ID" != "(unset)" ] || die "Indicá el proyecto: --project <ID> (o gcloud config set project <ID>)."

BUCKET="${PROJECT_ID}-aegisbank-docs"
info "Cuenta : $ACCOUNT"
info "Proyecto: $PROJECT_ID   Región: $REGION"
info "Crea (si no existen): APIs, Firestore (default) en $REGION [ubicación IRREVERSIBLE], plantilla Model Armor"
info "                      '$TEMPLATE_ID'$( [ "$WITH_GCS" -eq 1 ] && echo ", bucket gs://$BUCKET" )$( [ "$WITH_SA" -eq 1 ] && echo ", Service Account sa-financial-assistant" ) y $ENV_FILE"
confirm "¿Continuar en el proyecto $PROJECT_ID?" || die "Cancelado."

if [ "$DRY_RUN" -eq 0 ]; then
  BILLING="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null || true)"
  case "$BILLING" in
    True|true) ok "Facturación activa" ;;
    "") warn "No pude verificar la facturación (¿falta permiso?). Si falla algún paso, revisá que el proyecto tenga billing." ;;
    *) die "El proyecto $PROJECT_ID no tiene facturación vinculada. DLP, Vertex AI y Model Armor la requieren." ;;
  esac

  if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    info "Faltan las Application Default Credentials: se abre el login en el navegador."
    gcloud auth application-default login
  fi
  gcloud auth application-default set-quota-project "$PROJECT_ID" >/dev/null 2>&1 \
    && ok "Quota project de ADC = $PROJECT_ID" \
    || warn "No pude fijar el quota project: gcloud auth application-default set-quota-project $PROJECT_ID"
fi

# --- 1. APIs ----------------------------------------------------------------------------------------------------

step 1 "Habilitando APIs (idempotente)"
run gcloud services enable \
  dlp.googleapis.com aiplatform.googleapis.com firestore.googleapis.com \
  modelarmor.googleapis.com storage.googleapis.com \
  --project="$PROJECT_ID"
ok "APIs habilitadas"

# --- 2. Firestore -----------------------------------------------------------------------------------------------

step 2 "Firestore (default), modo nativo"
if exists gcloud firestore databases describe --database='(default)' --project="$PROJECT_ID"; then
  ok "Ya existe: $(gcloud firestore databases describe --database='(default)' --project="$PROJECT_ID" --format='value(locationId,type)' | tr '\t' ' ')"
else
  run_retry gcloud firestore databases create --database='(default)' --location="$REGION" --type=firestore-native \
    --project="$PROJECT_ID"
  ok "Creada en $REGION"
fi

# --- 3. Model Armor ---------------------------------------------------------------------------------------------

step 3 "Plantilla Model Armor '$TEMPLATE_ID'"
if exists gcloud model-armor templates describe "$TEMPLATE_ID" --location="$REGION" --project="$PROJECT_ID"; then
  ok "Ya existe (no se modifica)"
else
  run_retry gcloud model-armor templates create "$TEMPLATE_ID" --location="$REGION" --project="$PROJECT_ID" \
    --pi-and-jailbreak-filter-settings-enforcement=enabled \
    --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above
  ok "Creada (prompt injection y jailbreak, confianza medium-and-above)"
fi

# --- 4. GCS -----------------------------------------------------------------------------------------------------

step 4 "Bucket GCS con el comprobante"
if [ "$WITH_GCS" -eq 1 ]; then
  [ -f "$DOC_FILE" ] || die "No existe $DOC_FILE"
  if exists gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT_ID"; then
    ok "Ya existe gs://$BUCKET"
  else
    run gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT_ID" --location="$REGION" \
      --uniform-bucket-level-access --public-access-prevention
    ok "Creado gs://$BUCKET (acceso uniforme, sin acceso público)"
  fi
  run gcloud storage cp "$DOC_FILE" "gs://$BUCKET/$DOC_OBJECT" --project="$PROJECT_ID"
  ok "Comprobante subido a gs://$BUCKET/$DOC_OBJECT"
else
  info "Omitido (--no-gcs): el documento se lee de backend/local_storage."
fi

# --- 5. Service Account opcional --------------------------------------------------------------------------------

step 5 "Service Account de mínimo privilegio (opcional)"
if [ "$WITH_SA" -eq 1 ]; then
  run env GCP_PROJECT_ID="$PROJECT_ID" bash deploy/iam-setup.sh
  if [ "$WITH_GCS" -eq 1 ]; then
    run gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --project="$PROJECT_ID" \
      --member="serviceAccount:sa-financial-assistant@${PROJECT_ID}.iam.gserviceaccount.com" \
      --role="roles/storage.objectViewer"
  fi
else
  info "Omitido. La demo local corre con tu usuario; la SA solo hace falta para un despliegue en Cloud Run (--with-sa)."
fi

# --- 6. .env.gcp ------------------------------------------------------------------------------------------------

step 6 "Archivo $ENV_FILE"
if [ "$DRY_RUN" -eq 0 ] && [ ! -f "$ENV_FILE" ]; then
  (umask 077; cat > "$ENV_FILE" <<'EOF'
# Generado por deploy/gcp-bootstrap.sh. NO se commitea (contiene el secreto del token vault).
# Lo carga `make attack-gcp`. Se puede volver a ejecutar el bootstrap: actualiza solo las claves de infraestructura.
EOF
  )
  ok "Creado $ENV_FILE"
fi
set_env_var GCP_PROJECT_ID "$PROJECT_ID"
set_env_var GCP_LOCATION "$REGION"
set_env_var MODEL_ARMOR_TEMPLATE_ID "$TEMPLATE_ID"
set_env_var USE_GCS_INGESTION "$([ "$WITH_GCS" -eq 1 ] && echo true || echo false)"
set_env_var GCS_BUCKET_NAME "$([ "$WITH_GCS" -eq 1 ] && echo "$BUCKET" || echo "")"
ensure_env_var ENVIRONMENT production
ensure_env_var SECURITY_MODE gcp_live
ensure_env_var LLM_PROVIDER vertexai
ensure_env_var LLM_MODEL ""
ensure_env_var VERTEX_MODEL_NAME gemini-3.1-flash-lite
ensure_env_var VERTEX_LOCATION global
ensure_env_var CHECKPOINTER_TYPE firestore
ensure_env_var FIRESTORE_COLLECTION langgraph_checkpoints
ensure_env_var USE_GCP_MODEL_ARMOR true
ensure_env_var PDP_FALLBACK_MODE deny
if [ -z "$(get_env_var TOKEN_VAULT_SECRET)" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then info "[dry-run] $ENV_FILE: TOKEN_VAULT_SECRET=<aleatorio>"; else
    command -v openssl >/dev/null 2>&1 || die "Hace falta openssl para generar TOKEN_VAULT_SECRET."
    set_env_var TOKEN_VAULT_SECRET "$(openssl rand -hex 32)"
  fi
fi
ok "Configuración escrita en $ENV_FILE (modelo y ubicación de Vertex se confirman con los smoke tests)"

# --- 7. Smoke tests ---------------------------------------------------------------------------------------------

step 7 "Smoke tests de los servicios reales"
if [ "$SKIP_SMOKE" -eq 1 ]; then
  info "Omitido (--skip-smoke). Se puede ejecutar despues con: make smoke-gcp"
elif [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] python scripts/gcp_smoke_test.py --project $PROJECT_ID --env-file $ENV_FILE"
else
  PY="backend/.venv/bin/python"
  [ -x "$PY" ] || { warn "No existe backend/.venv: se usa python3 (necesita las dependencias de backend/requirements.txt)."; PY="python3"; }
  RESULT_JSON="$(mktemp)"
  SMOKE_STATUS=0
  "$PY" scripts/gcp_smoke_test.py --project "$PROJECT_ID" --env-file "$ENV_FILE" --json "$RESULT_JSON" || SMOKE_STATUS=$?
  # Si la sonda eligió modelo y ubicación, se guardan en el archivo de entorno.
  CHOSEN="$("$PY" -c '
import json, sys
for r in json.load(open(sys.argv[1])):
    if r["name"] == "llm" and r["status"] == "ok":
        print(r["data"]["model"], r["data"]["location"])
' "$RESULT_JSON" 2>/dev/null || true)"
  rm -f "$RESULT_JSON"
  if [ -n "$CHOSEN" ]; then
    set_env_var VERTEX_MODEL_NAME "${CHOSEN% *}"
    set_env_var VERTEX_LOCATION "${CHOSEN#* }"
    ok "Vertex AI: modelo ${CHOSEN% *} en ${CHOSEN#* } (guardado en $ENV_FILE)"
  fi
  [ "$SMOKE_STATUS" -eq 0 ] || die "Algún smoke test falló. Corregí lo indicado y volvé a ejecutar (es idempotente)."
fi

printf '\n\033[1m✅ Listo.\033[0m  Siguiente paso:  make attack-gcp\n'
