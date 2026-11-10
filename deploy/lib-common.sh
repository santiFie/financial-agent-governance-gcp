#!/usr/bin/env bash
# Utilidades compartidas por deploy/gcp-bootstrap.sh y deploy/gcp-destroy.sh (se usan con `source`).
# Esperan que el script que las carga defina DRY_RUN, ASSUME_YES y ENV_FILE.

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
