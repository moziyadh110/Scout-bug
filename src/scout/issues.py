"""Algorithm 1, step 3: keep only issues a newcomer can actually pick up."""

import re

from scout.models import MAINTAINER_ROLES, Issue

CLAIM = re.compile(
    r"\b(i'?m working on|i am working on|i'?ll (take|work on|pick)|i will (take|work on|pick)"
    r"|can i (take|work on|pick)|(please )?assign (it |this )?(to )?me|i'?d like to work on"
    r"|i would like to work on|working on (it|this))\b",
    re.I,
)


def drop_reason(issue: Issue) -> str | None:
    """Why an issue is not a candidate, or None if it is."""
    if issue.is_pull_request:
        return "is a pull request"
    if issue.state != "open":
        return "closed"
    if issue.assignees:
        return f"assigned to {', '.join(issue.assignees)}"
    if issue.linked_prs:
        return f"has linked PR #{issue.linked_prs[0]}"
    for c in issue.comments:
        if c.association not in MAINTAINER_ROLES and CLAIM.search(c.body):
            return f"claimed by {c.author}"
    if not any(c.association in MAINTAINER_ROLES for c in issue.comments):
        return "no maintainer response"
    return None


def filter_issues(issues: list[Issue]) -> tuple[list[Issue], dict[str, str]]:
    kept, dropped = [], {}
    for i in issues:
        if reason := drop_reason(i):
            dropped[i.key] = reason
        else:
            kept.append(i)
    return kept, dropped
