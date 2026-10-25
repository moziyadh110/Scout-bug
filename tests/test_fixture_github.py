from datetime import timedelta

from scout.adapters.local.fixture_github import FixtureGitHub
from scout.models import now

from .conftest import ROOT


def gh() -> FixtureGitHub:
    return FixtureGitHub(ROOT / "fixtures")


def test_relative_timestamps_resolve_against_now():
    repo = gh().repo("acme/tinycsv")
    assert timedelta(days=1) < now() - repo.pushed_at < timedelta(days=3)


def test_open_issues_excludes_closed_and_lookup_works():
    g = gh()
    assert {i.number for i in g.open_issues("acme/tinycsv")} >= {12, 13, 18}
    assert g.issue("acme/tinycsv", 12).labels == ["good first issue", "enhancement"]


def test_files_users_pulls_collaborators():
    g = gh()
    assert "bounties: true" in (g.read_file("acme/tinycsv", ".scout.yml") or "")
    assert g.read_file("acme/dateparse", ".scout.yml") is None
    assert len(g.user_repos("devjane")) == 5 and g.user_repos("nobody") == []
    assert [p.number for p in g.closing_prs("acme/tinycsv", 18)] == [22, 23]
    assert g.has_write_access("acme/tinycsv", "maria")
    assert not g.has_write_access("acme/tinycsv", "newbie")


def test_checkout_copies_source(tmp_path):
    out = gh().checkout("acme/tinycsv", tmp_path / "co")
    assert (out / "tinycsv" / "__init__.py").exists()
