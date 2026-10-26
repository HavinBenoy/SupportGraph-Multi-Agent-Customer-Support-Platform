"""Static checks on the Langfuse compose file: it must stay isolated from the app database."""

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "langfuse" / "docker-compose.yml"
APP_COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"


@pytest.fixture(scope="module")
def lf():
    return yaml.safe_load(COMPOSE.read_text())


def _published(compose):
    return [p for svc in compose["services"].values() for p in svc.get("ports", [])]


def test_every_published_port_is_bound_to_localhost(lf):
    assert _published(lf), "expected at least the web UI port"
    assert all(str(p).startswith("127.0.0.1:") for p in _published(lf))


def test_no_host_port_collisions_with_app_db_or_frontend(lf):
    host_ports = {str(p).split(":")[1] for p in _published(lf)}  # "127.0.0.1:<host>:<container>"
    assert "5432" not in host_ports  # SupportGraph's pgvector DB
    assert not any(h.startswith("3000") for h in host_ports)  # Next.js frontend, later
    assert all("${LANGFUSE_WEB_PORT:-3100}" in str(p) or "${LANGFUSE_MINIO_PORT:-9090}" in str(p) for p in _published(lf))


def test_separate_project_name_and_volumes(lf):
    app = yaml.safe_load(APP_COMPOSE.read_text())
    assert lf["name"] == "supportgraph-langfuse"
    assert set(lf["volumes"]).isdisjoint(app["volumes"])  # never shares the pgdata volume
    assert "container_name" not in yaml.dump(lf)  # avoids clashing with supportgraph-db


def test_all_secrets_are_required_not_defaulted(lf):
    text = yaml.dump(lf)  # parsed values only, so explanatory comments can't trip the check
    for weak_default in ("mysalt", "mysecret", "miniosecret", "myredissecret", ":-postgres}", ":-clickhouse}"):
        assert weak_default not in text
    for var in ("SALT", "ENCRYPTION_KEY", "NEXTAUTH_SECRET", "LANGFUSE_PG_PASSWORD", "CLICKHOUSE_PASSWORD",
                "MINIO_ROOT_PASSWORD", "REDIS_AUTH"):
        assert f"${{{var}:?" in text, f"{var} must be a required variable"


def test_langfuse_uses_its_own_postgres_not_the_app_one(lf):
    assert lf["services"]["postgres"]["image"].startswith("docker.io/postgres:")
    assert "pgvector" not in yaml.dump(lf)
