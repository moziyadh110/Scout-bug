import json

from fastapi.testclient import TestClient

from scout.web.app import create_app
from scout.webhook import sign


def test_local_demo_end_to_end_over_http(deps):
    deps.settings.dispute_window_seconds = 0
    with TestClient(create_app(deps)) as c:
        assert c.get("/healthz").json() == {
            "status": "ok",
            "mode": "mock",
            "ledger": "sqlite",
            "github": "fixtures",
        }
        assert "No index yet" in c.get("/").text
        assert c.post("/index").status_code == 200  # follows the redirect to /index
        assert "claimed by sam" in c.get("/index").text

        html = c.post("/match", data={"login": "newbie"}).text
        assert "Support a custom delimiter in read_rows" in html and "✓ builds" in html

        assert (
            "opted in"
            in c.post("/bounties", data={"repo": "acme/dateparse", "issue": 5, "usdc": 10}).text
        )
        c.post("/bounties", data={"repo": "acme/tinycsv", "issue": 12, "usdc": 10})
        assert "bounty 10 test USDC" in c.post("/match", data={"login": "newbie"}).text

        c.post("/demo/simulate-merge", data={"repo": "acme/tinycsv", "pr": 21})
        assert "verified" in c.get("/bounties").text
        c.post("/bounties/tick")
        page = c.get("/bounties").text
        assert "released" in page and "credential" in page
    assert deps.ledger.balance("gh:newbie") == 10_000_000


def test_webhook_rejects_unsigned_and_accepts_signed(deps, monkeypatch):
    deps.settings.github_webhook_secret = "whsec-test"
    body = json.dumps(
        {
            "action": "closed",
            "repository": {"full_name": "acme/tinycsv"},
            "pull_request": {"number": 21, "merged": True},
        }
    ).encode()
    with TestClient(create_app(deps)) as c:
        h = {"X-GitHub-Event": "pull_request"}
        assert c.post("/webhooks/github", content=body, headers=h).status_code == 401
        bad = h | {"X-Hub-Signature-256": sign("wrong", body)}
        assert c.post("/webhooks/github", content=body, headers=bad).status_code == 401
        good = h | {"X-Hub-Signature-256": sign("whsec-test", body)}
        assert c.post("/webhooks/github", content=body, headers=good).json()["handled"]


def test_dashboard_nightly_and_verify(deps):
    with TestClient(create_app(deps)) as c:
        c.post("/nightly")
        page = c.get("/dashboard").text
        assert "v2" in page and "70%" in page and "accepted v2" in page
        assert "matches ledger" in c.post("/verify/2").text


def test_simulate_route_is_local_only(deps):
    deps.settings.scout_env = "cloud"
    deps.settings.github_webhook_secret = "x" * 16
    with TestClient(create_app(deps)) as c:
        r = c.post("/demo/simulate-merge", data={"repo": "acme/tinycsv", "pr": 21})
        assert r.status_code == 404
