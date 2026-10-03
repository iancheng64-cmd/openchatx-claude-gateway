"""Configuration loading for the MCP gateway.

Everything is driven by a single YAML file (see config.example.yaml).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: str) -> str:
    """Expand ${VAR} / ${VAR:-default} references in config values."""

    def repl(match: re.Match[str]) -> str:
        var, default = match.group(1), match.group(2)
        if var in os.environ:
            return os.environ[var]
        if default is not None:
            return default
        raise ValueError(f"Config references undefined environment variable: {var}")

    return _ENV_PATTERN.sub(repl, value)


def _expand_tree(node: object) -> object:
    if isinstance(node, str):
        return _expand_env(node)
    if isinstance(node, list):
        return [_expand_tree(item) for item in node]
    if isinstance(node, dict):
        return {key: _expand_tree(value) for key, value in node.items()}
    return node


class UserConfig(BaseModel):
    """A local user allowed to log in to the gateway's authorization server."""

    username: str
    # Exactly one of these must be set. `password_hash` is a bcrypt hash
    # (generate with `mcp-gateway hash-password`); `password` is plaintext and
    # only meant for quick local testing.
    password_hash: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def _check_password(self) -> UserConfig:
        if bool(self.password_hash) == bool(self.password):
            raise ValueError(
                f"User {self.username!r}: set exactly one of 'password_hash' or 'password'"
            )
        return self


class ServerConfig(BaseModel):
    # Public HTTPS URL clients use to reach the gateway (behind the reverse proxy).
    public_url: str
    host: str = "0.0.0.0"
    port: int = 8000
    # Peers uvicorn trusts to set X-Forwarded-For / X-Forwarded-Proto (a
    # reverse proxy's own address, or a comma-separated list of them).
    # Trusting these headers from arbitrary clients lets them spoof their
    # source IP (poisoning audit logs and rate limiting) and claim
    # X-Forwarded-Proto: https on a plaintext connection. Defaults to
    # loopback only, which covers `docker compose`'s common
    # reverse-proxy-on-the-host pattern; set explicitly (e.g. the Docker
    # bridge subnet, or "*" only if you fully trust the network path) when
    # the proxy connects from elsewhere.
    trusted_proxy_ips: str = "127.0.0.1"

    @field_validator("public_url")
    @classmethod
    def _normalize_public_url(cls, v: str) -> str:
        return v.rstrip("/")


class AuthConfig(BaseModel):
    users: list[UserConfig]
    # Fernet key (or arbitrary passphrase, which is stretched via scrypt) used to
    # encrypt secrets at rest in the SQLite database. Normally supplied via
    # ${MCP_GATEWAY_ENCRYPTION_KEY}; set MCP_GATEWAY_ENCRYPTION_KEY_FILE instead
    # to read it from a file (e.g. a Docker/Compose secret) -- see
    # _read_encryption_key_file() and the README's "Encryption key" section.
    encryption_key: str

    @field_validator("encryption_key")
    @classmethod
    def _check_encryption_key(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "auth.encryption_key is empty -- set MCP_GATEWAY_ENCRYPTION_KEY or "
                "MCP_GATEWAY_ENCRYPTION_KEY_FILE"
            )
        return v

    # Secret for signing browser session cookies; derived from encryption_key when unset.
    session_secret: str | None = None
    access_token_expiry_seconds: int = 3600
    refresh_token_expiry_seconds: int = 60 * 60 * 24 * 30
    authorization_code_expiry_seconds: int = 300
    login_session_expiry_seconds: int = 60 * 60 * 8
    # Optional allow-list of redirect URI patterns for dynamically registered /
    # CIMD clients (e.g. "https://claude.ai/*"). When unset, standard validation
    # applies: exact match against registered URIs with loopback ports allowed to vary.
    allowed_client_redirect_uris: list[str] | None = None
    # Scopes advertised to MCP clients. The gateway is a single-identity AS, so
    # scopes are informational; "mcp" is the default catch-all.
    scopes_supported: list[str] = Field(default_factory=lambda: ["mcp"])


class BackendAuthConfig(BaseModel):
    """How the gateway authenticates against an upstream MCP server."""

    type: Literal["none", "bearer", "headers", "oauth"] = "none"
    # type == "bearer": static token injected as `Authorization: Bearer <token>`.
    token: str | None = None
    # type == "headers": arbitrary static headers (e.g. X-API-Key).
    headers: dict[str, str] = Field(default_factory=dict)
    # type == "oauth": scopes to request from the upstream authorization server.
    scopes: list[str] | None = None
    # type == "oauth": force DCR even if the upstream AS supports CIMD.
    prefer_dcr: bool = False
    # type == "oauth": pre-registered client credentials. Required for upstream
    # authorization servers that support neither CIMD nor Dynamic Client
    # Registration (e.g. GitHub's, which requires a manually created OAuth App).
    # When set, these are used directly and no CIMD/DCR is attempted.
    client_id: str | None = None
    client_secret: str | None = None

    @model_validator(mode="after")
    def _check(self) -> BackendAuthConfig:
        if self.type == "bearer" and not self.token:
            raise ValueError("backend auth type 'bearer' requires 'token'")
        if self.type == "headers" and not self.headers:
            raise ValueError("backend auth type 'headers' requires 'headers'")
        if self.type != "oauth" and self.client_secret:
            raise ValueError("client_secret is only valid for auth type 'oauth'")
        return self


class BackendConfig(BaseModel):
    """An upstream MCP server exposed through the gateway."""

    url: str
    enabled: bool = True
    auth: BackendAuthConfig = Field(default_factory=BackendAuthConfig)
    # Extra static headers sent with every request regardless of auth type.
    headers: dict[str, str] = Field(default_factory=dict)


class StorageConfig(BaseModel):
    path: str = "data/gateway.db"


class GatewayConfig(BaseModel):
    server: ServerConfig
    auth: AuthConfig
    storage: StorageConfig = Field(default_factory=StorageConfig)
    backends: dict[str, BackendConfig] = Field(default_factory=dict)

    @field_validator("backends")
    @classmethod
    def _validate_backend_names(cls, v: dict[str, BackendConfig]) -> dict[str, BackendConfig]:
        for name in v:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
                raise ValueError(
                    f"Backend name {name!r} must be alphanumeric with '-'/'_' "
                    "(it is used as a tool-name prefix)"
                )
        return v


def _read_encryption_key_file() -> str | None:
    """Read the key from MCP_GATEWAY_ENCRYPTION_KEY_FILE, if set. Returns
    None if the variable isn't set.

    Deliberately does *not* go through os.environ (e.g. by setting
    MCP_GATEWAY_ENCRYPTION_KEY and letting the normal ${...} expansion pick it
    up): an environment variable is readable by anything sharing this
    process's UID via /proc/<pid>/environ, gets swept up by crash-reporting/APM
    tools' default "environment" capture, and is inherited by every child
    process -- exactly the leak surface a file-based secret (Docker/Compose
    `secrets:`, a Kubernetes `Secret` volume, a Vault Agent sidecar -- see
    https://docs.docker.com/compose/how-tos/use-secrets/) is meant to avoid.
    The value returned here is spliced directly into the parsed config dict
    by load_config() instead, entirely in this process's own memory.
    """
    key_file = os.environ.get("MCP_GATEWAY_ENCRYPTION_KEY_FILE")
    if not key_file:
        return None
    try:
        return Path(key_file).read_text().strip()
    except OSError as exc:
        raise ValueError(
            f"MCP_GATEWAY_ENCRYPTION_KEY_FILE={key_file!r} could not be read: {exc}"
        ) from exc


def load_config(path: str | Path) -> GatewayConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise TypeError(f"Config file {path} must contain a YAML mapping")
    expanded = _expand_tree(raw)

    key_from_file = _read_encryption_key_file()
    if key_from_file is not None:
        auth = expanded.setdefault("auth", {}) if isinstance(expanded, dict) else {}
        if isinstance(auth, dict):
            auth["encryption_key"] = key_from_file

    return GatewayConfig.model_validate(expanded)


def load_storage_path(path: str | Path) -> str:
    """Read just `storage.path` from a config file, without requiring the
    rest of the config -- notably `auth.encryption_key` -- to validate.

    Used by `mcp-gateway rotate-key`, which supplies its own keys via
    --old-key-file/--new-key-file and so doesn't need a working
    encryption_key just to locate the database.
    """
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise TypeError(f"Config file {path} must contain a YAML mapping")
    storage_raw = _expand_tree(raw.get("storage") or {})
    return str(storage_raw.get("path", StorageConfig().path))
