"""Layer 1 of the internal-document defence: the loader (app/rag/documents.py) only reads user docs."""

import hashlib

import pytest

from app.rag.documents import IngestError, InternalDocumentError, load_public_documents
from tests.rag.conftest import doc_text, make_tenant


def test_real_sample_docs_load_sorted_and_user_only():
    docs = load_public_documents("qspiders_sample")
    assert [d.doc_id for d in docs] == sorted(d.doc_id for d in docs) and len(docs) == 8
    assert all(d.audience =="public" and d.rel_path.startswith("docs/") for d in docs)
    assert all(len(d.content_hash) == 64 for d in docs)


def test_content_hash_is_the_sha256_of_the_file(tmp_path):
    root = make_tenant(tmp_path, "t", {"a.md": doc_text("a", "A", "Body text.")})
    (doc,) = load_public_documents("t", root)
    assert doc.content_hash == hashlib.sha256((root / "t" / "docs" / "a.md").read_bytes()).hexdigest()


@pytest.mark.parametrize("audience", ["internal", "staff", "User", "", None])
def test_anything_but_exactly_public_is_refused(tmp_path, audience):
    text = doc_text("a", "A", "Body.").replace("audience: public", f"audience: {audience}" if audience is not None else "")
    root = make_tenant(tmp_path, "t", {"a.md": text})
    with pytest.raises(InternalDocumentError):
        load_public_documents("t", root)


def test_one_internal_doc_aborts_the_whole_load_instead_of_being_skipped(tmp_path):
    root = make_tenant(tmp_path, "t", {"ok.md": doc_text("ok", "Ok", "Fine."),
                                      "bad.md": doc_text("bad", "Bad", "Secret.", audience="internal")})
    with pytest.raises(InternalDocumentError):
        load_public_documents("t", root)


def test_the_internal_folder_is_never_read(tmp_path):
    root = make_tenant(tmp_path, "t", {"a.md": doc_text("a", "A", "Body.")})
    (root / "t" / "internal").mkdir()
    (root / "t" / "internal" / "broken.md").write_text("not even front matter \x00 \xff", encoding="latin-1")
    assert [d.doc_id for d in load_public_documents("t", root)] == ["a"]  # a read would have raised


def test_symlink_from_docs_into_internal_is_refused(tmp_path):
    root = make_tenant(tmp_path, "t", {"a.md": doc_text("a", "A", "Body.")})
    internal = root / "t" / "internal"
    internal.mkdir()
    (internal / "secret.md").write_text(doc_text("secret", "Secret", "Staff only.", audience="public"))  # even if mislabelled
    (root / "t" / "docs" / "innocent.md").symlink_to(internal / "secret.md")
    with pytest.raises(InternalDocumentError, match="internal folder"):
        load_public_documents("t", root)


def test_symlink_pointing_outside_the_docs_folder_is_refused(tmp_path):
    root = make_tenant(tmp_path, "t", {"a.md": doc_text("a", "A", "Body.")})
    outside = tmp_path / "elsewhere.md"
    outside.write_text(doc_text("x", "X", "Body."))
    (root / "t" / "docs" / "link.md").symlink_to(outside)
    with pytest.raises(IngestError, match="outside the docs folder"):
        load_public_documents("t", root)


@pytest.mark.parametrize("bad_tenant", ["../x", "a/../b", "Acme", "", "a b"])
def test_tenant_id_cannot_escape_the_tenants_folder(bad_tenant):
    with pytest.raises(ValueError, match="Invalid tenant id"):
        load_public_documents(bad_tenant)


def test_non_markdown_files_are_ignored(tmp_path):
    root = make_tenant(tmp_path, "t", {"a.md": doc_text("a", "A", "Body."), "notes.txt": "not a doc", "img.png": "x"})
    assert [d.doc_id for d in load_public_documents("t", root)] == ["a"]


@pytest.mark.parametrize("text, message", [
    ("no front matter at all\n# Title\nBody", "missing front matter"),
    ("---\ndoc_id: [unclosed\n---\nBody", "not valid YAML"),
    ("---\n- a list\n---\nBody", "must be a mapping"),
    ("---\naudience: public\ntitle: T\n---\nBody", "doc_id must be"),
    ("---\ndoc_id: Bad_ID\naudience: public\ntitle: T\n---\nBody", "doc_id must be"),
    ("---\ndoc_id: ok\naudience: public\n---\nBody", "title is required"),
    ("---\ndoc_id: ok\naudience: public\ntitle: '  '\n---\nBody", "title is required"),
])
def test_malformed_documents_are_refused_with_a_clear_message(tmp_path, text, message):
    root = make_tenant(tmp_path, "t", {"a.md": text})
    with pytest.raises(IngestError, match=message):
        load_public_documents("t", root)


def test_duplicate_doc_ids_are_refused(tmp_path):
    root = make_tenant(tmp_path, "t", {"a.md": doc_text("same", "A", "x"), "b.md": doc_text("same", "B", "y")})
    with pytest.raises(IngestError, match="already used"):
        load_public_documents("t", root)


def test_a_tenant_with_no_documents_is_an_error_not_an_empty_success(tmp_path):
    root = make_tenant(tmp_path, "t", {})
    with pytest.raises(IngestError, match="No user documents"):
        load_public_documents("t", root)


def test_missing_docs_folder_is_an_error(tmp_path):
    root = make_tenant(tmp_path, "t", {"a.md": doc_text("a", "A", "x")})
    (root / "t" / "docs" / "a.md").unlink()
    (root / "t" / "docs").rmdir()
    with pytest.raises(IngestError, match="No docs folder"):
        load_public_documents("t", root)
