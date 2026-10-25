"""Offline tests for the live GitHub adapter's parsing and safety (no network)."""

import io
import tarfile

import pytest

from scout.adapters.cloud.github_api import GitHubAPI, issue_from_gql, pr_from_gql, stats_from_gql
from scout.adapters.local.fixture_github import FixtureGitHub
from scout.bounty import Bounties, declared_maintainers


def test_stats_from_graphql():
    r = {
        "nameWithOwner": "o/r",
        "description": None,
        "stargazerCount": 900,
        "pushedAt": "2026-10-20T00:00:00Z",
        "isArchived": False,
        "primaryLanguage": {"name": "Python"},
        "licenseInfo": {"spdxId": "MIT"},
        "repositoryTopics": {"nodes": [{"topic": {"name": "csv"}}]},
        "defaultBranchRef": {"name": "main", "target": {"history": {"totalCount": 41}}},
        "c1": None,
        "c2": {"id": "x"},
        "ci": {"id": "y"},
        "issues": {
            "nodes": [
                {
                    "createdAt": "2026-10-01T00:00:00Z",
                    "comments": {
                        "nodes": [
                            {"authorAssociation": "NONE", "createdAt": "2026-10-01T01:00:00Z"},
                            {"authorAssociation": "OWNER", "createdAt": "2026-10-01T10:00:00Z"},
                        ]
                    },
                },
                {
                    "createdAt": "2026-10-02T00:00:00Z",
                    "comments": {
                        "nodes": [
                            {
                                "authorAssociation": "MEMBER",
                                "createdAt": "2026-10-02T20:00:00Z",
                            }
                        ]
                    },
                },
                {"createdAt": "2026-10-03T00:00:00Z", "comments": {"nodes": []}},
            ]
        },
        "pullRequests": {
            "nodes": [
                {"authorAssociation": "FIRST_TIME_CONTRIBUTOR", "merged": True},
                {"authorAssociation": "FIRST_TIMER", "merged": False},
                {"authorAssociation": "MEMBER", "merged": True},
            ]
        },
    }
    s = stats_from_gql(r)
    assert (s.stars, s.license, s.default_branch, s.commits_90d) == (900, "MIT", "main", 41)
    assert s.has_contributing and s.has_ci and s.topics == ["csv"]
    assert s.maintainer_response_hours == 15.0  # median of 10h and 20h; unanswered ignored
    assert s.first_timer_merge_rate == 0.5


def test_issue_and_pr_from_graphql():
    i = issue_from_gql(
        "o/r",
        {
            "number": 7,
            "title": "t",
            "body": None,
            "state": "OPEN",
            "createdAt": "2026-10-01T00:00:00Z",
            "closedAt": None,
            "author": None,
            "labels": {"nodes": [{"name": "good first issue"}]},
            "assignees": {"nodes": []},
            "comments": {
                "nodes": [
                    {
                        "author": {"login": "m"},
                        "authorAssociation": "OWNER",
                        "body": "yes",
                        "createdAt": "2026-10-01T02:00:00Z",
                    }
                ]
            },
            "closedByPullRequestsReferences": {"nodes": [{"number": 9}]},
        },
    )
    assert i.key == "o/r#7" and i.linked_prs == [9] and i.comments[0].association == "OWNER"
    p = pr_from_gql(
        "o/r",
        {
            "number": 9,
            "title": "fix",
            "merged": True,
            "mergedAt": "2026-10-05T00:00:00Z",
            "baseRefName": "main",
            "authorAssociation": "FIRST_TIMER",
            "additions": 10,
            "deletions": 2,
            "changedFiles": 1,
            "author": {"login": "new"},
            "mergedBy": {"login": "m"},
            "closingIssuesReferences": {"nodes": [{"number": 7}]},
            "statusCheckRollup": {"state": "SUCCESS"},
        },
    )
    assert (p.closes_issues, p.ci_state, p.merged_by) == ([7], "success", "m")
    assert (
        pr_from_gql(
            "o/r",
            {
                **p.model_dump(),
                "number": 9,
                "title": "x",
                "mergedAt": None,
                "baseRefName": "main",
                "authorAssociation": "NONE",
                "changedFiles": 1,
                "author": None,
                "mergedBy": None,
                "closingIssuesReferences": {"nodes": []},
                "statusCheckRollup": None,
            },
        ).ci_state
        == "none"
    )


def test_declared_maintainers():
    assert declared_maintainers("bounties: true\nmaintainers: [maria, '@ivo']\n") == {
        "maria",
        "ivo",
    }
    assert declared_maintainers("bounties: true") == set() and declared_maintainers(None) == set()


class NoPermissions(FixtureGitHub):
    def has_write_access(self, full_name: str, login: str) -> bool | None:
        return None  # like a read-only token


def test_write_check_falls_back_to_scout_yml_maintainers(deps):
    deps.github = NoPermissions(deps.settings.fixtures_dir)
    bs = Bounties(deps)
    bs.fund("acme/tinycsv", 12, deps.ledger.funder_account(), 1_000_000)
    (v,) = bs.on_pr_merged("acme/tinycsv", 21)  # merged by maria, listed in .scout.yml
    check = next(c for c in v.checks if c.name.startswith("merged by"))
    assert check.ok and ".scout.yml" in check.detail


def _tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_checkout_extracts_safely(tmp_path, monkeypatch):
    api = GitHubAPI("t", 1, 1, 1)
    monkeypatch.setattr(
        api, "_http", lambda url, data=None: _tar({"o-r-abc/pkg/__init__.py": b"x=1\n"})
    )
    out = api.checkout("o/r", tmp_path / "src")
    assert (out / "pkg" / "__init__.py").read_text() == "x=1\n"

    evil = _tar({"o-r-abc/ok.py": b"", "o-r-abc/../../escape.py": b"pwned"})
    monkeypatch.setattr(api, "_http", lambda url, data=None: evil)
    with pytest.raises(tarfile.TarError):
        api.checkout("o/r", tmp_path / "src2")
    assert not (tmp_path / "escape.py").exists()

    monkeypatch.setattr(api, "_http", lambda url, data=None: b"x" * (2 * 1024 * 1024))
    with pytest.raises(ValueError, match="exceeds"):
        api.checkout("o/r", tmp_path / "src3")
