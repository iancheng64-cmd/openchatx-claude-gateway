"""Applies the Alembic migration chain in `mcp_gateway.migrations` to the
gateway's SQLite database.

Two entry points:
- `run_migrations`: takes an already-open `sqlite3.Connection` and migrates
  it in place. Used by `Storage.__init__` so the server auto-migrates on
  every startup, and works for a `:memory:` connection too (see
  migrations/env.py for why that requires sharing the caller's connection
  rather than opening a fresh one).
- `run_migrations_for_path`: opens `db_path` itself and migrates it, for the
  standalone `mcp-gateway migrate` CLI command -- e.g. to apply migrations
  as an explicit deploy step, without booting the rest of the gateway.

Both are safe to call repeatedly, including against a pre-Alembic database
left over from an older version of the gateway (see migrations/versions/):
Alembic tracks the applied revision in an `alembic_version` table, so a
database already at the latest revision is a no-op either way.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return cfg


def run_migrations(connection: sqlite3.Connection) -> None:
    """Bring `connection`'s schema up to the latest revision, in place.

    Wraps `connection` in a SQLAlchemy engine that always hands back this
    same DBAPI connection (`creator=` + `StaticPool`) rather than opening
    its own, so Alembic operates on the exact connection/session the caller
    is using. `dispose(close=False)` at the end tears down that wrapper
    without closing `connection` itself -- it's still the caller's to use
    and close afterwards.
    """
    engine = create_engine("sqlite://", creator=lambda: connection, poolclass=StaticPool)
    try:
        with engine.connect() as bound_connection:
            cfg = _alembic_config()
            cfg.attributes["connection"] = bound_connection
            command.upgrade(cfg, "head")
    finally:
        engine.dispose(close=False)


def run_migrations_for_path(db_path: str | Path) -> None:
    """`mcp-gateway migrate`: open `db_path` directly and migrate it."""
    resolved = Path(db_path)
    if str(resolved) != ":memory:":
        resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved))
    try:
        run_migrations(conn)
    finally:
        conn.close()
