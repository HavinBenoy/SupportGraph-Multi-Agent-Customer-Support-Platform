"""Input rules for knowledge documents. Nothing the client sends is trusted: ids, titles, tags, text and file names are all checked here."""

import hashlib
import re

from app.config import Settings
from app.db.models import KB_CATEGORIES
from app.knowledge.errors import Invalid
from app.rag.chunking import DEFAULT_MAX_CHARS, chunk_markdown

DOC_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")              # the same shape the disk ingester accepts
TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")
MAX_TAGS = 10
ALLOWED_EXT = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}
ALLOWED_MIME = {"text/markdown", "text/plain"}
_FRONT_MATTER = re.compile(r"\A\s*---\r?\n")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FILENAME_OK = re.compile(r"[^A-Za-z0-9._ -]")


def doc_id(v) -> str:
    if not isinstance(v, str) or not DOC_ID.fullmatch(v):
        raise Invalid("doc_id must be lowercase letters, digits and hyphens (1-128 characters, starting with a letter or digit)")
    return v


def title(v) -> str:
    t = " ".join(str(v or "").split())
    if not t or len(t) > 300 or _CONTROL.search(t):
        raise Invalid("title is required (at most 300 characters)")
    return t


def category(v) -> str:
    if v not in KB_CATEGORIES:
        raise Invalid(f"category must be one of: {', '.join(KB_CATEGORIES)}")
    return v


def description(v) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    if len(t) > 1000 or _CONTROL.search(t):
        raise Invalid("description must be at most 1000 characters")
    return t or None


def tags(v) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > MAX_TAGS or not all(isinstance(t, str) and TAG.fullmatch(t) for t in v):
        raise Invalid(f"tags: at most {MAX_TAGS}, each lowercase letters, digits and hyphens (1-30 characters)")
    return sorted(set(v))


def change_note(v, *, required: bool, default: str = "") -> str:
    t = " ".join(str(v or "").split())
    if not t:
        if required:
            raise Invalid("change_note is required: say what changed and why (at most 500 characters)")
        t = default
    if len(t) > 500 or _CONTROL.search(t):
        raise Invalid("change_note must be at most 500 characters")
    return t


def source(filename, mime) -> tuple[str | None, str | None]:
    """A display name only. Directory parts are dropped, odd characters removed, and only text types are accepted; nothing is read from or written to disk."""
    if filename is None and mime is None:
        return None, None
    name = None
    ext_mime = None
    if filename is not None:
        base = str(filename).replace("\\", "/").rsplit("/", 1)[-1].strip()
        base = _FILENAME_OK.sub("", base).strip(" .")
        dot = base.rfind(".")
        ext = base[dot:].lower() if dot > 0 else ""
        if not base or len(base) > 100 or ext not in ALLOWED_EXT:
            raise Invalid("source_filename must be a .md, .markdown or .txt file name (letters, digits, dot, dash, underscore, space; at most 100 characters)")
        name, ext_mime = base, ALLOWED_EXT[ext]
    if mime is not None and mime not in ALLOWED_MIME:
        raise Invalid("source_mime must be text/markdown or text/plain")
    if mime and ext_mime and mime != ext_mime and not (mime == "text/plain" and ext_mime == "text/markdown"):
        raise Invalid("source_mime does not match the file name")
    return name, (mime or ext_mime)


def content(v, doc_title: str, s: Settings) -> tuple[str, int]:
    """Normalised body and its size in bytes. Refuses empty text, control characters, front matter, oversize text and text that would produce no
    passages or too many (so a version can never be created that cannot be indexed)."""
    if not isinstance(v, str):
        raise Invalid("content must be text")
    text = v.replace("\r\n", "\n").replace("\r", "\n")
    if _CONTROL.search(text):
        raise Invalid("content contains control characters")
    if not text.strip():
        raise Invalid("content is empty")
    if _FRONT_MATTER.match(text):
        raise Invalid("content must not start with front matter (---): the title, category and tags are separate fields")
    size = len(text.encode("utf-8"))
    if size > s.kb_max_bytes:
        raise Invalid(f"content is {size} bytes; the limit is {s.kb_max_bytes}")
    n = len(chunk_markdown(text, doc_title=doc_title, max_chars=DEFAULT_MAX_CHARS))
    if n == 0:
        raise Invalid("content has nothing to index after the title")
    if n > s.kb_max_chunks:
        raise Invalid(f"content would need {n} passages; the limit is {s.kb_max_chunks}")
    return text, size


def checksum(doc_title: str, body: str) -> str:
    return hashlib.sha256(f"{doc_title}\n\x00\n{body}".encode("utf-8")).hexdigest()
