"""GitHub port backed by hand-written JSON fixtures and local source trees.

Timestamps may be written relative to now, e.g. "-3d" or "-12h", so fixtures never go stale.
"""

import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout.models import ClosedIssue, Issue, PullRequest, RepoStats, UserRepo, now

_REL = re.compile(r"^-(\d+)([dh])$")


def _resolve(obj: Any, at: datetime) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve(v, at) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(v, at) for v in obj]
    if isinstance(obj, str) and (m := _REL.match(obj)):
        n, unit = int(m.group(1)), m.group(2)
        return (
            at - timedelta(days=n if unit == "d" else 0, hours=n if unit == "h" else 0)
        ).isoformat()
    return obj


class FixtureGitHub:
    def __init__(self, root: Path, at: datetime | None = None) -> None:
        self.root = root
        self.at = at or now()
        self._cache: dict[str, Any] = {}

    def _load(self, name: str) -> Any:
        if name not in self._cache:
            path = self.root / "github" / f"{name}.json"
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            self._cache[name] = _resolve(raw, self.at)
        return self._cache[name]

    def search_repos(
        self, min_stars: int, max_stars: int, pushed_after: datetime
    ) -> list[RepoStats]:
        # The live API filters server-side; fixtures return everything and let callers filter.
        return [RepoStats.model_validate(r) for r in self._load("repos")]

    def repo(self, full_name: str) -> RepoStats:
        for r in self._load("repos"):
            if r["full_name"] == full_name:
                return RepoStats.model_validate(r)
        raise KeyError(full_name)

    def open_issues(self, full_name: str) -> list[Issue]:
        issues = [Issue.model_validate(i) for i in self._load("issues").get(full_name, [])]
        return [i for i in issues if i.state == "open"]

    def issue(self, full_name: str, number: int) -> Issue:
        for i in self._load("issues").get(full_name, []):
            if i["number"] == number:
                return Issue.model_validate(i)
        raise KeyError(f"{full_name}#{number}")

    def read_file(self, full_name: str, path: str, ref: str | None = None) -> str | None:
        content = self._load("files").get(full_name, {}).get(path)
        return str(content) if content is not None else None

    def user_repos(self, login: str) -> list[UserRepo]:
        return [UserRepo.model_validate(r) for r in self._load("users").get(login, [])]

    def pull_request(self, full_name: str, number: int) -> PullRequest:
        for p in self._load("pulls").get(full_name, []):
            if p["number"] == number:
                return PullRequest.model_validate(p)
        raise KeyError(f"{full_name}!{number}")

    def closing_prs(self, full_name: str, issue_number: int) -> list[PullRequest]:
        pulls = [PullRequest.model_validate(p) for p in self._load("pulls").get(full_name, [])]
        return [p for p in pulls if issue_number in p.closes_issues]

    def has_write_access(self, full_name: str, login: str) -> bool | None:
        return login in self._load("collaborators").get(full_name, [])

    def closed_issues(
        self, full_name: str, closed_after: datetime, closed_before: datetime
    ) -> list[ClosedIssue]:
        items = [
            ClosedIssue.model_validate(c) for c in self._load("closed_issues").get(full_name, [])
        ]
        return [c for c in items if closed_after <= c.closed_at <= closed_before]

    def checkout(self, full_name: str, dest: Path) -> Path:
        src = self.root / "repos" / full_name.replace("/", "__")
        if not src.is_dir():
            raise FileNotFoundError(f"no source fixture for {full_name}")
        shutil.copytree(src, dest)
        return dest
