"""Deterministic offline LLM. Anything scored with it is labelled 'mock'."""

from scout.ports import Completion


class MockLLM:
    def complete(self, model: str, system: str, user: str) -> Completion:
        return Completion("", len(user) // 4, "mock")

    def list_models(self) -> list[str]:
        return ["mock"]
