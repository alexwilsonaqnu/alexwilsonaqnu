"""Shared fixtures for tests/ and evals/. They need a real Postgres with pgvector (CI: pgvector/pgvector image).

Set TEST_DATABASE_URL to point at a server; a throwaway database `solbrief_test` is (re)created on it.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
TEST_DB = "solbrief_test"


def _with_db(url: str, db: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{db}"))


# Configure the app BEFORE any solbrief import reads settings.
os.environ["DATABASE_URL"] = _with_db(ADMIN_URL, TEST_DB)
os.environ["APP_ENV"] = "test"
os.environ["DRY_RUN"] = "true"
os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ.pop("LOGFIRE_TOKEN", None)
os.environ.pop("POSTMARK_SERVER_TOKEN", None)
os.environ.pop("ANTHROPIC_API_KEY", None)  # tests never call Claude


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.abspath(__file__))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "src/solbrief/db/migrations"))
    command.upgrade(cfg, "head")
    from solbrief import telemetry

    telemetry.configure("test")
    yield


@pytest.fixture(autouse=True)
def no_live_sources(monkeypatch):
    """No test touches the network: the adapter registry is empty unless a test installs fakes."""
    import solbrief.adapters

    monkeypatch.setattr(solbrief.adapters, "ADAPTERS", ())


@pytest.fixture(autouse=True)
def clean_tables(database):
    yield
    from sqlalchemy import text

    from solbrief.db import get_engine

    with get_engine().begin() as conn:
        conn.execute(
            text("TRUNCATE runs, problems, solution_cards, experts, source_fetch_cache, procrastinate_jobs CASCADE")
        )
