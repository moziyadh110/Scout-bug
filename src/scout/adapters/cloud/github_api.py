"""Live GitHub adapter (read-only token): GraphQL for repos, issues and PRs, REST for files,
user repos and source tarballs. Standard library HTTP only."""

import base64
import json
import shutil
import statistics
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from scout.models import (
    FIRST_TIMER_ROLES,
    MAINTAINER_ROLES,
    ClosedIssue,
    Comment,
    Issue,
    PullRequest,
    RepoStats,
    UserRepo,
    now,
)

API = "https://api.github.com"
CI_STATE = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "ERROR": "failure",
    "PENDING": "pending",
    "EXPECTED": "pending",
}


def _retryable(e: BaseException) -> bool:
    return isinstance(e, urllib.error.HTTPError) and e.code in (429, 500, 502, 503, 504)


ISSUE_FIELDS = """
  number title body state createdAt closedAt author { login }
  labels(first: 10) { nodes { name } }
  assignees(first: 5) { nodes { login } }
  comments(first: 20) { nodes { author { login } authorAssociation body createdAt } }
  closedByPullRequestsReferences(first: 3, includeClosedPrs: true) {
    nodes { number merged mergedAt authorAssociation author { login } }
  }
"""
PR_FIELDS = """
  number title merged mergedAt baseRefName authorAssociation additions deletions changedFiles
  author { login } mergedBy { login }
  closingIssuesReferences(first: 10) { nodes { number } }
  statusCheckRollup { state }
"""
REPO_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner description stargazerCount pushedAt isArchived
    primaryLanguage { name } licenseInfo { spdxId }
    repositoryTopics(first: 10) { nodes { topic { name } } }
    defaultBranchRef { name target { ... on Commit { history(since: $since) { totalCount } } } }
    c1: object(expression: "HEAD:CONTRIBUTING.md") { id }
    c2: object(expression: "HEAD:.github/CONTRIBUTING.md") { id }
    ci: object(expression: "HEAD:.github/workflows") { id }
    issues(last: 30) { nodes { createdAt
      comments(first: 10) { nodes { authorAssociation createdAt } } } }
    pullRequests(last: 50, states: [MERGED, CLOSED]) { nodes { authorAssociation merged } }
  }
}
"""


def by_number(kind: str, fields: str) -> str:
    """GraphQL query for one issue or PR of a repo by number."""
    return (
        "query($o:String!,$n:String!,$i:Int!){repository(owner:$o,name:$n)"
        f"{{{kind}(number:$i){{{fields}}}}}}}"
    )


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def stats_from_gql(r: dict[str, Any]) -> RepoStats:
    """Health inputs computed from one GraphQL response."""
    waits = []
    for i in r["issues"]["nodes"]:
        replies = [c for c in i["comments"]["nodes"] if c["authorAssociation"] in MAINTAINER_ROLES]
        if replies:
            waits.append(
                (_ts(replies[0]["createdAt"]) - _ts(i["createdAt"])).total_seconds() / 3600
            )
    firsts = [p for p in r["pullRequests"]["nodes"] if p["authorAssociation"] in FIRST_TIMER_ROLES]
    branch = r.get("defaultBranchRef") or {}
    history = ((branch.get("target") or {}).get("history") or {}).get("totalCount", 0)
    return RepoStats(
        full_name=r["nameWithOwner"],
        description=r.get("description") or "",
        stars=r["stargazerCount"],
        pushed_at=_ts(r["pushedAt"]),
        archived=r["isArchived"],
        language=(r.get("primaryLanguage") or {}).get("name"),
        license=(r.get("licenseInfo") or {}).get("spdxId"),
        default_branch=branch.get("name") or "main",
        topics=[t["topic"]["name"] for t in r["repositoryTopics"]["nodes"]],
        has_contributing=bool(r.get("c1") or r.get("c2")),
        has_ci=bool(r.get("ci")),
        commits_90d=history,
        maintainer_response_hours=round(statistics.median(waits), 1) if waits else None,
        first_timer_merge_rate=round(sum(p["merged"] for p in firsts) / len(firsts), 3)
        if firsts
        else None,
    )


def issue_from_gql(repo: str, i: dict[str, Any]) -> Issue:
    return Issue(
        repo=repo,
        number=i["number"],
        title=i["title"],
        body=i.get("body") or "",
        author=(i.get("author") or {}).get("login", ""),
        labels=[x["name"] for x in i["labels"]["nodes"]],
        state="open" if i["state"] == "OPEN" else "closed",
        assignees=[x["login"] for x in i["assignees"]["nodes"]],
        linked_prs=[
            p["number"] for p in (i.get("closedByPullRequestsReferences") or {}).get("nodes", [])
        ],
        comments=[
            Comment(
                author=(c.get("author") or {}).get("login", "ghost"),
                association=c["authorAssociation"],
                body=c.get("body") or "",
                created_at=_ts(c["createdAt"]),
            )
            for c in i["comments"]["nodes"]
        ],
        created_at=_ts(i["createdAt"]),
    )


def pr_from_gql(repo: str, p: dict[str, Any], reverted: bool = False) -> PullRequest:
    rollup = p.get("statusCheckRollup") or {}
    return PullRequest(
        repo=repo,
        number=p["number"],
        title=p["title"],
        author=(p.get("author") or {}).get("login", "ghost"),
        author_association=p["authorAssociation"],
        merged=p["merged"],
        merged_by=(p.get("mergedBy") or {}).get("login"),
        base_ref=p["baseRefName"],
        closes_issues=[n["number"] for n in p["closingIssuesReferences"]["nodes"]],
        ci_state=CI_STATE.get(rollup.get("state") or "", "none"),  # type: ignore[arg-type]
        additions=p["additions"],
        deletions=p["deletions"],
        changed_files=p["changedFiles"],
        merged_at=_ts(p["mergedAt"]) if p.get("mergedAt") else None,
        reverted=reverted,
    )


class GitHubAPI:
    def __init__(
        self, token: str, discover_limit: int, backtest_per_repo: int, max_tarball_mb: int
    ) -> None:
        if not token:
            raise ValueError("GITHUB_TOKEN is not set")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "scout",
        }
        self.discover_limit, self.backtest_per_repo = discover_limit, backtest_per_repo
        self.max_bytes = max_tarball_mb * 1024 * 1024

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(min=2, max=30),
        reraise=True,
    )
    def _http(self, url: str, data: bytes | None = None) -> bytes:
        req = urllib.request.Request(
            url, data=data, headers=self.headers, method="POST" if data else "GET"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return bytes(r.read())

    def _rest(self, path: str) -> Any:
        return json.loads(self._http(API + path))

    def _gql(self, query: str, **variables: Any) -> dict[str, Any]:
        out = json.loads(
            self._http(
                API + "/graphql", json.dumps({"query": query, "variables": variables}).encode()
            )
        )
        if out.get("errors"):
            raise RuntimeError(f"GitHub GraphQL error: {out['errors'][0].get('message')}")
        return dict(out["data"])

    @staticmethod
    def _split(full_name: str) -> tuple[str, str]:
        owner, name = full_name.split("/", 1)
        return owner, name

    # --- GitHub port -----------------------------------------------------------------

    def search_repos(
        self, min_stars: int, max_stars: int, pushed_after: datetime
    ) -> list[RepoStats]:
        q = (
            f"language:python stars:{min_stars}..{max_stars} "
            f"pushed:>={pushed_after.date().isoformat()} archived:false is:public"
        )
        items = self._rest(
            f"/search/repositories?q={urllib.parse.quote(q)}&sort=updated"
            f"&order=desc&per_page={self.discover_limit}"
        )["items"]
        return [self.repo(i["full_name"]) for i in items]

    def repo(self, full_name: str) -> RepoStats:
        owner, name = self._split(full_name)
        since = now().replace(microsecond=0) - timedelta(days=90)
        r = self._gql(REPO_QUERY, owner=owner, name=name, since=since.isoformat())["repository"]
        if r is None:
            raise KeyError(full_name)
        return stats_from_gql(r)

    def open_issues(self, full_name: str) -> list[Issue]:
        owner, name = self._split(full_name)
        q = (
            "query($o:String!,$n:String!){repository(owner:$o,name:$n){issues(states:OPEN,"
            f"first:30,orderBy:{{field:UPDATED_AT,direction:DESC}}){{nodes{{{ISSUE_FIELDS}}}}}}}}}"
        )
        nodes = self._gql(q, o=owner, n=name)["repository"]["issues"]["nodes"]
        return [issue_from_gql(full_name, i) for i in nodes]

    def issue(self, full_name: str, number: int) -> Issue:
        owner, name = self._split(full_name)
        q = by_number("issue", ISSUE_FIELDS)
        i = self._gql(q, o=owner, n=name, i=number)["repository"]["issue"]
        if i is None:
            raise KeyError(f"{full_name}#{number}")
        return issue_from_gql(full_name, i)

    def read_file(self, full_name: str, path: str, ref: str | None = None) -> str | None:
        url = f"/repos/{full_name}/contents/{urllib.parse.quote(path)}"
        try:
            data = self._rest(url + (f"?ref={urllib.parse.quote(ref)}" if ref else ""))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return None
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    def user_repos(self, login: str) -> list[UserRepo]:
        try:
            items = self._rest(
                f"/users/{urllib.parse.quote(login)}/repos?type=owner&sort=pushed&per_page=100"
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise KeyError(f"no GitHub user {login}") from e
            raise
        return [
            UserRepo(
                name=r["name"],
                description=r.get("description") or "",
                language=r.get("language"),
                topics=r.get("topics") or [],
                stars=r["stargazers_count"],
                fork=r["fork"],
                pushed_at=_ts(r["pushed_at"]),
            )
            for r in items
        ]

    def _reverted(self, full_name: str, title: str) -> bool:
        q = f'repo:{full_name} is:pr is:merged in:title "Revert \\"{title}\\""'
        items = self._rest(f"/search/issues?q={urllib.parse.quote(q)}&per_page=5")["items"]
        return any(i["title"].startswith(f'Revert "{title}"') for i in items)

    def pull_request(self, full_name: str, number: int) -> PullRequest:
        owner, name = self._split(full_name)
        q = by_number("pullRequest", PR_FIELDS)
        p = self._gql(q, o=owner, n=name, i=number)["repository"]["pullRequest"]
        if p is None:
            raise KeyError(f"{full_name}!{number}")
        return pr_from_gql(
            full_name, p, self._reverted(full_name, p["title"]) if p["merged"] else False
        )

    def closing_prs(self, full_name: str, issue_number: int) -> list[PullRequest]:
        return [
            self.pull_request(full_name, n) for n in self.issue(full_name, issue_number).linked_prs
        ]

    def has_write_access(self, full_name: str, login: str) -> bool | None:
        try:
            r = self._rest(
                f"/repos/{full_name}/collaborators/{urllib.parse.quote(login)}/permission"
            )
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):  # read-only tokens cannot read permissions
                return None
            raise
        return r.get("permission") in ("admin", "write")

    def closed_issues(
        self, full_name: str, closed_after: datetime, closed_before: datetime
    ) -> list[ClosedIssue]:
        q = (
            f"repo:{full_name} is:issue is:closed linked:pr "
            f"closed:{closed_after.date().isoformat()}..{closed_before.date().isoformat()}"
        )
        items = self._rest(
            f"/search/issues?q={urllib.parse.quote(q)}&per_page={self.backtest_per_repo}"
        )["items"]
        owner, name = self._split(full_name)
        out = []
        for it in items:
            qq = by_number("issue", ISSUE_FIELDS)
            raw = self._gql(qq, o=owner, n=name, i=it["number"])["repository"]["issue"]
            if not raw or not raw.get("closedAt"):
                continue
            merged = [p for p in raw["closedByPullRequestsReferences"]["nodes"] if p["merged"]]
            pr = None
            if merged:
                m = merged[0]
                pr = PullRequest(
                    repo=full_name,
                    number=m["number"],
                    author=(m.get("author") or {}).get("login", "ghost"),
                    author_association=m["authorAssociation"],
                    merged=True,
                    merged_at=_ts(m["mergedAt"]) if m.get("mergedAt") else None,
                )
            out.append(
                ClosedIssue(
                    issue=issue_from_gql(full_name, raw),
                    closed_at=_ts(raw["closedAt"]),
                    closing_pr=pr,
                )
            )
        return out

    def checkout(self, full_name: str, dest: Path) -> Path:
        """Download the default-branch tarball and extract it safely into dest."""
        data = self._http(f"{API}/repos/{full_name}/tarball")
        if len(data) > self.max_bytes:
            raise ValueError(f"{full_name} tarball exceeds {self.max_bytes // 2**20} MB")
        with tempfile.TemporaryDirectory(prefix="scout-tar-") as tmp:
            archive = Path(tmp) / "src.tar.gz"
            archive.write_bytes(data)
            out = Path(tmp) / "x"
            with tarfile.open(archive) as tf:
                tf.extractall(out, filter="data")  # blocks traversal, absolute paths, devices
            (top,) = list(out.iterdir())  # GitHub wraps everything in one top-level directory
            shutil.move(str(top), dest)
        return dest
