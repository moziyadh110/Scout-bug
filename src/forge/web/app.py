import asyncio
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from forge.agent import load_task, load_tasks, memory_example
from forge.bounty import Bounties, BountyError
from forge.improve import latest, playbook_hash, verify, versions
from forge.models import Playbook, Run, RunStatus
from forge.wiring import Deps

HERE = Path(__file__).parent
LAMPORTS = 1_000_000_000


@dataclass
class LiveRun:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    done: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        with self.lock:
            self.events.append((kind, data))


def events_from_run(run: Run) -> list[tuple[str, dict[str, Any]]]:
    """Rebuild the live event stream from a stored trace."""
    ev: list[tuple[str, dict[str, Any]]] = []
    if run.retrieved:
        ev.append(("memory", {"examples": run.retrieved}))
    ev.append(("plan", {"plan": run.plan}))
    for a in run.attempts:
        ev.append(("patch", {"attempt": a.n, "diff": a.diff or "(no valid file edits returned)"}))
        ev.append(
            (
                "tests",
                {
                    "attempt": a.n,
                    "passed": a.test_exit_code == 0 and not a.timed_out,
                    "output": a.test_output,
                },
            )
        )
        if a.judge_verdict:
            ev.append(("judge", {"verdict": a.judge_verdict}))
    ev.append(("done", {"status": run.status.value}))
    return ev


def _sse(event: str, html: str) -> str:
    data = "\n".join(f"data: {line}" for line in html.splitlines() or [""])
    return f"event: {event}\n{data}\n\n"


def create_app(deps: Deps) -> FastAPI:
    bounties = Bounties(deps)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(bounties.sweep_expired)  # refund anything stuck past deadline
        yield

    app = FastAPI(title="Forge", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters["sol"] = lambda v: f"{v / LAMPORTS:.4f}"
    live: dict[str, LiveRun] = {}
    tasks_bg: set[asyncio.Task[None]] = set()

    def current_playbook() -> tuple[Playbook, int | None]:
        v = latest(deps)
        if v:
            return v.playbook, v.version
        return Playbook.model_validate_json(deps.settings.playbook_seed.read_text()), None

    def work(bounty_id: str, run_id: str) -> None:
        state = live[run_id]
        b = bounties.get(bounty_id)
        try:
            task = load_task(deps.settings.benchmarks_dir, b.task_id)
            pb, version = current_playbook()
            run = deps.agent.solve(task, pb, playbook_hash(pb), version, state.emit, run_id)
            deps.store.put_json(f"traces/{run.id}.json", run.model_dump(mode="json"))
            if run.status == RunStatus.PASSED:
                deps.memory.add(
                    run.id,
                    run.task_id,
                    run.issue + "\n" + run.attempts[-1].diff,
                    memory_example(run),
                )
            b = bounties.settle(b, run)
            state.emit(
                "bounty",
                {
                    "state": b.state.value,
                    "ref": b.settle_ref,
                    "url": deps.ledger.explorer_url(b.settle_ref or ""),
                },
            )
        except Exception as e:  # surface to the UI; the bounty stays where it was
            state.emit("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            state.done = True

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": deps.mode, "ledger": deps.settings.ledger_kind}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        payer = deps.ledger.payer_account()
        _, version = current_playbook()
        return tpl.TemplateResponse(
            request,
            "index.html",
            {
                "tasks": load_tasks(deps.settings.benchmarks_dir),
                "bounties": bounties.all(),
                "payer": payer,
                "version": version,
                "mode": deps.mode,
                "balances": {
                    "payer": deps.ledger.balance(payer),
                    "escrow": deps.ledger.balance("escrow"),
                    "treasury": deps.ledger.balance("treasury"),
                },
                "cap": deps.settings.bounty_cap_lamports,
                "explorer": deps.ledger.explorer_url,
            },
        )

    @app.post("/bounties")
    async def create_bounty(task_id: str = Form(...), amount_sol: float = Form(...)) -> Any:
        try:
            load_task(deps.settings.benchmarks_dir, task_id)
        except FileNotFoundError:
            raise HTTPException(404, "unknown task") from None
        try:
            b = await asyncio.to_thread(
                bounties.create, task_id, deps.ledger.payer_account(), round(amount_sol * LAMPORTS)
            )
        except BountyError as e:
            raise HTTPException(400, str(e)) from None
        run_id = uuid.uuid4().hex[:12]
        live[run_id] = LiveRun()
        bounties.start(b, run_id)
        t = asyncio.create_task(asyncio.to_thread(work, b.id, run_id))
        tasks_bg.add(t)
        t.add_done_callback(tasks_bg.discard)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    def _bounty_for(run_id: str) -> Any:
        return next((b for b in bounties.all() if b.run_id == run_id), None)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str) -> HTMLResponse:
        stored = deps.store.get_json(f"traces/{run_id}.json")
        if stored is None and run_id not in live:
            raise HTTPException(404, "unknown run")
        bounty = _bounty_for(run_id)
        st = live.get(run_id)
        finished = stored is not None and (st is None or st.done)
        events = events_from_run(Run.model_validate(stored)) if finished else []
        return tpl.TemplateResponse(
            request,
            "run.html",
            {
                "run_id": run_id,
                "bounty": bounty,
                "finished": finished,
                "events": events,
                "explorer": deps.ledger.explorer_url,
                "task": load_task(
                    deps.settings.benchmarks_dir,
                    bounty.task_id if bounty else Run.model_validate(stored).task_id,
                ),
            },
        )

    @app.get("/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str) -> StreamingResponse:
        st = live.get(run_id)
        if st is None:
            raise HTTPException(404, "run not live")
        event_tpl = tpl.env.get_template("_event.html")

        async def stream() -> AsyncIterator[str]:
            i = 0
            while True:
                # ponytail: polls the in-memory event list; fine for one process, use a
                # broker if web and worker split.
                with st.lock:
                    batch = st.events[i:]
                i += len(batch)
                for kind, data in batch:
                    html = event_tpl.render(kind=kind, data=data, explorer=deps.ledger.explorer_url)
                    yield _sse("status" if kind == "status" else "log", html)
                if st.done and i == len(st.events):
                    yield _sse("close", "")
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/runs/{run_id}/patch.diff", response_class=PlainTextResponse)
    def patch(run_id: str) -> PlainTextResponse:
        stored = deps.store.get_json(f"traces/{run_id}.json")
        if not stored or not stored["attempts"]:
            raise HTTPException(404, "no patch")
        return PlainTextResponse(
            stored["attempts"][-1]["diff"],
            headers={"Content-Disposition": f'attachment; filename="forge-{run_id}.diff"'},
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        reports = [deps.store.get_json(k) for k in reversed(deps.store.list("nightly"))]
        return tpl.TemplateResponse(
            request,
            "dashboard.html",
            {
                "versions": versions(deps),
                "reports": reports[:10],
                "mode": deps.mode,
                "explorer": deps.ledger.explorer_url,
            },
        )

    @app.post("/verify/{n}", response_class=HTMLResponse)
    def verify_version(request: Request, n: int) -> HTMLResponse:
        return tpl.TemplateResponse(request, "_verify.html", {"r": verify(deps, n)})

    return app
