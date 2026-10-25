from fastapi.testclient import TestClient

from forge.agent import load_tasks
from forge.web.app import create_app

BUGGY = "def average(xs):\n    return sum(xs) // len(xs)\n"
TEST = "from average import average\n\ndef test_basic():\n    assert average([1, 2]) == 1.5\n"


def _post(client, **overrides):
    data = {
        "name": "average",
        "issue": "average() floors the result",
        "code": BUGGY,
        "test": TEST,
        "amount_sol": "0.01",
    } | overrides
    return client.post("/bugs", data=data, follow_redirects=False)


def test_pasted_bug_gets_a_bounty_but_not_benchmarked(deps):
    with TestClient(create_app(deps)) as client:
        r = _post(client)
        assert r.status_code == 303
        run_id = r.headers["location"].rsplit("/", 1)[1]
        with client.stream("GET", f"/runs/{run_id}/events") as s:
            body = "".join(s.iter_text())
        # The mock has no script for user bugs, so it fails and the bounty is refunded.
        assert "Bounty refunded" in body
        assert "average" in client.get("/").text
    assert "average" not in {t.name for t in load_tasks(deps.settings.benchmarks_dir)}


def test_rejects_bug_whose_test_already_passes(deps):
    with TestClient(create_app(deps)) as client:
        r = _post(client, code="def average(xs):\n    return sum(xs) / len(xs)\n")
        assert r.status_code == 400 and "test passes" in r.text
        assert _post(client).status_code == 303  # the name was freed after rejection


def test_rejects_bad_names_and_duplicates(deps):
    with TestClient(create_app(deps)) as client:
        assert _post(client, name="../evil").status_code == 400
        assert _post(client, name="gcd").status_code == 400  # clashes with a benchmark task
        assert deps.ledger.balance("escrow") == 0


def test_disabled_in_cloud_without_hardened_sandbox(deps):
    deps.settings.allow_user_bugs = False
    with TestClient(create_app(deps)) as client:
        assert _post(client).status_code == 403
        assert "Report a new bug" not in client.get("/").text
