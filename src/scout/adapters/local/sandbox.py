import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scout.ports import TestResult

# Loaded via PYTHONPATH in the child interpreter. Blocks sockets for Python code.
# ponytail: best-effort only (ctypes/subprocess can bypass); cloud mode needs OS-level isolation.
_GUARD = """
import socket
def _blocked(*a, **k):
    raise OSError("network access is disabled in the Forge sandbox")
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
"""

MAX_OUTPUT = 4000


class SubprocessSandbox:
    def __init__(self, timeout_seconds: int, secrets: list[str] | None = None) -> None:
        self.timeout = timeout_seconds
        self.secrets = [s for s in secrets or [] if len(s) >= 8]

    def _redact(self, out: str) -> str:
        # ponytail: exact-match only; encoded exfiltration gets through. Cloud mode needs a
        # sandbox that cannot read the host filesystem at all.
        for s in self.secrets:
            out = out.replace(s, "[REDACTED]")
        return out[-MAX_OUTPUT:]

    def run_tests(self, repo_dir: Path, files: dict[str, str]) -> TestResult:
        with tempfile.TemporaryDirectory(prefix="forge-sbx-") as tmp:
            work = Path(tmp) / "repo"
            guard = Path(tmp) / "guard"
            shutil.copytree(repo_dir, work)
            for rel, content in files.items():
                target = (work / rel).resolve()
                if not target.is_relative_to(work.resolve()):
                    return TestResult(2, f"refused path outside repo: {rel}", False)
                target.write_text(content, encoding="utf-8")
            guard.mkdir()
            (guard / "sitecustomize.py").write_text(_GUARD)
            env = {
                "PYTHONPATH": str(guard),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "TEMP": tmp,
                "TMP": tmp,
                "HOME": tmp,
            }
            if "SYSTEMROOT" in os.environ:  # required by Python on Windows
                env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
            cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short"]
            try:
                proc = subprocess.run(
                    cmd, cwd=work, env=env, capture_output=True, text=True, timeout=self.timeout
                )
            except subprocess.TimeoutExpired as e:
                out = (
                    (e.stdout or b"").decode(errors="replace")
                    if isinstance(e.stdout, bytes)
                    else (e.stdout or "")
                )
                return TestResult(124, self._redact(f"TIMEOUT after {self.timeout}s\n{out}"), True)
            return TestResult(proc.returncode, self._redact(proc.stdout + proc.stderr), False)
