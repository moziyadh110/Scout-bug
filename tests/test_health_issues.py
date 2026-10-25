from datetime import timedelta

from scout.health import discover, health
from scout.issues import drop_reason, filter_issues
from scout.models import Comment, Issue, RepoStats, now


def test_discover_applies_every_filter(deps):
    kept, skipped = discover(deps.github, deps.settings)
    assert {r.full_name for r in kept} == {"acme/tinycsv", "acme/dateparse", "solo/quicktoml"}
    assert skipped == {
        "oldco/legacyhttp": "no push in 90 days",
        "megacorp/bigframe": "45000 stars is outside 200-20000",
        "acme/archived-utils": "archived",
    }


def test_health_scores_are_deterministic_and_ordered(deps):
    scores = {r.full_name: health(r).score for r in discover(deps.github, deps.settings)[0]}
    assert scores == {"acme/tinycsv": 0.925, "acme/dateparse": 0.5915, "solo/quicktoml": 0.0133}
    passing = {n for n, s in scores.items() if s >= deps.settings.min_health}
    assert passing == {"acme/tinycsv", "acme/dateparse"}


def test_health_clamps_and_treats_unknown_as_zero():
    r = RepoStats(
        full_name="a/b",
        stars=300,
        pushed_at=now(),
        maintainer_response_hours=999,
        commits_90d=500,
        license="Proprietary",
    )
    h = health(r)
    assert h.parts["response"] == 0 and h.parts["activity"] == 1 and h.parts["license"] == 0


def test_issue_filter_reasons(deps):
    kept, dropped = filter_issues(deps.github.open_issues("acme/tinycsv"))
    assert [i.number for i in kept] == [12, 18]
    assert dropped == {
        "acme/tinycsv#13": "assigned to bob",
        "acme/tinycsv#14": "has linked PR #15",
        "acme/tinycsv#16": "claimed by sam",
        "acme/tinycsv#17": "no maintainer response",
        "acme/tinycsv#19": "is a pull request",
    }


def test_maintainer_saying_working_on_it_is_not_a_claim():
    t = now() - timedelta(days=1)
    i = Issue(
        repo="a/b",
        number=1,
        title="x",
        created_at=t,
        comments=[
            Comment(
                author="m",
                association="OWNER",
                body="I'm working on the release, PRs welcome",
                created_at=t,
            )
        ],
    )
    assert drop_reason(i) is None
