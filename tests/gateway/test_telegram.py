from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

import httpx
import pytest

from fin_graph.gateway import GatewayResult, GatewayRunPending
from fin_graph.gateway.settings import TelegramGatewaySettings
from fin_graph.gateway.telegram import TelegramGateway


class Store:
    def __init__(self) -> None:
        self.events: set[tuple[str, str]] = set()
        self.links: dict[tuple[str, str, str], str] = {}
        self.approval_ids: dict[tuple[str, str, str], str | None] = {}
        self.pending: dict[tuple[str, str], dict[str, Any]] = {}

    async def claim_gateway_event(
        self,
        platform: str,
        event_id: str,
        payload: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> bool:
        key = (platform, event_id)
        if key in self.events:
            return False
        self.events.add(key)
        if payload is not None:
            self.pending[key] = payload
        return True

    async def pending_gateway_events(
        self, platform: str, before: datetime, *, limit: int = 100
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (event_id, payload)
            for (name, event_id), payload in self.pending.items()
            if name == platform
        ][:limit]

    async def claim_stale_gateway_event(
        self, platform: str, event_id: str, before: datetime, owner: str | None = None
    ) -> bool:
        return (platform, event_id) in self.pending

    async def touch_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool:
        return (platform, event_id) in self.pending

    async def complete_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool:
        return self.pending.pop((platform, event_id), None) is not None

    async def fail_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool:
        return (platform, event_id) in self.pending

    async def bind_gateway_thread(
        self,
        platform: str,
        destination: str,
        remote_id: str,
        thread_id: str,
        *,
        approval_id: str | None = None,
    ) -> None:
        self.links[(platform, destination, remote_id)] = thread_id
        self.approval_ids[(platform, destination, remote_id)] = approval_id

    async def gateway_thread(self, platform: str, destination: str, remote_id: str) -> str | None:
        return self.links.get((platform, destination, remote_id))

    async def gateway_approval_id(
        self, platform: str, destination: str, remote_id: str
    ) -> str | None:
        return self.approval_ids.get((platform, destination, remote_id))


@pytest.fixture
def setup() -> tuple[TelegramGateway, Store, list[httpx.Request], list[str], list[str]]:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 50}})
        return httpx.Response(200, json={"ok": True, "result": True})

    store = Store()
    submitted: list[str] = []
    decisions: list[str] = []

    async def submit(
        text: str, platform: str, destination: str, remote_id: str, event_id: str
    ) -> GatewayResult:
        submitted.append(text)
        assert (platform, destination, remote_id) == ("telegram", "-1001", "50")
        return GatewayResult("thread-1", "<b>report</b>", "orders", approval_id="request-1")

    async def decide(kind: str, thread_id: str, choice: str, actor: str, approval_id: str) -> str:
        assert approval_id == "request-1"
        decisions.append(f"{kind}:{thread_id}:{choice}:{actor}")
        return "Rejected"

    gateway = TelegramGateway(
        TelegramGatewaySettings(
            _env_file=None,
            enabled=True,
            bot_token="123:abc",
            webhook_secret="secret-token",
            chat_id="-1001",
            approver_user_ids=("42",),
        ),
        store,
        submit,
        decide,
        transport=httpx.MockTransport(respond),
    )
    return gateway, store, requests, submitted, decisions


async def test_valid_message_acks_before_investigation_and_updates_report(setup: Any) -> None:
    gateway, store, requests, submitted, _ = setup
    started = asyncio.Event()
    release = asyncio.Event()

    async def submit(
        text: str, platform: str, destination: str, remote_id: str, event_id: str
    ) -> GatewayResult:
        started.set()
        await release.wait()
        submitted.append(text)
        return GatewayResult("thread-1", "<b>report</b>", "orders", approval_id="request-1")

    gateway.submit_request = submit
    update = {
        "update_id": 1,
        "message": {"message_id": 2, "chat": {"id": -1001}, "from": {"id": 7}, "text": "CPU high"},
    }
    assert (await gateway.handle(update, "secret-token")) == 200
    await asyncio.wait_for(started.wait(), timeout=2)
    assert submitted == []
    release.set()
    await gateway.close()
    assert submitted == ["CPU high"]
    assert store.links[("telegram", "-1001", "50")] == "thread-1"
    assert [request.url.path for request in requests] == [
        "/bot123:abc/sendMessage",
        "/bot123:abc/editMessageText",
    ]
    edited = json.loads(requests[1].content)
    assert edited["text"] == "<b>report</b>"
    assert len(edited["reply_markup"]["inline_keyboard"][0]) == 2


async def test_webhook_ack_requires_durable_event_claim(setup: Any) -> None:
    gateway, store, _, _, _ = setup
    entered = asyncio.Event()
    release = asyncio.Event()
    original = store.claim_gateway_event

    async def slow_claim(
        platform: str,
        event_id: str,
        payload: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> bool:
        entered.set()
        await release.wait()
        return await original(platform, event_id, payload, owner)

    store.claim_gateway_event = slow_claim  # type: ignore[method-assign]
    update = {
        "update_id": 77,
        "message": {"chat": {"id": -1001}, "from": {"id": 7}, "text": "alert"},
    }
    response_task = asyncio.create_task(gateway.handle(update, "secret-token"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert not response_task.done()
    release.set()
    assert await asyncio.wait_for(response_task, timeout=2) == 200
    await gateway.close()


async def test_pending_alert_replays_after_claim_without_worker(setup: Any) -> None:
    gateway, store, _, submitted, _ = setup
    payload = {"type": "analysis", "text": "recover alert", "destination": "-1001"}
    assert await store.claim_gateway_event("telegram", "77", payload)
    await gateway.replay_pending()
    assert submitted == ["recover alert"]
    assert not store.pending
    await gateway.close()


async def test_active_run_defers_replay_without_marking_gateway_failure(setup: Any) -> None:
    gateway, store, requests, _, _ = setup

    async def pending(
        text: str, platform: str, destination: str, remote_id: str, event_id: str
    ) -> GatewayResult:
        raise GatewayRunPending()

    gateway.submit_request = pending
    payload = {"type": "analysis", "text": "recover alert", "destination": "-1001"}
    assert await store.claim_gateway_event("telegram", "Ev-running", payload)
    await gateway.replay_pending()
    assert ("telegram", "Ev-running") in store.pending
    assert len(requests) == 1  # Progress only; no misleading failure edit.
    await gateway.close()


async def test_pending_approval_replays_after_claim_without_worker(setup: Any) -> None:
    gateway, store, _, _, decisions = setup
    await store.bind_gateway_thread("telegram", "-1001", "50", "thread-1", approval_id="request-1")
    payload = {
        "type": "approval",
        "approval_id": "request-1",
        "kind": "orders",
        "thread_id": "thread-1",
        "choice": "reject",
        "user_id": "42",
        "destination": "-1001",
        "remote_id": "50",
        "query_id": "expired-query",
    }
    assert await store.claim_gateway_event("telegram", "88", payload)
    await gateway.replay_pending()
    assert decisions == ["orders:thread-1:reject:telegram:42"]
    assert not store.pending
    await gateway.close()


async def test_bad_secret_wrong_chat_and_duplicate_update_have_no_extra_effect(setup: Any) -> None:
    gateway, store, requests, submitted, _ = setup
    update = {
        "update_id": 1,
        "message": {"message_id": 2, "chat": {"id": -1001}, "from": {"id": 7}, "text": "alert"},
    }
    assert await gateway.handle(update, "bad") == 401
    assert await gateway.handle(update, "secret-token") == 200
    assert await gateway.handle(update, "secret-token") == 200
    update["update_id"] = 2
    update["message"]["chat"]["id"] = -1002
    assert await gateway.handle(update, "secret-token") == 200
    await gateway.close()
    assert submitted == ["alert"]
    assert len(requests) == 2
    assert store.events == {("telegram", "1")}


async def test_callback_requires_exact_user_and_link(setup: Any) -> None:
    gateway, store, requests, _, decisions = setup
    await store.bind_gateway_thread("telegram", "-1001", "50", "thread-1", approval_id="request-1")
    prefix = "o:thread-1:x"
    nonce = hmac.new(
        b"secret-token", f"telegram:-1001:50:{prefix}".encode(), hashlib.sha256
    ).hexdigest()[:8]
    callback = {
        "update_id": 3,
        "callback_query": {
            "id": "cb1",
            "from": {"id": 7},
            "message": {"message_id": 50, "chat": {"id": -1001}},
            "data": f"{prefix}:{nonce}",
        },
    }
    assert await gateway.handle(callback, "secret-token") == 200
    callback["update_id"] = 4
    callback["callback_query"]["from"]["id"] = 42
    callback["callback_query"]["message"]["message_id"] = 51
    assert await gateway.handle(callback, "secret-token") == 200
    callback["update_id"] = 5
    callback["callback_query"]["message"]["message_id"] = 50
    assert await gateway.handle(callback, "secret-token") == 200
    await gateway.close()
    assert decisions == ["orders:thread-1:reject:telegram:42"]
    assert [request.url.path for request in requests].count("/bot123:abc/answerCallbackQuery") == 3


async def test_bot_message_is_ignored(setup: Any) -> None:
    gateway, store, requests, submitted, _ = setup
    update = {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "chat": {"id": -1001},
            "from": {"id": 99, "is_bot": True},
            "text": "alert",
        },
    }
    assert await gateway.handle(update, "secret-token") == 200
    await gateway.close()
    assert not store.events and not requests and not submitted


async def test_publish_approval_binds_message_and_keyboard(setup: Any) -> None:
    gateway, store, requests, _, _ = setup
    remote_id = await gateway.publish_approval(
        "orders", "thread-1", "¿Comprar una acción?", "request-1"
    )
    assert remote_id == "50"
    assert store.links[("telegram", "-1001", remote_id)] == "thread-1"
    edited = json.loads(requests[-1].content)
    assert len(edited["reply_markup"]["inline_keyboard"][0]) == 2


async def test_web_api_failure_never_logs_bot_token(caplog: pytest.LogCaptureFixture) -> None:
    def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": False, "description": "failed"})

    async def submit(
        _text: str, _platform: str, _destination: str, _remote_id: str, _event_id: str
    ) -> GatewayResult:
        raise AssertionError("submission should not start")

    async def decide(
        _kind: str, _thread_id: str, _choice: str, _actor: str, _approval_id: str
    ) -> str:
        raise AssertionError("decision should not start")

    gateway = TelegramGateway(
        TelegramGatewaySettings(
            _env_file=None,
            enabled=True,
            bot_token="123:private-token",
            webhook_secret="secret-token",
            chat_id="-1001",
        ),
        Store(),
        submit,
        decide,
        transport=httpx.MockTransport(unavailable),
    )
    update = {
        "update_id": 1,
        "message": {"message_id": 2, "chat": {"id": -1001}, "from": {"id": 7}, "text": "alert"},
    }
    assert await gateway.handle(update, "secret-token") == 200
    await gateway.close()
    assert "private-token" not in caplog.text
