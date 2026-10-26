"""Local chat session and the prompt-toolkit REPL command handlers."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.base import BaseStore
from langgraph.types import Command

from fin_graph.domain.analysis_report import AnalysisReport
from fin_graph.domain.rendering.analysis import render_analysis_markdown
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.safety.secrets import redact_secrets, redact_value
from fin_graph.server.storage import Storage


@dataclass(frozen=True)
class ChatTurn:
    text: str
    status: str
    requests: list[dict[str, Any]]


def _pending_actions(requests: list[Any]) -> list[dict[str, Any]]:
    if len(requests) != 1 or not isinstance(requests[0], dict):
        raise ValueError("expected exactly one pending approval")
    actions = requests[0].get("action_requests")
    if (
        not isinstance(actions, list)
        or not actions
        or any(
            not isinstance(action, dict) or not isinstance(action.get("name"), str)
            for action in actions
        )
    ):
        raise ValueError("invalid pending actions")
    return actions


def _approval_decisions(argument: str, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Approve/reject the visible batch; edit only a single pending action."""
    decision_type, _, edit_text = argument.partition(" ")
    if decision_type not in {"approve", "edit", "reject"}:
        raise ValueError("use /approve approve|reject|edit")
    if decision_type != "edit":
        return [{"type": decision_type} for _ in actions]
    if len(actions) != 1:
        raise ValueError("edit supports only one pending action; approve or reject the whole batch")
    try:
        edited = json.loads(edit_text)
    except ValueError as exc:
        raise ValueError("edit requires JSON action") from exc
    if not isinstance(edited, dict):
        raise ValueError("edited action must be an object")
    if edited.get("name") != actions[0]["name"] or not isinstance(edited.get("args"), dict):
        raise ValueError("edited action must keep the pending tool and provide an args object")
    return [{"type": "edit", "edited_action": edited}]


class ChatSession:
    """Own one local user's thread list; messages remain only in graph checkpoints."""

    def __init__(
        self,
        graph: Any,
        store: BaseStore,
        context: RunContext,
        *,
        registry: IntegrationRegistry | None = None,
        analysis: Any = None,
        user_id: str = "local",
        approval_audit: Storage | None = None,
    ) -> None:
        self.graph = graph
        self.store = store
        self.context = context
        self.registry = registry
        self.analysis = analysis
        self.user_id = user_id
        self.approval_audit = approval_audit
        self.thread_id: str | None = None
        self.threads: list[str] = []
        self.tokens = 0

    async def start(self) -> str:
        saved = await self.store.aget(("users", self.user_id, "prefs"), "threads")
        if saved is not None:
            self.threads = [str(item) for item in saved.value.get("ids", [])][:200]
            active = saved.value.get("active")
            if active in self.threads:
                self.thread_id = str(active)
        if self.thread_id is None:
            return await self.new_thread()
        return self.thread_id

    async def new_thread(self) -> str:
        self.thread_id = str(uuid4())
        self.threads.append(self.thread_id)
        self.threads = self.threads[-200:]
        await self._save_threads()
        return self.thread_id

    async def send(self, message: str) -> ChatTurn:
        if not message.strip() or len(message) > 16000:
            raise ValueError("message must contain 1-16000 characters")
        if self.thread_id is None:
            await self.start()
        config = self._config()
        state = await self.graph.aget_state(config)
        if any(task.interrupts for task in state.tasks):
            raise ValueError("approval pending; use /approve")
        result = await self.graph.ainvoke(
            {"messages": [HumanMessage(message)]}, config, context=self.context
        )
        return self._turn(result)

    async def command(self, line: str) -> str:
        name, _, argument = line.partition(" ")
        argument = argument.strip()
        if name == "/new":
            return await self.new_thread()
        if name == "/threads":
            return "\n".join(self.threads)
        if name == "/resume":
            if argument not in self.threads:
                raise ValueError("unknown local thread")
            self.thread_id = argument
            await self._save_threads()
            state = await self.graph.aget_state(self._config())
            pending = [item.value for task in state.tasks for item in task.interrupts]
            if pending:
                safe = redact_value(pending)
                return f"{argument}\nAprobación pendiente: {json.dumps(safe, ensure_ascii=False)}"
            return argument
        if name == "/approve":
            return await self._approve(argument)
        if name == "/integrations":
            if self.registry is None:
                return "integrations unavailable"
            checks = await self.registry.verify_all(self.context)
            return "\n".join(f"{item.integration}: {item.status}" for item in checks)
        if name == "/analiza":
            if self.analysis is None:
                return "análisis no disponible"
            if not argument:
                raise ValueError("/analiza necesita tickers o una pregunta: /analiza AAPL MSFT")
            result = await self.analysis.ainvoke(
                {"raw_request": argument, "user_requested": True}, context=self.context
            )
            if result.get("is_noise"):
                return "el mensaje no nombra ningún ticker"
            if not result.get("report"):
                return "sin reporte"
            return render_analysis_markdown(AnalysisReport.model_validate(result["report"]))
        if name == "/cost":
            return f"{self.tokens} tokens (monetary cost unavailable without provider pricing)"
        if name == "/exit":
            return "exit"
        raise ValueError("unknown command")

    async def _approve(self, argument: str) -> str:
        config = self._config()
        state = await self.graph.aget_state(config)
        requests = [item.value for task in state.tasks for item in task.interrupts]
        actions = _pending_actions(requests)
        decisions = _approval_decisions(argument, actions)
        context = replace(self.context, actor=f"local:{self.user_id}", role="approver")
        if self.approval_audit is not None:
            records = []
            for action, decision in zip(actions, decisions, strict=True):
                name = action.get("name")
                args = action.get("arguments", action.get("args"))
                if not isinstance(name, str) or not isinstance(args, dict):
                    raise ValueError("invalid pending action")
                if decision["type"] == "edit":
                    args = decision["edited_action"]["args"]
                digest = sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
                records.append((decision["type"], name, digest))
            for decision_type, name, digest in records:
                await self.approval_audit.record_approval(
                    self.thread_id or "", "chat", context.actor or "", decision_type, name, digest
                )
        result = await self.graph.ainvoke(
            Command(resume={"decisions": decisions}), config, context=context
        )
        turn = self._turn(result)
        if turn.status == "awaiting_approval":
            pending = json.dumps(turn.requests, ensure_ascii=False)
            return f"{turn.text}\nAprobación pendiente: {pending}".strip()
        return turn.text

    def _turn(self, result: dict[str, Any]) -> ChatTurn:
        messages = result.get("messages") or []
        last = next(
            (
                item
                for item in reversed(messages)
                if isinstance(item, AIMessage) and not item.tool_calls
            ),
            None,
        )
        if last is not None and last.usage_metadata:
            self.tokens += last.usage_metadata.get("total_tokens", 0)
        pending = result.get("__interrupt__", ())
        return ChatTurn(
            text=redact_secrets(str(last.text)) if last is not None else "",
            status="awaiting_approval" if pending else "completed",
            requests=redact_value([item.value for item in pending]),
        )

    def _config(self) -> dict[str, Any]:
        if self.thread_id is None:
            raise ValueError("no active thread")
        return {"configurable": {"thread_id": self.thread_id}}

    async def _save_threads(self) -> None:
        await self.store.aput(
            ("users", self.user_id, "prefs"),
            "threads",
            {"ids": self.threads, "active": self.thread_id},
        )


class RemoteChatSession:
    """Chat over the server API; thread IDs live in this CLI process."""

    def __init__(
        self, client: httpx.AsyncClient, api_key: str, *, thread_id: str | None = None
    ) -> None:
        if not api_key:
            raise ValueError("API key required")
        if thread_id is not None and not self._valid_thread_id(thread_id):
            raise ValueError("invalid remote thread ID")
        self.client = client
        self.api_key = api_key
        self.thread_id = thread_id
        self.threads = [thread_id] if thread_id else []
        self._pending_requests: dict[str, list[dict[str, Any]]] = {}

    async def start(self) -> str:
        if self.thread_id is None:
            return await self.new_thread()
        return self.thread_id

    async def new_thread(self) -> str:
        self.thread_id = str(uuid4())
        self.threads.append(self.thread_id)
        self.threads = self.threads[-200:]
        self._pending_requests[self.thread_id] = []
        return self.thread_id

    async def send(self, message: str) -> ChatTurn:
        if not message.strip() or len(message) > 16000:
            raise ValueError("message must contain 1-16000 characters")
        thread_id = await self.start()
        messages: list[str] = []
        requests: list[dict[str, Any]] = []
        status = "completed"
        finished = False
        try:
            async with self.client.stream(
                "POST",
                f"/v1/chat/threads/{thread_id}/messages",
                json={"message": message},
                headers={"X-API-Key": self.api_key},
            ) as response:
                self._check_status(response)
                event = ""
                data: list[str] = []
                async for line in response.aiter_lines():
                    if not line:
                        if data:
                            payload = self._event_payload(data)
                            if event == "message":
                                content = payload.get("text")
                                if not isinstance(content, str):
                                    raise ValueError("invalid server event")
                                messages.append(self._redact_text(content))
                            elif event == "approval":
                                raw_requests = payload.get("requests")
                                if not isinstance(raw_requests, list):
                                    raise ValueError("invalid server event")
                                requests = self._redact_value(redact_value(raw_requests))
                                status = "awaiting_approval"
                                finished = True
                            elif event == "done":
                                finished = True
                            elif event == "error":
                                raise ValueError("remote chat failed")
                        event = ""
                        data = []
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                if data:
                    raise ValueError("incomplete server event")
                if not finished:
                    raise ValueError("incomplete server response")
        except httpx.RequestError as exc:
            raise ValueError(f"remote request failed ({type(exc).__name__})") from None
        self._pending_requests[thread_id] = requests
        return ChatTurn(text="\n".join(messages), status=status, requests=requests)

    async def command(self, line: str) -> str:
        name, _, argument = line.partition(" ")
        argument = argument.strip()
        if name == "/new":
            return await self.new_thread()
        if name == "/threads":
            return "\n".join(self.threads)
        if name == "/resume":
            if not self._valid_thread_id(argument):
                raise ValueError("/resume requires a valid thread ID")
            try:
                response = await self.client.get(
                    f"/v1/chat/threads/{argument}", headers={"X-API-Key": self.api_key}
                )
            except httpx.RequestError as exc:
                raise ValueError(f"remote request failed ({type(exc).__name__})") from None
            payload = self._json_object(response)
            self.thread_id = argument
            if argument not in self.threads:
                self.threads.append(argument)
            self._pending_requests[argument] = []
            if payload.get("status") == "awaiting_approval":
                requests = payload.get("requests")
                if not isinstance(requests, list):
                    raise ValueError("invalid server response")
                safe = self._redact_value(redact_value(requests))
                self._pending_requests[argument] = safe
                return f"{argument}\nAprobación pendiente: {json.dumps(safe, ensure_ascii=False)}"
            return argument
        if name == "/approve":
            return await self._approve(argument)
        if name == "/integrations":
            try:
                response = await self.client.get(
                    "/v1/integrations", headers={"X-API-Key": self.api_key}
                )
            except httpx.RequestError as exc:
                raise ValueError(f"remote request failed ({type(exc).__name__})") from None
            self._check_status(response)
            items = response.json()
            if not isinstance(items, list):
                raise ValueError("invalid server response")
            return "\n".join(f"{item['id']}: {item['status']}" for item in items)
        if name == "/analiza":
            return await self._analyze(argument)
        if name == "/cost":
            return "monetary cost unavailable from server"
        if name == "/exit":
            return "exit"
        raise ValueError("unknown command")

    async def _approve(self, argument: str) -> str:
        thread_id = await self.start()
        if thread_id not in self._pending_requests:
            await self.command(f"/resume {thread_id}")
        actions = _pending_actions(self._pending_requests[thread_id])
        decisions = _approval_decisions(argument, actions)
        try:
            response = await self.client.post(
                f"/v1/chat/threads/{thread_id}/approve",
                json={"decisions": decisions},
                headers={"X-API-Key": self.api_key},
            )
        except httpx.RequestError as exc:
            raise ValueError(f"remote request failed ({type(exc).__name__})") from None
        payload = self._json_object(response)
        if not isinstance(payload.get("reply"), str):
            raise ValueError("invalid server response")
        reply = self._redact_text(payload["reply"])
        self._pending_requests[thread_id] = []
        if payload.get("status") == "interrupted":
            requests = payload.get("requests")
            if not isinstance(requests, list):
                raise ValueError("invalid server response")
            safe_requests = self._redact_value(redact_value(requests))
            self._pending_requests[thread_id] = safe_requests
            pending = json.dumps(safe_requests, ensure_ascii=False)
            return f"{reply}\nAprobación pendiente: {pending}".strip()
        return reply

    async def _analyze(self, text: str) -> str:
        if not text:
            raise ValueError("/analiza necesita tickers o una pregunta: /analiza AAPL MSFT")
        headers = {"X-API-Key": self.api_key}
        try:
            response = await self.client.post("/v1/analyses", json={"text": text}, headers=headers)
        except httpx.RequestError as exc:
            raise ValueError(f"remote request failed ({type(exc).__name__})") from None
        payload = self._json_object(response)
        thread_id = payload.get("thread_id")
        if not isinstance(thread_id, str) or not self._valid_thread_id(thread_id):
            raise ValueError("invalid server response")
        for attempt in range(120):
            if attempt:
                await asyncio.sleep(0.25)
            try:
                response = await self.client.get(f"/v1/analyses/{thread_id}", headers=headers)
            except httpx.RequestError as exc:
                raise ValueError(f"remote request failed ({type(exc).__name__})") from None
            current = self._json_object(response)
            report = current.get("report")
            if report:
                try:
                    return self._redact_text(
                        render_analysis_markdown(AnalysisReport.model_validate(report))
                    )
                except ValueError:
                    raise ValueError("invalid server report") from None
            run = current.get("run")
            if not isinstance(run, dict) or not isinstance(run.get("status"), str):
                raise ValueError("invalid server response")
            if run["status"] == "completed":
                return "sin reporte: el mensaje no nombra ninguna acción"
            if run["status"] in {"failed", "interrupted"}:
                raise ValueError(f"remote analysis {run['status']}")
        raise ValueError("remote analysis timed out")

    def _redact_text(self, value: str) -> str:
        return redact_secrets(value).replace(self.api_key, "[REDACTED:api_key]")

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, dict):
            return {key: self._redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        return value

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        if not 200 <= response.status_code < 300:
            raise ValueError(f"server returned HTTP {response.status_code}")

    @classmethod
    def _json_object(cls, response: httpx.Response) -> dict[str, Any]:
        cls._check_status(response)
        try:
            payload = response.json()
        except ValueError:
            raise ValueError("invalid server response") from None
        if not isinstance(payload, dict):
            raise ValueError("invalid server response")
        return payload

    @staticmethod
    def _event_payload(data: list[str]) -> dict[str, Any]:
        try:
            payload = json.loads("\n".join(data))
        except ValueError as exc:
            raise ValueError("invalid server event") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid server event")
        return payload

    @staticmethod
    def _valid_thread_id(thread_id: str) -> bool:
        return re.fullmatch(r"[A-Za-z0-9_-]{1,64}", thread_id) is not None
