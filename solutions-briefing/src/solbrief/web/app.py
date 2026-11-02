"""Web process (Fly process group `web`).

M1b: health check, a placeholder home page, and the authenticated admin API.
Later milestones add search (M4), card pages (M4), inbound-email webhook (M6), onboarding/settings (M7).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import logfire
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

from solbrief import telemetry
from solbrief.config import get_settings
from solbrief.db import get_engine
from solbrief.web.admin import router as admin_router


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

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        try:
            with get_engine().connect() as conn:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception as exc:
            return JSONResponse({"ok": False, "db": "error", "detail": type(exc).__name__}, status_code=503)
        return JSONResponse({"ok": True, "db": "ok", "schema_revision": revision, "env": get_settings().app_env})

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (
            "<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Solutions Briefing</title>"
            "<body style='font-family:system-ui;max-width:40rem;margin:3rem auto;padding:0 1rem'>"
            "<h1>Solutions Briefing</h1><p>A Knight Lab project. Search and sign-up are coming soon.</p>"
        )

    return app


app = create_app()
