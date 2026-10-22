"""Explicit, deterministic offline model for the synthetic async pilot.

This is a message-driven protocol exerciser, never a substitute for measured LLM
reasoning. It has no response queue and can handle repeated runs and notifications.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal
from uuid import uuid4

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def fixture_scope(
    text: str, previous: tuple[str, str] = ("demo-checkout", "15m")
) -> tuple[str, str]:
    """Read a small explicit demo vocabulary; all returned data remains synthetic."""
    service = re.search(r"(?:service|servicio)\s*[:=]\s*([\w.-]+)", text, re.I)
    window = re.search(r"(?:window|ventana)\s*[:=]\s*(\d+\s*[mhd])", text, re.I)
    natural_window = re.search(r"(\d+)\s*(minutos?|minutes?|horas?|hours?)", text, re.I)
    service_name = (
        service.group(1) if service else ("pagos" if "pagos" in text.lower() else previous[0])
    )
    if window:
        window_name = window.group(1).replace(" ", "").lower()
    elif natural_window:
        unit = "h" if natural_window.group(2).lower().startswith("h") else "m"
        window_name = natural_window.group(1) + unit
    else:
        window_name = previous[1]
    return service_name, window_name


class OfflinePilotModel(BaseChatModel):
    """Choose repeatable tool calls from conversation state, without any provider."""

    role: Literal["supervisor", "metrics", "logs"]
    allow_delegation: bool = True

    @property
    def _llm_type(self) -> str:
        return "sre-pilot-offline"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> OfflinePilotModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        answer = self._answer(messages)
        return ChatResult(generations=[ChatGeneration(message=answer)])

    def _answer(self, messages: list[BaseMessage]) -> AIMessage:
        latest_user = next(
            (i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)),
            -1,
        )
        user = messages[latest_user] if latest_user >= 0 else HumanMessage("")
        text = user.text
        results = [m for m in messages[latest_user + 1 :] if isinstance(m, ToolMessage)]
        if results:
            if self.role != "supervisor":
                return AIMessage(content=results[-1].content)
            return AIMessage(
                content="Offline deterministic pilot; synthetic fixtures.\n"
                + "\n".join(m.text for m in results)
            )
        notification = user.additional_kwargs.get("deepagents_notification")
        consolidate = self.role == "supervisor" and text.lower().startswith("consolida")
        if notification or "<task-notification>" in text or consolidate:
            notices: dict[str, str] = {}
            for message in messages:
                if isinstance(message, HumanMessage) and (
                    event := message.additional_kwargs.get("deepagents_notification")
                ):
                    notices[event["task_id"]] = message.text
            return AIMessage(
                content="Offline pilot: synthetic task results.\n" + "\n".join(notices.values())
            )
        if self.role != "supervisor":
            service, window = "demo-checkout", "15m"
            for message in messages:
                if isinstance(message, HumanMessage):
                    service, window = fixture_scope(message.text, (service, window))
            return self._calls(
                [(f"read_{self.role}_fixture", {"service": service, "window": window})]
            )
        lower = text.lower()
        if not self.allow_delegation:
            answer = (
                "p95 is the 95th percentile: 95% of observed values are at or below it."
                if "p95" in lower
                else "Direct-response fixture for: " + text
            )
            return AIMessage(content="Offline deterministic pilot (no LLM inference). " + answer)
        tasks = self._tracked_tasks(messages)
        if any(word in lower for word in ("actualiza", "update", "enfoca", "focus")):
            names = self._requested_agents(lower)
            selected = [(name, task_id) for name, task_id in tasks.items() if name in names]
            if not selected:
                return AIMessage(content="Offline pilot: no tracked child matches the update.")
            return self._calls(
                [
                    ("update_async_task", {"task_id": task_id, "message": text})
                    for _, task_id in selected
                ]
            )
        if any(word in lower for word in ("cancela", "cancel", "detén", "stop")):
            selected_ids = [
                task_id for name, task_id in tasks.items() if name in self._requested_agents(lower)
            ]
            if not selected_ids:
                return AIMessage(content="Offline pilot: no tracked child to cancel.")
            return self._calls(
                [("cancel_async_task", {"task_id": task_id}) for task_id in selected_ids]
            )
        if any(word in lower for word in ("estado", "status", "progreso", "progress")):
            return self._calls([("list_async_tasks", {"status_filter": "all"})])
        return self._calls(
            [
                ("start_async_task", {"subagent_type": name, "description": text})
                for name in self._requested_agents(lower)
            ]
        )

    @staticmethod
    def _requested_agents(text: str) -> list[str]:
        names = []
        if any(word in text for word in ("metric", "métric", "latencia", "latency")):
            names.append("metrics")
        if any(word in text for word in ("log", "registro")):
            names.append("logs")
        return names or ["metrics", "logs"]

    @staticmethod
    def _tracked_tasks(messages: list[BaseMessage]) -> dict[str, str]:
        starts: dict[str, str] = {}
        tasks: dict[str, str] = {}
        for message in messages:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    if call["name"] == "start_async_task" and call["id"] is not None:
                        starts[call["id"]] = call["args"].get("subagent_type", "")
            elif isinstance(message, ToolMessage) and message.tool_call_id in starts:
                match = re.search(r"Launched async subagent\. task_id: ([\w-]+)", message.text)
                if match:
                    tasks[starts[message.tool_call_id]] = match.group(1)
            elif event := message.additional_kwargs.get("deepagents_notification"):
                tasks[event["agent_name"]] = event["task_id"]
        return tasks

    @staticmethod
    def _calls(calls: list[tuple[str, dict[str, Any]]]) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {"id": f"offline-{uuid4().hex}", "name": name, "args": args} for name, args in calls
            ],
        )
