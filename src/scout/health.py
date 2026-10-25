"""Algorithm 1, steps 1-2: discover candidate repos and score their health. No LLM involved."""

from datetime import datetime, timedelta

from scout.config import Settings
from scout.models import Health, RepoStats, now
from scout.ports import GitHub

OPEN_LICENSES = {
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MPL-2.0",
    "GPL-2.0",
    "GPL-3.0",
    "LGPL-2.1",
    "LGPL-3.0",
    "AGPL-3.0",
    "Unlicense",
}
WEIGHTS = {
    "response": 0.25,
    "first_timers": 0.25,
    "activity": 0.20,
    "contributing": 0.10,
    "license": 0.10,
    "ci": 0.10,
}
FAST_REPLY_HOURS, SLOW_REPLY_HOURS = 24.0, 14 * 24.0
ACTIVE_COMMITS_90D = 30


def discover(
    gh: GitHub, s: Settings, at: datetime | None = None
) -> tuple[list[RepoStats], dict[str, str]]:
    """Python repos in the star band, recently pushed, not archived. Returns (kept, skipped)."""
    pushed_after = (at or now()) - timedelta(days=s.discover_pushed_within_days)
    kept, skipped = [], {}
    # Re-check every filter locally: the search API is a hint, not a guarantee.
    for r in gh.search_repos(s.discover_min_stars, s.discover_max_stars, pushed_after):
        if r.archived:
            skipped[r.full_name] = "archived"
        elif (r.language or "").lower() != "python":
            skipped[r.full_name] = f"language is {r.language}"
        elif not s.discover_min_stars <= r.stars <= s.discover_max_stars:
            skipped[r.full_name] = (
                f"{r.stars} stars is outside {s.discover_min_stars}-{s.discover_max_stars}"
            )
        elif r.pushed_at < pushed_after:
            skipped[r.full_name] = f"no push in {s.discover_pushed_within_days} days"
        else:
            kept.append(r)
    return kept, skipped


def health(r: RepoStats) -> Health:
    """Weighted 0..1 score. Unknown signals score 0: a repo has to show it is welcoming."""
    if r.maintainer_response_hours is None:
        response = 0.0
    else:
        span = SLOW_REPLY_HOURS - FAST_REPLY_HOURS
        response = min(1.0, max(0.0, 1 - (r.maintainer_response_hours - FAST_REPLY_HOURS) / span))
    parts = {
        "response": response,
        "first_timers": r.first_timer_merge_rate or 0.0,
        "activity": min(1.0, r.commits_90d / ACTIVE_COMMITS_90D),
        "contributing": float(r.has_contributing),
        "license": float(r.license in OPEN_LICENSES),
        "ci": float(r.has_ci),
    }
    score = sum(WEIGHTS[k] * v for k, v in parts.items())
    return Health(score=round(score, 4), parts={k: round(v, 4) for k, v in parts.items()})
