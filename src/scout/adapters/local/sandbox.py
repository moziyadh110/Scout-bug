"""Local sandbox: a private copy of the repo, a scrubbed environment, a workdir outside the
project, a timeout, and (when repo code runs) a Python audit hook that denies file access
outside the sandbox, process spawning and network.

ponytail: Python-level isolation only. Native code (ctypes, C extensions) can bypass the
audit hook. Cloud mode must use an OS-level sandbox (Harness Runtime or a MicroVM).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scout.ports import RunResult

MAX_OUTPUT = 6000

# Installed via PYTHONPATH as sitecustomize, before any repo code is imported.
_GUARD = r"""
import os, sys

def _norm(p):
    return os.path.normcase(os.path.realpath(p))

_READ = [_norm(p) for p in os.environ.pop("SCOUT_SBX_READ").split(os.pathsep)]
_WRITE = [_norm(os.environ.pop("SCOUT_SBX_WRITE"))]
_DENY = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
         "os.startfile", "os.fork", "socket.connect", "socket.getaddrinfo", "socket.bind",
         "socket.gethostbyname", "socket.sendto"}
_NULL = {os.devnull.lower(), "nul", "/dev/null", "\\\\.\\nul"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

def _inside(path, roots):
    p = _norm(path)
    return any(p == r or p.startswith(r + os.sep) for r in roots)

def _hook(event, args):
    if event == "open":
        path, mode, flags = args
        if path is None or isinstance(path, int):
            return
        path = os.fsdecode(path)
        if path.lower() in _NULL or _norm(path).lower() in _NULL:
            return
        if isinstance(mode, str):
            writing = any(c in mode for c in "wax+")
        else:
            writing = bool(flags & _WRITE_FLAGS)
        if not _inside(path, _WRITE if writing else _READ):
            raise PermissionError(f"sandbox: {'write' if writing else 'read'} denied: {path}")
    elif event in ("os.listdir", "os.scandir") and args and args[0] not in (None, ""):
        if not isinstance(args[0], int) and not _inside(os.fsdecode(args[0]), _READ):
            raise PermissionError(f"sandbox: listing denied: {args[0]}")
    elif event in _DENY:
        raise PermissionError(f"sandbox: {event} is not allowed")

sys.addaudithook(_hook)
"""


class SubprocessSandbox:
    def __init__(self, timeout_seconds: int, secrets: list[str] | None = None) -> None:
        self.timeout = timeout_seconds
        self.secrets = [s for s in secrets or [] if len(s) >= 8]

    def _redact(self, out: str) -> str:
        # ponytail: exact-match only, a second line of defence behind the audit hook.
        for s in self.secrets:
            out = out.replace(s, "[REDACTED]")
        return out[-MAX_OUTPUT:]

    def run(
        self,
        repo_dir: Path,
        args: list[str],
        files: dict[str, str] | None = None,
        execute_repo_code: bool = True,
    ) -> RunResult:
        base = Path(tempfile.mkdtemp(prefix="scout-sbx-")).resolve()
        try:
            if base.is_relative_to(Path.cwd().resolve()):
                raise RuntimeError("sandbox workdir must be outside the project")
            work, guard, tmp = base / "repo", base / "guard", base / "tmp"
            shutil.copytree(repo_dir, work, ignore=shutil.ignore_patterns(".git", ".env*"))
            for rel, content in (files or {}).items():
                target = (work / rel).resolve()
                if not target.is_relative_to(work):
                    return RunResult(2, f"refused path outside repo: {rel}", False)
                target.write_text(content, encoding="utf-8")
            guard.mkdir()
            tmp.mkdir()
            env = {  # nothing is inherited from the host environment
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONHASHSEED": "0",
                "PYTHONIOENCODING": "utf-8",
                "TEMP": str(tmp),
                "TMP": str(tmp),
                "TMPDIR": str(tmp),
                "HOME": str(tmp),
                "USERPROFILE": str(tmp),
            }
            if "SYSTEMROOT" in os.environ:  # Windows needs it to start Python at all
                env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
            if execute_repo_code:
                (guard / "sitecustomize.py").write_text(_GUARD)
                read_roots = [str(base), sys.prefix, sys.base_prefix]
                env |= {
                    "PYTHONPATH": str(guard),
                    "SCOUT_SBX_READ": os.pathsep.join(read_roots),
                    "SCOUT_SBX_WRITE": str(base),
                }
            cmd = [sys.executable, *args]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as e:
                out = (
                    e.stdout
                    if isinstance(e.stdout, str)
                    else (e.stdout or b"").decode(errors="replace")
                )
                return RunResult(124, self._redact(f"TIMEOUT after {self.timeout}s\n{out}"), True)
            return RunResult(proc.returncode, self._redact(proc.stdout + proc.stderr), False)
        finally:
            shutil.rmtree(base, ignore_errors=True)
