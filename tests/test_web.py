import time

from fastapi.testclient import TestClient

from forge.web.app import create_app


def test_end_to_end_bounty_flow(deps):
    with TestClient(create_app(deps)) as client:
        assert client.get("/healthz").json()["mode"] == "mock"
        assert "quicksort" in client.get("/").text

        r = client.post(
            "/bounties", data={"task_id": "gcd", "amount_sol": "0.01"}, follow_redirects=False
        )
        assert r.status_code == 303
        run_id = r.headers["location"].rsplit("/", 1)[1]

        with client.stream("GET", f"/runs/{run_id}/events") as s:
            body = "".join(s.iter_text())
        assert (
            "event: close" in body and "Sandbox tests passed" in body and "Bounty released" in body
        )
        assert "event: stepper" in body and "Payout" in body

        for _ in range(50):  # the worker thread finishes just after the last event
            page = client.get(f"/runs/{run_id}").text
            if "Download patch" in page:
                break
            time.sleep(0.1)
        assert "Download patch" in page
        diff = client.get(f"/runs/{run_id}/patch.diff").text
        assert diff.startswith("--- a/gcd.py")
        assert deps.ledger.balance("treasury") == 10_000_000


def test_rejects_over_cap_and_unknown_task(deps):
    with TestClient(create_app(deps)) as client:
        assert (
            client.post("/bounties", data={"task_id": "gcd", "amount_sol": "5"}).status_code == 400
        )
        assert (
            client.post("/bounties", data={"task_id": "nope", "amount_sol": "0.01"}).status_code
            == 404
        )


def test_dashboard_and_verify(deps):
    from forge.improve import ensure_seed

    ensure_seed(deps)
    with TestClient(create_app(deps)) as client:
        assert "v1" in client.get("/dashboard").text
        assert "matches ledger" in client.post("/verify/1").text
