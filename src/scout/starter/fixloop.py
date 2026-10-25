"""planner -> coder -> sandbox -> retry, producing a draft patch for a human to review."""

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from scout.config import Role, Settings
from scout.ports import LLMProvider, Sandbox
from scout.prompts import system, untrusted

# Tolerates "## FILE: `x.py`" and fences like ```py or ```python3 from real models.
FILE_BLOCK = re.compile(r"#+\s*FILE:\s*[`'\"]?([\w./-]+?)[`'\"]?\s*\n```[^\n]*\n(.*?)```", re.S)
THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class Attempt:
    n: int
    diff: str
    passed: bool
    test_output: str


@dataclass
class Draft:
    plan: str = ""
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].passed


def source_files(repo_dir: Path) -> dict[str, str]:
    """Editable files: every non-test .py file at the repo root."""
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(repo_dir.glob("*.py"))
        if not p.name.startswith("test_")
    }


def parse_files(text: str, allowed: set[str]) -> dict[str, str]:
    """Extract whole-file edits. Only existing non-test files may be changed."""
    return {
        name: body if body.endswith("\n") else body + "\n"
        for name, body in FILE_BLOCK.findall(text)
        if name in allowed
    }


def _diff(old: dict[str, str], new: dict[str, str]) -> str:
    out: list[str] = []
    for name, content in new.items():
        out += difflib.unified_diff(
            old.get(name, "").splitlines(keepends=True),
            content.splitlines(keepends=True),
            f"a/{name}",
            f"b/{name}",
        )
    return "".join(out)


def _code(files: dict[str, str]) -> str:
    return "\n".join(untrusted(f"FILE {n}", c) for n, c in files.items())


def draft_patch(
    llm: LLMProvider,
    sandbox: Sandbox,
    settings: Settings,
    repo_dir: Path,
    issue: str,
    max_retries: int = 1,
) -> Draft:
    if not settings.enable_starter_patch:
        raise RuntimeError("starter patches are disabled (SCOUT_ENABLE_STARTER_PATCH=false)")
    src, draft = source_files(repo_dir), Draft()

    def ask(role: Role, user: str) -> str:
        c = llm.complete(settings.model_for(role), system(""), user)
        return THINK.sub("", c.text).strip()

    draft.plan = ask(
        "planner",
        f"## TASK: PLAN\n{untrusted('ISSUE', issue)}\n{_code(src)}\n"
        "List the concrete steps to fix the bug. Be brief.",
    )
    feedback = ""
    for n in range(max_retries + 1):
        reply = ask(
            "coder",
            f"## TASK: CODE\n{untrusted('ISSUE', issue)}\n## PLAN\n{draft.plan}\n"
            f"{feedback}{_code(src)}\nReturn every changed file in full as:\n"
            "### FILE: <name>\n```python\n<entire file>\n```",
        )
        files = parse_files(reply, set(src))
        result = sandbox.run(repo_dir, ["-m", "pytest", "-q", "-p", "no:cacheprovider"], files)
        attempt = Attempt(n + 1, _diff(src, files), result.passed, result.output)
        draft.attempts.append(attempt)
        if attempt.passed:
            break
        feedback = (
            f"## PREVIOUS ATTEMPT FAILED\n{untrusted('TEST OUTPUT', result.output[-2000:])}\n"
        )
    return draft
