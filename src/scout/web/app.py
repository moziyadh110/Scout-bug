import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from scout.bounty import Bounties, BountyError, opted_in
from scout.improve import history, nightly, verify
from scout.index import build_index, latest_index
from scout.match import match
from scout.models import now
from scout.playbook import current_playbook, versions
from scout.skills import VOCABULARY
from scout.webhook import merged_pr, sign, signature_ok
from scout.wiring import Deps

HERE = Path(__file__).parent


def create_app(deps: Deps) -> FastAPI:
    s = deps.settings
    local = s.scout_env == "local"
    # Local demo only: a per-process secret so simulated webhooks take the signed path.
    webhook_secret = s.github_webhook_secret or (secrets.token_hex(16) if local else "")
    bounties = Bounties(deps)

    app = FastAPI(title="Scout")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    scale = 10**s.usdc_decimals
    tpl.env.filters["usdc"] = lambda u: f"{u / scale:,.2f}".rstrip("0").rstrip(".")
    tpl.env.filters["pct"] = lambda x: f"{round(100 * x)}%"
    tpl.env.globals.update(
        mode=deps.mode,
        ledger_kind=s.ledger_kind,
        local=local,
        github_kind=s.github_kind,
        explorer=deps.ledger.explorer_url,
    )

    def page(request: Request, name: str, ctx: dict[str, Any], status: int = 200) -> HTMLResponse:
        return tpl.TemplateResponse(request, name, ctx, status_code=status)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": deps.mode, "ledger": s.ledger_kind, "github": s.github_kind}

    # --- matching ------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        users = (
            sorted(json.loads((s.fixtures_dir / "github" / "users.json").read_text()))
            if s.github_kind == "fixtures"
            else []
        )
        return page(
            request,
            "home.html",
            {
                "snap": latest_index(deps),
                "users": users,
                "skills": sorted(VOCABULARY),
                "version": current_playbook(deps)[1],
            },
        )

    @app.post("/match", response_class=HTMLResponse)
    async def do_match(request: Request, login: str = Form(...)) -> HTMLResponse:
        form = await request.form()
        skills = [str(x) for x in form.getlist("skills")]
        try:
            result = await asyncio.to_thread(match, deps, login.strip(), skills)
        except (RuntimeError, KeyError) as e:
            return page(request, "_error.html", {"error": str(e)}, 400)
        return page(request, "_matches.html", {"r": result})

    # --- index ---------------------------------------------------------------------------

    @app.get("/index", response_class=HTMLResponse)
    def index_page(request: Request) -> HTMLResponse:
        return page(request, "index.html", {"snap": latest_index(deps)})

    @app.post("/index")
    async def do_index() -> RedirectResponse:
        await asyncio.to_thread(build_index, deps, current_playbook(deps)[0])
        return RedirectResponse("/index", status_code=303)

    # --- bounties ------------------------------------------------------------------------

    def bounty_page(request: Request, error: str = "", status: int = 200) -> HTMLResponse:
        snap = latest_index(deps)
        opted = [
            r.full_name
            for r in (snap.repos if snap else [])
            if opted_in(deps.github.read_file(r.full_name, ".scout.yml", r.default_branch))
        ]
        funder = deps.ledger.funder_account()
        return page(
            request,
            "bounties.html",
            {
                "bounties": bounties.all(),
                "opted": opted,
                "error": error,
                "now": now(),
                "funder": funder,
                "cap": s.bounty_cap_units,
                "window": s.dispute_window_seconds,
                "balances": {
                    "funder": deps.ledger.balance(funder),
                    "escrow": deps.ledger.balance("escrow"),
                },
            },
            status,
        )

    @app.get("/bounties", response_class=HTMLResponse)
    def bounties_page(request: Request) -> HTMLResponse:
        return bounty_page(request)

    @app.post("/bounties", response_model=None)
    async def fund(
        request: Request, repo: str = Form(...), issue: int = Form(...), usdc: float = Form(...)
    ) -> Response:
        try:
            await asyncio.to_thread(
                bounties.fund, repo, issue, deps.ledger.funder_account(), round(usdc * scale)
            )
        except (BountyError, KeyError) as e:
            return bounty_page(request, str(e), 400)
        return RedirectResponse("/bounties", status_code=303)

    @app.post("/bounties/{bounty_id}/dispute", response_model=None)
    async def dispute(request: Request, bounty_id: str, reason: str = Form("")) -> Response:
        try:  # ponytail: no user accounts yet; the backend acts as the demo funder
            bounties.dispute(bounty_id, deps.ledger.funder_account(), reason or "disputed")
        except BountyError as e:
            return bounty_page(request, str(e), 400)
        return RedirectResponse("/bounties", status_code=303)

    @app.post("/bounties/tick")
    async def tick() -> RedirectResponse:
        await asyncio.to_thread(bounties.tick)
        return RedirectResponse("/bounties", status_code=303)

    def handle_webhook(body: bytes, signature: str | None, event: str | None) -> dict[str, Any]:
        if not signature_ok(webhook_secret, body, signature):
            raise HTTPException(401, "bad or missing signature")
        ref = merged_pr(event, body)
        if ref is None:
            return {"handled": False}
        touched = bounties.on_pr_merged(*ref)  # re-fetches the PR; the payload is only a trigger
        return {"handled": True, "bounties": [b.id for b in touched]}

    @app.post("/webhooks/github")
    async def webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
        h = request.headers
        return await asyncio.to_thread(
            handle_webhook, body, h.get("X-Hub-Signature-256"), h.get("X-GitHub-Event")
        )

    @app.post("/demo/simulate-merge", response_model=None)
    async def simulate_merge(
        request: Request, repo: str = Form(...), pr: int = Form(...)
    ) -> Response:
        """Local only: a signed 'PR merged' event through the same handler as real webhooks."""
        if not local:
            raise HTTPException(404)
        body = json.dumps(
            {
                "action": "closed",
                "repository": {"full_name": repo},
                "pull_request": {"number": pr, "merged": True},
            }
        ).encode()
        try:
            await asyncio.to_thread(
                handle_webhook, body, sign(webhook_secret, body), "pull_request"
            )
        except KeyError as e:
            return bounty_page(request, f"unknown PR: {e}", 400)
        return RedirectResponse("/bounties", status_code=303)

    # --- self-improvement ----------------------------------------------------------------

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return page(
            request,
            "dashboard.html",
            {"versions": versions(deps), "reports": list(reversed(history(deps)))[:10]},
        )

    @app.post("/nightly")
    async def run_nightly() -> RedirectResponse:
        await asyncio.to_thread(nightly, deps)
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/verify/{n}", response_class=HTMLResponse)
    def verify_version(request: Request, n: int) -> HTMLResponse:
        return page(request, "_verify.html", {"r": verify(deps, n)})

    return app
