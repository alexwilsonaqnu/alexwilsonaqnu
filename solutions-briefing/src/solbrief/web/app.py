"""Web process (Fly process group `web`).

Health check, reporter search + card pages (M4), and the authenticated admin API.
Later milestones add the inbound-email webhook (M6) and onboarding/settings (M7).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import logfire
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from solbrief import telemetry
from solbrief.config import get_settings
from solbrief.db import get_engine
from solbrief.web.admin import router as admin_router
from solbrief.web.search import router as search_router

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from solbrief.queue import app as queue_app

    async with queue_app.open_async():
        yield


def create_app() -> FastAPI:
    telemetry.configure("web")
    app = FastAPI(title="Solutions Briefing", lifespan=lifespan, docs_url=None, redoc_url=None)
    logfire.instrument_fastapi(app, excluded_urls="/healthz")
    app.include_router(admin_router)
    app.include_router(search_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        try:
            with get_engine().connect() as conn:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception as exc:
            return JSONResponse({"ok": False, "db": "error", "detail": type(exc).__name__}, status_code=503)
        return JSONResponse({"ok": True, "db": "ok", "schema_revision": revision, "env": get_settings().app_env})

    return app


app = create_app()
