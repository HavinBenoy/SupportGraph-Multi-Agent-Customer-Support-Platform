.PHONY: venv db-up db-down db-psql db-migrate db-seed db-setup ingest faq eval-rag test test-pg demo langfuse-init langfuse-up langfuse-wait langfuse-down langfuse-logs langfuse-reset verify-tracing verify-tracing-faq support eval-routing verify-tracing-support review auth-init demo-accounts api frontend-install frontend-dev frontend-test frontend-build e2e eval judge-calibrate llm-smoke bench-check bench-llm bench-embed bench-stt bench-tts bench-report bench-review bench-quick bench-full bench-experiment test-smoke test-fast test-targeted test-full

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

# Create/upgrade the schema (Alembic), then load the institute sample data. Needs `make db-up` first.
db-migrate:
	cd backend && ../.venv/bin/alembic -c alembic.ini upgrade head
	cd backend && ../.venv/bin/python scripts/setup_checkpointer.py

db-seed:
	cd backend && ../.venv/bin/python scripts/seed_store.py

# One command after `make db-up`: wait for Postgres, migrate, seed.
db-setup:
	@for i in $$(seq 1 30); do docker compose exec -T db pg_isready -U supportgraph >/dev/null 2>&1 && break; sleep 2; done
	$(MAKE) db-migrate
	$(MAKE) db-seed

# Build step 2 (RAG FAQ baseline). Needs `make db-up db-setup` first. Safe to re-run: unchanged docs are skipped.
ingest:
	cd backend && ../.venv/bin/python scripts/ingest_docs.py

# Ask the FAQ bot: make faq Q="What is the fee for the Data Science with Python course?"
faq:
	cd backend && ../.venv/bin/python scripts/ask_faq.py --sources "$(or $(Q),What is the fee for the Data Science with Python course?)"

# Baseline numbers + leak check on the labelled tickets (needs `make ingest`). Fails if anything leaks.
eval-rag:
	cd backend && ../.venv/bin/python scripts/eval_rag.py

# Build step 3 (triage + routing + knowledge/action agents).
# make support Q="What is my attendance in PYF-101?" STUDENT=stu_asha [TICKET=demo1]   (omit STUDENT to ask as an anonymous prospect;
# reuse a TICKET to continue the same conversation)
support:
	cd backend && ../.venv/bin/python scripts/ask_support.py $(if $(STUDENT),--student $(STUDENT)) $(if $(TICKET),--ticket $(TICKET)) "$(or $(Q),What courses are available?)"

llm-smoke:
	cd backend && ../.venv/bin/python scripts/llm_smoke.py

bench-check:
	cd backend && ../.venv/bin/python scripts/bench.py check

# Usage: make bench-llm MODELS="qwen3:4b qwen3:1.7b" [ARGS="--heldout --limit 10"]
bench-llm:
	cd backend && ../.venv/bin/python scripts/bench.py llm $(if $(MODELS),--models $(MODELS),) $(ARGS)

bench-embed:
	cd backend && ../.venv/bin/python scripts/bench.py embed $(if $(MODELS),--models $(MODELS),) $(ARGS)

# Optional (voice milestone): make bench-stt AUDIO=~/wavs MODEL=base.en
bench-stt:
	cd backend && ../.venv/bin/python scripts/bench.py stt --audio $(AUDIO) --model $(or $(MODEL),base.en)

bench-tts:
	cd backend && ../.venv/bin/python scripts/bench.py tts --voice $(VOICE)

# Human-review sheet of FAQ failures the strict scorer cannot judge on meaning (read-only; changes no score). DIR=bench-cmp to pick a folder.
bench-review:
	cd backend && ../.venv/bin/python scripts/bench.py --out-dir ../evals/$(or $(DIR),bench) review

bench-report:
	cd backend && ../.venv/bin/python scripts/bench.py report

# Quick real-model check while developing: few tickets per split, no cold-start timing, own output folder (evals/bench-quick),
# so it can never be mixed with a full run. Defaults to the smaller model; override: make bench-quick MODELS="qwen3:4b" N=6
# NOT a decision benchmark: ~8 tickets per split cannot separate models. Use bench-full for that.
bench-quick:
	cd backend && ../.venv/bin/python scripts/bench.py --out-dir ../evals/bench-quick llm --models $(or $(MODELS),qwen3:1.7b) --limit $(or $(N),8) --no-cold-start $(ARGS)
	cd backend && ../.venv/bin/python scripts/bench.py --out-dir ../evals/bench-quick report

# The full measurement (both splits, all tickets, cold start). Slow on CPU for large models.
bench-full:
	cd backend && ../.venv/bin/python scripts/bench.py llm $(if $(MODELS),--models $(MODELS),) $(ARGS)
	cd backend && ../.venv/bin/python scripts/bench.py report

# One controlled variant of a model, kept apart from the baseline (own folder evals/bench-<TAG>), e.g. the Qwen3 sampling test:
#   make bench-experiment MODELS="qwen3:4b" TAG=sampling SETS="llm_temperature=0.7 llm_top_p=0.8 llm_top_k=20"   (defaults: dev split, FAQ suite only)
bench-experiment:
	@test -n "$(TAG)" -a -n "$(SETS)" || { echo 'usage: make bench-experiment MODELS="qwen3:4b" TAG=sampling SETS="llm_temperature=0.7 llm_top_k=20" [N=20] [SPLITS="dev heldout"] [SUITES="faq triage"]'; exit 1; }
	cd backend && ../.venv/bin/python scripts/bench.py --out-dir ../evals/bench-$(TAG) llm --models $(MODELS) --tag $(TAG) $(foreach s,$(SETS),--set $(s)) --splits $(or $(SPLITS),dev) --suites $(or $(SUITES),faq) $(if $(N),--limit $(N),) --no-cold-start
	cd backend && ../.venv/bin/python scripts/bench.py --out-dir ../evals/bench-$(TAG) report

# ---- test tiers (see docs/TESTING.md) ----
# Each tier is a subset of the next. `make test` and `make test-pg` are unchanged.
test-smoke:  ## ~5-10 s: security isolation, benchmark kit, FAQ validation/truncation, prompts, gateway, router/triage units
	cd backend && ../.venv/bin/python -m pytest -q -x tests/security tests/bench tests/rag/test_faq_graph.py tests/rag/test_faq_truncation.py tests/rag/test_prompts.py tests/unit/test_llm_gateway_hardening.py tests/unit/test_router.py tests/unit/test_triage.py tests/unit/test_escalation_floor.py

test-fast:  ## whole SQLite backend suite, in parallel when pytest-xdist is installed, failed-first, stops at first failure
	cd backend && ../.venv/bin/python -m pytest -q -x --ff $$(../.venv/bin/python -c "import xdist" 2>/dev/null && echo "-n auto")

test-targeted:  ## run only what the changed files need (git status). Preview: make test-targeted DRY=1  or  FILES="backend/app/rag/prompts.py"
	cd backend && ../.venv/bin/python scripts/test_select.py $(if $(FILES),--files $(FILES),) $(if $(DRY),,--run)

test-full:  ## everything except real models and browser e2e: backend (parallel) + Postgres + frontend tests + production build
	$(MAKE) test-fast
	$(MAKE) test-pg
	$(MAKE) frontend-test
	$(MAKE) frontend-build

eval-routing:
	cd backend && ../.venv/bin/python scripts/eval_routing.py

test:
	cd backend && ../.venv/bin/python -m pytest -q

# Also runs the real-Postgres tests (each uses its own throwaway database; your dev data is untouched).
# Needs `make db-up`. The pgvector check needs the pgvector image from docker-compose.yml.
test-pg:
	cd backend && SG_TEST_DATABASE_URL=postgresql+psycopg://supportgraph:supportgraph@localhost:5432/postgres ../.venv/bin/python -m pytest -q -rs

# Needs the database to be up, migrated and seeded: make db-up db-setup
demo:
	cd backend && ../.venv/bin/python scripts/run_agent.py "What is my attendance in PYF-101?"

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

# Same check for the FAQ workflow (spans: retrieve, generate, validate). Needs `make ingest` and a running Langfuse.
verify-tracing-faq:
	cd backend && ../.venv/bin/python scripts/verify_tracing.py --workflow faq

# Runs one prospect question through the support graph and checks Langfuse received the triage/route/knowledge spans.
verify-tracing-support:
	cd backend && ../.venv/bin/python scripts/verify_tracing.py --workflow support

# Build step 4: staff console. make review ARGS="list"   |   make review ARGS="approve TICKET --staff s_1"
review:
	cd backend && ../.venv/bin/python scripts/review_queue.py $(ARGS)

auth-init:  ## write a random SG_AUTH_SECRET into .env if none is set (never prints it)
	@test -f .env || cp .env.example .env
	@if grep -Eq '^SG_AUTH_SECRET=.{32,}' .env; then echo "SG_AUTH_SECRET already set"; else \
	  sed -i '/^SG_AUTH_SECRET=/d' .env && echo "SG_AUTH_SECRET=$$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" >> .env && echo "SG_AUTH_SECRET written to .env"; fi

demo-accounts:  ## demo logins for the SAMPLE tenant (password printed once, or set SG_DEMO_PASSWORD)
	cd backend && ../.venv/bin/python scripts/demo_accounts.py

api:  ## run the HTTP API on :8000
	cd backend && ../.venv/bin/uvicorn app.main:app --port 8000

frontend-install:
	cd frontend && npm install

frontend-dev:  ## web UI on :3000, proxying /api to the API on :8000
	cd frontend && npm run dev

frontend-test:
	cd frontend && npm test

frontend-build:
	cd frontend && npm run build

e2e:  ## browser end-to-end run (needs the API and frontend running, see docs/STEP5.md)
	cd frontend && npm run e2e

# Build step 6: full evaluation (dev + held-out sets, baseline comparison, security suite, judge calibration). make eval ARGS="--reference ..."
eval:
	cd backend && ../.venv/bin/python scripts/run_eval.py $(ARGS)

judge-calibrate:  ## agreement of the judge with human labels; ARGS="--relabel" lets you label the cases yourself
	cd backend && ../.venv/bin/python scripts/judge_calibrate.py $(ARGS)
