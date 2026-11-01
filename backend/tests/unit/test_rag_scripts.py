"""The RAG CLIs must give an actionable message, not a traceback, when the database is not ready."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ENV = {**os.environ, "SG_DATABASE_URL": "postgresql+psycopg://x:y@127.0.0.1:9/z", "SG_TRACING_ENABLED": "false",
       "SG_LLM_PROVIDER": "fake", "SG_EMBEDDING_PROVIDER": "fake"}


@pytest.mark.parametrize("args", [["scripts/ingest_docs.py"], ["scripts/ask_faq.py", "What is the fee?"], ["scripts/eval_rag.py"]])
def test_unreachable_database_is_explained(args):
    r = subprocess.run([sys.executable, *args], cwd=BACKEND, env=ENV, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2
    assert "make db-up db-setup" in r.stderr and "Traceback" not in r.stderr
