"""The demo CLI must give an actionable message, not a traceback, when the database is not ready."""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def test_demo_explains_how_to_fix_an_unreachable_database():
    env = {**os.environ, "SG_DATABASE_URL": "postgresql+psycopg://x:y@127.0.0.1:9/z",
           "SG_TRACING_ENABLED": "false", "SG_LLM_PROVIDER": "fake"}
    r = subprocess.run([sys.executable, "scripts/run_agent.py", "Where is my order A1001?"],
                       cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "make db-up db-setup" in r.stderr
    assert "Traceback" not in r.stderr
