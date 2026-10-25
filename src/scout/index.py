"""Algorithm 1: the nightly index build."""

import tempfile
from datetime import datetime
from pathlib import Path

from scout.classify import classify_issues
from scout.gaps import builds, implicit_gaps, tool_status
from scout.health import discover, health
from scout.issues import filter_issues
from scout.models import Gap, IndexSnapshot, Issue, IssueClass, Playbook, now
from scout.wiring import Deps


def issue_gap(i: Issue, c: IssueClass) -> Gap:
    return Gap(
        id=i.key,
        repo=i.repo,
        kind="issue",
        title=i.title,
        detail=i.body,
        labels=i.labels,
        skills=c.skills,
        size=c.size,
        clarity=c.clarity,
        issue_number=i.number,
        source="github",
        likely_files=c.likely_files,
    )


def build_index(deps: Deps, pb: Playbook, at: datetime | None = None) -> IndexSnapshot:
    s = deps.settings
    repos, skipped = discover(deps.github, s, at)
    scores = {r.full_name: health(r) for r in repos}
    healthy = [r for r in repos if scores[r.full_name].score >= s.min_health]
    for r in repos:
        if r not in healthy:
            skipped[r.full_name] = f"health {scores[r.full_name].score:.2f} < {s.min_health}"

    issues: list[Issue] = []
    for r in healthy:
        issues += deps.github.open_issues(r.full_name)
    kept, dropped = filter_issues(issues)
    classes, failed = classify_issues(deps.batch, s, pb, kept)
    gaps = [issue_gap(i, classes[i.key]) for i in kept]

    by_health = sorted(healthy, key=lambda r: -scores[r.full_name].score)
    for r in by_health[: s.gap_top_n_repos]:
        with tempfile.TemporaryDirectory(prefix="scout-co-") as d:
            try:
                src = deps.github.checkout(r.full_name, Path(d) / "src")
            except FileNotFoundError:
                skipped[r.full_name] = "no source checkout available"
                continue
            ok, why = builds(deps.sandbox, src)
            if not ok:
                skipped[r.full_name] = f"does not build: {why}"
                continue
            gaps += implicit_gaps(deps.sandbox, src, r.full_name)

    snap = IndexSnapshot(
        repos=healthy,
        health={r.full_name: scores[r.full_name] for r in healthy},
        gaps=gaps,
        dropped_issues=dropped,
        skipped_repos=skipped,
        mode=deps.mode,
        tool_status=tool_status(),
        classify_failed=failed,
    )
    data = snap.model_dump(mode="json")
    stamp = (at or now()).strftime("%Y%m%dT%H%M%S")
    deps.store.put_json(f"index/{stamp}.json", data)
    deps.store.put_json("index/latest.json", data)
    # ponytail: summary rows go to the object store until the Postgres port (step 12) exists.
    deps.store.put_json(
        "index/summary.json",
        [
            {
                "repo": r.full_name,
                "health": scores[r.full_name].score,
                "built_at": data["built_at"],
                "issue_gaps": sum(g.repo == r.full_name and g.kind == "issue" for g in gaps),
                "implicit_gaps": sum(g.repo == r.full_name and g.kind == "implicit" for g in gaps),
            }
            for r in healthy
        ],
    )
    return snap


def latest_index(deps: Deps) -> IndexSnapshot | None:
    data = deps.store.get_json("index/latest.json")
    return IndexSnapshot.model_validate(data) if data else None
