"""Priority (b): sandboxed code must never see .env, keypairs or any other host secret."""

from pathlib import Path

from scout.adapters.local.sandbox import SubprocessSandbox

PYTEST = ["-m", "pytest", "-q", "-p", "no:cacheprovider"]
SECRET = "sk-live-very-secret-value-42"

ESCAPES = """
import os, socket, subprocess, sys
from pathlib import Path

import pytest

PROJECT = Path(r"{project}")


def test_cannot_read_project_env():
    with pytest.raises(PermissionError):
        (PROJECT / ".env").read_text()


def test_cannot_read_keypair():
    with pytest.raises(PermissionError):
        open(PROJECT / "keys" / "escrow.keypair.json").read()


def test_cannot_list_the_project():
    with pytest.raises(PermissionError):
        os.listdir(PROJECT)


def test_cannot_write_outside_workdir():
    with pytest.raises(PermissionError):
        (PROJECT / "pwned.txt").write_text("x")


def test_cannot_spawn_processes():
    with pytest.raises(PermissionError):
        subprocess.run([sys.executable, "-c", "print(1)"])


def test_cannot_open_network():
    with pytest.raises(PermissionError):
        socket.create_connection(("example.com", 80), timeout=2)


def test_environment_is_scrubbed():
    assert "SCOUT_TEST_SECRET" not in os.environ
    assert not any("{secret}" in v for v in os.environ.values())
    assert "PATH" not in os.environ


def test_workdir_is_outside_the_project():
    assert not Path.cwd().resolve().is_relative_to(PROJECT.resolve())


def test_repo_env_files_are_not_copied():
    assert not Path(".env").exists()


def test_normal_code_still_works(tmp_path):
    (tmp_path / "ok.txt").write_text("fine")
    assert (tmp_path / "ok.txt").read_text() == "fine"
    import json
    assert json.loads("[1]") == [1]
"""


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    (project / "keys").mkdir(parents=True)
    (project / ".env").write_text(f"LLM_API_KEY={SECRET}\n")
    (project / "keys" / "escrow.keypair.json").write_text("[1,2,3]")
    repo = project / "repo"
    repo.mkdir()
    (repo / ".env").write_text(f"TOKEN={SECRET}\n")
    (repo / "test_escapes.py").write_text(ESCAPES.format(project=project, secret=SECRET))
    return project, repo


def test_untrusted_code_cannot_reach_host_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_TEST_SECRET", SECRET)
    _, repo = _project(tmp_path)
    r = SubprocessSandbox(60, [SECRET]).run(repo, PYTEST)
    assert r.exit_code == 0, r.output
    assert "10 passed" in r.output


def test_output_redacts_known_secrets(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "test_leak.py").write_text(f"def test_leak():\n    print({SECRET!r})\n    assert 0\n")
    out = SubprocessSandbox(60, [SECRET]).run(repo, PYTEST).output
    assert SECRET not in out and "[REDACTED]" in out


def test_timeout_and_path_escape(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "test_hang.py").write_text("def test_hang():\n    while True: pass\n")
    sbx = SubprocessSandbox(5)
    assert sbx.run(repo, PYTEST).timed_out
    assert sbx.run(repo, PYTEST, files={"../evil.py": "x"}).exit_code == 2
    assert not (tmp_path / "evil.py").exists()


def test_static_mode_runs_tools_without_the_guard(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "m.py").write_text("import os\n")
    r = SubprocessSandbox(60).run(
        repo,
        ["-m", "ruff", "check", "--isolated", "--output-format=concise", "."],
        execute_repo_code=False,
    )
    assert "F401" in r.output
