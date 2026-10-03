"""Alembic environment for the gateway's SQLite schema.

This runs in two different ways:

- **Embedded** (the normal path): `mcp_gateway.db_migrations.run_migrations`
  builds a `Config` with `attributes["connection"]` set to an already-open
  SQLAlchemy connection wrapping the caller's own `sqlite3.Connection` --
  used both by `Storage.__init__` (auto-migrate on startup) and by
  `mcp-gateway migrate` (see cli.py). Running against the caller's own
  connection, rather than opening a fresh one from a path, matters for a
  `:memory:` database: its schema only exists for the lifetime of the one
  connection that created it, so a second connection to `:memory:` would see
  an empty database.
- **Plain Alembic CLI**, for local development when authoring a new
  migration (see the README's "Database migrations" section), via the
  top-level `alembic.ini`: `alembic -x db-path=/path/to/dev.db revision -m
  "..."` / `... upgrade head`. There's no long-lived connection to share in
  this mode, so a throwaway engine is built from `-x db-path`.

There's no SQLAlchemy ORM/Core metadata for this schema (see the versions/
migrations themselves) -- it's plain, hand-rolled SQLite DDL via
`op.execute()`, consistent with the rest of `storage.py`. That also means
`--autogenerate` isn't available and offline mode (SQL-script generation
without a live connection) isn't supported.
"""

from __future__ import annotations

from alembic import context

config = context.config
target_metadata = None


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    # Plain CLI usage: `alembic -x db-path=/path/to/gateway.db upgrade head`
    db_path = context.get_x_argument(as_dictionary=True).get("db-path")
    if not db_path:
        raise RuntimeError(
            "Pass -x db-path=/path/to/gateway.db, e.g.: "
            "alembic -x db-path=/tmp/dev.db upgrade head "
            "(see the README's 'Database migrations' section)"
        )
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    connectable = create_engine(f"sqlite:///{db_path}", poolclass=NullPool)
    with connectable.connect() as db_connection:
        context.configure(connection=db_connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError(
        "Offline migrations (alembic ... --sql) are not supported by this project"
    )
else:
    run_migrations_online()
