"""Application-level ASGI middleware."""

import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, status

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
_INTAKE_PATHS = {path for path in WRITE_PATHS if path.startswith("/intake/")}

_RATE_LIMIT_WINDOW_SECONDS = 60
# Cap on distinct source IPs tracked simultaneously; prevents unbounded memory
# growth when clients vary X-Forwarded-For or rotate source addresses (#250).
_DEFAULT_MAX_DISTINCT_IPS = 10_000

# Module-level request tracking so tests and CLI commands can reset state.
_requests_by_ip: dict[str, deque[float]] = {}
_requests_last_access: dict[str, float] = {}

# Separate per-IP intake budget: bounds authenticated intake volume regardless
# of how many distinct senders or source_ids a client rotates (#312).
_intake_requests_by_ip: dict[str, deque[float]] = {}
_intake_requests_last_access: dict[str, float] = {}
_intake_rate_limit_lock = threading.Lock()


def reset_rate_limits() -> None:
    """Clear all in-memory rate-limit counters."""
    _requests_by_ip.clear()
    _requests_last_access.clear()
    with _intake_rate_limit_lock:
        _intake_requests_by_ip.clear()
        _intake_requests_last_access.clear()


class InMemoryRateLimitMiddleware:
    """ASGI middleware enforcing per-IP request-rate limits.

    Tracks request timestamps in memory per source IP. Requests beyond
    ``GC_RATE_LIMIT_PER_MINUTE`` within a rolling 60-second window receive
    ``429 Too Many Requests``. ``/health`` is exempt. Intake requests consume
    a pre-auth admission token; successful authentication releases it so the
    durable sender/duplicate guard can classify retries without starving a new
    submission, while invalid attempts remain rate-limited (#300).
    Successful authenticated intake also charges a separate per-source-IP
    intake budget (``GC_INTAKE_RATE_LIMIT_PER_IP_PER_MINUTE``); duplicate
    ``409`` responses release that charge so retries do not starve new
    submissions (#312).

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
        normalized_path = path.rstrip("/")
        if normalized_path == "/health":
            await self.app(scope, receive, send)
            return

        is_intake = normalized_path in _INTAKE_PATHS

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

        released = False

        def release_admission() -> None:
            """Release this request's token at most once."""
            nonlocal released
            if released:
                return
            released = True
            if _requests_by_ip.get(ip) is not window:
                return
            try:
                window.remove(now)
            except ValueError:
                return
            if not window:
                _requests_by_ip.pop(ip, None)
                _requests_last_access.pop(ip, None)

        if is_intake:
            scope.setdefault("state", {})[
                "release_intake_rate_limit"
            ] = release_admission

            intake_limit = settings.intake_rate_limit_per_ip_per_minute
            if intake_limit > 0:
                intake_window: deque[float] | None = None

                def _ensure_intake_window() -> deque[float]:
                    """Return the (possibly empty) intake window for this IP."""
                    nonlocal intake_window
                    if intake_window is not None:
                        return intake_window
                    fresh = _intake_requests_by_ip.setdefault(ip, deque())
                    _intake_requests_last_access[ip] = time.monotonic()
                    cutoff = time.monotonic() - _RATE_LIMIT_WINDOW_SECONDS
                    while fresh and fresh[0] <= cutoff:
                        fresh.popleft()
                    if not fresh:
                        _intake_requests_by_ip.pop(ip, None)
                        _intake_requests_last_access.pop(ip, None)
                        fresh = _intake_requests_by_ip.setdefault(ip, deque())
                        _intake_requests_last_access[ip] = time.monotonic()
                    max_ips = getattr(
                        settings, "rate_limit_max_ips", _DEFAULT_MAX_DISTINCT_IPS
                    )
                    while len(_intake_requests_by_ip) > max_ips:
                        oldest_ip = min(
                            _intake_requests_last_access,
                            key=_intake_requests_last_access.get,  # type: ignore[arg-type]
                        )
                        _intake_requests_by_ip.pop(oldest_ip, None)
                        _intake_requests_last_access.pop(oldest_ip, None)
                    intake_window = fresh
                    return fresh

                def charge_intake_ip_rate_limit() -> None:
                    """Charge a successful authenticated intake request.

                    Raises HTTPException(429) when the per-source-IP intake
                    budget is exhausted. Duplicate submissions are released by
                    the response wrapper below.
                    """
                    with _intake_rate_limit_lock:
                        window = _ensure_intake_window()
                        if len(window) >= intake_limit:
                            raise HTTPException(
                                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                detail="Intake rate limit exceeded",
                            )
                        window.append(now)
                        _intake_requests_last_access[ip] = time.monotonic()

                def release_intake_ip_rate_limit() -> None:
                    """Release the intake token for a duplicate submission."""
                    with _intake_rate_limit_lock:
                        if intake_window is None:
                            return
                        if _intake_requests_by_ip.get(ip) is not intake_window:
                            return
                        try:
                            intake_window.remove(now)
                        except ValueError:
                            return
                        if not intake_window:
                            _intake_requests_by_ip.pop(ip, None)
                            _intake_requests_last_access.pop(ip, None)

                scope.setdefault("state", {})[
                    "charge_intake_ip_rate_limit"
                ] = charge_intake_ip_rate_limit
                scope.setdefault("state", {})[
                    "release_intake_ip_rate_limit"
                ] = release_intake_ip_rate_limit

        async def send_response(message: dict[str, Any]) -> None:
            """Release the admission token for duplicate intake responses."""
            await send(message)
            if (
                message.get("type") == "http.response.start"
                and message.get("status") == status.HTTP_409_CONFLICT
                and path.startswith("/intake/")
                and scope.get("method") == "POST"
            ):
                release_admission()
                release_intake = scope.get("state", {}).get(
                    "release_intake_ip_rate_limit"
                )
                if callable(release_intake):
                    release_intake()

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
