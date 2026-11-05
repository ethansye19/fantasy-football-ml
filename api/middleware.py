"""Pure ASGI middleware: request body limit (QA-F02) and inbound rate limiting (QA-F03).

No new dependency. Both answer with the API error envelope (``api.errors``)
and run inside the headers middleware, so their responses carry the usual
security headers.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.errors import error_body

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- body size


def _too_large(max_bytes: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content=error_body(
            "payload_too_large", f"Request body larger than {max_bytes} bytes is not accepted."
        ),
    )


class BodyLimitMiddleware:
    """Refuse request bodies over ``max_bytes`` with 413.

    A ``Content-Length`` above the limit is refused before anything is read.
    Otherwise the body is read up to ``max_bytes + 1`` bytes before the
    application runs (chunked uploads and lying ``Content-Length`` values
    included) and replayed to it, so the 413 never races the application's
    own response. Bodies on this API are small, so buffering is cheap.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _too_large(self.max_bytes)(scope, receive, send)
            return
        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break  # http.disconnect: let the app see it
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                await _too_large(self.max_bytes)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------- rate limiting

FAMILIES: tuple[str, ...] = ("sleeper", "write", "heavy", "read", "meta")


def route_family(method: str, path: str) -> str:
    """Bucket family of a request (docs/SECURITY.md section 5)."""
    p = path.rstrip("/") or "/"
    if p.startswith("/meta") or p in ("/", "/docs", "/redoc", "/openapi.json"):
        return "meta"
    if p.startswith("/sleeper/"):
        return "sleeper"
    if p.startswith("/leagues"):
        parts = p.split("/")
        tail = parts[3] if len(parts) > 3 else ""
        if p == "/leagues/import/sleeper" or tail == "sync":
            return "sleeper"
        if tail == "waivers" or (tail == "trades" and len(parts) > 4):
            return "heavy"
        if tail in ("projections", "ros", "start-sit"):
            return "read"
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            return "write"
        return "read"
    return "read"


@dataclass
class Limit:
    per_minute: float
    burst: int

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0 and self.burst > 0

    @classmethod
    def parse(cls, spec: str | None) -> Limit:
        """``"<per_minute>/<burst>"``; ``"0"`` or empty disables the bucket."""
        text = (spec or "").strip()
        if not text or text == "0":
            return cls(0.0, 0)
        per_minute, _, burst = text.partition("/")
        rate = float(per_minute)
        return cls(rate, int(burst) if burst else max(int(math.ceil(rate / 6)), 1))


class _Bucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, capacity: float, now: float) -> None:
        self.tokens = capacity
        self.updated = now


class RateLimiter:
    """Token buckets per ``(client, family)``; ``check`` returns seconds to wait or ``None``."""

    def __init__(
        self,
        limits: dict[str, Limit],
        *,
        trusted_proxies: tuple[str, ...] = (),
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = 10_000,
    ) -> None:
        self.limits = {f: limits.get(f, Limit(0.0, 0)) for f in FAMILIES}
        self.trusted_proxies = frozenset(trusted_proxies)
        self.clock = clock
        self.max_clients = max_clients
        self._buckets: OrderedDict[tuple[str, str], _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return any(limit.enabled for limit in self.limits.values())

    def client_of(self, scope: Scope) -> str:
        peer = (scope.get("client") or ("unknown", 0))[0] or "unknown"
        if peer in self.trusted_proxies:
            for name, value in scope.get("headers", []):
                if name == b"x-forwarded-for":
                    first = value.decode("latin-1").split(",")[0].strip()
                    if first:
                        return first
        return str(peer)

    def check(self, client: str, family: str) -> float | None:
        limit = self.limits.get(family)
        if limit is None or not limit.enabled:
            return None
        rate = limit.per_minute / 60.0
        now = self.clock()
        key = (client, family)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(float(limit.burst), now)
                self._buckets[key] = bucket
                while len(self._buckets) > self.max_clients:
                    self._buckets.popitem(last=False)
            else:
                self._buckets.move_to_end(key)
                elapsed = max(0.0, now - bucket.updated)
                bucket.tokens = min(float(limit.burst), bucket.tokens + elapsed * rate)
                bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return None
            return (1.0 - bucket.tokens) / rate

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class RateLimitMiddleware:
    """429 with ``Retry-After`` when a client's bucket for the route family is empty."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.limiter.enabled or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        family = route_family(scope["method"], scope.get("path", "/"))
        wait = self.limiter.check(self.limiter.client_of(scope), family)
        if wait is None:
            await self.app(scope, receive, send)
            return
        retry_after = max(1, int(math.ceil(wait)))
        response = JSONResponse(
            status_code=429,
            content=error_body(
                "rate_limited",
                f"Too many {family} requests. Try again in {retry_after} s.",
                {"family": family, "retry_after_seconds": retry_after},
            ),
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)


def limiter_from_settings(settings: Any) -> RateLimiter:
    """Build the limiter from ``ApiSettings`` (``rate_limit_<family>`` specs, trusted proxies)."""
    if not getattr(settings, "rate_limit_enabled", True):
        return RateLimiter({f: Limit(0.0, 0) for f in FAMILIES})
    limits = {f: Limit.parse(getattr(settings, f"rate_limit_{f}", "0")) for f in FAMILIES}
    return RateLimiter(limits, trusted_proxies=tuple(getattr(settings, "trusted_proxies", ())))


__all__ = [
    "FAMILIES",
    "BodyLimitMiddleware",
    "Limit",
    "RateLimitMiddleware",
    "RateLimiter",
    "limiter_from_settings",
    "route_family",
]
