from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from scout.wiring import Deps

HERE = Path(__file__).parent


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="Scout")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": deps.mode, "ledger": deps.settings.ledger_kind}

    return app
