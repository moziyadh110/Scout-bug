"""Algorithm 2, steps 2-3: filter gaps for a contributor and rank them.

score = impact^w1 * feasibility^w2 * fit^w3 * welcome^w4 * label boosts * (1 + bounty boost)
Weights and boosts come from the playbook, which the nightly backtest tunes.
"""

import math

from scout.models import Gap, Health, Match, Playbook, Profile, RepoStats

SIZE_FEASIBILITY = {"S": 1.0, "M": 0.7, "L": 0.4}
SIZES_FOR_LEVEL = {"beginner": {"S"}, "intermediate": {"S", "M"}, "advanced": {"S", "M", "L"}}
LABEL_IMPACT = {"bug": 0.9, "security": 1.0, "enhancement": 0.7, "documentation": 0.5}
TOOL_IMPACT = {
    "pip-audit": 0.9,
    "coverage": 0.6,
    "mypy": 0.5,
    "interrogate": 0.4,
    "todo": 0.4,
    "ruff": 0.3,
}
MAX_STARS_LOG = math.log10(20_000)


def candidates(gaps: list[Gap], profile: Profile, pb: Playbook) -> list[Gap]:
    sizes = SIZES_FOR_LEVEL[profile.level]
    return [
        g
        for g in gaps
        if g.size in sizes
        and any(profile.skills.get(s, 0) > 0 for s in g.skills)
        and (g.kind == "implicit" or g.clarity >= pb.min_clarity)
    ]


def parts(gap: Gap, profile: Profile, repo: RepoStats, health: Health) -> dict[str, float]:
    if gap.kind == "implicit":
        kind = TOOL_IMPACT.get(gap.source, 0.4)
    else:
        kind = max((LABEL_IMPACT.get(lbl, 0.6) for lbl in gap.labels), default=0.6)
    popularity = min(1.0, math.log10(repo.stars + 1) / MAX_STARS_LOG)
    fit = sum(profile.skills.get(s, 0.0) for s in gap.skills) / len(gap.skills) if gap.skills else 0
    return {
        "impact": round(0.5 * kind + 0.5 * popularity, 4),
        "feasibility": round(gap.clarity / 5 * SIZE_FEASIBILITY[gap.size], 4),
        "fit": round(fit, 4),
        "welcome": health.score,
    }


def score(p: dict[str, float], gap: Gap, pb: Playbook, funded_units: int = 0) -> float:
    s = math.prod(max(p[k], 1e-6) ** pb.weights.get(k, 1.0) for k in p)
    s *= math.prod(pb.label_boosts.get(lbl, 1.0) for lbl in gap.labels)
    if funded_units > 0:
        s *= 1 + pb.bounty_boost
    return round(s, 6)


def rank(
    gaps: list[Gap],
    profile: Profile,
    pb: Playbook,
    repos: dict[str, RepoStats],
    health: dict[str, Health],
    funded: dict[str, int] | None = None,
) -> list[Match]:
    """Best first. `funded` maps gap id -> escrowed test-USDC units."""
    funded = funded or {}
    out = []
    for g in candidates(gaps, profile, pb):
        p = parts(g, profile, repos[g.repo], health[g.repo])
        out.append(
            Match(
                gap=g,
                score=score(p, g, pb, funded.get(g.id, 0)),
                parts=p,
                funded_units=funded.get(g.id, 0),
            )
        )
    return sorted(out, key=lambda m: (-m.score, m.gap.id))
