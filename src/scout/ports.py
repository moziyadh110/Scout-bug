"""Interfaces for every external service. Business logic depends on these only."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from scout.models import ClosedIssue, Issue, PullRequest, RepoStats, UserRepo


@dataclass
class Completion:
    text: str
    tokens: int
    model: str = ""  # the model that actually answered, as reported by the provider


@dataclass
class RunResult:
    exit_code: int
    output: str
    timed_out: bool

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class LLMProvider(Protocol):
    def complete(self, model: str, system: str, user: str) -> Completion: ...
    def list_models(self) -> list[str]: ...


class Sandbox(Protocol):
    def run(
        self,
        repo_dir: Path,
        args: list[str],
        files: dict[str, str] | None = None,
        execute_repo_code: bool = True,
    ) -> RunResult:
        """Run `python <args>` on a private copy of repo_dir with `files` overlaid.

        Never touches repo_dir and never exposes host secrets. execute_repo_code=False is
        for static tools (ruff, mypy) that read the code without running it.
        """
        ...


class ObjectStore(Protocol):
    def put_json(self, key: str, data: Any) -> None: ...
    def get_json(self, key: str) -> Any | None: ...
    def list(self, prefix: str) -> list[str]: ...


class GitHub(Protocol):
    """Read-only GitHub access."""

    def search_repos(
        self, min_stars: int, max_stars: int, pushed_after: datetime
    ) -> list[RepoStats]:
        """Python repos in the star range, pushed after the date, not archived."""
        ...

    def repo(self, full_name: str) -> RepoStats: ...
    def open_issues(self, full_name: str) -> list[Issue]: ...
    def issue(self, full_name: str, number: int) -> Issue: ...
    def read_file(self, full_name: str, path: str, ref: str | None = None) -> str | None: ...
    def user_repos(self, login: str) -> list[UserRepo]: ...
    def pull_request(self, full_name: str, number: int) -> PullRequest: ...
    def closing_prs(self, full_name: str, issue_number: int) -> list[PullRequest]: ...
    def has_write_access(self, full_name: str, login: str) -> bool: ...
    def closed_issues(
        self, full_name: str, closed_after: datetime, closed_before: datetime
    ) -> list[ClosedIssue]: ...
    def checkout(self, full_name: str, dest: Path) -> Path:
        """Put the default branch source at dest (a fresh directory) and return it."""
        ...


@dataclass
class BatchRequest:
    id: str
    model: str
    system: str
    user: str


class BatchLLM(Protocol):
    """Many independent completions at once (DigitalOcean Batch Inference in cloud mode)."""

    def run(self, requests: list[BatchRequest]) -> dict[str, Completion]:
        """Results keyed by request id. Failed requests are missing from the result."""
        ...
