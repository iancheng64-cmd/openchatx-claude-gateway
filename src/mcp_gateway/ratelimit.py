"""In-memory rate limiting for anonymous, expensive endpoints (login, DCR).

State is per-process, which matches the gateway's existing concurrency model
(single-process by design: SQLite plus in-memory connect flows already
assume that — see the README's Architecture section). This is not a
substitute for a WAF or edge rate limiter in front of a multi-instance
deployment, but the gateway ships and is documented as a single instance.
"""

from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    """Sliding-window request limiter keyed by an arbitrary string (e.g. client IP)."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True

    def purge_stale(self) -> None:
        """Drop keys with no hits inside the window, to bound memory growth."""
        now = time.time()
        stale = [
            key
            for key, hits in self._hits.items()
            if not hits or hits[-1] <= now - self.window_seconds
        ]
        for key in stale:
            del self._hits[key]
