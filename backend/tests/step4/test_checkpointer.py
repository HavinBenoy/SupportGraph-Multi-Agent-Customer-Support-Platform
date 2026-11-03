"""Checkpointer selection and the "pause survives rebuilding the graph" property (in-memory saver here; Postgres in tests/db)."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings
from app.graph.checkpointer import _conninfo, get_checkpointer, setup_checkpointer
from app.graph.review import resume_ticket
from app.graph.support import ask_support, build_support_graph
from app.tenants.scope import Reviewer
from tests.rag.conftest import settings
from tests.step4.conftest import MakeupRequest, embedder, ingested, rows  # noqa: F401


def test_default_is_memory_and_setup_is_a_noop():
    saver = get_checkpointer(Settings(_env_file=None))
    assert isinstance(saver, InMemorySaver)
    setup_checkpointer(saver)


def test_libpq_url_conversion():
    assert _conninfo("postgresql+psycopg://u:p@h:5/db") == "postgresql://u:p@h:5/db"
    assert _conninfo("postgresql://u:p@h:5/db") == "postgresql://u:p@h:5/db"


def test_unknown_checkpointer_setting_is_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, checkpointer="redis")


def test_a_pause_survives_rebuilding_the_graph_on_the_same_saver(ingested):
    saver = InMemorySaver()
    first = build_support_graph(settings=settings(), checkpointer=saver)
    out = ask_support(first, "Please arrange a make-up class for the session I missed in PYD-301.", tenant_id="qspiders_sample",
                      student_id="stu_meera", ticket_id="tk-r", today="2026-10-24")
    assert out["awaiting"] == "staff"
    del first                                                        # "restart": new graph object, same stored checkpoints
    second = build_support_graph(settings=settings(), checkpointer=saver)
    done = resume_ticket(second, Reviewer("qspiders_sample", "s_1", "staff"), "tk-r", approve=True, today="2026-10-24")
    assert done["outcome"] == "approved" and rows(MakeupRequest)[0].status == "approved"


def test_a_fresh_saver_knows_nothing_about_old_pauses(ingested):
    g1 = build_support_graph(settings=settings(), checkpointer=InMemorySaver())
    ask_support(g1, "Please arrange a make-up class for the session I missed in PYD-301.", tenant_id="qspiders_sample",
                student_id="stu_meera", ticket_id="tk-q", today="2026-10-24")
    g2 = build_support_graph(settings=settings(), checkpointer=InMemorySaver())
    from app.graph.review import ReviewError

    with pytest.raises(ReviewError):
        resume_ticket(g2, Reviewer("qspiders_sample", "s_1", "staff"), "tk-q", approve=True, today="2026-10-24")
    assert rows(MakeupRequest)[0].status == "pending"


def test_state_of_one_message_does_not_leak_into_the_next_on_the_same_thread(ingested):
    g = build_support_graph(settings=settings())
    kw = dict(tenant_id="qspiders_sample", student_id="stu_asha", ticket_id="tk-s", today="2026-10-24")
    a = ask_support(g, "What is my attendance in PYF-101?", **kw)
    assert a["tools_called"]
    b = ask_support(g, "What payment methods do you accept?", **kw)
    assert b["route"] == "knowledge" and b["tools_called"] == [] and b["qa_revisions"] == 0
    assert [m.type for m in b["messages"]] == ["human", "ai", "human", "ai"]
