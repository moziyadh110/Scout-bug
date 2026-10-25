"""Interfaces for every external service. Business logic depends on these only."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Completion:
    text: str
    tokens: int
    model: str = ""  # the model that actually answered, as reported by the provider


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
    def list_models(self) -> list[str]: ...


class Sandbox(Protocol):
    def run_tests(self, repo_dir: Path, files: dict[str, str]) -> TestResult:
        """Copy repo_dir, overlay files, run pytest. Never touches repo_dir."""
        ...


class ObjectStore(Protocol):
    def put_json(self, key: str, data: Any) -> None: ...
    def get_json(self, key: str) -> Any | None: ...
    def list(self, prefix: str) -> list[str]: ...
