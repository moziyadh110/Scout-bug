import asyncio
import logging
import shutil
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
from starlette.responses import Response

from forge.agent import (
    create_user_bug,
    find_task,
    judge_passed,
    load_tasks,
    memory_example,
    user_bugs_dir,
)
from forge.bounty import Bounties, BountyError
from forge.improve import latest, playbook_hash, verify, versions
from forge.models import Playbook, Run, RunStatus
from forge.web.ui import LAMPORTS, board_stats, diff_html, sol, stepper
from forge.wiring import Deps

HERE = Path(__file__).parent
log = logging.getLogger("forge")


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
        passed = a.test_exit_code == 0 and not a.timed_out
        ev.append(("tests", {"attempt": a.n, "passed": passed, "output": a.test_output}))
        if a.judge_verdict:
            ev.append(("judge", {"verdict": a.judge_verdict}))
    ev.append(("done", {"status": run.status.value}))
    return ev


def _sse(event: str, html: str) -> str:
    data = "\n".join(f"data: {line}" for line in html.splitlines() or [""])
    return f"event: {event}\n{data}\n\n"


def _stage(kind: str, data: dict[str, Any]) -> str | None:
    """Which progress stage an event moves the run to, if any."""
    return {
        "status": data.get("status"),
        "done": data.get("status"),
        "bounty": data.get("state"),
    }.get(kind)


def create_app(deps: Deps) -> FastAPI:
    bounties = Bounties(deps)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(bounties.sweep_expired)  # refund anything stuck past deadline
        yield

    app = FastAPI(title="Forge", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters.update(sol=sol, diff=diff_html)
    tpl.env.globals.update(
        mode=deps.mode,
        coder_model=deps.settings.model_for("coder"),
        ledger_kind=deps.settings.ledger_kind,
        stepper=stepper,
        judge_passed=judge_passed,
        explorer=deps.ledger.explorer_url,
    )
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
            task = find_task(deps.settings, b.task_id)
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
        except Exception as e:  # surface to the UI and give the money back
            log.exception("run %s failed", run_id)
            state.emit("error", {"message": f"{type(e).__name__}: {e}"})
            try:
                b = bounties.abort(bounties.get(bounty_id))
            except Exception:
                log.exception("refund after crash failed for bounty %s", bounty_id)
        finally:
            state.emit(
                "bounty",
                {
                    "state": b.state.value,
                    "ref": b.settle_ref,
                    "url": deps.ledger.explorer_url(b.settle_ref) if b.settle_ref else None,
                    "run_id": run_id,
                    "amount": b.amount,
                },
            )
            state.done = True

    def render_index(
        request: Request, error: str = "", status: int = 200, tab: str = "bench"
    ) -> HTMLResponse:
        bounties.sweep_expired()
        payer = deps.ledger.payer_account()
        v = latest(deps)
        all_bounties = bounties.all()
        return tpl.TemplateResponse(
            request,
            "index.html",
            status_code=status,
            context={
                "error": error,
                "tab": tab,
                "user_bugs": deps.settings.user_bugs_enabled,
                "bench_tasks": load_tasks(deps.settings.benchmarks_dir),
                "user_tasks": load_tasks(user_bugs_dir(deps.settings)),
                "bounties": all_bounties,
                "stats": board_stats(all_bounties),
                "version": v,
                "payer": payer,
                "balances": {
                    "payer": deps.ledger.balance(payer),
                    "escrow": deps.ledger.balance("escrow"),
                    "treasury": deps.ledger.balance("treasury"),
                },
                "cap": deps.settings.bounty_cap_lamports,
            },
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": deps.mode, "ledger": deps.settings.ledger_kind}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return render_index(request)

    async def launch(request: Request, task_id: str, amount_sol: float, tab: str) -> Response:
        try:
            b = await asyncio.to_thread(
                bounties.create, task_id, deps.ledger.payer_account(), round(amount_sol * LAMPORTS)
            )
        except BountyError as e:
            return render_index(request, str(e), 400, tab)
        run_id = uuid.uuid4().hex[:12]
        live[run_id] = LiveRun()
        bounties.start(b, run_id)
        t = asyncio.create_task(asyncio.to_thread(work, b.id, run_id))
        tasks_bg.add(t)
        t.add_done_callback(tasks_bg.discard)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/bounties")
    async def create_bounty(
        request: Request, task_id: str = Form(...), amount_sol: float = Form(...)
    ) -> Response:
        try:
            find_task(deps.settings, task_id)
        except FileNotFoundError:
            return render_index(request, f"Unknown bug {task_id!r}.", 404)
        return await launch(request, task_id, amount_sol, "bench")

    @app.post("/bugs")
    async def create_bug(
        request: Request,
        name: str = Form(...),
        issue: str = Form(...),
        code: str = Form(...),
        test: str = Form(...),
        amount_sol: float = Form(...),
    ) -> Response:
        if not deps.settings.user_bugs_enabled:
            raise HTTPException(403, "pasted bugs are disabled without a hardened sandbox")
        if not 0 < round(amount_sol * LAMPORTS) <= deps.settings.bounty_cap_lamports:
            return render_index(
                request, "Bounty amount is outside the allowed range.", 400, "paste"
            )
        try:
            task = create_user_bug(deps.settings, name.strip(), issue, code, test)
        except ValueError as e:
            return render_index(request, str(e), 400, "paste")
        # A bug must be proven: its test has to fail (or hang) on the submitted code.
        result = await asyncio.to_thread(deps.sandbox.run_tests, task.repo_dir, {})
        if result.passed or result.exit_code == 5:  # 5 = pytest collected no tests
            shutil.rmtree(task.repo_dir.parent)
            why = "the test passes on this code" if result.passed else "no tests were found"
            return render_index(
                request, f"Rejected: {why}, so there is no bug to fix.", 400, "paste"
            )
        return await launch(request, task.name, amount_sol, "paste")

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str) -> HTMLResponse:
        stored = deps.store.get_json(f"traces/{run_id}.json")
        if stored is None and run_id not in live:
            raise HTTPException(404, "unknown run")
        run = Run.model_validate(stored) if stored else None
        bounty = next((b for b in bounties.all() if b.run_id == run_id), None)
        st = live.get(run_id)
        finished = run is not None and (st is None or st.done)
        stage = bounty.state.value if bounty else run.status.value if run else "queued"
        return tpl.TemplateResponse(
            request,
            "run.html",
            {
                "run_id": run_id,
                "run": run,
                "bounty": bounty,
                "finished": finished,
                "stage": stage if finished else "queued",
                "events": events_from_run(run) if finished and run else [],
                "models": sorted({p.model for p in run.prompts}) if run else [],
                "task": find_task(deps.settings, bounty.task_id if bounty else run.task_id),  # type: ignore[union-attr]
            },
        )

    @app.get("/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str) -> StreamingResponse:
        st = live.get(run_id)
        if st is None:
            raise HTTPException(404, "run not live")
        event_tpl = tpl.env.get_template("_event.html")
        stepper_tpl = tpl.env.get_template("_stepper.html")

        async def stream() -> AsyncIterator[str]:
            i = 0
            while True:
                # ponytail: polls the in-memory event list; fine for one process, use a
                # broker if web and worker split.
                with st.lock:
                    batch = st.events[i:]
                i += len(batch)
                for kind, data in batch:
                    stage = _stage(kind, data)
                    if stage:
                        steps = stepper(stage, data.get("attempt"))
                        yield _sse("stepper", stepper_tpl.render(steps=steps))
                    if kind != "status":
                        yield _sse("log", event_tpl.render(kind=kind, data=data, run_id=run_id))
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
            },
        )

    @app.post("/verify/{n}", response_class=HTMLResponse)
    def verify_version(request: Request, n: int) -> HTMLResponse:
        return tpl.TemplateResponse(request, "_verify.html", {"r": verify(deps, n)})

    return app
