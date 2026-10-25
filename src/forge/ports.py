"""Interfaces for every external service. Business logic depends on these only."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Completion:
    text: str
    tokens: int


@dataclass
class TestResult:
    exit_code: int
    output: str
    timed_out: bool

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class LLMProvider(Protocol):
    def complete(self, model: str, system: str, user: str) -> Completion: ...


class Sandbox(Protocol):
    def run_tests(self, repo_dir: Path, files: dict[str, str]) -> TestResult:
        """Copy repo_dir, overlay files, run pytest. Never touches repo_dir."""
        ...


class ObjectStore(Protocol):
    def put_json(self, key: str, data: Any) -> None: ...
    def get_json(self, key: str) -> Any | None: ...
    def list(self, prefix: str) -> list[str]: ...


class Memory(Protocol):
    def add(self, doc_id: str, task_id: str, text: str, example: str) -> None: ...
    def search(self, query: str, k: int, exclude_task: str | None = None) -> list[str]: ...
    def snapshot_hash(self) -> str: ...


class Ledger(Protocol):
    """Value moves payer -> escrow -> treasury|payer. `key` makes each call idempotent."""

    def lock(self, key: str, payer: str, amount: int) -> str: ...
    def release(self, key: str, amount: int, memo: str) -> str: ...
    def refund(self, key: str, payer: str, amount: int, memo: str) -> str: ...
    def record(self, key: str, memo: str) -> str: ...
    def get_memo(self, ref: str) -> str | None: ...
    def balance(self, account: str) -> int: ...
    def explorer_url(self, ref: str) -> str | None: ...
    def payer_account(self) -> str:
        """Demo payer the backend can sign for (no browser wallet in this build)."""
        ...
