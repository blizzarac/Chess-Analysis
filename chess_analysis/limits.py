"""Per-client rate limiting and client address handling."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from .config import settings


class RateLimiter:
    """Sliding-window counter per key, in memory (one web process is assumed)."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.time()
        with self._lock:
            q = self._events[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._events) > 50_000:  # crude memory bound
                for k in [k for k, v in self._events.items() if not v or v[-1] < now - window_seconds]:
                    del self._events[k]
            return True


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]
