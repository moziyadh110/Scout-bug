"""Deterministic scripted LLM for offline runs. Scores produced with it are labelled 'mock'.

Script: easy tasks are fixed on the first try. Hard tasks are fixed only when the prompt
carries extra signal (a previous failure or retrieved past fixes), so playbook changes
like more retries or memory change outcomes. This exercises the pipeline; it proves nothing
about real model quality.
"""

import json
import re
from pathlib import Path

from forge.ports import Completion

FILE_RE = re.compile(r"### FILE:\s*(\S+)")


class MockLLM:
    def __init__(self, benchmarks_dir: Path) -> None:
        self.benchmarks = benchmarks_dir

    def complete(self, model: str, system: str, user: str) -> Completion:
        if "## TASK: PLAN" in user:
            text = "1. Read the spec.\n2. Find the faulty line.\n3. Fix it minimally."
        elif "## TASK: CODE" in user:
            text = self._code(user)
        elif "## TASK: JUDGE" in user:
            text = "PASS"
        elif "## TASK: IMPROVE" in user:
            text = self._improve(user)
        else:
            text = ""
        return Completion(text, (len(user) + len(text)) // 4)

    def _code(self, user: str) -> str:
        code = user.split("## CODE", 1)[1]
        m = FILE_RE.search(code)
        if not m:
            return ""
        name = m.group(1).removesuffix(".py")
        task = self.benchmarks / name
        if not (task / "meta.json").exists():  # user-submitted bug: the mock has no script for it
            return "I could not find a fix."
        meta = json.loads((task / "meta.json").read_text())
        helped = "## PREVIOUS ATTEMPT FAILED" in user or "## SIMILAR PAST FIXES" in user
        src = task / ("fix" if meta["difficulty"] == "easy" or helped else "repo") / f"{name}.py"
        return f"### FILE: {name}.py\n```python\n{src.read_text()}```"

    def _improve(self, user: str) -> str:
        cur = json.loads(user.split("## CURRENT PLAYBOOK", 1)[1].split("## FAILURES", 1)[0])
        return json.dumps(
            [
                {
                    "max_retries": min(cur["max_retries"] + 1, 4),
                    "rationale": "Failures were fixable with test feedback; allow one more retry.",
                },
                {
                    "use_memory": True,
                    "memory_k": 2,
                    "rationale": "Retrieve similar past fixes as examples.",
                },
                {
                    "strategy": "Change as little code as possible.",
                    "rationale": "Keep patches minimal.",
                },
            ]
        )
