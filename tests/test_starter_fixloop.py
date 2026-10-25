import pytest

from scout.ports import Completion
from scout.starter.fixloop import draft_patch, parse_files

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


class FixingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, model: str, system: str, user: str) -> Completion:
        self.prompts.append(user)
        if "## TASK: CODE" in user:
            return Completion(f"### FILE: calc.py\n```python\n{FIXED}```", 1)
        return Completion("1. use +", 1)

    def list_models(self) -> list[str]:
        return []


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(BUGGY)
    (repo / "test_calc.py").write_text(TEST)
    return repo


def test_disabled_by_default(deps, tmp_path):
    with pytest.raises(RuntimeError, match="disabled"):
        draft_patch(deps.llm, deps.sandbox, deps.settings, _repo(tmp_path), "add is wrong")


def test_drafts_a_passing_patch_with_untrusted_framing(deps, tmp_path):
    deps.settings.enable_starter_patch = True
    llm = FixingLLM()
    draft = draft_patch(llm, deps.sandbox, deps.settings, _repo(tmp_path), "add subtracts")
    assert draft.passed and "+    return a + b" in draft.attempts[-1].diff
    assert all("<<<UNTRUSTED ISSUE" in p and "<<<UNTRUSTED FILE calc.py" in p for p in llm.prompts)


def test_parse_files_tolerates_real_model_formatting_and_rejects_tests():
    reply = (
        "## FILE: `calc.py`\n```py\nx = 1\n```\n"
        "### FILE: test_calc.py\n```python\nassert True\n```\n"
    )
    assert list(parse_files(reply, {"calc.py"})) == ["calc.py"]
