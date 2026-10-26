.PHONY: venv db-up db-down db-psql db-migrate db-seed db-setup test test-pg demo langfuse-init langfuse-up langfuse-wait langfuse-down langfuse-logs langfuse-reset verify-tracing

venv:
	python3 -m venv .venv
	.venv/bin/pip install -q -e "backend[dev]"

db-up:
	docker compose up -d db
	docker compose ps

db-down:
	docker compose down

db-psql:
	docker compose exec db psql -U supportgraph -d supportgraph

# Create/upgrade the schema (Alembic), then load the mock store. Needs `make db-up` first.
db-migrate:
	cd backend && ../.venv/bin/alembic -c alembic.ini upgrade head

db-seed:
	cd backend && ../.venv/bin/python scripts/seed_store.py

# One command after `make db-up`: wait for Postgres, migrate, seed.
db-setup:
	@for i in $$(seq 1 30); do docker compose exec -T db pg_isready -U supportgraph >/dev/null 2>&1 && break; sleep 2; done
	$(MAKE) db-migrate
	$(MAKE) db-seed

test:
	cd backend && ../.venv/bin/python -m pytest -q

# Also runs the real-Postgres tests (each uses its own throwaway database; your dev data is untouched).
# Needs `make db-up`. The pgvector check needs the pgvector image from docker-compose.yml.
test-pg:
	cd backend && SG_TEST_DATABASE_URL=postgresql+psycopg://supportgraph:supportgraph@localhost:5432/postgres ../.venv/bin/python -m pytest -q -rs

# Needs the database to be up, migrated and seeded: make db-up db-setup
demo:
	cd backend && ../.venv/bin/python scripts/run_agent.py "Where is my order A1001?"

# ---- Langfuse (self-hosted tracing UI; separate Compose project from the app DB) ----
LF_COMPOSE = docker compose -f infra/langfuse/docker-compose.yml --env-file infra/langfuse/.env
LF_PORT = $(shell grep -s '^LANGFUSE_WEB_PORT=' infra/langfuse/.env | cut -d= -f2 | grep . || echo 3100)

langfuse-init:
	cd backend && ../.venv/bin/python scripts/langfuse_init.py

infra/langfuse/.env:
	@echo "Missing infra/langfuse/.env -- run: make langfuse-init" && exit 1

langfuse-up: infra/langfuse/.env
	$(LF_COMPOSE) up -d
	@echo "Langfuse starting (first boot takes 1-2 min). Run: make langfuse-wait"

langfuse-wait:
	@echo "Waiting for http://localhost:$(LF_PORT)/api/public/health ..."
	@for i in $$(seq 1 60); do curl -fsS http://localhost:$(LF_PORT)/api/public/health >/dev/null 2>&1 && echo "Langfuse is up: http://localhost:$(LF_PORT)" && exit 0; sleep 3; done; echo "Timed out. Try: make langfuse-logs"; exit 1

langfuse-down: infra/langfuse/.env
	$(LF_COMPOSE) down

langfuse-logs: infra/langfuse/.env
	$(LF_COMPOSE) logs -f --tail=100 langfuse-web langfuse-worker

# DESTRUCTIVE: deletes all Langfuse data (traces, users) and the generated secrets.
langfuse-reset: infra/langfuse/.env
	$(LF_COMPOSE) down -v
	rm -f infra/langfuse/.env
	@echo "Langfuse data and secrets removed. Run: make langfuse-init"

verify-tracing:
	cd backend && ../.venv/bin/python scripts/verify_tracing.py
