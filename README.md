# SupportGraph

Multi-tenant customer support system run by LangGraph agents. See `PROJECT_BRIEF.md` for the full plan.

**Status:** build step 0 (skeleton, Postgres+pgvector, minimal ReAct agent with two mock-store tools).

## Quick start

```bash
cp .env.example .env        # defaults to the scripted fake model: no LLM, no network
make venv                   # create .venv and install backend + dev deps
make test                   # 24 tests
make demo                   # run the agent on "Where is my order A1001?"

docker --version            # Checking if docker is installed
docker compose version      # Checking of the docker compose is installed
#If docker compose aint installed , then follow the below two steps to install it:
    sudo apt update
    sudo apt install docker-compose-v2
    # Then check again if docker compose is installed

make db-up                  # start Postgres + pgvector (needs Docker running)
docker compose ps           # if the status says as "healthy", then move on to the next steps
```

Verify pgvector after `make db-up`:

```bash
make db-psql        # if successfull , you will see,  " supportgraph=# ", then give the sql command as below.

supportgraph=# SELECT extname FROM pg_extension WHERE extname = 'vector';
```

## Using a real model

Edit `.env`:

- Ollama: `SG_LLM_PROVIDER=ollama`, `SG_LLM_MODEL=qwen2.5:7b`, then `ollama pull qwen2.5:7b` and `pip install -e "backend[ollama]"`.
- Any OpenAI-compatible API: `SG_LLM_PROVIDER=openai_compat`, set `SG_LLM_BASE_URL`, `SG_LLM_API_KEY`, `SG_LLM_MODEL`, and `pip install -e "backend[openai]"`.

## Layout rules

- Tenant-specific logic lives only in `tenants/` and the database.
- `backend/app/llm/gateway.py` is the only place that knows the LLM provider.
- `backend/app/guardrails/` is deterministic code and never imports `graph/`.
