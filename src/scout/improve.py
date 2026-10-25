"""Algorithm 4: nightly self-improvement by backtest. No fine-tuning: only the playbook changes.

Backtest set: issues closed 6-12 months ago, labelled positive when a merged PR by a first-time
contributor closed them, and snapshotted as they looked while open.
Metric: precision@10 over a skill-agnostic ranking (the denominator is always 10, so dropping
issues cannot inflate it).
"""

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from scout.classify import classify_issues
from scout.health import discover, health
from scout.index import issue_gap
from scout.models import (
    EvalResult,
    Issue,
    Playbook,
    PlaybookVersion,
    Profile,
    PullRequest,
    now,
)
from scout.playbook import versions
from scout.prompts import untrusted
from scout.rank import parts, score
from scout.skills import VOCABULARY
from scout.wiring import Deps

K = 10
EDITABLE = {
    "classify_prompt",
    "plan_prompt",
    "weights",
    "label_boosts",
    "min_clarity",
    "bounty_boost",
}
THINK = re.compile(r"<think>.*?</think>", re.S)
EVERYONE = Profile(login="backtest", skills={s: 1.0 for s in VOCABULARY}, level="advanced")


class BacktestItem(BaseModel):
    issue: Issue  # as it looked while open
    positive: bool


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def playbook_hash(pb: Playbook) -> str:
    return sha256(canonical(pb.model_dump(mode="json")))


def version_hash(pb: Playbook, ev: EvalResult) -> str:
    return sha256(
        canonical({"playbook": pb.model_dump(mode="json"), "eval": ev.model_dump(mode="json")})
    )


# --- 1. backtest set --------------------------------------------------------------------


def build_backtest(deps: Deps, at: datetime | None = None) -> list[BacktestItem]:
    t = at or now()
    repos, _ = discover(deps.github, deps.settings, t)
    items: list[BacktestItem] = []
    for r in repos:
        pending: list[tuple[Issue, PullRequest | None]] = []
        for c in deps.github.closed_issues(
            r.full_name, t - timedelta(days=365), t - timedelta(days=182)
        ):
            pr = c.closing_pr
            solver = pr.author if pr else None
            snap = c.issue.model_copy(
                update={
                    "state": "open",
                    "assignees": [],
                    "linked_prs": [],
                    # nothing written at/after closing, and nothing by the eventual solver
                    "comments": [
                        x
                        for x in c.issue.comments
                        if x.created_at < c.closed_at and x.author != solver
                    ],
                }
            )
            pending.append((snap, pr))
        # Positive: the closing PR was its author's first merged PR in the repo (bots excluded).
        asks = [
            (pr.author, pr.merged_at)
            for _, pr in pending
            if pr and pr.merged and pr.merged_at and not pr.author_is_bot
        ]
        counts = iter(deps.github.prior_merged_counts(r.full_name, asks)) if asks else iter([])
        for snap, pr in pending:
            first = bool(
                pr and pr.merged and pr.merged_at and not pr.author_is_bot and next(counts) == 0
            )
            items.append(BacktestItem(issue=snap, positive=first))
    items.sort(key=lambda i: i.issue.key)
    deps.store.put_json("backtest/set.json", [i.model_dump(mode="json") for i in items])
    return items


def backtest_hash(items: list[BacktestItem]) -> str:
    return sha256(canonical([i.model_dump(mode="json") for i in items]))


# --- 2/4. score a playbook ------------------------------------------------------------------


class Evaluator:
    """Scores playbooks on one backtest set; classifications are cached per classify prompt."""

    def __init__(self, deps: Deps, items: list[BacktestItem]) -> None:
        self.deps, self.items = deps, items
        self.hash = backtest_hash(items)
        # Exactly the repos the items come from: a fresh search can return a different set.
        names = sorted({i.issue.repo for i in items})
        repos = {n: deps.github.repo(n) for n in names}
        self.repos, self.health = repos, {k: health(r) for k, r in repos.items()}
        self._classes: dict[str, dict[str, Any]] = {}

    def evaluate(self, pb: Playbook) -> EvalResult:
        if pb.classify_prompt not in self._classes:
            issues = [i.issue for i in self.items]
            self._classes[pb.classify_prompt], _ = classify_issues(
                self.deps.batch, self.deps.settings, pb, issues
            )
        classes = self._classes[pb.classify_prompt]
        scored = []
        for it in self.items:
            gap = issue_gap(it.issue, classes[it.issue.key])
            if gap.clarity < pb.min_clarity:
                continue
            p = parts(gap, EVERYONE, self.repos[gap.repo], self.health[gap.repo])
            p["fit"] = 1.0  # the backtest measures newcomer suitability, not personal fit
            scored.append((score(p, gap, pb), gap.id, it.positive))
        scored.sort(key=lambda x: (-x[0], x[1]))
        top = scored[:K]
        return EvalResult(
            playbook_hash=playbook_hash(pb),
            backtest_hash=self.hash,
            mode=self.deps.mode,  # type: ignore[arg-type]
            precision_at_10=round(sum(pos for _, _, pos in top) / K, 4),
            top10=[gid for _, gid, _ in top],
            n_issues=len(self.items),
            n_positive=sum(i.positive for i in self.items),
        )


# --- registry ----------------------------------------------------------------------------


def commit(
    deps: Deps, pb: Playbook, parent: int | None, ev: EvalResult, rationale: str
) -> PlaybookVersion:
    n = (parent or 0) + 1
    v = PlaybookVersion(
        version=n,
        parent=parent,
        playbook=pb,
        eval=ev,
        hash=version_hash(pb, ev),
        rationale=rationale,
    )
    v.ledger_ref = deps.ledger.record(f"version:{v.hash}", f"scout:playbook:v{n}:{v.hash}")
    deps.store.put_json(f"versions/v{n:04d}.json", v.model_dump(mode="json"))
    return v


def verify(deps: Deps, n: int) -> dict[str, Any]:
    """Recompute a version's hash from the store and compare it with the ledger record."""
    data = deps.store.get_json(f"versions/v{n:04d}.json")
    if data is None:
        return {"ok": False, "error": f"no version {n}"}
    v = PlaybookVersion.model_validate(data)
    recomputed = version_hash(v.playbook, v.eval)
    memo = deps.ledger.get_memo(v.ledger_ref) if v.ledger_ref else None
    return {
        "ok": recomputed == v.hash and memo == f"scout:playbook:v{n}:{recomputed}",
        "version": n,
        "stored_hash": v.hash,
        "recomputed_hash": recomputed,
        "ledger_memo": memo,
        "ledger_ref": v.ledger_ref,
        "explorer_url": deps.ledger.explorer_url(v.ledger_ref) if v.ledger_ref else None,
    }


# --- 3/5/6. propose, probe, compose, accept ---------------------------------------------


def _merge(base: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in edit.items():
        out[k] = {**base[k], **v} if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def proposals(text: str, cur: Playbook) -> list[tuple[dict[str, Any], Playbook, str]]:
    text = THINK.sub("", text)
    try:
        items = json.loads(text[text.index("[") : text.rindex("]") + 1])
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        edit = {k: v for k, v in item.items() if k in EDITABLE}
        try:
            cand = Playbook.model_validate(_merge(cur.model_dump(), edit))
        except ValidationError:
            continue
        if edit and cand != cur:
            out.append((edit, cand, str(item.get("rationale", ""))))
    return out


def reasoner_prompt(cur: Playbook, ev: EvalResult, items: list[BacktestItem], k: int) -> str:
    by_id = {i.issue.key: i for i in items}
    misses = [by_id[g] for g in ev.top10 if not by_id[g].positive]
    found = set(ev.top10)
    missed = [i for i in items if i.positive and i.issue.key not in found]

    def show(it: BacktestItem) -> str:
        return untrusted(
            f"ISSUE {it.issue.key}", f"{it.issue.title}\nLabels: {', '.join(it.issue.labels)}"
        )

    return (
        f"## TASK: IMPROVE\n## CURRENT PLAYBOOK\n{cur.model_dump_json(indent=2)}\n"
        f"## SCORE\nprecision@10 = {ev.precision_at_10}\n"
        "## RANKED HIGH BUT NOT SOLVED BY A FIRST-TIMER\n" + "\n".join(map(show, misses)) + "\n"
        "## SOLVED BY A FIRST-TIMER BUT RANKED LOW\n" + "\n".join(map(show, missed)) + "\n"
        f"Propose up to {k} independent edits as a JSON list. Each item holds only the fields to "
        "change (classify_prompt, plan_prompt, weights {impact, feasibility, fit, welcome}, "
        "label_boosts {label: multiplier}, min_clarity 1-5, bounty_boost) and a 'rationale'. "
        "Return only JSON."
    )


def ensure_seed(deps: Deps, ev: Evaluator) -> PlaybookVersion:
    vs = versions(deps)
    if vs:
        return vs[-1]
    pb = Playbook.model_validate_json(deps.settings.playbook_seed.read_text())
    return commit(deps, pb, None, ev.evaluate(pb), "seed playbook")


def nightly(deps: Deps, at: datetime | None = None) -> dict[str, Any]:
    s = deps.settings
    items = build_backtest(deps, at)
    ev = Evaluator(deps, items)
    cur = ensure_seed(deps, ev)
    if cur.eval.mode != deps.mode:
        raise RuntimeError(
            f"the registry holds {cur.eval.mode} versions but the LLM is "
            f"{deps.mode}; use a separate SCOUT_DATA_DIR per mode"
        )
    base = ev.evaluate(cur.playbook)  # re-score: the backtest set may have moved
    report: dict[str, Any] = {
        "at": (at or now()).isoformat(),
        "from_version": cur.version,
        "mode": deps.mode,
        "baseline": base.precision_at_10,
        "backtest_hash": ev.hash,
        "candidates": [],
        "accepted": None,
    }

    reply = deps.llm.complete(
        s.model_for("reasoner"), "", reasoner_prompt(cur.playbook, base, items, s.proposals_k)
    ).text
    scored = []
    for edit, cand, why in proposals(reply, cur.playbook)[: s.proposals_k]:  # probe
        r = ev.evaluate(cand)
        scored.append((r.precision_at_10, edit, why))
        report["candidates"].append(
            {"edit": edit, "rationale": why, "precision_at_10": r.precision_at_10}
        )
    if not scored:
        report["note"] = "the reasoner proposed no valid edits"
        return _save(deps, report)

    best_two = sorted(scored, key=lambda x: -x[0])[:2]  # compose: the best edit wins clashes
    merged = cur.playbook.model_dump()
    for _, edit, _ in reversed(best_two):
        merged = _merge(merged, edit)
    composed = Playbook.model_validate(merged)
    cev = ev.evaluate(composed)
    report["composed"] = {
        "edits": [e for _, e, _ in best_two],
        "precision_at_10": cev.precision_at_10,
    }
    if cev.precision_at_10 >= base.precision_at_10 + s.min_gain - 1e-9:
        v = commit(deps, composed, cur.version, cev, " ".join(w for _, _, w in best_two))
        report["accepted"] = v.version
    else:
        report["note"] = (
            f"composed {cev.precision_at_10} does not beat {base.precision_at_10} "
            f"by MIN_GAIN {s.min_gain}"
        )
    return _save(deps, report)


def _save(deps: Deps, report: dict[str, Any]) -> dict[str, Any]:
    deps.store.put_json(f"nightly/{report['at'].replace(':', '-')}.json", report)
    return report


def history(deps: Deps) -> list[dict[str, Any]]:
    """Score history for the dashboard: every nightly report, oldest first."""
    return [r for k in deps.store.list("nightly") if (r := deps.store.get_json(k))]
