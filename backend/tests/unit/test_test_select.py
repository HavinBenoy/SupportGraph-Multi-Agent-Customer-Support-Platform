"""The change-scope test selector: never selects less than it should, unknown backend files mean the full suite."""

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("test_select", Path(__file__).resolve().parents[2] / "scripts" / "test_select.py")
ts = importlib.util.module_from_spec(spec)
sys.modules["test_select"] = ts
spec.loader.exec_module(ts)


def plan(*files):
    return ts.plan_for(list(files))


def test_docs_only_runs_nothing():
    p = plan("docs/BENCHMARKS.md", "CHANGES.md")
    assert not p.paths and not p.full and ts.commands(p) == []


def test_faq_prompt_runs_rag_and_bench_and_hints_real_model():
    p = plan("backend/app/rag/prompts.py")
    assert {"tests/rag", "tests/bench"} <= p.paths and p.real_model and not p.postgres and not p.frontend


def test_models_and_migrations_need_postgres():
    assert plan("backend/app/db/models.py").postgres and plan("backend/app/db/migrations/versions/0006_x.py").postgres


def test_auth_and_api_run_security_and_api_tests():
    assert {"tests/api", "tests/security"} <= plan("backend/app/auth/tokens.py").paths


def test_frontend_change_runs_frontend_only():
    p = plan("frontend/src/app/page.tsx")
    assert p.frontend and not p.paths and "make frontend-build" in " ".join(ts.commands(p))


def test_unknown_backend_file_and_shared_infra_fall_back_to_full():
    assert plan("backend/app/brand_new/thing.py").full and plan("backend/app/brand_new/thing.py").paths == {"tests"}
    assert plan("backend/app/config.py").full and plan("Makefile").full


def test_changed_test_file_runs_itself_and_union_is_taken():
    p = plan("backend/tests/rag/test_faq_graph.py", "backend/app/qa/critic.py")
    assert "tests/rag/test_faq_graph.py" in p.paths and "tests/step4" in p.paths


def test_every_rule_points_at_existing_tests():
    root = Path(__file__).resolve().parents[2]
    for prefix, paths, *_ in ts.RULES:
        for t in paths:
            assert (root / t).exists(), f"rule {prefix} references missing {t}"


def test_command_for_plan_is_a_single_pytest_invocation():
    cmds = ts.commands(plan("backend/app/bench/report.py"))
    assert len(cmds) == 1 and cmds[0].endswith("tests/bench") and "pytest" in cmds[0]
