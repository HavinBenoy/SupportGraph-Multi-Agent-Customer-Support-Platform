"""Bring the articles that `make ingest` loads from disk under the knowledge store, so they gain versions, history and an audit trail.

Each article that is not yet managed becomes document + version 1 (origin `imported`), is processed and published through the SAME functions as any other
document. After that `make ingest` leaves it alone (see app.rag.ingest). Safe to repeat: managed articles are skipped."""

from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db.models import KbDocument
from app.db.session import session_scope
from app.knowledge.purge import is_purged, purged_ids
from app.knowledge import audit, pipeline, store
from app.knowledge.errors import KbError
from app.rag.documents import load_public_documents
from app.tenants.loader import TENANTS_DIR

_CATEGORY_HINTS = (("fee", "fees"), ("course", "courses"), ("batch", "schedules"), ("admission", "admissions"), ("eligib", "admissions"), ("placement", "placement"),
                   ("attendance", "attendance"), ("assessment", "examinations"), ("exam", "examinations"), ("certificate", "certificates"), ("support", "support"),
                   ("privacy", "policies"), ("policy", "policies"), ("faq", "faq"))


def guess_category(doc_id: str) -> str:
    for needle, cat in _CATEGORY_HINTS:
        if needle in doc_id:
            return cat
    return "other"


def unmanaged_disk_docs(tenant_id: str, tenants_dir: Path = TENANTS_DIR) -> list[str]:
    try:
        docs = load_public_documents(tenant_id, tenants_dir)
    except Exception:  # noqa: BLE001 - e.g. no docs folder
        return []
    with session_scope() as db:
        managed = set(db.execute(select(KbDocument.doc_id).where(KbDocument.tenant_id == tenant_id)).scalars()) | purged_ids(db, tenant_id)
    return [d.doc_id for d in docs if d.doc_id not in managed]


def import_from_disk(tenant_id: str, actor: str, embedder, s: Settings, tenants_dir: Path = TENANTS_DIR) -> dict:
    docs = load_public_documents(tenant_id, tenants_dir)
    imported, skipped, failed = [], [], []
    for d in docs:
        with session_scope() as db:
            if db.get(KbDocument, (tenant_id, d.doc_id)) is not None or is_purged(db, tenant_id, d.doc_id):
                skipped.append(d.doc_id)                                  # already managed, or permanently deleted (never re-imported)
                continue
            try:
                store.create_document(db, tenant_id, actor, s, doc_id=d.doc_id, title=d.title, category=guess_category(d.doc_id), content=d.body.strip("\n") + "\n",
                                      change_note=f"Imported from {d.rel_path}", source_filename=d.rel_path.rsplit("/", 1)[-1], origin="imported")
            except KbError as e:
                failed.append({"doc_id": d.doc_id, "error": e.detail})
                continue
        res = pipeline.process_version(tenant_id, actor, d.doc_id, 1, embedder, s)
        if res["status"] != "ready":
            failed.append({"doc_id": d.doc_id, "error": res.get("error", "processing failed")})
            continue
        try:
            with session_scope() as db:
                pipeline.publish_version(db, tenant_id, actor, d.doc_id, 1, embedder.name)
        except KbError as e:
            failed.append({"doc_id": d.doc_id, "error": e.detail})
            continue
        imported.append(d.doc_id)
    with session_scope() as db:
        audit.record(db, tenant_id, actor, "disk_import", "document", "*", imported=len(imported), skipped=len(skipped), failed=len(failed))
    return {"imported": imported, "skipped": skipped, "failed": failed}
