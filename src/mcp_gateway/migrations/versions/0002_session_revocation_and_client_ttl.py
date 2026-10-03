"""Add revoked_sessions (session logout) and oauth_clients.last_used_at
(unused-client TTL purge, see storage.purge_expired).

This is the migration that used to crash startup on a database created
before it: `oauth_clients.last_used_at` was added to the raw
`CREATE TABLE IF NOT EXISTS` schema string `Storage` used to run directly,
which -- being `IF NOT EXISTS` -- never actually added the column to an
already-existing table, so `purge_expired()` failed with "no such column:
last_used_at" on every subsequent startup against an older database.

The column add below is guarded by an existence check rather than assumed
safe just because it follows 0001 in sequence: a database that was already
running that hand-rolled fix (before it was rewritten to use Alembic) may
already have `last_used_at`, but with no `alembic_version` row recording
it -- so this migration still runs against it once Alembic is adopted, and
must not fail by re-adding a column that's already there. `revoked_sessions`
doesn't need the same guard since `CREATE TABLE IF NOT EXISTS` is already
safe to repeat.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS revoked_sessions (
            session_id TEXT PRIMARY KEY,
            expires_at REAL NOT NULL     -- the session's own natural expiry; safe to forget after
        )
        """
    )

    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("oauth_clients")}
    if "last_used_at" not in columns:
        op.add_column(
            "oauth_clients",
            # set when a client completes an authorization
            sa.Column("last_used_at", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    raise NotImplementedError("Downgrading this migration is not supported")
