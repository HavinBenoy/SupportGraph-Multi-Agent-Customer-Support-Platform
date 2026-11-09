"""Deterministic scenarios for processing ownership (M6 fencing). No sleeps: a gated embedder blocks inside `embed_documents` until the test releases it, and
the test ages a claim by rewriting `processing_started_at`. The same scenarios run on SQLite (tests/knowledge/test_processing_fencing.py) and PostgreSQL
(tests/db/test_postgres_m6.py). Every function takes nothing and uses the database the calling fixture configured."""

import threading
import time
from datetime import timedelta

from sqlalchemy import func, select, update

from app.config import Settings
from app.db.models import Chunk, KbEvent, KbVersion, KbVersionChunk
from app.db.session import session_scope
from app.knowledge import pipeline, store
from app.knowledge.errors import Conflict
from app.llm.fake_embeddings import HashingEmbeddings

T = "qspiders_sample"
DOC = "fence-doc"
S = Settings(_env_file=None, embedding_provider="fake", llm_provider="fake")
BODY = "# Fees\n\nStudents may pay the programme fee in {n} instalments. The first instalment is due at enrolment.\n"
WAIT = 30


class Gated:
    """A fake embedder that can be told to block and/or fail inside the (transaction-free) embedding step."""

    def __init__(self, name="fake:hashing-v1-768", *, block=False, fail=False):
        self.inner = HashingEmbeddings()
        self.name, self.dim = name, getattr(self.inner, "dim", 768)
        self.entered, self.release = threading.Event(), threading.Event()
        self.block, self.fail, self.calls = block, fail, 0
        if not block:
            self.release.set()

    def embed_documents(self, texts, *a, **k):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(WAIT), "test bug: the gated embedder was never released"
        if self.fail:
            raise RuntimeError("embedding service down")
        return self.inner.embed_documents(texts)

    def embed_query(self, t):
        return self.inner.embed_query(t)


class Worker(threading.Thread):
    def __init__(self, embedder, actor, *, force=False):
        super().__init__(daemon=True)
        self.embedder, self.actor, self.force, self.result, self.error = embedder, actor, force, None, None

    def run(self):
        try:
            self.result = pipeline.process_version(T, self.actor, DOC, 1, self.embedder, S, force=self.force)
        except Exception as e:  # noqa: BLE001
            self.error = e

    def finish(self):
        self.join(WAIT)
        assert not self.is_alive(), "worker did not finish"
        return self


def new_doc(version_content=BODY.format(n="two")):
    with session_scope() as db:
        store.create_document(db, T, "admin1", S, doc_id=DOC, title="Fees", category="fees", content=version_content)


def age_claim(seconds=100_000):
    with session_scope() as db:
        db.execute(update(KbVersion).where(KbVersion.doc_id == DOC, KbVersion.version_no == 1)
                   .values(processing_started_at=store.now() - timedelta(seconds=seconds)).execution_options(synchronize_session=False))


def version():
    with session_scope() as db:
        v = db.get(KbVersion, (T, DOC, 1))
        return {"status": v.status, "model": v.embedding_model, "chunks": v.chunk_count, "error": v.processing_error, "attempt": getattr(v, "processing_attempt", None)}


def staged():
    with session_scope() as db:
        return list(db.execute(select(KbVersionChunk.chunk_index, KbVersionChunk.embedding_model).where(KbVersionChunk.doc_id == DOC).order_by(KbVersionChunk.chunk_index)).all())


def events():
    with session_scope() as db:
        return [(e.action, e.actor, e.summary) for e in db.execute(select(KbEvent).where(KbEvent.doc_id == DOC).order_by(KbEvent.id)).scalars()
                if e.action.startswith("ingestion_")]


def kinds():
    return [a for a, _, _ in events()]


def start_blocked(embedder, actor, *, force=False):
    w = Worker(embedder, actor, force=force)
    w.start()
    assert embedder.entered.wait(WAIT), "worker never reached the embedding step"
    return w


# ---------------------------------------------------------------------------------------------------------------------------------- cases
def case_a_old_worker_succeeds_after_takeover_completed():
    new_doc()
    a = Gated("fake:worker-A", block=True)
    wa = start_blocked(a, "A")
    age_claim()
    rb = pipeline.process_version(T, "B", DOC, 1, Gated("fake:hashing-v1-768"), S)               # B takes over the stale claim and completes
    assert rb["status"] == "ready"
    a.release.set()
    wa.finish()
    assert isinstance(wa.error, Conflict) and wa.result is None                                  # A learns it lost; it returns no success
    v = version()
    assert (v["status"], v["model"]) == ("ready", "fake:hashing-v1-768")                         # B's result, not A's
    assert staged() and {m for _, m in staged()} == {"fake:hashing-v1-768"} and len(staged()) == v["chunks"]
    ev = events()
    assert [a_ for a_, _, _ in ev].count("ingestion_completed") == 1 and "ingestion_failed" not in kinds()
    done = next(e for e in ev if e[0] == "ingestion_completed")
    assert done[1] == "B" and done[2]["attempt"] == 2
    sup = [e for e in ev if e[0] == "ingestion_superseded"]
    assert len(sup) == 1 and sup[0][1] == "A" and sup[0][2]["attempt"] == 1


def case_a2_old_worker_finishes_while_new_worker_is_still_processing():
    new_doc()
    a, b = Gated("fake:worker-A", block=True), Gated("fake:hashing-v1-768", block=True)
    wa = start_blocked(a, "A")
    age_claim()
    wb = start_blocked(b, "B")                                                                   # B holds the live claim, still embedding
    a.release.set()
    wa.finish()                                                                                  # A finishes FIRST but no longer owns the attempt
    assert isinstance(wa.error, Conflict)
    assert version()["status"] == "processing" and staged() == []                                # A committed nothing
    b.release.set()
    wb.finish()
    assert wb.error is None and wb.result["status"] == "ready"
    v = version()
    assert (v["status"], v["model"], v["attempt"]) == ("ready", "fake:hashing-v1-768", 2) and {m for _, m in staged()} == {"fake:hashing-v1-768"}
    assert kinds().count("ingestion_completed") == 1


def case_b_old_worker_fails_after_takeover_completed():
    new_doc()
    a = Gated("fake:worker-A", block=True, fail=True)
    wa = start_blocked(a, "A")
    age_claim()
    assert pipeline.process_version(T, "B", DOC, 1, Gated(), S)["status"] == "ready"
    before = staged()
    assert before
    a.release.set()
    wa.finish()
    assert isinstance(wa.error, Conflict) and wa.result is None
    v = version()
    assert v["status"] == "ready" and v["error"] is None and staged() == before                  # B's staged chunks were not deleted, state not failed
    assert "ingestion_failed" not in kinds() and kinds().count("ingestion_completed") == 1


def case_b2_old_worker_fails_while_new_worker_is_still_processing():
    new_doc()
    a, b = Gated("fake:worker-A", block=True, fail=True), Gated(block=True)
    wa = start_blocked(a, "A")
    age_claim()
    wb = start_blocked(b, "B")
    a.release.set()
    wa.finish()
    assert isinstance(wa.error, Conflict)
    assert version()["status"] == "processing"                                                   # A's failure did not kill B's claim
    b.release.set()
    wb.finish()
    assert wb.result["status"] == "ready" and version()["status"] == "ready" and "ingestion_failed" not in kinds()


def case_c_current_worker_fails_normally():
    new_doc()
    r = pipeline.process_version(T, "A", DOC, 1, Gated(fail=True), S)
    assert r["status"] == "failed" and r["error"] == "RuntimeError"
    v = version()
    assert (v["status"], v["error"], v["chunks"], v["model"]) == ("failed", "RuntimeError", None, None) and staged() == []
    assert kinds() == ["ingestion_started", "ingestion_failed"]
    assert events()[-1][2]["attempt"] == 1
    retry = pipeline.process_version(T, "A", DOC, 1, Gated(), S)                                 # recoverable: a failed version can simply be processed again
    assert retry["status"] == "ready" and version()["attempt"] == 2 and version()["error"] is None


def case_d_concurrent_takeovers_of_one_stale_claim():
    new_doc()
    a = Gated("fake:worker-A", block=True)
    wa = start_blocked(a, "A")
    age_claim()
    n = 6
    embedders = [Gated(block=True) for _ in range(n)]
    gate = threading.Barrier(n)
    workers = []

    class Racer(Worker):
        def run(self):
            gate.wait(WAIT)
            super().run()

    for i, e in enumerate(embedders):
        w = Racer(e, f"R{i}")
        workers.append(w)
        w.start()
    deadline = time.monotonic() + WAIT
    while sum(not w.is_alive() for w in workers) < n - 1 and time.monotonic() < deadline:        # wait for the condition (n-1 refusals), not for a fixed delay
        time.sleep(0.005)
    losers = [w for w in workers if not w.is_alive()]
    assert len(losers) == n - 1 and all(isinstance(w.error, Conflict) for w in losers)
    winner = next(w for w in workers if w.is_alive())
    assert sum(e.calls > 0 or e.entered.is_set() for e in embedders) == 1                        # exactly one racer reached embedding
    assert version()["attempt"] == 2
    a.release.set()
    wa.finish()                                                                                  # the original stale worker is also refused
    assert isinstance(wa.error, Conflict) and version()["status"] == "processing"
    winner.embedder.release.set()
    winner.finish()
    assert winner.result["status"] == "ready" and version()["status"] == "ready" and len(staged()) == version()["chunks"]
    assert kinds().count("ingestion_completed") == 1 and kinds().count("ingestion_started") == 2


def case_e_normal_retry_no_duplicates_and_other_versions_untouched():
    new_doc()
    with session_scope() as db:
        store.create_version(db, T, "admin1", S, DOC, content=BODY.format(n="three"), change_note="v2")
    assert pipeline.process_version(T, "A", DOC, 2, Gated(), S)["status"] == "ready"
    v2_before = _v2()
    assert pipeline.process_version(T, "A", DOC, 1, Gated(fail=True), S)["status"] == "failed"
    ok = pipeline.process_version(T, "A", DOC, 1, Gated(), S)
    assert ok["status"] == "ready" and len(staged_v(1)) == ok["chunks"]
    again = pipeline.process_version(T, "A", DOC, 1, Gated(), S)
    assert again["unchanged"] is True and len(staged_v(1)) == ok["chunks"]
    forced = pipeline.process_version(T, "A", DOC, 1, Gated(), S, force=True)
    assert forced["status"] == "ready" and len(staged_v(1)) == ok["chunks"]
    assert _v2() == v2_before                                                                    # a sibling version is untouched by every attempt on v1
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.doc_id == DOC)) == 0   # nothing leaked into the live index


def case_f_stale_worker_after_publish_changes_nothing():
    new_doc()
    a = Gated("fake:worker-A", block=True)
    wa = start_blocked(a, "A")
    age_claim()
    assert pipeline.process_version(T, "B", DOC, 1, Gated(), S)["status"] == "ready"
    with session_scope() as db:
        pipeline.publish_version(db, T, "admin1", DOC, 1, "fake:hashing-v1-768")
    with session_scope() as db:
        live = list(db.execute(select(Chunk.chunk_index, Chunk.text, Chunk.embedding_model).where(Chunk.doc_id == DOC).order_by(Chunk.chunk_index)).all())
    assert live
    a.release.set()
    wa.finish()
    assert isinstance(wa.error, Conflict)
    assert version()["status"] == "published" and staged() == []
    with session_scope() as db:
        assert list(db.execute(select(Chunk.chunk_index, Chunk.text, Chunk.embedding_model).where(Chunk.doc_id == DOC).order_by(Chunk.chunk_index)).all()) == live


def case_g_archive_during_processing_does_not_strand_the_version():
    new_doc()
    a = Gated(block=True)
    wa = start_blocked(a, "A")
    with session_scope() as db:
        pipeline.archive_document(db, T, "admin1", DOC)
    a.release.set()
    wa.finish()
    v = version()
    assert v["status"] != "processing" and staged() == []                                        # never left stuck in `processing`
    assert kinds().count("ingestion_completed") == 0


def _v2():
    with session_scope() as db:
        v = db.get(KbVersion, (T, DOC, 2))
        return (v.status, v.chunk_count, v.embedding_model, v.checksum, len(staged_v(2, db)))


def staged_v(n, db=None):
    def q(d):
        return list(d.execute(select(KbVersionChunk.chunk_index).where(KbVersionChunk.doc_id == DOC, KbVersionChunk.version_no == n)).all())
    if db is not None:
        return q(db)
    with session_scope() as d:
        return q(d)


ALL = [case_a_old_worker_succeeds_after_takeover_completed, case_a2_old_worker_finishes_while_new_worker_is_still_processing, case_b_old_worker_fails_after_takeover_completed,
       case_b2_old_worker_fails_while_new_worker_is_still_processing, case_c_current_worker_fails_normally, case_d_concurrent_takeovers_of_one_stale_claim,
       case_e_normal_retry_no_duplicates_and_other_versions_untouched, case_f_stale_worker_after_publish_changes_nothing, case_g_archive_during_processing_does_not_strand_the_version]
