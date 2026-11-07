# ==============================================================================
# Makefile - AegisBank AI (Sistema Multi-Agente Bancario Defense-in-Depth)
# ==============================================================================

.DEFAULT_GOAL := help

DOCKER_COMPOSE ?= docker compose

.PHONY: help up-dev up-prod down logs ps test restart-dev restart-prod opa-up attack attack-n

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
	@echo "  make test         Ejecuta los tests automatizados con pytest"
	@echo "  make opa-up       Levanta solo la política OPA (PDP) en localhost:8181"
	@echo "  make attack       Ejecuta y narra el escenario 3.1 (LLM real; levanta OPA)"
	@echo "  make attack-n N=5 Repite el escenario N veces y reporta tasas"
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

test:
	@echo "🧪 Ejecutando tests automatizados..."
	@if [ -f backend/.venv/bin/pytest ]; then \
		backend/.venv/bin/pytest backend/tests -v; \
	else \
		pytest backend/tests -v; \
	fi

opa-up:
	@echo "🛡️  Levantando OPA (PDP) en http://localhost:8181 ..."
	$(DOCKER_COMPOSE) up -d --wait opa

# Ataque 3.1 contra el grafo real: requiere credenciales LLM en .env y OPA arriba.
# Un ataque exitoso NO es un error: el exit code es != 0 solo ante errores del grafo o de configuración.
attack: opa-up
	$(PYTHON) scripts/demo_attack.py $(ARGS)

attack-n: opa-up
	$(PYTHON) scripts/demo_attack.py --runs $(N) $(ARGS)

restart-dev: down up-dev

restart-prod: down up-prod
