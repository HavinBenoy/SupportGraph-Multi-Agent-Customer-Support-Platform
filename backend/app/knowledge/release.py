"""Process and publish an approved change request (Part 2): ONE publication unit for several documents, run from the Change Requests screen.

States:  approved | failed -> processing -> publishing -> published | failed        (see changes.TRANSITIONS; every change goes through `check_transition`)

Steps
  1. begin (one short transaction): claim the request with a fenced attempt number, re-check that every included document is still published at the version the
     change was based on, and create one immutable DRAFT version per document (reusing the version an earlier attempt already made when it still matches).
  2. process (per document, own transactions, embedding outside any transaction): the existing M6 pipeline chunks, embeds and stages the version. A failure for any
     document is recorded per document and stops the release before anything is published; live knowledge is untouched.
  3. publish (ONE database transaction): re-verify ownership and staleness, then publish_version for every document, mark the items and the request published, and
     store the release record. It commits or rolls back as a whole.

What is atomic and what is not (verified in tests/db/test_postgres_ai_release.py)
  * All documents' live rows (documents, chunks), their version statuses and the request's status change in ONE transaction, on PostgreSQL and on SQLite. A reader of
    the live index therefore sees every document at its OLD text until that transaction commits and every document at its NEW text afterwards, never a mixture, for any
    single retrieval statement. (Two separate statements issued by one chat turn can still straddle the commit: that is inherent to read-committed isolation.)
  * Processing (embedding) is NOT part of that transaction by design: it is slow and must not hold row locks. It writes only staged rows and draft versions, which
    retrieval never reads, so a half-processed request is invisible to the live index.
  * If the process dies between two steps the request stays `processing`/`publishing`; after `kb_release_stale_s` another administrator's retry takes it over (the
    attempt number fences the old worker out). Nothing is ever published twice: publish_version of a published version is a no-op and the request is only marked
    published inside the publishing transaction."""

import logging
import threading
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import KbChangeItem, KbChangeRequest, KbDocument
from app.db.session import session_scope
from app.knowledge import audit, changes, pipeline, store
from app.knowledge.errors import Conflict, Invalid, KbError, NotFound

log = logging.getLogger(__name__)


class Superseded(Exception):
    """This worker no longer owns the release (a newer attempt took over): it must stop without writing."""


def _aware(t):
    return t if t is None or t.tzinfo else t.replace(tzinfo=store.now().tzinfo)


def _items(db: Session, tenant_id: str, request_id: str) -> list[KbChangeItem]:
    return [i for i in changes._items(db, tenant_id, request_id) if changes.included(i)]


def _fail_request(db: Session, tenant_id: str, request_id: str, attempt: int, from_status: str, actor: str, failure: dict) -> bool:
    changes.check_transition(from_status, "failed")
    n = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id, KbChangeRequest.status == from_status,
                                                 KbChangeRequest.release_attempt == attempt)
                   .values(status="failed", failure=failure, row_version=KbChangeRequest.row_version + 1, updated_at=store.now()).execution_options(synchronize_session=False)).rowcount
    if n == 1:
        audit.record(db, tenant_id, actor, "change_release_failed", "request", request_id, request_id=request_id, attempt=attempt, stage=failure.get("stage"),
                     error=str(failure.get("message", ""))[:200], failed=failure.get("documents", []))
    return n == 1


# ------------------------------------------------------------------------------------------------------------------------------------ step 1
def begin(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, *, expected_row_version: int | None = None) -> dict:
    """Claim the release and prepare the draft versions. Returns {"outcome": "started" | "already_published" | "failed", "attempt": n}. Raises Conflict (409) when another
    release is running, Invalid when nothing can be published."""
    r = changes.get_request(db, tenant_id, request_id)
    if r.status in changes.PRIVATE or (r.status == "cancelled" and r.submitted_at is None):
        raise NotFound("change request not found")
    if r.status == "published":
        return {"outcome": "already_published", "attempt": r.release_attempt}                      # a repeated click: nothing to do, nothing duplicated
    takeover = r.status in ("processing", "publishing")
    if takeover:
        t = _aware(r.phase_started_at)
        if t is None or store.now() - t <= timedelta(seconds=s.kb_release_stale_s):
            raise Conflict("this request is already being processed and published")
    elif r.status not in ("approved", "failed"):
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: only an approved request can be processed and published")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    items = _items(db, tenant_id, request_id)
    if not items:
        raise Invalid("nothing is left to publish: every document was discarded or excluded")
    retry = r.release_attempt > 0
    attempt = r.release_attempt + 1
    prior = r.status
    stale = changes._stale_docs(db, tenant_id, items)
    values = dict(status="processing", release_attempt=attempt, phase_started_at=store.now(), failure=None, row_version=KbChangeRequest.row_version + 1, updated_at=store.now())
    if not takeover:
        changes.check_transition(prior, "processing")             # a stale takeover restarts from the beginning: the dead worker's attempt number is fenced out
    claimed = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id, KbChangeRequest.status == prior,
                                                       KbChangeRequest.release_attempt == r.release_attempt)
                         .values(**values).execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        raise Conflict("this request is already being processed and published")
    db.expire(r)
    audit.record(db, tenant_id, actor, "change_release_started", "request", request_id, request_id=request_id, attempt=attempt, items=len(items), from_status=prior,
                 stale_takeover=takeover)
    if retry:
        audit.record(db, tenant_id, actor, "change_release_retried", "request", request_id, request_id=request_id, attempt=attempt, retry=True)
    if stale:
        for it in items:
            bad = it.doc_id in stale
            db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == it.doc_id)
                       .values(process_status="failed" if bad else "pending", publish_status="not_published",
                               process_error="The live text changed after this change was prepared (stale): it was not processed." if bad else None)
                       .execution_options(synchronize_session=False))
        _fail_request(db, tenant_id, request_id, attempt, "processing", actor,
                      {"stage": "validation", "message": "Some documents were published again after the change was prepared, so it no longer applies to the live text.", "documents": stale})
        return {"outcome": "failed", "attempt": attempt}
    note = (r.description or "Change request")[:500]
    for it in items:
        ver = None
        if it.result_version is not None:
            try:
                old = store.get_version(db, tenant_id, it.doc_id, it.result_version)
                ver = old if old.checksum == it.proposed_checksum and old.status in ("draft", "ready", "processing", "failed") else None
            except NotFound:
                ver = None
        if ver is None:
            ver = store.create_version(db, tenant_id, actor, s, it.doc_id, content=it.proposed_content, title=it.proposed_title, change_note=note, author=r.created_by)
        db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == it.doc_id)
                   .values(result_version=ver.version_no, process_status="pending", publish_status="pending", process_error=None).execution_options(synchronize_session=False))
    db.flush()
    return {"outcome": "started", "attempt": attempt}


# --------------------------------------------------------------------------------------------------------------------------------- steps 2-3
def _still_mine(db: Session, tenant_id: str, request_id: str, attempt: int, *statuses: str) -> KbChangeRequest:
    r = db.get(KbChangeRequest, (tenant_id, request_id))
    if r is None or r.release_attempt != attempt or r.status not in statuses:
        raise Superseded()
    db.refresh(r)
    return r


def _set_item(tenant_id: str, request_id: str, doc_id: str, **values) -> None:
    with session_scope() as db:
        db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == doc_id)
                   .values(**values).execution_options(synchronize_session=False))


def _process_all(tenant_id: str, actor: str, request_id: str, attempt: int, s: Settings, embedder) -> list[str]:
    with session_scope() as db:
        _still_mine(db, tenant_id, request_id, attempt, "processing")
        todo = [(i.doc_id, i.result_version) for i in _items(db, tenant_id, request_id)]
    failed: list[str] = []
    for doc_id, ver_no in todo:
        with session_scope() as db:
            _still_mine(db, tenant_id, request_id, attempt, "processing")
        _set_item(tenant_id, request_id, doc_id, process_status="processing", process_error=None)
        try:
            out = pipeline.process_version(tenant_id, actor, doc_id, ver_no, embedder, s)
            ok = out.get("status") == "ready"
            err = None if ok else str(out.get("error") or "processing failed")[:300]
        except KbError as e:
            ok, err = False, str(e)[:300]
        except Exception as e:                                                   # noqa: BLE001 - recorded per document; never leaks text
            ok, err = False, pipeline._safe_error(e)
        _set_item(tenant_id, request_id, doc_id, process_status="ready" if ok else "failed", process_error=err)
        with session_scope() as db:
            audit.record(db, tenant_id, actor, "change_item_processed", "request", request_id, doc_id=doc_id, version_no=ver_no, request_id=request_id,
                         status="ready" if ok else "failed", error=err or "", attempt=attempt, chunks=int(out.get("chunks") or 0) if ok else 0)
        if not ok:
            failed.append(doc_id)
    return failed


def _publish_all(tenant_id: str, actor: str, request_id: str, attempt: int, s: Settings, embedder) -> dict:
    """The single publishing transaction. Anything raised inside rolls EVERYTHING back (live rows, version statuses, item and request status)."""
    with session_scope() as db:
        r = db.get(KbChangeRequest, (tenant_id, request_id))
        if r is None:
            raise Superseded()
        changes.check_transition("publishing", "published")
        won = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id, KbChangeRequest.status == "publishing",
                                                       KbChangeRequest.release_attempt == attempt)
                         .values(row_version=KbChangeRequest.row_version + 1, updated_at=store.now()).execution_options(synchronize_session=False)).rowcount
        if won != 1:
            raise Superseded()                                                    # also takes the request row lock on PostgreSQL: two publishers cannot both continue
        items = _items(db, tenant_id, request_id)
        stale = changes._stale_docs(db, tenant_id, items)
        if stale:
            raise Conflict("documents were published again while this request was being processed: " + ", ".join(stale))
        versions, previous = {}, {}
        for it in items:
            doc = db.get(KbDocument, (tenant_id, it.doc_id))
            previous[it.doc_id] = doc.published_version
            pipeline.publish_version(db, tenant_id, actor, it.doc_id, it.result_version, embedder.name)
            versions[it.doc_id] = it.result_version
            db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == it.doc_id)
                       .values(publish_status="published", process_status="ready").execution_options(synchronize_session=False))
        t = store.now()
        db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id)
                   .values(status="published", published_by=actor[:64], published_at=t, failure=None,
                           release={"versions": versions, "previous": previous, "attempts": attempt, "model": embedder.name, "published_at": store._iso(t)})
                   .execution_options(synchronize_session=False))
        audit.record(db, tenant_id, actor, "change_release_published", "request", request_id, request_id=request_id, attempt=attempt, count=len(versions), model=embedder.name,
                     documents=[f"{d}@{v}" for d, v in versions.items()], status="published")
        return versions


def run(tenant_id: str, actor: str, request_id: str, attempt: int, s: Settings, embedder) -> None:
    """Steps 2 and 3. Never raises: it publishes, records a failure, or notices it was superseded."""
    try:
        failed = _process_all(tenant_id, actor, request_id, attempt, s, embedder)
        if failed:
            with session_scope() as db:
                _fail_request(db, tenant_id, request_id, attempt, "processing", actor,
                              {"stage": "processing", "message": f"{len(failed)} document(s) could not be processed. Nothing was published and the live knowledge is unchanged.",
                               "documents": failed})
            return
        with session_scope() as db:
            _still_mine(db, tenant_id, request_id, attempt, "processing")
            changes.check_transition("processing", "publishing")
            n = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id, KbChangeRequest.status == "processing",
                                                         KbChangeRequest.release_attempt == attempt)
                           .values(status="publishing", phase_started_at=store.now(), row_version=KbChangeRequest.row_version + 1).execution_options(synchronize_session=False)).rowcount
            if n != 1:
                raise Superseded()
        try:
            _publish_all(tenant_id, actor, request_id, attempt, s, embedder)
            if s.kb_ai_verify:                                                    # a separate, later look at the published base; it can never fail or undo the release
                from app.knowledge.impact import verify
                verify.verify_request(tenant_id, request_id, s, actor)
        except Superseded:
            raise
        except Exception as e:                                                   # noqa: BLE001 - the publishing transaction rolled back: every document is still at its old text
            msg = str(e)[:300] if isinstance(e, KbError) else pipeline._safe_error(e)
            log.warning("release %s attempt %s failed while publishing: %s", request_id, attempt, type(e).__name__)
            with session_scope() as db:
                db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.publish_status == "pending")
                           .values(publish_status="not_published").execution_options(synchronize_session=False))
                _fail_request(db, tenant_id, request_id, attempt, "publishing", actor,
                              {"stage": "publishing", "message": "Publishing failed and was rolled back as a whole: no document was changed. " + msg, "documents": []})
    except Superseded:
        log.info("release %s attempt %s was superseded; stopping", request_id, attempt)
    except Exception:                                                            # noqa: BLE001
        log.exception("release %s attempt %s crashed", request_id, attempt)
        try:
            with session_scope() as db:
                r = db.get(KbChangeRequest, (tenant_id, request_id))
                if r is not None and r.release_attempt == attempt and r.status in ("processing", "publishing"):
                    _fail_request(db, tenant_id, request_id, attempt, r.status, actor, {"stage": r.status, "message": "The release stopped unexpectedly. Retry is safe.", "documents": []})
        except Exception:                                                        # noqa: BLE001
            log.exception("could not record the release failure for %s", request_id)


def launch(tenant_id: str, actor: str, request_id: str, attempt: int, s: Settings, embedder) -> None:
    if not s.kb_ai_background:
        run(tenant_id, actor, request_id, attempt, s, embedder)
        return
    threading.Thread(target=run, args=(tenant_id, actor, request_id, attempt, s, embedder), name=f"release-{request_id}", daemon=True).start()
