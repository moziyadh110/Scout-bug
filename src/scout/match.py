"""Algorithm 2: match a contributor to verified, planned contributions."""

import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from scout.gaps import builds, run_test_suite
from scout.index import latest_index
from scout.issues import drop_reason
from scout.models import Gap, Match, MatchResult, Playbook, Verification
from scout.playbook import current_playbook
from scout.profile import build_profile
from scout.prompts import system, untrusted
from scout.rank import rank
from scout.wiring import Deps

THINK = re.compile(r"<think>.*?</think>", re.S)
CLAIMED = ("assigned to", "has linked PR", "claimed by")
OPEN_ISSUE_FIRST = (
    "Open an issue first. This gap was found by an automated tool, not reported by the "
    "maintainers: describe what you found and ask whether a PR is welcome before you start."
)
MAX_FILE_LINES = 80


def verify(deps: Deps, gap: Gap, src: Path) -> Verification:
    """Still open and unclaimed (re-fetched live), builds, and the test suite runs."""
    still_open, unclaimed, notes = True, True, []
    if gap.kind == "issue" and gap.issue_number is not None:
        issue = deps.github.issue(gap.repo, gap.issue_number)
        still_open = issue.state == "open"
        reason = drop_reason(issue) or ""
        unclaimed = not reason.startswith(CLAIMED)
        if reason:
            notes.append(reason)
    ok, why = builds(deps.sandbox, src)
    if not ok:
        notes.append(f"build failed: {why}")
    r = run_test_suite(deps.sandbox, src)
    tests_ran = not r.timed_out and r.exit_code in (0, 1)  # 1 = some tests fail, still runnable
    tail = r.output.strip().splitlines()[-1] if r.output.strip() else ""
    notes.append(f"pytest exit {r.exit_code}: {tail}"[:200])
    return Verification(
        still_open=still_open,
        unclaimed=unclaimed,
        builds=ok,
        tests_ran=tests_ran,
        detail="; ".join(notes),
    )


def plan_prompt(gap: Gap, src: Path) -> str:
    blocks = [untrusted("GAP", f"Repository: {gap.repo}\nTitle: {gap.title}\n\n{gap.detail}")]
    for rel in gap.likely_files[:3]:
        p = (src / rel).resolve()
        if p.is_relative_to(src.resolve()) and p.is_file():
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[:MAX_FILE_LINES]
            blocks.append(untrusted(f"FILE {rel}", "\n".join(lines)))
    return (
        "## TASK: PLAN\n" + "\n".join(blocks) + "\n"
        "Write a numbered contribution plan of at most 6 steps for a developer new to this "
        "repository: files to read, the change to make, tests to add, and how to open the PR."
    )


def plan_for(deps: Deps, pb: Playbook, gap: Gap, src: Path) -> tuple[str, dict[str, Any]]:
    prompt = plan_prompt(gap, src)
    c = deps.llm.complete(deps.settings.model_for("planner"), system(pb.plan_prompt), prompt)
    text = THINK.sub("", c.text).strip()
    if gap.kind == "implicit":  # enforced in code, not left to the model
        text = f"{OPEN_ISSUE_FIRST}\n\n{text}"
    return text, {
        "gap": gap.id,
        "model": c.model,
        "prompt": prompt,
        "response": c.text,
        "tokens": c.tokens,
    }


def funded_units(deps: Deps) -> dict[str, int]:
    """Escrowed test-USDC per gap id, for the bounty boost."""
    out: dict[str, int] = {}
    for key in deps.store.list("bounties"):
        b = deps.store.get_json(key) or {}
        if b.get("state") == "funded":
            gid = f"{b['repo']}#{b['issue_number']}"
            out[gid] = out.get(gid, 0) + int(b["amount"])
    return out


def match(deps: Deps, login: str, extra_skills: list[str] | None = None) -> MatchResult:
    snap = latest_index(deps)
    if snap is None:
        raise RuntimeError("no index yet: run `scout index` first")
    pb, version = current_playbook(deps)
    profile = build_profile(deps.github, login, extra_skills)
    repos = {r.full_name: r for r in snap.repos}
    ranked = rank(snap.gaps, profile, pb, repos, snap.health, funded_units(deps))
    top: list[Match] = ranked[: deps.settings.verify_top_k]
    prompts = []
    for m in top:
        with tempfile.TemporaryDirectory(prefix="scout-co-") as d:
            src = deps.github.checkout(m.gap.repo, Path(d) / "src")
            m.verification = verify(deps, m.gap, src)
            if m.verification.ok:
                m.plan, record = plan_for(deps, pb, m.gap, src)
                prompts.append(record)
    result = MatchResult(
        id=uuid.uuid4().hex[:12],
        login=login,
        profile=profile,
        matches=top,
        playbook_version=version,
        mode=deps.mode,
    )
    deps.store.put_json(
        f"traces/match-{result.id}.json",
        {"result": result.model_dump(mode="json"), "prompts": prompts, "candidates": len(ranked)},
    )
    return result
