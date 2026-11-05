"""Pick the tests that matter for what you changed. `make test-targeted` runs this.

    python scripts/test_select.py                 # plan for `git diff HEAD` + untracked files, nothing is run
    python scripts/test_select.py --run           # run the plan (backend pytest, then Postgres/frontend if the plan says so)
    python scripts/test_select.py --files a b     # plan for explicit paths (relative to the project root)

Rules are first-match-wins per file and the plan is the UNION over all changed files. A backend file this script does not
know about falls back to the FULL backend suite: unknown means run more, never less. Docs-only changes run nothing.
This is a speed-up for the development loop, not a substitute for `make test-full` before you hand work over.
It cannot tell whether a prompt/model change is good: for that run `make bench-quick` (real model).
"""

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FULL = "FULL"

# (path prefix or exact path, backend test paths, postgres?, frontend?, real-model hint?)
RULES: list[tuple[str, list[str], bool, bool, bool]] = [
    ("frontend/", [], False, True, False),
    ("docs/", [], False, False, False),
    ("CHANGES.md", [], False, False, False),
    ("README.md", [], False, False, False),
    ("backend/app/rag/prompts.py", ["tests/rag", "tests/bench", "tests/test_eval_regression.py"], False, False, True),
    ("backend/app/rag/faq.py", ["tests/rag", "tests/bench", "tests/graph", "tests/test_eval_regression.py"], False, False, True),
    ("backend/app/rag/", ["tests/rag", "tests/graph", "tests/test_eval_regression.py"], False, False, True),
    ("backend/app/agents/", ["tests/unit/test_triage.py", "tests/unit/test_router.py", "tests/graph", "tests/test_eval_regression.py"], False, False, True),
    ("backend/app/guardrails/", ["tests/security", "tests/api", "tests/step4", "tests/unit/test_escalation_floor.py", "tests/graph"], False, False, False),
    ("backend/app/auth/", ["tests/api", "tests/security"], False, False, False),
    ("backend/app/api/", ["tests/api", "tests/security"], False, False, False),
    ("backend/app/db/models.py", ["tests/unit/test_db_store.py", "tests/rag", "tests/step4", "tests/api"], True, False, False),
    ("backend/app/db/migrations/", ["tests/unit/test_db_store.py", "tests/step4"], True, False, False),
    ("backend/app/db/", ["tests/unit/test_db_store.py", "tests/rag", "tests/step4", "tests/api"], True, False, False),
    ("backend/app/graph/", ["tests/graph", "tests/step4", "tests/unit/test_router.py", "tests/unit/test_graph_evaluate.py", "tests/test_eval_regression.py"], False, False, True),
    ("backend/app/actions/", ["tests/step4", "tests/api"], False, False, False),
    ("backend/app/escalation/", ["tests/step4", "tests/api"], False, False, False),
    ("backend/app/qa/", ["tests/step4", "tests/graph"], False, False, True),
    ("backend/app/llm/", ["tests/unit/test_llm_gateway_hardening.py", "tests/graph/test_model_unavailable.py", "tests/rag", "tests/bench"], False, False, True),
    ("backend/app/bench/", ["tests/bench"], False, False, False),
    ("backend/scripts/bench.py", ["tests/bench"], False, False, False),
    ("backend/app/evals/", ["tests/unit/test_evals_harness.py", "tests/unit/test_run_eval_paths.py", "tests/test_eval_regression.py"], False, False, False),
    ("backend/app/voice/", ["tests/unit"], False, False, False),
]
FULL_TRIGGERS = ("backend/app/config.py", "backend/app/main.py", "backend/app/tenants/", "backend/app/observability/", "backend/app/tools/",
                 "backend/pyproject.toml", "backend/tests/conftest.py", "Makefile", ".env.example", "docker-compose.yml")


@dataclass
class Plan:
    paths: set[str] = field(default_factory=set)
    full: bool = False
    postgres: bool = False
    frontend: bool = False
    real_model: bool = False
    reasons: list[str] = field(default_factory=list)


def plan_for(files: list[str]) -> Plan:
    p = Plan()
    for raw in files:
        f = raw.strip().lstrip("./")
        if not f:
            continue
        if f.startswith("backend/tests/"):
            rel = f.removeprefix("backend/")
            if rel.endswith("conftest.py"):
                p.paths.add(str(Path(rel).parent) or "tests")  # a conftest affects its whole directory
                if rel == "tests/conftest.py":
                    p.full = True
            else:
                p.paths.add(rel)
            p.reasons.append(f"{f}: the changed test itself")
            continue
        if any(f == t or f.startswith(t) for t in FULL_TRIGGERS):
            p.full = True
            p.postgres = p.postgres or f.endswith(("pyproject.toml", "docker-compose.yml"))
            p.reasons.append(f"{f}: shared infrastructure -> full backend suite")
            continue
        for prefix, paths, pg, fe, rm in RULES:
            if f == prefix or f.startswith(prefix):
                p.paths.update(paths)
                p.postgres |= pg
                p.frontend |= fe
                p.real_model |= rm
                p.reasons.append(f"{f}: {prefix}")
                break
        else:
            if f.startswith("backend/"):
                p.full = True
                p.reasons.append(f"{f}: not covered by a rule -> full backend suite")
            else:
                p.reasons.append(f"{f}: outside the code (no tests)")
    if p.full:
        p.paths = {"tests"}
    return p


def commands(p: Plan) -> list[str]:
    cmds = []
    if p.paths:
        cmds.append("cd backend && ../.venv/bin/python -m pytest -q " + " ".join(shlex.quote(x) for x in sorted(p.paths)))
    if p.postgres:
        cmds.append("make test-pg   # needs: make db-up")
    if p.frontend:
        cmds.append("make frontend-test && make frontend-build")
    if p.real_model:
        cmds.append("# prompt/model-facing code changed: tests above use fakes only. Check real behaviour with: make bench-quick")
    return cmds


def changed_files() -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line[3:].split(" -> ")[-1] for line in out.splitlines() if line.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", help="explicit changed paths relative to the project root")
    ap.add_argument("--run", action="store_true", help="run the plan instead of only printing it")
    a = ap.parse_args(argv)
    try:
        files = a.files if a.files is not None else changed_files()
    except (OSError, subprocess.CalledProcessError):
        print("Not a git checkout (or git missing): pass --files, or run `make test-fast`.", file=sys.stderr)
        return 2
    p = plan_for(files)
    if not files:
        print("No changed files found: nothing to run. (`make test-fast` runs the whole backend suite.)")
        return 0
    print("Changed:", ", ".join(files))
    for r in p.reasons:
        print("  -", r)
    cmds = commands(p)
    print("\nPlan:" if cmds else "\nPlan: no tests needed for these files.")
    for c in cmds:
        print("  ", c)
    if not a.run:
        return 0
    rc = 0
    for c in cmds:
        if c.startswith("#"):
            continue
        shell_cmd = c.split("   #")[0]
        print(f"\n$ {shell_cmd}")
        rc = subprocess.run(shell_cmd, shell=True, cwd=ROOT).returncode or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
