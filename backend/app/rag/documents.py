"""Load a tenant's PUBLIC help articles from disk, and refuse anything that is not one.

This is the first of three layers that keep staff-only policy out of user answers:
  1. here: only `tenants/<id>/docs/` is read, and every file must declare `audience: public`;
  2. the database: `documents.audience` / `chunks.audience` have a CHECK that only allows 'public';
  3. retrieval: every query filters on tenant_id AND audience = 'public'.
Each layer is independently tested, so a bug in one does not leak.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from app.tenants.loader import TENANTS_DIR, load_tenant_config

_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_DOC_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


class IngestError(Exception):
    """The documents on disk cannot be ingested as they are. The message says what to fix."""


class InternalDocumentError(IngestError):
    """A staff-only document was found where only public ones are allowed. Never ingested."""


@dataclass(frozen=True)
class SourceDoc:
    doc_id: str
    title: str
    audience: str
    last_reviewed: date | None
    body: str  # markdown after the front matter
    rel_path: str  # relative to the tenant folder, e.g. "docs/returns-and-refunds.md"
    content_hash: str  # sha256 of the whole file


def load_public_documents(tenant_id: str, tenants_dir: Path = TENANTS_DIR) -> list[SourceDoc]:
    """Return the tenant's user documents, sorted by doc_id. Raises IngestError / InternalDocumentError."""
    load_tenant_config(tenant_id, tenants_dir)  # validates the id's shape (no path tricks) and that the tenant exists
    tenant_dir = (tenants_dir / tenant_id).resolve()
    docs_dir = (tenant_dir / "docs").resolve()
    if not docs_dir.is_dir():
        raise IngestError(f"No docs folder for tenant '{tenant_id}': expected {docs_dir}")

    seen: dict[str, str] = {}
    out: list[SourceDoc] = []
    for path in sorted(docs_dir.glob("*.md")):
        real = path.resolve()  # follows symlinks: a link inside docs/ pointing at internal/ must not slip through
        if real.is_relative_to(tenant_dir) and "internal" in real.relative_to(tenant_dir).parts:
            raise InternalDocumentError(f"{path.name} resolves into the internal folder; refusing to ingest it")
        if not real.is_relative_to(docs_dir):
            raise IngestError(f"{path.name} resolves outside the docs folder ({real}); refusing to ingest it")

        raw = real.read_bytes()
        text = raw.decode("utf-8")
        m = _FRONT_MATTER.match(text)
        if not m:
            raise IngestError(f"{path.name}: missing front matter (--- doc_id, title, audience ---)")
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            raise IngestError(f"{path.name}: front matter is not valid YAML: {e}") from e
        if not isinstance(meta, dict):
            raise IngestError(f"{path.name}: front matter must be a mapping")

        audience = meta.get("audience")
        if audience != "public":  # fail closed: a missing or misspelt audience is refused, not defaulted
            raise InternalDocumentError(
                f"{path.name}: audience is {audience!r}, not 'public'. Staff-only documents belong in "
                f"tenants/{tenant_id}/internal/ and are never ingested for user answers."
            )
        doc_id, title = meta.get("doc_id"), meta.get("title")
        if not isinstance(doc_id, str) or not _DOC_ID.fullmatch(doc_id):
            raise IngestError(f"{path.name}: doc_id must be lowercase letters, digits and hyphens, got {doc_id!r}")
        if not isinstance(title, str) or not title.strip():
            raise IngestError(f"{path.name}: title is required")
        if doc_id in seen:
            raise IngestError(f"{path.name}: doc_id {doc_id!r} is already used by {seen[doc_id]}")
        seen[doc_id] = path.name
        reviewed = meta.get("last_reviewed")
        out.append(SourceDoc(
            doc_id=doc_id, title=title.strip(), audience="public",
            last_reviewed=reviewed if isinstance(reviewed, date) else None,
            body=text[m.end():], rel_path=f"docs/{path.name}",
            content_hash=hashlib.sha256(raw).hexdigest(),
        ))
    if not out:
        raise IngestError(f"No user documents found in {docs_dir}")
    return sorted(out, key=lambda d: d.doc_id)
