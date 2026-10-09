"""Telegram secret-token webhook ingress with bounded, authenticated callbacks."""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from uuid import uuid4

import httpx

from sre_graph.gateway import (
    DecideApproval,
    GatewayLeaseLost,
    GatewayRunPending,
    GatewayStore,
    SubmitAlert,
)
from sre_graph.gateway.buttons import button_value, parse_button
from sre_graph.gateway.settings import TelegramGatewaySettings
from sre_graph.safety.secrets import redact_secrets

_LOG = logging.getLogger(__name__)


class TelegramGateway:
    def __init__(
        self,
        settings: TelegramGatewaySettings,
        store: GatewayStore,
        submit_alert: SubmitAlert,
        decide: DecideApproval,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.enabled or not settings.webhook_secret or not settings.bot_token:
            raise ValueError("Telegram gateway is not configured")
        self.settings = settings
        self.store = store
        self.submit_alert = submit_alert
        self.decide = decide
        self._secret = settings.webhook_secret.get_secret_value()
        self.client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{settings.bot_token.get_secret_value()}/",
            timeout=15,
            transport=transport,
        )
        self._tasks: set[asyncio.Task[None]] = set()

    async def handle(self, update: dict[str, Any], secret: str | None) -> int:
        if secret is None or not hmac.compare_digest(secret, self._secret):
            return 401
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            return 400
        if len(str(update).encode()) > self.settings.max_body_bytes:
            return 413
        message = update.get("message")
        callback = update.get("callback_query")
        if isinstance(message, dict):
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            text = message.get("text")
            if (
                str(chat.get("id")) != self.settings.chat_id
                or sender.get("is_bot")
                or not isinstance(text, str)
                or not text.strip()
            ):
                return 200
            event_id = str(update["update_id"])
            payload = {
                "type": "alert",
                "text": redact_secrets(text),
                "destination": str(chat["id"]),
            }
            owner = uuid4().hex
            if await self.store.claim_gateway_event("telegram", event_id, payload, owner):
                self._spawn(self._process_event(event_id, payload, owner))
            return 200
        if isinstance(callback, dict):
            message_data = callback.get("message")
            if not isinstance(message_data, dict):
                return 200
            chat = message_data.get("chat") or {}
            user = callback.get("from") or {}
            if not isinstance(chat, dict) or not isinstance(user, dict):
                return 200
            destination = str(chat.get("id"))
            user_id = str(user.get("id"))
            remote_id = str(message_data.get("message_id"))
            query_id = callback.get("id")
            if destination != self.settings.chat_id or not isinstance(query_id, str):
                return 200
            parsed = parse_button(
                self._secret, "telegram", destination, remote_id, str(callback.get("data", ""))
            )
            allowed = (
                user_id in self.settings.approver_user_ids
                and parsed is not None
                and await self.store.gateway_thread("telegram", destination, remote_id) == parsed[1]
            )
            if not allowed or parsed is None:
                self._spawn(self._answer_callback(query_id, "Not authorized"))
                return 200
            kind, thread_id, choice = parsed
            approval_id = await self.store.gateway_approval_id("telegram", destination, remote_id)
            if not approval_id:
                self._spawn(self._answer_callback(query_id, "Approval is no longer available"))
                return 200
            event_id = str(update["update_id"])
            payload = {
                "type": "approval",
                "kind": kind,
                "thread_id": thread_id,
                "approval_id": approval_id,
                "choice": choice,
                "user_id": user_id,
                "destination": destination,
                "remote_id": remote_id,
                "query_id": query_id,
            }
            owner = uuid4().hex
            if await self.store.claim_gateway_event("telegram", event_id, payload, owner):
                self._spawn(self._process_event(event_id, payload, owner))
            return 200
        return 200

    async def _answer_callback(self, query_id: str, status: str) -> None:
        try:
            await self._api("answerCallbackQuery", {"callback_query_id": query_id, "text": status})
        except ValueError:
            _LOG.error("Telegram callback acknowledgement failed")

    async def _process_event(self, event_id: str, payload: dict[str, Any], owner: str) -> None:
        worker = asyncio.current_task()
        heartbeat = asyncio.create_task(self._heartbeat(event_id, owner, worker))
        try:
            if payload.get("type") == "alert":
                await self._investigate(payload["text"], payload["destination"], event_id, owner)
            elif payload.get("type") == "approval":
                await self._require_owner(event_id, owner)
                await self._answer_callback(payload["query_id"], "Received")
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
            await self.store.complete_gateway_event("telegram", event_id, owner)
        except (GatewayRunPending, GatewayLeaseLost):
            pass
        except Exception:
            retryable = await self.store.fail_gateway_event("telegram", event_id, owner)
            _LOG.error("Telegram gateway event %s", "pending for replay" if retryable else "dead")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(
        self, event_id: str, owner: str, worker: asyncio.Task[None] | None
    ) -> None:
        while True:
            await asyncio.sleep(30)
            if not await self.store.touch_gateway_event("telegram", event_id, owner):
                if worker is not None:
                    worker.cancel()
                return

    async def _require_owner(self, event_id: str, owner: str) -> None:
        if not await self.store.touch_gateway_event("telegram", event_id, owner):
            raise GatewayLeaseLost()

    async def replay_pending(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(minutes=2)
        for event_id, payload in await self.store.pending_gateway_events("telegram", cutoff):
            owner = uuid4().hex
            if await self.store.claim_stale_gateway_event("telegram", event_id, cutoff, owner):
                await self._process_event(event_id, payload, owner)

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks))
        await self.client.aclose()

    async def publish_approval(
        self, kind: str, thread_id: str, summary: str, approval_id: str
    ) -> str:
        """Post an inline keyboard for one pending checkpoint."""
        if kind not in {"chat", "remediation"} or not 1 <= len(thread_id) <= 48 or not approval_id:
            raise ValueError("invalid approval notification")
        destination = self.settings.chat_id
        if destination is None:
            raise ValueError("Telegram chat is missing")
        safe_summary = escape(redact_secrets(summary)[:3000])
        response = await self._api(
            "sendMessage", {"chat_id": destination, "text": safe_summary, "parse_mode": "HTML"}
        )
        remote_id = str(response["result"]["message_id"])
        await self.store.bind_gateway_thread(
            "telegram", destination, remote_id, thread_id, approval_id=approval_id
        )
        keyboard = [
            {
                "text": label,
                "callback_data": button_value(
                    self._secret, "telegram", destination, remote_id, kind, thread_id, choice
                ),
            }
            for choice, label in (("approve", "Approve"), ("reject", "Reject"))
        ]
        await self._api(
            "editMessageText",
            {
                "chat_id": destination,
                "message_id": int(remote_id),
                "text": safe_summary,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": [keyboard]},
            },
        )
        return remote_id

    def _spawn(self, operation: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOG.error("Telegram gateway task failed", exc_info=task.exception())

    async def _api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.post(method, json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("Telegram API request failed") from None
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise ValueError("Telegram API request failed")
        return data

    async def _investigate(self, text: str, destination: str, event_id: str, owner: str) -> None:
        remote_id: str | None = None
        try:
            await self._require_owner(event_id, owner)
            response = await self._api(
                "sendMessage", {"chat_id": destination, "text": "Investigating alert…"}
            )
            remote_id = str(response["result"]["message_id"])
            await self._require_owner(event_id, owner)
            result = await self.submit_alert(text, "telegram", destination, remote_id, event_id)
            await self._require_owner(event_id, owner)
            await self.store.bind_gateway_thread(
                "telegram", destination, remote_id, result.thread_id, approval_id=result.approval_id
            )
            payload: dict[str, Any] = {
                "chat_id": destination,
                "message_id": int(remote_id),
                "text": result.report_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if result.approval_kind and result.approval_id:
                payload["reply_markup"] = {
                    "inline_keyboard": [
                        [
                            {
                                "text": label,
                                "callback_data": button_value(
                                    self._secret,
                                    "telegram",
                                    destination,
                                    remote_id,
                                    result.approval_kind,
                                    result.thread_id,
                                    choice,
                                ),
                            }
                            for choice, label in (("approve", "Approve"), ("reject", "Reject"))
                        ]
                    ]
                }
            await self._require_owner(event_id, owner)
            await self._api("editMessageText", payload)
        except (GatewayRunPending, GatewayLeaseLost):
            raise
        except Exception:
            _LOG.error("Telegram investigation failed")
            if remote_id is not None:
                try:
                    await self._api(
                        "editMessageText",
                        {
                            "chat_id": destination,
                            "message_id": int(remote_id),
                            "text": "Investigation failed. Please retry via the API.",
                        },
                    )
                except ValueError:
                    _LOG.error("Telegram failure notice could not be delivered")
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
        status = await self.decide(kind, thread_id, choice, f"telegram:{user_id}", approval_id)
        await self._api(
            "editMessageText",
            {
                "chat_id": destination,
                "message_id": int(remote_id),
                "text": status,
            },
        )
