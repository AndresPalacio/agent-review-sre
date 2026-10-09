import asyncio

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from sre_graph.server.http_guards import HttpGuards, install_http_guards


async def test_limits_body_before_handler_and_sets_response_boundaries() -> None:
    app = FastAPI()
    reached = []

    @app.api_route("/v1/input", methods=["POST", "GET"])
    async def receive(request: Request):
        reached.append(True)
        return await request.json()

    @app.get("/")
    async def ui():
        return {"ok": True}

    install_http_guards(app, max_body_bytes=8)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def chunks():
            yield b"12345"
            yield b"67890"

        response = await client.post("/v1/input", content=chunks())
        assert response.status_code == 413 and reached == []
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        get_response = await client.request("GET", "/v1/input", content=chunks())
        assert get_response.status_code == 413 and reached == []
        assert (await client.post("/v1/input", json={})).status_code == 200
        assert (
            (await client.get("/"))
            .headers["content-security-policy"]
            .startswith("default-src 'self'")
        )
        assert "content-security-policy" not in (await client.get("/docs")).headers


async def test_request_and_stream_capacity_released_on_cancel() -> None:
    app = FastAPI()
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/v1/slow")
    async def slow():
        entered.set()
        await release.wait()
        return {"ok": True}

    @app.get("/v1/threads/t/stream")
    async def stream():
        async def events():
            entered.set()
            await release.wait()
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    install_http_guards(app, max_requests=1, max_streams=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/v1/slow", "/v1/threads/t/stream"):
            entered.clear()
            release.clear()
            pending = asyncio.create_task(client.get(path))
            await asyncio.wait_for(entered.wait(), 1)
            rejected = await client.get(path)
            assert rejected.status_code == 429
            assert rejected.headers["retry-after"] == "1"
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            release.set()
            assert (await client.get(path)).status_code == 200


async def test_security_headers_preserve_multiple_cookies() -> None:
    app = FastAPI()

    @app.get("/")
    async def root():
        response = Response("ok")
        response.set_cookie("first", "1")
        response.set_cookie("second", "2")
        return response

    install_http_guards(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
    assert len(response.headers.get_list("set-cookie")) == 2


async def test_slow_body_and_disconnect_release_capacity() -> None:
    reached = []

    async def app(scope, receive, send):
        reached.append(True)

    guard = HttpGuards(app, max_requests=1, body_timeout_seconds=0.01)
    scope = {"type": "http", "path": "/v1/input", "method": "POST", "headers": []}
    sent = []

    async def send(message):
        sent.append(message)

    async def slow():
        await asyncio.Event().wait()

    await guard(scope, slow, send)
    assert sent[0]["status"] == 408
    assert not reached and guard.capacity.requests == 0

    async def disconnected():
        return {"type": "http.disconnect"}

    await guard(scope, disconnected, send)
    assert not reached and guard.capacity.requests == 0
