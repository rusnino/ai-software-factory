"""Application-level ASGI middleware."""

import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import status

from governance_controller.config import settings

MAX_BODY_SIZE_BYTES = 64 * 1024
WRITE_PATHS = {
    "/events",
    "/tasks",
    "/approvals",
    "/intake/email",
    "/intake/idea",
    "/intake/telegram",
    "/webhooks/plane",
}

_RATE_LIMIT_WINDOW_SECONDS = 60
# Cap on distinct source IPs tracked simultaneously; prevents unbounded memory
# growth when clients vary X-Forwarded-For or rotate source addresses (#250).
_DEFAULT_MAX_DISTINCT_IPS = 10_000

# Module-level request tracking so tests and CLI commands can reset state.
_requests_by_ip: dict[str, deque[float]] = {}
_requests_last_access: dict[str, float] = {}


def reset_rate_limits() -> None:
    """Clear all in-memory rate-limit counters."""
    _requests_by_ip.clear()
    _requests_last_access.clear()


class InMemoryRateLimitMiddleware:
    """ASGI middleware enforcing per-IP request-rate limits.

    Tracks request timestamps in memory per source IP. Requests beyond
    ``GC_RATE_LIMIT_PER_MINUTE`` within a rolling 60-second window receive
    ``429 Too Many Requests``. ``/health`` is exempt so load balancers and
    monitoring stay functional.

    # ponytail: in-memory only; multi-process deployments need a shared store
    # (Redis, memcached) once rate limits must be cluster-wide.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.rstrip("/") == "/health":
            await self.app(scope, receive, send)
            return

        limit = settings.rate_limit_per_minute
        if limit <= 0:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        ip = client[0] if isinstance(client, (list, tuple)) and client else "unknown"
        now = time.monotonic()
        window = _requests_by_ip.setdefault(ip, deque())
        _requests_last_access[ip] = now
        cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
        while window and window[0] <= cutoff:
            window.popleft()
        if not window:
            _requests_by_ip.pop(ip, None)
            _requests_last_access.pop(ip, None)
            window = _requests_by_ip.setdefault(ip, deque())
            _requests_last_access[ip] = now

        # Evict least-recently-accessed IPs once we exceed the distinct-IP cap.
        max_ips = getattr(settings, "rate_limit_max_ips", _DEFAULT_MAX_DISTINCT_IPS)
        while len(_requests_by_ip) > max_ips:
            oldest_ip = min(_requests_last_access, key=_requests_last_access.get)  # type: ignore[arg-type]
            _requests_by_ip.pop(oldest_ip, None)
            _requests_last_access.pop(oldest_ip, None)

        if len(window) >= limit:
            await send(
                {
                    "type": "http.response.start",
                    "status": status.HTTP_429_TOO_MANY_REQUESTS,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"Rate limit exceeded"}',
                }
            )
            return

        window.append(now)

        async def send_response(message: dict[str, Any]) -> None:
            """Release the admission token for duplicate intake responses."""
            await send(message)
            if (
                message.get("type") == "http.response.start"
                and message.get("status") == status.HTTP_409_CONFLICT
                and path.startswith("/intake/")
                and scope.get("method") == "POST"
            ):
                try:
                    window.remove(now)
                except ValueError:
                    return
                if not window:
                    _requests_by_ip.pop(ip, None)
                    _requests_last_access.pop(ip, None)

        await self.app(scope, receive, send_response)


# Re-export class name for backward compatibility with tests that may import it.
RateLimitMiddleware = InMemoryRateLimitMiddleware


class WriteBodySizeLimitMiddleware:
    """ASGI middleware that caps the actual request body read for POST writes.

    FastAPI/Starlette's built-in body parsing reads the entire ASGI stream into
    memory before the route handler runs, so checking only the Content-Length
    header trusts the client. This middleware buffers the whole body itself,
    rejects the request if the buffered bytes exceed the cap, and only then
    hands the buffered messages to the application. A client that omits
    Content-Length, sends a chunked body, or lies about the length is capped by
    the actual bytes received.

    Applies to the mutating Controller endpoints defined in WRITE_PATHS.

    # ponytail: buffers the entire body in memory up to the cap; streaming
    # handling would need a different approach if we ever want to support
    # multi-megabyte event bodies without buffering.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        if method != "POST" or path.rstrip("/") not in WRITE_PATHS:
            await self.app(scope, receive, send)
            return

        buffered: list[dict[str, Any]] = []
        total_bytes = 0
        complete = False

        while not complete:
            message = await receive()
            buffered.append(message)
            if message.get("type") == "http.request":
                total_bytes += len(message.get("body", b""))
                complete = not message.get("more_body", False)
                if total_bytes > MAX_BODY_SIZE_BYTES:
                    # Drain the rest of the stream before responding so the
                    # transport sees a complete request lifecycle.
                    while not complete:
                        tail = await receive()
                        if tail.get("type") == "http.disconnect":
                            return
                        complete = tail.get("type") == "http.request" and not tail.get(
                            "more_body", False
                        )
                    await send(
                        {
                            "type": "http.response.start",
                            "status": status.HTTP_413_CONTENT_TOO_LARGE,
                            "headers": [(b"content-type", b"text/plain")],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"Payload Too Large",
                        }
                    )
                    return
            elif message.get("type") == "http.disconnect":
                return

        index = 0

        async def _replay_receive() -> dict[str, Any]:
            nonlocal index
            if index < len(buffered):
                msg = buffered[index]
                index += 1
                return msg
            return await receive()

        scope.setdefault("state", {})["raw_body"] = b"".join(
            msg.get("body", b"")
            for msg in buffered
            if msg.get("type") == "http.request"
        )
        await self.app(scope, _replay_receive, send)
