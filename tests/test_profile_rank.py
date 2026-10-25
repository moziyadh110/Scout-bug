from scout.health import health
from scout.models import Gap, Playbook, Profile
from scout.profile import build_profile
from scout.rank import rank

from .conftest import ROOT

PB = Playbook.model_validate_json((ROOT / "playbooks" / "v1.json").read_text())


def test_profiles_from_fixture_repos(deps):
    newbie = build_profile(deps.github, "newbie")
    assert newbie.level == "beginner"
    assert newbie.skills == {"csv": 0.5, "docs": 0.5, "parsing": 0.5}
    jane = build_profile(deps.github, "devjane", extra_skills=["docs", "not-a-skill"])
    assert jane.level == "intermediate"  # the fork is ignored
    assert jane.skills["parsing"] == 0.5 and jane.skills["docs"] == 0.5
    assert "not-a-skill" not in jane.skills and "http" not in jane.skills


def _gap(gid, **kw):
    base = dict(
        id=gid,
        repo="acme/tinycsv",
        kind="issue",
        title=gid,
        skills=["parsing"],
        size="S",
        clarity=5,
    )
    return Gap(**(base | kw))


def _ctx(deps):
    r = deps.github.repo("acme/tinycsv")
    return {r.full_name: r}, {r.full_name: health(r)}


def test_filter_by_skill_and_size_for_level(deps):
    prof = Profile(login="x", skills={"parsing": 1.0}, level="beginner")
    gaps = [_gap("ok"), _gap("too-big", size="M"), _gap("wrong-skill", skills=["http"])]
    assert [m.gap.id for m in rank(gaps, prof, PB, *_ctx(deps))] == ["ok"]


def test_ranking_parts_and_bounty_boost(deps):
    prof = Profile(login="x", skills={"parsing": 1.0, "docs": 0.5}, level="advanced")
    gaps = [_gap("clear"), _gap("vague", clarity=2), _gap("docs", skills=["docs"])]
    ranked = rank(gaps, prof, PB, *_ctx(deps))
    assert [m.gap.id for m in ranked] == ["clear", "docs", "vague"]
    top = ranked[0]
    assert set(top.parts) == {"impact", "feasibility", "fit", "welcome"} and top.parts["fit"] == 1
    boosted = rank(gaps, prof, PB, *_ctx(deps), funded={"vague": 5_000_000})
    vague = next(m for m in boosted if m.gap.id == "vague")
    assert vague.funded_units == 5_000_000
    plain = next(m for m in ranked if m.gap.id == "vague")
    assert abs(vague.score - plain.score * 1.5) < 1e-5


def test_playbook_weights_and_label_boosts_change_order(deps):
    prof = Profile(login="x", skills={"parsing": 1.0}, level="advanced")
    gaps = [_gap("plain", clarity=5), _gap("gfi", clarity=4, labels=["good first issue"])]
    assert rank(gaps, prof, PB, *_ctx(deps))[0].gap.id == "plain"
    tuned = PB.model_copy(update={"label_boosts": {"good first issue": 1.5}})
    assert rank(gaps, prof, tuned, *_ctx(deps))[0].gap.id == "gfi"
