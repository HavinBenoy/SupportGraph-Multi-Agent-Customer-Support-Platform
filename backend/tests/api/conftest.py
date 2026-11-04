"""Fixtures for the HTTP API tests: the real FastAPI app over the real support graph (offline fakes) on the seeded SQLite DB."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.auth.accounts import create_account
from app.db.session import session_scope
from app.graph.support import build_support_graph
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401

T = "qspiders_sample"
PW = "correct horse battery"


@pytest.fixture
def api(ingested, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SG_AUTH_SECRET", "t" * 48)
    s = settings()
    s.auth_secret = type(s.auth_secret)("t" * 48) if s.auth_secret else None
    if s.auth_secret is None:
        from pydantic import SecretStr
        s.auth_secret = SecretStr("t" * 48)
    app = create_app(s, graph=build_support_graph(settings=s))
    app.state.today = date(2026, 10, 24)
    with session_scope() as db:
        create_account(db, T, "asha", PW, "student", "stu_asha")
        create_account(db, T, "meera", PW, "student", "stu_meera")
        create_account(db, T, "staff1", PW, "staff")
        create_account(db, T, "admin1", PW, "admin")
    with TestClient(app) as c:
        c.app = app
        yield c


def login(api, user, pw=PW, tenant=T):
    r = api.post("/api/auth/login", json={"username": user, "password": pw, "tenant": tenant})
    return r


def auth(api, user, tenant=T):
    r = login(api, user, tenant=tenant)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}
