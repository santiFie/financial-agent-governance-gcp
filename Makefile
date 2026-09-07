# ==============================================================================
# Makefile - AegisBank AI (Sistema Multi-Agente Bancario Defense-in-Depth)
# ==============================================================================

.DEFAULT_GOAL := help

DOCKER_COMPOSE ?= docker compose

.PHONY: help up-dev up-prod down logs ps test restart-dev restart-prod

help:
	@echo ""
	@echo "Comandos disponibles:"
	@echo "  make up-dev       Levanta el entorno local con mocks (.env.development)"
	@echo "  make up-prod      Levanta el entorno en la nube usando GCP (.env.production)"
	@echo "  make down         Baja todos los servicios (sin eliminar volúmenes)"
	@echo "  make logs         Muestra los logs en tiempo real de los contenedores"
	@echo "  make ps           Muestra el estado de los contenedores"
	@echo "  make test         Ejecuta los tests automatizados con pytest"
	@echo "  make restart-dev  Reinicia el entorno local de desarrollo"
	@echo "  make restart-prod Reinicia el entorno de producción"
	@echo ""

up-dev:
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

restart-dev: down up-dev

restart-prod: down up-prod
