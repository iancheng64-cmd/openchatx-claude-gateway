"""Local user verification and signed browser sessions."""

from __future__ import annotations

import hmac
import json
import secrets
import time

import bcrypt
from itsdangerous import BadSignature, URLSafeTimedSerializer

from mcp_gateway.config import AuthConfig, UserConfig
from mcp_gateway.storage import Storage

SESSION_COOKIE = "mcp_gateway_session"

# Precomputed once at import time, at the same cost factor bcrypt.gensalt()
# uses by default (the same factor `mcp-gateway hash-password` produces).
# Checking against this on every "unknown user" / "plaintext user" path keeps
# the bcrypt cost identical regardless of whether the username exists or
# which password kind is configured, closing the timing side-channel that a
# cheaper (or skipped) dummy hash would otherwise leak.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt()).decode()


def verify_user(auth: AuthConfig, username: str, password: str) -> bool:
    """Constant-cost-per-path username/password check.

    Always encodes to bytes before ``hmac.compare_digest`` (it rejects
    non-ASCII ``str`` arguments with a ``TypeError``, which would otherwise
    make a non-ASCII configured username impossible to log in as and would
    hand an unauthenticated caller an unhandled 500). Always performs exactly
    one bcrypt check, at the real cost factor, regardless of whether the
    username matched or which password kind that user is configured with, so
    that response time does not leak which usernames exist.
    """
    matched: UserConfig | None = None
    for user in auth.users:
        if hmac.compare_digest(user.username.encode(), username.encode()):
            matched = user
            # No early return: keep looping so the number of comparisons
            # (and thus timing) doesn't depend on where a match falls.

    if matched is not None and matched.password_hash is not None:
        try:
            return bcrypt.checkpw(password.encode(), matched.password_hash.encode())
        except ValueError:
            return False

    # Either no user matched, or the matched user has a plaintext password
    # (no bcrypt cost of its own to pay) — spend the same bcrypt check either
    # way so the two cases are indistinguishable by timing.
    bcrypt.checkpw(password.encode(), _DUMMY_HASH.encode())
    if matched is not None and matched.password is not None:
        return hmac.compare_digest(matched.password.encode(), password.encode())
    return False


class SessionManager:
    """Signed, time-limited login session cookies.

    Each cookie carries a random per-login session id. Logging out revokes
    that one session id (persisted in ``storage`` until it would have
    expired anyway) so a captured cookie stops working immediately instead
    of remaining valid for the rest of its natural lifetime.
    """

    def __init__(self, secret: str, max_age_seconds: int, storage: Storage):
        self._serializer = URLSafeTimedSerializer(secret, salt="mcp-gateway-session")
        self.max_age_seconds = max_age_seconds
        self._storage = storage

    def create(self, username: str) -> str:
        session_id = secrets.token_urlsafe(24)
        return self._serializer.dumps(
            json.dumps({"u": username, "t": time.time(), "sid": session_id})
        )

    def validate(self, cookie_value: str | None) -> str | None:
        """Return the logged-in username, or None."""
        if not cookie_value:
            return None
        try:
            payload = json.loads(
                self._serializer.loads(cookie_value, max_age=self.max_age_seconds)
            )
        except (BadSignature, ValueError):
            return None
        session_id = payload.get("sid")
        if session_id and self._storage.is_session_revoked(session_id):
            return None
        return payload.get("u") or None

    def revoke(self, cookie_value: str | None) -> None:
        """Invalidate the session carried by this cookie, if any (logout)."""
        if not cookie_value:
            return
        try:
            # Accept an already-expired signature too: still record the
            # revocation for its remaining nominal lifetime is unnecessary
            # once expired, but decoding the payload requires the signature
            # to be intact, so use loads() without max_age to recover it.
            payload = json.loads(self._serializer.loads(cookie_value, max_age=None))
        except (BadSignature, ValueError):
            return
        session_id = payload.get("sid")
        created_at = payload.get("t")
        if not session_id or created_at is None:
            return
        expires_at = float(created_at) + self.max_age_seconds
        if expires_at > time.time():
            self._storage.revoke_session(session_id, expires_at)
