"""Signed Slack Bolt ingress and asynchronous investigation/approval dispatch."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import Response
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.authorization.authorize_result import AuthorizeResult
from slack_sdk.web.async_client import AsyncWebClient

from sre_graph.gateway import (
    DecideApproval,
    GatewayLeaseLost,
    GatewayRunPending,
    GatewayStore,
    SubmitAlert,
)
from sre_graph.gateway.buttons import button_value, parse_button
from sre_graph.gateway.settings import SlackGatewaySettings
from sre_graph.safety.secrets import redact_secrets

_LOG = logging.getLogger(__name__)


class SlackGateway:
    def __init__(
        self,
        settings: SlackGatewaySettings,
        store: GatewayStore,
        submit_alert: SubmitAlert,
        decide: DecideApproval,
        *,
        web_client: AsyncWebClient | None = None,
        authorize: Callable[..., Awaitable[AuthorizeResult]] | None = None,
    ) -> None:
        if not settings.enabled or not settings.signing_secret or not settings.bot_token:
            raise ValueError("Slack gateway is not configured")
        self.settings = settings
        self.store = store
        self.submit_alert = submit_alert
        self.decide = decide
        self._secret = settings.signing_secret.get_secret_value()
        self.web_client = web_client or AsyncWebClient(token=settings.bot_token.get_secret_value())
        self.app = AsyncApp(
            client=self.web_client,
            signing_secret=self._secret,
            authorize=authorize,
            process_before_response=True,
        )
        self.handler = AsyncSlackRequestHandler(self.app)
        self._socket_handler: AsyncSocketModeHandler | None = None
        self._tasks: set[asyncio.Task[None]] = set()

        @self.app.event("message")
        async def on_message(event: dict[str, Any], body: dict[str, Any], context: Any) -> None:
            if (
                event.get("channel") != self.settings.channel_id
                or (event.get("bot_id") and event["bot_id"] == context.get("bot_id"))
                or (event.get("user") and event["user"] == context.get("bot_user_id"))
                or event.get("subtype") not in {None, "bot_message"}
                or not isinstance(event.get("text"), str)
                or not event["text"].strip()
                or not isinstance(body.get("event_id"), str)
            ):
                return
            event_id = body["event_id"]
            payload = {
                "type": "alert",
                "text": redact_secrets(event["text"]),
                "destination": str(event["channel"]),
            }
            owner = uuid4().hex
            if await self.store.claim_gateway_event("slack", event_id, payload, owner):
                self._spawn(self._process_event(event_id, payload, owner))

        @self.app.action(re.compile(r"^sre_(approve|reject)$"))
        async def on_action(ack: Any, body: dict[str, Any]) -> None:
            item = await self._approval_event(body)
            if item is not None:
                event_id, payload = item
                owner = uuid4().hex
                if await self.store.claim_gateway_event("slack", event_id, payload, owner):
                    self._spawn(self._process_event(event_id, payload, owner))
            await ack()

    async def _approval_event(self, body: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        actions = body.get("actions")
        action = actions[0] if isinstance(actions, list) and actions else {}
        user = body.get("user") or {}
        channel = body.get("channel") or {}
        message = body.get("message") or {}
        if not all(isinstance(item, dict) for item in (action, user, channel, message)):
            return None
        user_id = user.get("id")
        destination = channel.get("id")
        remote_id = message.get("ts")
        value = action.get("value")
        if (
            not isinstance(destination, str)
            or destination != self.settings.channel_id
            or not isinstance(user_id, str)
            or user_id not in self.settings.approver_user_ids
            or not isinstance(remote_id, str)
            or not isinstance(value, str)
        ):
            return None
        parsed = parse_button(self._secret, "slack", destination, remote_id, value)
        if parsed is None or parsed[2] != action.get("action_id", "").removeprefix("sre_"):
            return None
        kind, thread_id, choice = parsed
        if await self.store.gateway_thread("slack", destination, remote_id) != thread_id:
            return None
        approval_id = await self.store.gateway_approval_id("slack", destination, remote_id)
        if not approval_id:
            return None
        event_id = f"action:{body.get('trigger_id', '')}:{user_id}:{value}"
        return event_id, {
            "type": "approval",
            "kind": kind,
            "thread_id": thread_id,
            "approval_id": approval_id,
            "choice": choice,
            "user_id": user_id,
            "destination": destination,
            "remote_id": remote_id,
        }

    async def _process_event(self, event_id: str, payload: dict[str, Any], owner: str) -> None:
        worker = asyncio.current_task()
        heartbeat = asyncio.create_task(self._heartbeat(event_id, owner, worker))
        try:
            if payload.get("type") == "alert":
                await self._investigate(payload["text"], payload["destination"], event_id, owner)
            elif payload.get("type") == "approval":
                await self._require_owner(event_id, owner)
                await self._apply_decision(
                    payload["kind"],
                    payload["thread_id"],
                    payload["choice"],
                    payload["user_id"],
                    payload["destination"],
                    payload["remote_id"],
                    payload.get("approval_id", ""),
                )
            else:
                raise ValueError("invalid gateway inbox payload")
            await self.store.complete_gateway_event("slack", event_id, owner)
        except (GatewayRunPending, GatewayLeaseLost):
            pass
        except Exception:
            retryable = await self.store.fail_gateway_event("slack", event_id, owner)
            _LOG.error("Slack gateway event %s", "pending for replay" if retryable else "dead")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(
        self, event_id: str, owner: str, worker: asyncio.Task[None] | None
    ) -> None:
        while True:
            await asyncio.sleep(30)
            if not await self.store.touch_gateway_event("slack", event_id, owner):
                if worker is not None:
                    worker.cancel()
                return

    async def _require_owner(self, event_id: str, owner: str) -> None:
        if not await self.store.touch_gateway_event("slack", event_id, owner):
            raise GatewayLeaseLost()

    async def replay_pending(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(minutes=2)
        for event_id, payload in await self.store.pending_gateway_events("slack", cutoff):
            owner = uuid4().hex
            if await self.store.claim_stale_gateway_event("slack", event_id, cutoff, owner):
                await self._process_event(event_id, payload, owner)

    async def handle(self, request: Request) -> Response:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > self.settings.max_body_bytes:
                return Response(status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)  # Bolt reads the same body.
        return await self.handler.handle(request)

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks))
        if self._socket_handler is not None:
            await self._socket_handler.close_async()  # type: ignore[no-untyped-call]
            self._socket_handler = None

    async def start_socket_mode(self) -> None:
        """Connect the development Socket Mode transport when explicitly enabled."""
        if not self.settings.socket_mode:
            return
        if self.settings.app_token is None:
            raise ValueError("Slack app_token is required for socket mode")
        if self._socket_handler is not None:
            return
        handler = AsyncSocketModeHandler(self.app, self.settings.app_token.get_secret_value())
        await handler.connect_async()  # type: ignore[no-untyped-call]
        self._socket_handler = handler

    async def publish_approval(
        self, kind: str, thread_id: str, summary: str, approval_id: str
    ) -> str:
        """Post a separately approved action and bind its buttons to this message."""
        if kind not in {"chat", "remediation"} or not 1 <= len(thread_id) <= 48 or not approval_id:
            raise ValueError("invalid approval notification")
        destination = self.settings.channel_id
        if destination is None:
            raise ValueError("Slack channel is missing")
        safe_summary = redact_secrets(summary)[:3000]
        response = await self.web_client.chat_postMessage(channel=destination, text=safe_summary)
        remote_id = str(response["ts"])
        await self.store.bind_gateway_thread(
            "slack", destination, remote_id, thread_id, approval_id=approval_id
        )
        elements = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": label},
                "action_id": f"sre_{choice}",
                "value": button_value(
                    self._secret, "slack", destination, remote_id, kind, thread_id, choice
                ),
            }
            for choice, label in (("approve", "Approve"), ("reject", "Reject"))
        ]
        await self.web_client.chat_update(
            channel=destination,
            ts=remote_id,
            text=safe_summary,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": safe_summary}},
                {"type": "actions", "elements": elements},
            ],
        )
        return remote_id

    def _spawn(self, operation: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOG.error("Slack gateway task failed", exc_info=task.exception())

    async def _investigate(self, text: str, destination: str, event_id: str, owner: str) -> None:
        await self._require_owner(event_id, owner)
        response = await self.web_client.chat_postMessage(
            channel=destination, text="Investigating alert…"
        )
        remote_id = str(response["ts"])
        try:
            await self._require_owner(event_id, owner)
            result = await self.submit_alert(text, "slack", destination, remote_id, event_id)
            await self._require_owner(event_id, owner)
            await self.store.bind_gateway_thread(
                "slack", destination, remote_id, result.thread_id, approval_id=result.approval_id
            )
            payload: dict[str, Any] = {
                "channel": destination,
                "ts": remote_id,
                "text": result.report_text,
            }
            if result.report_blocks:
                payload["blocks"] = list(result.report_blocks)
            if result.approval_kind and result.approval_id:
                blocks = payload.get("blocks") or [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": result.report_text[:3000]},
                    }
                ]
                payload["blocks"] = [
                    *blocks,
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": label},
                                "action_id": f"sre_{choice}",
                                "value": button_value(
                                    self._secret,
                                    "slack",
                                    destination,
                                    remote_id,
                                    result.approval_kind,
                                    result.thread_id,
                                    choice,
                                ),
                            }
                            for choice, label in (("approve", "Approve"), ("reject", "Reject"))
                        ],
                    },
                ]
            await self._require_owner(event_id, owner)
            await self.web_client.chat_update(**payload)
        except (GatewayRunPending, GatewayLeaseLost):
            raise
        except Exception:
            _LOG.exception("Slack investigation failed")
            try:
                await self.web_client.chat_update(
                    channel=destination,
                    ts=remote_id,
                    text="Investigation failed. Please retry via the API.",
                )
            except Exception:
                _LOG.error("Slack failure notice could not be delivered")
            raise

    async def _apply_decision(
        self,
        kind: str,
        thread_id: str,
        choice: str,
        user_id: str,
        destination: str,
        remote_id: str,
        approval_id: str,
    ) -> None:
        status = await self.decide(kind, thread_id, choice, f"slack:{user_id}", approval_id)
        await self.web_client.chat_update(channel=destination, ts=remote_id, text=status, blocks=[])
