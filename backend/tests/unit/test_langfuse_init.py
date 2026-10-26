"""The secrets generator: safe by default, never prints secrets, never rotates silently."""

import re
import stat

from scripts import langfuse_init as li


def _read_env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_generates_strong_unique_secrets_with_valid_shapes():
    a, b = li.generate_server_env(), li.generate_server_env()
    assert re.fullmatch(r"[0-9a-f]{64}", a["ENCRYPTION_KEY"])  # Langfuse requires 64 hex chars
    assert a["SALT"] != "mysalt" and a["NEXTAUTH_SECRET"] != "mysecret"
    assert a["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"].startswith("pk-lf-")
    assert a["LANGFUSE_INIT_PROJECT_SECRET_KEY"].startswith("sk-lf-")
    assert a["ENCRYPTION_KEY"] != b["ENCRYPTION_KEY"]  # fresh per install, not hard-coded
    assert re.fullmatch(r"[0-9a-f]+", a["LANGFUSE_PG_PASSWORD"])  # URL-safe: it goes inside DATABASE_URL


def test_init_writes_private_files_and_prints_no_secret(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("SG_LLM_PROVIDER=fake\nSG_TRACING_ENABLED=false\n")
    assert li.init(repo_root=tmp_path) == 0
    out = capsys.readouterr().out

    server = _read_env(tmp_path / "infra/langfuse/.env")
    app = _read_env(tmp_path / ".env")
    for path in (tmp_path / "infra/langfuse/.env", tmp_path / ".env"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert app["LANGFUSE_PUBLIC_KEY"] == server["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"]
    assert app["LANGFUSE_SECRET_KEY"] == server["LANGFUSE_INIT_PROJECT_SECRET_KEY"]
    assert app["SG_TRACING_ENABLED"] == "true"
    assert app["SG_LLM_PROVIDER"] == "fake"  # existing settings preserved

    non_secret = {"LANGFUSE_WEB_PORT", "LANGFUSE_INIT_USER_EMAIL"}  # the email is shown on purpose (login name)
    secrets_ = {k: v for k, v in server.items() if k not in non_secret}
    assert len(secrets_) >= 9
    for name, value in secrets_.items():
        assert value not in out, f"{name} leaked to stdout"


def test_init_refuses_to_overwrite_existing_secrets(tmp_path):
    assert li.init(repo_root=tmp_path) == 0
    before = (tmp_path / "infra/langfuse/.env").read_text()
    assert li.init(repo_root=tmp_path) == 1
    assert (tmp_path / "infra/langfuse/.env").read_text() == before


def test_upsert_env_replaces_and_appends_without_losing_other_lines(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nA=1\nSG_TRACING_ENABLED=false\n")
    li.upsert_env(f, {"SG_TRACING_ENABLED": "true", "NEW": "x"})
    assert f.read_text() == "# comment\nA=1\nSG_TRACING_ENABLED=true\nNEW=x\n"
