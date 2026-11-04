from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import SecretStr
from slack_bolt.authorization.authorize_result import AuthorizeResult
from slack_sdk.web.async_client import AsyncWebClient

from fin_graph.gateway import GatewayResult, GatewayRunPending
from fin_graph.gateway.settings import SlackGatewaySettings
from fin_graph.gateway.slack import SlackGateway


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


class WebClient(AsyncWebClient):
    def __init__(self) -> None:
        super().__init__(token="xoxb-test")
        self.posts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    async def auth_test(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "user_id": "BOT"}

    async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.posts.append(kwargs)
        return {"ok": True, "channel": kwargs["channel"], "ts": "100.200"}

    async def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        self.updates.append(kwargs)
        return {"ok": True, "channel": kwargs["channel"], "ts": kwargs["ts"]}


def signed_headers(body: bytes, *, secret: str = "sign-secret") -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256)
    return {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": "v0=" + digest.hexdigest(),
        "content-type": "application/json",
    }


def signed_button(channel: str, remote_id: str, prefix: str) -> str:
    data = f"slack:{channel}:{remote_id}:{prefix}".encode()
    nonce = hmac.new(b"sign-secret", data, hashlib.sha256).hexdigest()[:8]
    return f"{prefix}:{nonce}"


@pytest.fixture
def setup() -> tuple[SlackGateway, Store, WebClient, list[str], list[str]]:
    store = Store()
    web = WebClient()
    submitted: list[str] = []
    decisions: list[str] = []

    async def submit(
        text: str, platform: str, destination: str, remote_id: str, event_id: str
    ) -> GatewayResult:
        submitted.append(text)
        assert (platform, destination, remote_id) == ("slack", "C1", "100.200")
        return GatewayResult("thread-1", "report rendered", "orders", approval_id="request-1")

    async def decide(kind: str, thread_id: str, choice: str, actor: str, approval_id: str) -> str:
        assert approval_id == "request-1"
        decisions.append(f"{kind}:{thread_id}:{choice}:{actor}")
        return "Approved"

    async def authorize() -> AuthorizeResult:
        return AuthorizeResult(
            enterprise_id=None, team_id="T1", bot_user_id="BOT", bot_id="B1", bot_token="xoxb-test"
        )

    gateway = SlackGateway(
        SlackGatewaySettings(
            _env_file=None,
            enabled=True,
            bot_token="xoxb-test",
            signing_secret="sign-secret",
            channel_id="C1",
            approver_user_ids=("U1",),
        ),
        store,
        submit,
        decide,
        web_client=web,
        authorize=authorize,
    )
    return gateway, store, web, submitted, decisions


async def send(
    gateway: SlackGateway, body: dict[str, Any], *, valid: bool = True
) -> httpx.Response:
    app = FastAPI()

    @app.post("/slack")
    async def endpoint(request: Request) -> Any:
        return await gateway.handle(request)

    raw = (
        urlencode({"payload": json.dumps(body)}).encode()
        if body["type"] == "block_actions"
        else json.dumps(body).encode()
    )
    headers = signed_headers(raw, secret="sign-secret" if valid else "wrong")
    if body["type"] == "block_actions":
        headers["content-type"] = "application/x-www-form-urlencoded"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/slack", content=raw, headers=headers)


async def test_external_market_bot_is_analyzed_but_own_bot_is_ignored(setup: Any) -> None:
    gateway, store, _web, submitted, _ = setup
    for event_id, bot_id in (("market-signal", "MARKET_FEED"), ("own-message", "B1")):
        response = await send(
            gateway,
            {
                "type": "event_callback",
                "event_id": event_id,
                "team_id": "T1",
                "event": {
                    "type": "message",
                    "channel": "C1",
                    "text": "AAPL crossed the portfolio risk limit",
                    "bot_id": bot_id,
                    "subtype": "bot_message",
                    "ts": "100.100",
                },
            },
        )
        assert response.status_code == 200
    await gateway.close()
    assert submitted == ["AAPL crossed the portfolio risk limit"]
    assert ("slack", "market-signal") in store.events
    assert ("slack", "own-message") not in store.events


async def test_signed_channel_message_acks_then_publishes_report(setup: Any) -> None:
    gateway, store, web, submitted, _ = setup
    started = asyncio.Event()
    release = asyncio.Event()

    async def submit(
        text: str, platform: str, destination: str, remote_id: str, event_id: str
    ) -> GatewayResult:
        started.set()
        await release.wait()
        submitted.append(text)
        return GatewayResult("thread-1", "report rendered", "orders", approval_id="request-1")

    gateway.submit_request = submit
    event = {
        "type": "event_callback",
        "event_id": "Ev1",
        "event": {
            "type": "message",
            "channel": "C1",
            "user": "U2",
            "text": "CPU high",
            "ts": "1.2",
        },
    }
    response = await asyncio.wait_for(send(gateway, event), timeout=2)
    assert response.status_code == 200
    await asyncio.wait_for(started.wait(), timeout=2)
    assert web.posts[0]["text"] == "Analizando…"
    assert submitted == []
    release.set()
    await gateway.close()
    assert submitted == ["CPU high"]
    assert store.links[("slack", "C1", "100.200")] == "thread-1"
    assert web.updates[0]["text"] == "report rendered"
    assert len(web.updates[0]["blocks"][-1]["elements"]) == 2


async def test_message_ack_requires_durable_event_claim(setup: Any) -> None:
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
    event = {
        "type": "event_callback",
        "event_id": "slow-ledger",
        "event": {
            "type": "message",
            "channel": "C1",
            "text": "alert",
            "ts": "1",
            "user": "U2",
        },
    }
    response_task = asyncio.create_task(send(gateway, event))
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert not response_task.done()
    release.set()
    response = await asyncio.wait_for(response_task, timeout=2)
    assert response.status_code == 200
    await gateway.close()


async def test_pending_alert_replays_after_claim_without_worker(setup: Any) -> None:
    gateway, store, _, submitted, _ = setup
    payload = {"type": "analysis", "text": "recover alert", "destination": "C1"}
    assert await store.claim_gateway_event("slack", "Ev-crash", payload)
    await gateway.replay_pending()
    assert submitted == ["recover alert"]
    assert not store.pending
    await gateway.close()


async def test_active_run_defers_replay_without_marking_gateway_failure(setup: Any) -> None:
    gateway, store, web, _, _ = setup

    async def pending(
        text: str, platform: str, destination: str, remote_id: str, event_id: str
    ) -> GatewayResult:
        raise GatewayRunPending()

    gateway.submit_request = pending
    payload = {"type": "analysis", "text": "recover alert", "destination": "C1"}
    assert await store.claim_gateway_event("slack", "Ev-running", payload)
    await gateway.replay_pending()
    assert ("slack", "Ev-running") in store.pending
    assert web.updates == []
    await gateway.close()


async def test_pending_approval_replays_after_claim_without_worker(setup: Any) -> None:
    gateway, store, _, _, decisions = setup
    await store.bind_gateway_thread("slack", "C1", "100.200", "thread-1", approval_id="request-1")
    payload = {
        "type": "approval",
        "approval_id": "request-1",
        "kind": "orders",
        "thread_id": "thread-1",
        "choice": "reject",
        "user_id": "U1",
        "destination": "C1",
        "remote_id": "100.200",
    }
    assert await store.claim_gateway_event("slack", "action-crash", payload)
    await gateway.replay_pending()
    assert decisions == ["orders:thread-1:reject:slack:U1"]
    assert not store.pending
    await gateway.close()


async def test_inbox_failure_does_not_ack_slack_event(setup: Any) -> None:
    gateway, store, _, _, _ = setup

    async def unavailable(
        _platform: str,
        _event_id: str,
        _payload: dict[str, Any] | None = None,
        _owner: str | None = None,
    ) -> bool:
        raise RuntimeError("database unavailable")

    store.claim_gateway_event = unavailable  # type: ignore[method-assign]
    event = {
        "type": "event_callback",
        "event_id": "db-down",
        "event": {
            "type": "message",
            "channel": "C1",
            "text": "alert",
            "ts": "1",
            "user": "U2",
        },
    }
    response = await send(gateway, event)
    assert response.status_code >= 500
    await gateway.close()


async def test_report_blocks_are_preserved_in_slack_update(setup: Any) -> None:
    gateway, _, web, _, _ = setup
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*RCA*"}}]

    async def submit(
        text: str, platform: str, destination: str, remote_id: str, event_id: str
    ) -> GatewayResult:
        return GatewayResult("thread-2", "RCA", report_blocks=blocks)

    gateway.submit_request = submit
    await send(
        gateway,
        {
            "type": "event_callback",
            "event_id": "Ev-blocks",
            "event": {"type": "message", "channel": "C1", "text": "alert", "ts": "1", "user": "U2"},
        },
    )
    await gateway.close()
    assert web.updates[0]["blocks"] == blocks


async def test_publish_approval_binds_message_and_buttons(setup: Any) -> None:
    gateway, store, web, _, _ = setup
    remote_id = await gateway.publish_approval(
        "orders", "thread-1", "¿Comprar una acción?", "request-1"
    )
    assert remote_id == "100.200"
    assert store.links[("slack", "C1", remote_id)] == "thread-1"
    assert web.posts[0]["text"] == "¿Comprar una acción?"
    assert len(web.updates[0]["blocks"][-1]["elements"]) == 2


async def test_socket_mode_is_opt_in_and_closes_connection(setup: Any, monkeypatch) -> None:
    gateway, _, _, _, _ = setup
    events: list[str] = []

    class Handler:
        def __init__(self, _app: Any, token: str) -> None:
            events.append(token)

        async def connect_async(self) -> None:
            events.append("connected")

        async def close_async(self) -> None:
            events.append("closed")

    monkeypatch.setattr("fin_graph.gateway.slack.AsyncSocketModeHandler", Handler)
    await gateway.start_socket_mode()
    assert events == []
    gateway.settings = gateway.settings.model_copy(
        update={"socket_mode": True, "app_token": SecretStr("xapp-test")}
    )
    await gateway.start_socket_mode()
    await gateway.close()
    assert events == ["xapp-test", "connected", "closed"]


def test_socket_mode_requires_app_token() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SlackGatewaySettings(
            _env_file=None,
            enabled=True,
            bot_token="bot",
            signing_secret="secret",
            channel_id="C1",
            socket_mode=True,
        )


async def test_bad_signature_and_wrong_channel_have_no_effect(setup: Any) -> None:
    gateway, store, web, submitted, _ = setup
    event = {
        "type": "event_callback",
        "event_id": "Ev1",
        "event": {"type": "message", "channel": "C2", "user": "U2", "text": "alert", "ts": "1"},
    }
    assert (await send(gateway, event, valid=False)).status_code == 401
    assert (await send(gateway, event)).status_code == 200
    await gateway.close()
    assert not store.events and not web.posts and not submitted


async def test_oversized_slack_request_is_rejected_before_dispatch(setup: Any) -> None:
    gateway, store, web, submitted, _ = setup
    event = {
        "type": "event_callback",
        "event_id": "oversized",
        "event": {"type": "message", "channel": "C1", "text": "x" * (1024 * 1024), "ts": "1"},
    }
    assert (await send(gateway, event)).status_code == 413
    await gateway.close()
    assert not store.events and not web.posts and not submitted


async def test_duplicate_and_bot_message_are_ignored(setup: Any) -> None:
    gateway, store, web, submitted, _ = setup
    event = {
        "type": "event_callback",
        "event_id": "Ev1",
        "event": {"type": "message", "channel": "C1", "user": "U2", "text": "alert", "ts": "1"},
    }
    await send(gateway, event)
    await send(gateway, event)
    bot = {
        "type": "event_callback",
        "event_id": "Ev2",
        "event": {
            "type": "message",
            "channel": "C1",
            "user": "BOT",
            "bot_id": "B1",
            "text": "alert",
            "ts": "2",
        },
    }
    await send(gateway, bot)
    await gateway.close()
    assert submitted == ["alert"]
    assert len(web.posts) == 1
    assert len(store.events) == 1


async def test_button_requires_approver_and_matching_message(setup: Any) -> None:
    gateway, store, web, _, decisions = setup
    await store.bind_gateway_thread("slack", "C1", "100.200", "thread-1", approval_id="request-1")
    value = signed_button("C1", "100.200", "o:thread-1:a")
    action = {
        "type": "block_actions",
        "trigger_id": "tr1",
        "user": {"id": "U2"},
        "channel": {"id": "C1"},
        "message": {"ts": "100.200"},
        "actions": [{"action_id": "fin_approve", "value": value}],
    }
    assert (await send(gateway, action)).status_code == 200
    action["user"]["id"] = "U1"
    action["message"]["ts"] = "other"
    assert (await send(gateway, action)).status_code == 200
    action["message"]["ts"] = "100.200"
    assert (await send(gateway, action)).status_code == 200
    await gateway.close()
    assert decisions == ["orders:thread-1:approve:slack:U1"]
    assert web.updates[-1]["text"] == "Approved"
