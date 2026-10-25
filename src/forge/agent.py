"""planner -> coder -> sandbox -> (retry) -> judge."""

import difflib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.config import Role, Settings
from forge.models import Attempt, Playbook, PromptRecord, Run, RunStatus, now
from forge.ports import LLMProvider, Memory, Sandbox

Emit = Callable[[str, dict[str, Any]], None]

FILE_BLOCK = re.compile(r"### FILE:\s*(\S+)\s*\n```[a-z]*\n(.*?)```", re.S)
THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class Task:
    name: str
    issue: str
    repo_dir: Path
    difficulty: str
    split: str

    @property
    def source_files(self) -> dict[str, str]:
        """Editable files: every non-test .py file in the repo."""
        return {
            p.name: p.read_text(encoding="utf-8")
            for p in sorted(self.repo_dir.glob("*.py"))
            if not p.name.startswith("test_")
        }


def load_task(benchmarks_dir: Path, name: str) -> Task:
    d = benchmarks_dir / name
    meta = json.loads((d / "meta.json").read_text())
    issue = (d / "issue.md").read_text(encoding="utf-8")
    return Task(name, issue, d / "repo", meta["difficulty"], meta["split"])


def load_tasks(benchmarks_dir: Path) -> list[Task]:
    return [
        load_task(benchmarks_dir, p.parent.name) for p in sorted(benchmarks_dir.glob("*/meta.json"))
    ]


def _files_block(files: dict[str, str]) -> str:
    return "\n".join(f"### FILE: {n}\n```python\n{c}```" for n, c in files.items())


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


def parse_files(text: str, allowed: set[str]) -> dict[str, str]:
    """Extract whole-file edits. Only existing non-test files may be changed."""
    files = {}
    for name, body in FILE_BLOCK.findall(text):
        if name in allowed:
            files[name] = body if body.endswith("\n") else body + "\n"
    return files


class Agent:
    def __init__(self, llm: LLMProvider, sandbox: Sandbox, memory: Memory, settings: Settings):
        self.llm, self.sandbox, self.memory, self.settings = llm, sandbox, memory, settings

    def _ask(self, run: Run, pb: Playbook, role: Role, user: str) -> str:
        model = pb.model_roles.get(role) or self.settings.model_for(role)
        system = pb.system_prompts.get(role, "")
        c = self.llm.complete(model, system, user)
        text = THINK.sub("", c.text).strip()
        run.prompts.append(
            PromptRecord(
                role=role, model=model, system=system, user=user, response=text, tokens=c.tokens
            )
        )
        run.tokens += c.tokens
        return text

    def solve(
        self,
        task: Task,
        pb: Playbook,
        pb_hash: str,
        version: int | None = None,
        emit: Emit = lambda kind, data: None,
        run_id: str | None = None,
    ) -> Run:
        run = Run(
            id=run_id or uuid.uuid4().hex[:12],
            task_id=task.name,
            issue=task.issue,
            playbook_hash=pb_hash,
            playbook_version=version,
        )
        src = task.source_files

        def status(s: RunStatus, **data: Any) -> None:
            run.status = s
            emit("status", {"status": s.value, **data})

        examples = ""
        if pb.use_memory:
            run.retrieved = self.memory.search(
                task.issue + "\n" + "\n".join(src.values()), pb.memory_k, exclude_task=task.name
            )
            if run.retrieved:
                examples = "## SIMILAR PAST FIXES\n" + "\n\n".join(run.retrieved) + "\n\n"
                emit("memory", {"examples": run.retrieved})

        status(RunStatus.PLANNING)
        run.plan = self._ask(
            run,
            pb,
            "planner",
            f"## TASK: PLAN\n## ISSUE\n{task.issue}\n## CODE\n{_files_block(src)}\n"
            "List the concrete steps to fix the bug. Be brief.",
        )
        emit("plan", {"plan": run.plan})

        feedback = ""
        for n in range(pb.max_retries + 1):
            status(RunStatus.CODING, attempt=n + 1)
            reply = self._ask(
                run,
                pb,
                "coder",
                f"## TASK: CODE\n## ISSUE\n{task.issue}\n## PLAN\n{run.plan}\n"
                f"## STRATEGY\n{pb.strategy}\n{examples}{feedback}## CODE\n{_files_block(src)}\n"
                "Return every file you change in full, using exactly this format:\n"
                "### FILE: <name>\n```python\n<entire file>\n```\nTest files cannot be changed.",
            )
            files = parse_files(reply, set(src))
            diff = _diff(src, files)
            emit("patch", {"attempt": n + 1, "diff": diff or "(no valid file edits returned)"})

            status(RunStatus.TESTING, attempt=n + 1)
            result = self.sandbox.run_tests(task.repo_dir, files)
            attempt = Attempt(
                n=n + 1,
                files=files,
                diff=diff,
                test_exit_code=result.exit_code,
                test_output=result.output,
                timed_out=result.timed_out,
            )
            run.attempts.append(attempt)
            emit("tests", {"attempt": n + 1, "passed": result.passed, "output": result.output})

            if result.passed:
                status(RunStatus.JUDGING, attempt=n + 1)
                verdict = self._ask(
                    run,
                    pb,
                    "judge",
                    f"## TASK: JUDGE\n## ISSUE\n{task.issue}\n## DIFF\n{diff}\n"
                    f"## TEST OUTPUT\n{result.output[-1500:]}\n"
                    "Does the diff genuinely fix the issue (not special-casing tests)? "
                    "Answer PASS or FAIL: <reason> on the first line.",
                )
                attempt.judge_verdict = verdict.splitlines()[0] if verdict else "FAIL: empty"
                emit("judge", {"verdict": attempt.judge_verdict})
                if attempt.judge_verdict.upper().startswith("PASS"):
                    run.status, run.finished_at = RunStatus.PASSED, now()
                    emit("done", {"status": "passed"})
                    return run
                feedback = f"## PREVIOUS ATTEMPT FAILED REVIEW\n{attempt.judge_verdict}\n\n"
            else:
                feedback = (
                    f"## PREVIOUS ATTEMPT FAILED\n### DIFF\n{diff}\n"
                    f"### TEST OUTPUT\n{result.output[-2000:]}\n\n"
                )

        run.status, run.finished_at = RunStatus.FAILED, now()
        emit("done", {"status": "failed"})
        return run


def memory_example(run: Run) -> str:
    """How a passed run is stored in memory for future retrieval."""
    last = run.attempts[-1]
    return f"Issue: {run.issue.splitlines()[0]}\nFix:\n{last.diff}"
