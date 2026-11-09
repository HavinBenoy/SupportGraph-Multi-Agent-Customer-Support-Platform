"""Shared helpers for the M6 knowledge-store tests: HTTP shortcuts, direct reads of the live index, retrieval through the real search function."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.auth.accounts import create_account
from app.auth.tokens import Principal, issue_token
from app.db.models import Call, Chunk, Document, InterventionFeedback, KbVersionChunk
from app.db.session import session_scope
from app.rag.retrieval import search
from tests.api.conftest import PW, T, auth

OTHER = "other_institute"
BASE = "/api/admin/knowledge"
SECRET_TEXT = "ZEBRA-SENTINEL-4471"          # appears only in document bodies / feedback text: it must never reach an audit event

FEES_V1 = "# Fees\n\n## Instalments\n\nStudents may pay the programme fee in two instalments. The first instalment is due at enrolment.\n"
FEES_V2 = "# Fees\n\n## Instalments\n\nStudents may pay the programme fee in three instalments. The first instalment is due at enrolment.\n"
FEES_V3 = "# Fees\n\n## Instalments\n\nStudents may pay the programme fee in four instalments. Unique token quokkafee applies.\n"


def admin(api):
    return auth(api, "admin1")


def admin2(api):
    with session_scope() as db:
        if db.get(__import__("app.db.models", fromlist=["Account"]).Account, (T, "admin2")) is None:
            create_account(db, T, "admin2", PW, "admin")
    return auth(api, "admin2")


def other_admin(api):
    with session_scope() as db:
        if db.get(__import__("app.db.models", fromlist=["Account"]).Account, (OTHER, "oadmin")) is None:
            create_account(db, OTHER, "oadmin", PW, "admin")
    return {"Authorization": "Bearer " + issue_token(api.app.state.secret, Principal(OTHER, "admin", "oadmin", None), ttl_seconds=600)}


def payload(doc_id="fees-test", title="Fees Test", content=FEES_V1, category="fees", **kw):
    return {"doc_id": doc_id, "title": title, "category": category, "content": content, **kw}


def create(api, h=None, **kw):
    r = api.post(f"{BASE}/documents", json=payload(**kw), headers=h or admin(api))
    assert r.status_code == 201, r.text
    return r.json()


def process(api, doc_id, n, h=None, **body):
    r = api.post(f"{BASE}/documents/{doc_id}/versions/{n}/process", json=body or None, headers=h or admin(api))
    assert r.status_code == 200, r.text
    return r.json()


def publish(api, doc_id, n, h=None, expect=200):
    r = api.post(f"{BASE}/documents/{doc_id}/versions/{n}/publish", headers=h or admin(api))
    assert r.status_code == expect, r.text
    return r.json()


def add_version(api, doc_id, content, note="edit", h=None, **kw):
    r = api.post(f"{BASE}/documents/{doc_id}/versions", json={"content": content, "change_note": note, **kw}, headers=h or admin(api))
    assert r.status_code == 201, r.text
    return r.json()


def go_live(api, doc_id="fees-test", content=FEES_V1, n=1, **kw):
    """create (if n == 1) / process / publish."""
    if n == 1:
        create(api, doc_id=doc_id, content=content, **kw)
    process(api, doc_id, n)
    return publish(api, doc_id, n)


def live_chunks(doc_id, tenant=T):
    with session_scope() as db:
        return list(db.execute(select(Chunk.chunk_index, Chunk.text).where(Chunk.tenant_id == tenant, Chunk.doc_id == doc_id).order_by(Chunk.chunk_index)).all())


def live_doc(doc_id, tenant=T):
    with session_scope() as db:
        d = db.get(Document, (tenant, doc_id))
        return None if d is None else {"title": d.title, "kb_version": d.kb_version, "content_hash": d.content_hash}


def staged_count(doc_id, version, tenant=T):
    with session_scope() as db:
        return len(db.execute(select(KbVersionChunk).where(KbVersionChunk.tenant_id == tenant, KbVersionChunk.doc_id == doc_id, KbVersionChunk.version_no == version)).all())


def retrieve_ids(api, text, tenant=T, top_k=8, min_score=0.0):
    """What the real retrieval function returns for `text`: [(doc_id, version, source_id)]."""
    emb = api.app.state.embedder if getattr(api.app.state, "embedder", None) else __import__("app.llm.gateway", fromlist=["x"]).get_embedding_client(api.app.state.settings)
    q = emb.embed_query(text)
    with session_scope() as db:
        got = search(db, tenant_id=tenant, query_vector=q, model_name=emb.name, top_k=top_k)
    return [(c.doc_id, c.doc_version, c.source_id, c.text) for c in got if c.score > min_score]


def mk_feedback(cid, status="approved_for_update", staff="staff1", tenant=T, text="The assistant gave the wrong instalment rule.", fix="Fees can be paid in two instalments."):
    """A call and a submitted feedback record in the given review status, written directly (the review flow itself is M4.1/M5 and has its own tests)."""
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add(Call(tenant_id=tenant, id=cid, ticket_id=f"tk_{cid}", student_id=None, status="ended", owner="none", epoch=2, created_at=now, intervention_owner=staff,
                    takeover_at=now, ended_at=now))
        db.flush()
        db.add(InterventionFeedback(tenant_id=tenant, call_id=cid, staff_subject=staff, intervened_at=now, state="submitted", category="incorrect_information",
                                    what_was_wrong=text, suggested_correction=fix, submitted_at=now, review_status=status,
                                    reviewed_by="admin1" if status != "pending_review" else None, reviewed_at=now if status != "pending_review" else None))
    return cid
