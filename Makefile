.PHONY: venv db-up db-down db-psql test demo

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

test:
	cd backend && ../.venv/bin/python -m pytest -q

demo:
	cd backend && ../.venv/bin/python scripts/run_agent.py "Where is my order A1001?"
