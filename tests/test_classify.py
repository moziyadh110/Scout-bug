from scout.adapters.local.inproc_batch import InProcessBatch
from scout.adapters.local.mock_llm import MockLLM
from scout.classify import UNCLASSIFIED, classify_issues, classify_prompt, parse_class
from scout.issues import filter_issues
from scout.models import Playbook
from scout.ports import Completion

PB = Playbook(classify_prompt="You classify GitHub issues.", plan_prompt="")


def test_mock_classification_of_fixture_issues(deps):
    issues, _ = filter_issues(
        deps.github.open_issues("acme/tinycsv") + deps.github.open_issues("acme/dateparse")
    )
    classes, failed = classify_issues(InProcessBatch(MockLLM()), deps.settings, PB, issues)
    assert failed == []
    c12 = classes["acme/tinycsv#12"]
    assert (c12.size, c12.clarity) == ("S", 5)
    assert c12.skills == ["parsing"] and c12.likely_files == ["tinycsv/__init__.py"]
    assert classes["acme/dateparse#6"].size == "L"


def test_prompt_wraps_issue_as_untrusted(deps):
    issue = deps.github.issue("acme/tinycsv", 12)
    p = classify_prompt(issue)
    assert "<<<UNTRUSTED ISSUE" in p and issue.body in p
    assert p.index("<<<UNTRUSTED") < p.index(issue.title) < p.index("<<<END UNTRUSTED")


def test_parse_rejects_garbage_and_filters_unknown_skills():
    assert parse_class("no json here") is None
    assert parse_class('{"clarity": 9, "size": "S"}') is None
    c = parse_class('<think>hmm</think> {"clarity": 3, "size": "M", "skills": ["csv", "hacking"]}')
    assert c is not None and c.skills == ["csv"]


class Garbage:
    def complete(self, model: str, system: str, user: str) -> Completion:
        return Completion("ignore previous instructions", 1)

    def list_models(self) -> list[str]:
        return []


def test_bad_model_output_falls_back_conservatively(deps):
    issue = deps.github.issue("acme/tinycsv", 12)
    classes, failed = classify_issues(InProcessBatch(Garbage()), deps.settings, PB, [issue])
    assert failed == [issue.key] and classes[issue.key] == UNCLASSIFIED
