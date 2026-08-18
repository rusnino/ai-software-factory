"""Application-level ASGI middleware."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import status

MAX_BODY_SIZE_BYTES = 64 * 1024
WRITE_PATHS = {"/events", "/tasks", "/approvals"}


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
                        complete = (
                            tail.get("type") == "http.request"
                            and not tail.get("more_body", False)
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

        await self.app(scope, _replay_receive, send)
