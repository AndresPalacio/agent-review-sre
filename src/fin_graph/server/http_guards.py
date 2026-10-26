"""Bound HTTP admission/body memory without buffering streaming responses."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'"
)


@dataclass
class _Capacity:
    requests: int = 0
    streams: int = 0


class HttpGuards:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_requests: int = 128,
        max_streams: int = 64,
        max_body_bytes: int = 1024 * 1024,
        body_timeout_seconds: float = 10,
    ) -> None:
        if min(max_requests, max_streams, max_body_bytes, body_timeout_seconds) <= 0:
            raise ValueError("HTTP limits must be positive")
        self.app = app
        self.max_requests = max_requests
        self.max_streams = max_streams
        self.max_body_bytes = max_body_bytes
        self.body_timeout_seconds = body_timeout_seconds
        self.capacity = _Capacity()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        api = path == "/v1" or path.startswith("/v1/")

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                overrides = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"no-referrer",
                }
                if api:
                    overrides[b"cache-control"] = b"no-store"
                elif path.split("/")[1] not in {"docs", "redoc", "openapi.json"}:
                    overrides[b"content-security-policy"] = _CSP.encode("ascii")
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in overrides
                ]
                message = {**message, "headers": [*headers, *overrides.items()]}
            await send(message)

        async def reject(status: int, detail: str) -> None:
            headers = {"Retry-After": "1"} if status == 429 else None
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(
                scope, receive, secure_send
            )

        stream = path.endswith("/stream") or (
            path.startswith("/v1/chat/threads/") and path.endswith("/messages")
        )
        counted = api and path not in {"/v1/health", "/v1/ready"}
        category = "streams" if stream else "requests"
        limit = self.max_streams if stream else self.max_requests
        if counted:
            # No await between checking and reserving: atomic within this event loop.
            current = getattr(self.capacity, category)
            if current >= limit:
                await reject(429, "server capacity reached; retry shortly")
                return
            setattr(self.capacity, category, current + 1)
        try:
            lengths = [
                value for key, value in scope.get("headers", []) if key.lower() == b"content-length"
            ]
            if lengths:
                try:
                    length = int(lengths[0])
                    if length < 0 or any(value != lengths[0] for value in lengths):
                        raise ValueError
                except ValueError:
                    await reject(400, "invalid content length")
                    return
                if length > self.max_body_bytes:
                    await reject(413, "request exceeds body limit")
                    return
            if api:
                body = bytearray()
                try:
                    async with asyncio.timeout(self.body_timeout_seconds):
                        while True:
                            item = await receive()
                            if item["type"] == "http.disconnect":
                                return
                            chunk = item.get("body", b"")
                            if len(body) + len(chunk) > self.max_body_bytes:
                                await reject(413, "request exceeds body limit")
                                return
                            body.extend(chunk)
                            if not item.get("more_body", False):
                                break
                except TimeoutError:
                    await reject(408, "request body timed out")
                    return
                delivered = False

                async def replay() -> Message:
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                await self.app(scope, replay, secure_send)
            else:
                await self.app(scope, receive, secure_send)
        finally:
            if counted:
                setattr(self.capacity, category, getattr(self.capacity, category) - 1)


def install_http_guards(
    app: FastAPI,
    *,
    max_requests: int = 128,
    max_streams: int = 64,
    max_body_bytes: int = 1024 * 1024,
) -> None:
    app.add_middleware(
        HttpGuards,
        max_requests=max_requests,
        max_streams=max_streams,
        max_body_bytes=max_body_bytes,
    )
