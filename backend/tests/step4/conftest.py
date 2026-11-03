"""Fixtures for the build step 4 tests: the full support graph with a (default in-memory) checkpointer on the seeded SQLite DB."""

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, Escalation, MakeupRequest, Student, Ticket
from app.db.session import session_scope
from app.graph.review import converse, resume_ticket
from app.graph.support import ask_support, build_support_graph
from app.tenants.scope import Reviewer
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401  (fixtures reused from the RAG tests)

T = "qspiders_sample"
TODAY = "2026-10-24"


@pytest.fixture
def graph(ingested):  # noqa: F811
    return build_support_graph(settings=settings())


@pytest.fixture
def ask(graph):
    def _ask(text, student="stu_meera", ticket="tk-1", tenant=T):
        return ask_support(graph, text, tenant_id=tenant, student_id=student, ticket_id=ticket, today=TODAY)
    return _ask


@pytest.fixture
def chat(graph):
    def _chat(text, student="stu_meera", ticket="tk-1", tenant=T):
        return converse(graph, text, tenant_id=tenant, student_id=student, ticket_id=ticket, today=TODAY)
    return _chat


@pytest.fixture
def decide(graph):
    def _decide(ticket, *, approve=True, role="staff", reviewer="s_1", tenant=T):
        return resume_ticket(graph, Reviewer(tenant_id=tenant, reviewer_id=reviewer, role=role), ticket, approve=approve, today=TODAY)
    return _decide


def rows(model, **where):
    with session_scope() as db:
        stmt = select(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return list(db.execute(stmt).scalars())


def audit_actions(ticket=None):
    return [r.action for r in rows(AuditLog) if ticket is None or r.ticket_id == ticket]


__all__ = ["AuditLog", "Escalation", "MakeupRequest", "Student", "Ticket", "audit_actions", "rows"]
