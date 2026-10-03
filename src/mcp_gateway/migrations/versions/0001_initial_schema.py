"""Initial schema (baseline).

This is the schema as it looked before this project adopted Alembic --
every table `CREATE TABLE IF NOT EXISTS`'d directly by `Storage.__init__` in
past versions. Applying it with `IF NOT EXISTS` makes it a safe no-op
against a pre-Alembic database that already has some or all of these
tables: it only ever creates a missing table, never alters an existing one,
so upgrading from any older version starts here without needing a manual
"stamp" step first.

Revision ID: 0001
Revises:
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_clients (
            client_id TEXT PRIMARY KEY,
            data BLOB NOT NULL,          -- Fernet-encrypted JSON client record
            is_cimd INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_codes (
            code_hash TEXT PRIMARY KEY,
            data TEXT NOT NULL,          -- JSON AuthorizationCode payload (no secrets)
            expires_at REAL NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS access_tokens (
            token_hash TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            scopes TEXT NOT NULL,
            subject TEXT,
            resource TEXT,
            expires_at REAL NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            token_hash TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            scopes TEXT NOT NULL,
            subject TEXT,
            resource TEXT,
            expires_at REAL NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_txns (
            txn_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,          -- JSON transaction state
            expires_at REAL NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS upstream_data (
            backend TEXT NOT NULL,
            key TEXT NOT NULL,           -- 'tokens' | 'client_info'
            value BLOB NOT NULL,         -- Fernet-encrypted JSON
            updated_at REAL NOT NULL,
            PRIMARY KEY (backend, key)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrading the baseline schema is not supported")
