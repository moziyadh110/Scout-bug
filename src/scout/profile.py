"""Algorithm 2, step 1: a contributor profile from their public repos. No LLM involved."""

from collections import Counter

from scout.models import Level, Profile
from scout.ports import GitHub
from scout.skills import VOCABULARY, skills_in


def level_for(repo_count: int, stars: int) -> Level:
    if repo_count >= 8 or stars >= 500:
        return "advanced"
    if repo_count >= 3 or stars >= 50:
        return "intermediate"
    return "beginner"


def build_profile(gh: GitHub, login: str, extra_skills: list[str] | None = None) -> Profile:
    repos = [r for r in gh.user_repos(login) if not r.fork]  # forks say little about the user
    counts: Counter[str] = Counter()
    evidence = []
    for r in repos:
        found = skills_in(f"{r.name} {r.description} {' '.join(r.topics)}")
        counts.update(found)
        if found:
            evidence.append(f"{r.name}: {', '.join(found)}")
    skills = {s: min(1.0, n / 2) for s, n in counts.items()}  # two repos = full strength
    for s in extra_skills or []:
        if s in VOCABULARY:
            skills.setdefault(s, 0.5)  # self-reported, not shown in repos
    level = level_for(len(repos), sum(r.stars for r in repos))
    return Profile(login=login, skills=dict(sorted(skills.items())), level=level, evidence=evidence)
