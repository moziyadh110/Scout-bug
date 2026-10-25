from forge.agent import parse_files
from forge.improve import ensure_seed, nightly, verify, versions


def test_parse_files_rejects_tests_and_unknown_files():
    reply = (
        "### FILE: gcd.py\n```python\ndef gcd(a, b): ...\n```\n"
        "### FILE: test_gcd.py\n```python\nassert True\n```\n"
        "### FILE: other.py\n```python\nx = 1\n```\n"
    )
    assert list(parse_files(reply, {"gcd.py"})) == ["gcd.py"]


def test_seed_then_two_nightlies(deps):
    v1 = ensure_seed(deps)
    assert v1.eval.mode == "mock"
    assert v1.eval.train_pass_rate == 0.5  # mock: easy tasks pass, hard fail at 0 retries

    r1 = nightly(deps)
    assert r1["accepted"] == 2
    assert any(not c["improved"] for c in r1["candidates"])  # the no-op strategy edit is rejected
    vs = versions(deps)
    assert vs[1].eval.train_pass_rate > vs[0].eval.train_pass_rate
    assert vs[1].parent == 1

    r2 = nightly(deps)  # nothing left to learn from on train
    assert r2["accepted"] is None
    assert len(versions(deps)) == 2

    for v in versions(deps):
        assert verify(deps, v.version)["ok"]


def test_verify_detects_tampering(deps):
    ensure_seed(deps)
    data = deps.store.get_json("versions/v0001.json")
    data["eval"]["train_pass_rate"] = 1.0
    deps.store.put_json("versions/v0001.json", data)
    assert not verify(deps, 1)["ok"]
