"""Algorithm 1, step 4: classify issues (clarity, size, skills, likely files) in one batch."""

import json
import re

from pydantic import ValidationError

from scout.config import Settings
from scout.models import MAINTAINER_ROLES, Issue, IssueClass, Playbook
from scout.ports import BatchLLM, BatchRequest
from scout.prompts import system, untrusted
from scout.skills import VOCABULARY

THINK = re.compile(r"<think>.*?</think>", re.S)
UNCLASSIFIED = IssueClass(clarity=1, size="L", skills=[], likely_files=[])


def issue_text(i: Issue) -> str:
    replies = "\n".join(
        f"- {c.author}: {c.body}" for c in i.comments if c.association in MAINTAINER_ROLES
    )
    return (
        f"Title: {i.title}\nLabels: {', '.join(i.labels) or '(none)'}\n"
        f"Body:\n{i.body}\nMaintainer replies:\n{replies or '(none)'}"
    )


def classify_prompt(i: Issue) -> str:
    return (
        "## TASK: CLASSIFY\n"
        f"{untrusted('ISSUE', issue_text(i))}\n"
        "Return only a JSON object with keys: clarity (1-5, how well-specified the issue is), "
        'size ("S", "M" or "L"), skills (a list chosen only from: '
        f"{', '.join(sorted(VOCABULARY))}), likely_files (repo paths mentioned or implied)."
    )


def parse_class(text: str) -> IssueClass | None:
    text = THINK.sub("", text)
    try:
        data = json.loads(text[text.index("{") : text.rindex("}") + 1])
        c = IssueClass.model_validate(data)
    except (ValueError, ValidationError):
        return None
    c.skills = sorted({s for s in c.skills if s in VOCABULARY})
    c.likely_files = [f for f in c.likely_files if isinstance(f, str)][:10]
    return c


def classify_issues(
    batch: BatchLLM, s: Settings, pb: Playbook, issues: list[Issue]
) -> tuple[dict[str, IssueClass], list[str]]:
    """Returns (classes by issue key, keys that failed and got the conservative fallback)."""
    sys = system(pb.classify_prompt)
    reqs = [BatchRequest(i.key, s.model_for("classifier"), sys, classify_prompt(i)) for i in issues]
    results = batch.run(reqs)
    classes, failed = {}, []
    for i in issues:
        c = parse_class(results[i.key].text) if i.key in results else None
        if c is None:
            failed.append(i.key)
        classes[i.key] = c or UNCLASSIFIED
    return classes, failed
