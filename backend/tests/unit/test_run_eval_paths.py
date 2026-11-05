import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_eval.py"


def _mod():
    spec = importlib.util.spec_from_file_location("run_eval_script", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_repo_relative_reference_resolves_from_backend_dir(monkeypatch):
    m = _mod()
    monkeypatch.chdir(Path(__file__).resolve().parents[2])  # backend/, where `make eval` runs
    p = m.resolve_repo_path(Path("evals/reports/reference_fake_v1.json"))
    assert p.is_absolute() and p.exists()


def test_absolute_path_is_unchanged(tmp_path):
    m = _mod()
    assert m.resolve_repo_path(tmp_path / "x.json") == tmp_path / "x.json"
