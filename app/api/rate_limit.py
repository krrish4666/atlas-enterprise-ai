"""app/api/rate_limit.py — Lightweight in-memory IP rate limiter for demo protection.

Protects Gemini free-tier quota on public deployments without requiring Redis.
Supports reverse-proxy client IP forwarding via X-Forwarded-For / X-Real-IP.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import settings


def get_client_ip(request: Request) -> str:
    """Extract the originating client IP address from proxy headers or socket."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip

    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


class SlidingWindowRateLimiter:
    """In-memory per-IP sliding-window rate limiter over a configurable window."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Clear all recorded request timestamps (primarily for testing)."""
        with self._lock:
            self._requests.clear()

    def check(self, key: str, limit: int) -> None:
        """Record a request for key or raise HTTP 429 if limit is exceeded."""
        if limit <= 0:
            return

        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            timestamps = self._requests[key]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= limit:
                retry_after = max(1, math.ceil(self.window_seconds - (now - timestamps[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Rate limit exceeded. Maximum {limit} chat requests per minute "
                        "allowed per IP."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )

            timestamps.append(now)


chat_rate_limiter = SlidingWindowRateLimiter(window_seconds=60.0)


async def enforce_chat_rate_limit(request: Request) -> None:
    """FastAPI dependency enforcing CHAT_RATE_LIMIT_PER_MINUTE per client IP."""
    client_ip = get_client_ip(request)
    chat_rate_limiter.check(client_ip, settings.chat_rate_limit_per_minute)
