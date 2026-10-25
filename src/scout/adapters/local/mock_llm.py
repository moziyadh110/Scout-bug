"""Deterministic offline LLM. Anything scored with it is labelled 'mock'.

Each task is answered by a transparent heuristic, so local runs exercise the real pipeline
(prompts, parsing, ranking, backtests) without pretending to measure model quality.
"""

import json
import re

from scout.ports import Completion
from scout.skills import skills_in

BLOCK = re.compile(r"<<<UNTRUSTED (.+?) ([0-9a-f]{12})>>>\n(.*?)\n<<<END UNTRUSTED \2>>>", re.S)
PATH = re.compile(r"\b[\w/]+\.py\b")


def _blocks(user: str) -> dict[str, str]:
    return {label: body for label, _tag, body in BLOCK.findall(user)}


class MockLLM:
    def complete(self, model: str, system: str, user: str) -> Completion:
        if "## TASK: CLASSIFY" in user:
            text = self._classify(_blocks(user).get("ISSUE", ""))
        elif "## TASK: PLAN" in user:
            text = self._plan(_blocks(user))
        else:
            text = ""
        return Completion(text, (len(system) + len(user) + len(text)) // 4, "mock")

    def list_models(self) -> list[str]:
        return ["mock"]

    @staticmethod
    def _plan(blocks: dict[str, str]) -> str:
        gap = blocks.get("GAP") or blocks.get("ISSUE", "")
        title = next((ln[7:] for ln in gap.splitlines() if ln.startswith("Title: ")), "the change")
        files = [k[5:] for k in blocks if k.startswith("FILE ")] or [
            "the module named in the issue"
        ]
        return "\n".join(
            [
                f"1. Read {', '.join(files)} and the existing tests.",
                f"2. Write a failing test that captures: {title}.",
                "3. Make the smallest change that makes it pass.",
                "4. Run the full test suite with pytest.",
                "5. Open a PR that references the issue and explains the change.",
            ]
        )

    @staticmethod
    def _classify(issue: str) -> str:
        title = issue.split("\n", 1)[0]
        body = issue.split("Body:\n", 1)[-1].split("\nMaintainer replies:", 1)[0]
        low = issue.lower()
        clarity = 1 + sum(
            [
                len(body) > 80,
                bool(re.search(r"\b(expected|should)\b", low)),
                bool(re.search(r"\b(steps|e\.g\.|example|call)\b", low)),
                bool(PATH.search(body)),
            ]
        )
        if re.search(r"\b(refactor|design|large|across|touching most)\b", low):
            size = "L"
        elif "good first issue" in low or len(body) < 300:
            size = "S"
        else:
            size = "M"
        return json.dumps(
            {
                "clarity": clarity,
                "size": size,
                "skills": skills_in(title + " " + body),
                "likely_files": sorted(set(PATH.findall(body))),
            }
        )
