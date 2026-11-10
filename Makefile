# ==============================================================================
# Makefile - PoC de asistente financiero multi-agente (Defense-in-Depth)
# ==============================================================================

.DEFAULT_GOAL := help

DOCKER_COMPOSE ?= docker compose

.PHONY: help up-dev up-prod down logs ps test test-live restart-dev restart-prod opa-up attack attack-n attack-control server-results \
	build-gcp smoke-gcp attack-gcp attack-gcp-n destroy-gcp require-gcp-env

PYTHON ?= $(shell [ -x backend/.venv/bin/python ] && echo backend/.venv/bin/python || echo python3)
N ?= 5

help:
	@echo ""
	@echo "Comandos disponibles:"
	@echo "  make up-dev       Levanta el entorno local con mocks (.env.development)"
	@echo "  make up-prod      Levanta el entorno en la nube usando GCP (.env.production)"
	@echo "  make down         Baja todos los servicios (sin eliminar volúmenes)"
	@echo "  make logs         Muestra los logs en tiempo real de los contenedores"
	@echo "  make ps           Muestra el estado de los contenedores"
	@echo "  make test         Ejecuta los tests offline (sin red ni credenciales)"
	@echo "  make test-live    Ejecuta los tests y evals con LLM real (consume tokens)"
	@echo "  make opa-up       Levanta solo la política OPA (PDP) en localhost:8181"
	@echo "  make attack       Ejecuta y narra el escenario 3.1 (LLM real; levanta OPA)"
	@echo "  make attack-n N=5 Repite el escenario N veces y reporta tasas"
	@echo "  make attack-control  Caso de contraste: transferencia a destinatario no confiable (debe ir a HITL)"
	@echo "  make server-results  Abre el visor HTML de corridas guardadas (sin LLM ni Docker)"
	@echo "  make build-gcp    Crea y configura en TU proyecto GCP todo lo necesario (PROJECT=<id>; ver docs/gcp_deployment_and_iam.md, sección 2)"
	@echo "  make smoke-gcp    Smoke tests de DLP, Model Armor, Vertex AI, Firestore y GCS (servicios reales)"
	@echo "  make attack-gcp   Escenario 3.1 contra los servicios reales de GCP (GCS=false para el documento local)"
	@echo "  make attack-gcp-n N=5  Repite el escenario N veces contra GCP y reporta tasas"
	@echo "  make destroy-gcp  Borra lo creado por build-gcp (bucket, plantilla, checkpoints; pide confirmación)"
	@echo "  make restart-dev  Reinicia el entorno local de desarrollo"
	@echo "  make restart-prod Reinicia el entorno de producción"
	@echo ""

up-dev:
	@[ -f .env.development ] || { cp .env.development.example .env.development && echo "📝 Creado .env.development desde el ejemplo: completá al menos una API key de LLM."; }
	@echo "🚀 Levantando entorno local de desarrollo (con mocks y .env.development)..."
	$(DOCKER_COMPOSE) --env-file .env.development up --build
	@echo ""
	@echo "=========================================================="
	@echo "✅ Entorno DEV iniciado con éxito:"
	@echo "   - Frontend:  http://localhost:3000"
	@echo "   - Backend:   http://localhost:8000"
	@echo "   - API Docs:  http://localhost:8000/docs"
	@echo "   - Seguridad: local_mock (DLP regex + Model Armor local)"
	@echo "=========================================================="
	@echo "Tip: Usa 'make logs' para ver los logs o 'make down' para detenerlos."

up-prod:
	@[ -f .env.production ] || { cp .env.production.example .env.production && echo "📝 Creado .env.production desde el ejemplo: completalo antes de continuar." && exit 1; }
	@echo "☁️  Levantando entorno de producción en la nube GCP (.env.production)..."
	$(DOCKER_COMPOSE) --env-file .env.production up --build
	@echo ""
	@echo "=========================================================="
	@echo "✅ Entorno PROD iniciado con éxito:"
	@echo "   - Frontend:  http://localhost:3000"
	@echo "   - Backend:   http://localhost:8000"
	@echo "   - API Docs:  http://localhost:8000/docs"
	@echo "   - Seguridad: gcp_live (Cloud DLP + Vertex AI + Firestore)"
	@echo "=========================================================="
	@echo "Tip: Usa 'make logs' para ver los logs o 'make down' para detenerlos."

down:
	@echo "🛑 Bajando todos los servicios (preservando volúmenes)..."
	$(DOCKER_COMPOSE) down
	@echo "✅ Servicios detenidos correctamente. Los volúmenes fueron preservados."

logs:
	$(DOCKER_COMPOSE) logs -f

ps:
	$(DOCKER_COMPOSE) ps

# Suite offline: sin red ni credenciales. Los tests que llaman a un LLM real llevan el marker requires_live_llm.
test:
	@echo "🧪 Ejecutando tests automatizados (offline)..."
	$(PYTHON) -m pytest backend/tests -m "not requires_live_llm" -v

# Evals y tests con LLM real (consumen tokens): requieren credenciales; sin ellas se omiten.
test-live:
	@echo "🧪 Ejecutando tests con LLM real (consumen tokens)..."
	$(PYTHON) -m pytest backend/tests -m requires_live_llm -v

opa-up:
	@echo "🛡️  Levantando OPA (PDP) en http://localhost:8181 ..."
	$(DOCKER_COMPOSE) up -d --wait opa

# Ataque 3.1 contra el grafo real: requiere credenciales LLM en .env y OPA arriba.
# Un ataque exitoso NO es un error: el exit code es != 0 solo ante errores del grafo o de configuración.
attack: opa-up
	$(PYTHON) scripts/demo_attack.py $(ARGS)

attack-n: opa-up
	$(PYTHON) scripts/demo_attack.py --runs $(N) $(ARGS)

# Caso de contraste (no es un ataque): transferencia directa a un destinatario no confiable. Debe terminar en HITL.
attack-control: opa-up
	$(PYTHON) scripts/demo_attack.py --scenario control_hitl_untrusted_recipient_over_threshold $(ARGS)

# Visor offline de las corridas guardadas en docs/evidence (no requiere LLM). RESULTS_PORT=8765 por defecto.
RESULTS_PORT ?= 8765
server-results:
	python3 scripts/serve_results.py --port $(RESULTS_PORT)

restart-dev: down up-dev

restart-prod: down up-prod

# ------------------------------------------------------------------------------
# GCP real (Model Armor, Cloud DLP, Vertex AI, Firestore, GCS) con las credenciales de TU usuario.
#   make build-gcp PROJECT=mi-proyecto   crea/configura todo (idempotente) y escribe .env.gcp
#   make attack-gcp                      ejecuta el ataque contra esos servicios
#   make destroy-gcp                     borra lo creado
# ------------------------------------------------------------------------------
GCP_ENV_FILE ?= .env.gcp
PROJECT ?=
GCS ?=

# .env.gcp se carga en el entorno del proceso: tiene prioridad sobre el .env de desarrollo. GCS=true|false lo pisa.
GCP_ENV = set -a; . "$(abspath $(GCP_ENV_FILE))"; set +a; $(if $(GCS),USE_GCS_INGESTION=$(GCS))

require-gcp-env:
	@[ -f "$(GCP_ENV_FILE)" ] || { echo "❌ No existe $(GCP_ENV_FILE). Ejecutá primero: make build-gcp PROJECT=<id-del-proyecto>"; exit 1; }

build-gcp:
	deploy/gcp-bootstrap.sh $(if $(PROJECT),--project $(PROJECT)) --env-file $(GCP_ENV_FILE) $(ARGS)

smoke-gcp: require-gcp-env
	$(PYTHON) scripts/gcp_smoke_test.py --env-file $(GCP_ENV_FILE) $(if $(PROJECT),--project $(PROJECT)) $(ARGS)

attack-gcp: require-gcp-env opa-up
	@$(GCP_ENV) exec $(PYTHON) scripts/demo_attack.py --require-gcp $(ARGS)

attack-gcp-n: require-gcp-env opa-up
	@$(GCP_ENV) exec $(PYTHON) scripts/demo_attack.py --require-gcp --runs $(N) $(ARGS)

destroy-gcp:
	deploy/gcp-destroy.sh $(if $(PROJECT),--project $(PROJECT)) --env-file $(GCP_ENV_FILE) $(ARGS)
