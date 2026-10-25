import pytest
from fastapi.testclient import TestClient

from forge.adapters.local.sandbox import SubprocessSandbox
from forge.agent import judge_passed, parse_files
from forge.improve import ensure_seed, nightly
from forge.ports import Completion
from forge.web.app import create_app


def test_real_model_formatting_is_tolerated():
    reply = "## FILE: `gcd.py`\n```py\ndef gcd(a, b): ...\n```\n"
    assert list(parse_files(reply, {"gcd.py"})) == ["gcd.py"]
    assert judge_passed("**PASS**") and judge_passed("Verdict: PASS - fixed")
    assert not judge_passed("FAIL: special-cases the test") and not judge_passed("Verdict: FAIL")


def test_sandbox_output_redacts_secrets(tmp_path):
    secret = "sk-test-supersecret-123"
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "test_leak.py").write_text(f"def test_leak():\n    print({secret!r})\n    assert 0\n")
    out = SubprocessSandbox(10, [secret]).run_tests(repo, {}).output
    assert secret not in out and "[REDACTED]" in out


def test_crashed_run_refunds_the_bounty(deps):
    class Down:
        def complete(self, model: str, system: str, user: str) -> Completion:
            raise ConnectionError("LLM endpoint unreachable")

    deps.llm = Down()
    with TestClient(create_app(deps)) as client:
        r = client.post(
            "/bounties", data={"task_id": "gcd", "amount_sol": "0.01"}, follow_redirects=False
        )
        run_id = r.headers["location"].rsplit("/", 1)[1]
        with client.stream("GET", f"/runs/{run_id}/events") as s:
            body = "".join(s.iter_text())
    assert "unreachable" in body and "refunded" in body
    assert deps.ledger.balance("escrow") == 0


def test_nightly_refuses_to_mix_mock_and_real(deps):
    ensure_seed(deps)  # mock v1
    deps.settings.forge_llm = "openai"  # pretend the LLM switched to a real one
    with pytest.raises(RuntimeError, match="separate FORGE_DATA_DIR"):
        nightly(deps)
