"""Algorithm 1, step 5: implicit gaps found by tools run in the sandbox.

Static tools read the code without running it. Only pytest executes repo code, and it runs
under the sandbox's audit hook.
"""

import importlib.util
import json
import re
from pathlib import Path

from scout.models import Gap
from scout.ports import RunResult, Sandbox
from scout.skills import skills_in

PYTEST = ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header"]
BUILD_CHECK = """
import ast, pathlib, sys
bad = []
for p in sorted(pathlib.Path('.').rglob('*.py')):
    try:
        ast.parse(p.read_text(encoding='utf-8'), str(p))
    except (SyntaxError, UnicodeDecodeError) as e:
        bad.append(f'{p}: {e}')
print('\\n'.join(bad))
sys.exit(1 if bad else 0)
"""
TODO = re.compile(r"#\s*(TODO|FIXME)\b[:\s]*(.*)")
MYPY_LINE = re.compile(r"^(.+?):(\d+): error: (.+?)(?:\s+\[([\w-]+)\])?$")
# Tools that need a dependency nobody approved yet. They report as skipped, never as clean.
PENDING_TOOLS = {"coverage": "coverage", "interrogate": "interrogate", "pip-audit": "pip_audit"}


def builds(sandbox: Sandbox, src: Path) -> tuple[bool, str]:
    r = sandbox.run(src, ["-c", BUILD_CHECK], execute_repo_code=False)
    return r.exit_code == 0, r.output.strip()[:500]


def run_test_suite(sandbox: Sandbox, src: Path) -> RunResult:
    """Exit 0 (all pass) or 1 (some fail) means the suite runs; anything else means it can't."""
    return sandbox.run(src, PYTEST)


def packages(src: Path) -> list[str]:
    return sorted(p.parent.name for p in src.glob("*/__init__.py") if p.parent.name != "tests")


def ruff_gaps(sandbox: Sandbox, src: Path, repo: str) -> list[Gap]:
    r = sandbox.run(
        src,
        ["-m", "ruff", "check", "--isolated", "--output-format=json", "--exit-zero", "."],
        execute_repo_code=False,
    )
    try:
        findings = json.loads(r.output[r.output.index("[") :])
    except ValueError:
        return []
    if not findings:
        return []
    codes = sorted({f["code"] for f in findings if f.get("code")})
    detail = "\n".join(
        f"{Path(f['filename']).name}:{f['location']['row']} {f['code']} {f['message']}"
        for f in findings[:10]
    )
    return [
        Gap(
            id=f"{repo}:ruff",
            repo=repo,
            kind="implicit",
            source="ruff",
            title=f"Fix {len(findings)} lint finding(s) ({', '.join(codes[:4])})",
            detail=detail,
            skills=["refactoring"],
            size="S" if len(findings) <= 10 else "M",
            clarity=4,
        )
    ]


def mypy_gaps(sandbox: Sandbox, src: Path, repo: str) -> list[Gap]:
    pkgs = packages(src)
    if not pkgs:
        return []
    # An empty config on purpose: a repo's own mypy config could load plugins (code).
    r = sandbox.run(
        src,
        [
            "-m",
            "mypy",
            "--config-file",
            ".scout-mypy.ini",
            "--ignore-missing-imports",
            "--no-error-summary",
            "--show-error-codes",
            *pkgs,
        ],
        files={".scout-mypy.ini": "[mypy]\n"},
        execute_repo_code=False,
    )
    errors = [m for line in r.output.splitlines() if (m := MYPY_LINE.match(line.strip()))]
    if not errors:
        return []
    detail = "\n".join(f"{Path(m[1]).name}:{m[2]} {m[3]}" for m in errors[:10])
    return [
        Gap(
            id=f"{repo}:mypy",
            repo=repo,
            kind="implicit",
            source="mypy",
            title=f"Fix {len(errors)} type error(s) reported by mypy",
            detail=detail,
            skills=["typing"],
            size="S" if len(errors) <= 5 else "M",
            clarity=4,
            likely_files=sorted({m[1].replace("\\", "/") for m in errors}),
        )
    ]


def todo_gaps(src: Path, repo: str, limit: int = 5) -> list[Gap]:
    """Plain text scan on the host: reading files is safe, nothing is executed."""
    out = []
    for p in sorted(src.rglob("*.py")):
        rel = p.relative_to(src).as_posix()
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if m := TODO.search(line):
                text = m.group(2).strip() or m.group(1)
                out.append(
                    Gap(
                        id=f"{repo}:todo:{rel}:{n}",
                        repo=repo,
                        kind="implicit",
                        source="todo",
                        title=f"{m.group(1)} in {rel}: {text}"[:120],
                        detail=f"{rel}:{n}: {line.strip()}",
                        skills=skills_in(text) or ["refactoring"],
                        size="S",
                        clarity=3,
                        likely_files=[rel],
                    )
                )
    return out[:limit]


def tool_status() -> dict[str, str]:
    status = {"build-check": "ok", "ruff": "ok", "mypy": "ok", "todo": "ok"}
    for name, module in PENDING_TOOLS.items():
        if importlib.util.find_spec(module) is None:
            status[name] = "skipped: not installed (awaiting dependency approval)"
    return status


def implicit_gaps(sandbox: Sandbox, src: Path, repo: str) -> list[Gap]:
    return ruff_gaps(sandbox, src, repo) + mypy_gaps(sandbox, src, repo) + todo_gaps(src, repo)
