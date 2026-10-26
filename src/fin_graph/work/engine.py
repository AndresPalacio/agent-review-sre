"""LangGraph SDK adapter. All execution and child notification belong to the server."""

from typing import Any

import httpx
from langgraph_sdk import get_client

_ACTIVE = {"pending", "running"}


class AgentServerEngine:
    def __init__(self, url: str = "http://127.0.0.1:8011", *, client: Any = None) -> None:
        self.client: Any = client if client is not None else get_client(url=url)

    async def ensure_thread(self, work_id: str, metadata: dict[str, Any]) -> None:
        await self.client.threads.create(
            thread_id=work_id, metadata=metadata, if_exists="do_nothing"
        )

    async def submit(
        self,
        work_id: str,
        text: str,
        command_id: str,
        context: dict[str, Any],
        *,
        enqueue: bool = False,
    ) -> str:
        run = await self.client.runs.create(
            thread_id=work_id,
            assistant_id="supervisor",
            input={"messages": [{"role": "user", "content": text}]},
            metadata={"work_command_id": command_id},
            context=context,
            multitask_strategy="enqueue" if enqueue else "reject",
        )
        return str(run["run_id"])

    async def _runs(self, work_id: str) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        while True:
            page = await self.client.runs.list(work_id, limit=100, offset=len(runs))
            runs.extend(page)
            if len(page) < 100:
                return runs

    async def find_run(self, work_id: str, command_id: str) -> str | None:
        try:
            runs = await self._runs(work_id)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise
        return next(
            (
                str(run["run_id"])
                for run in runs
                if (run.get("metadata") or {}).get("work_command_id") == command_id
            ),
            None,
        )

    async def _children(self, work_id: str) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        while True:
            page = await self.client.threads.search(
                metadata={"parent_thread_id": work_id}, limit=100, offset=len(children)
            )
            children.extend(page)
            if len(page) < 100:
                return children

    async def snapshot(self, work_id: str) -> dict[str, Any]:
        parent = await self.client.threads.get(work_id)
        state = await self.client.threads.get_state(work_id)
        values = state.get("values") or {}
        tasks = values.get("async_tasks") or {}
        tracked = {task["thread_id"]: task for task in tasks.values()}
        children = []
        # Search metadata as well as parent state: a crash can occur between
        # creating a child and checkpointing async_tasks in its parent.
        found = {child["thread_id"]: child for child in await self._children(work_id)}
        for thread_id in tracked.keys() - found.keys():
            found[thread_id] = await self.client.threads.get(thread_id)
        for thread_id, child in found.items():
            task = tracked.get(thread_id, {})
            remote = child.get("status", "busy")
            status = {
                "busy": "running",
                "idle": "success",
                "interrupted": "waiting_for_input",
                "error": "error",
            }[remote]
            # A finished child still needs its parent to consume the notification.
            # Do not present the parent's old 'started' message as the final result.
            if status == "success" and task.get("status") not in {"success", "cancelled"}:
                if parent["status"] == "idle":
                    status = "awaiting_parent" if task else "recovery_required"
                else:
                    status = "running"
            if task.get("status") == "cancelled" and remote != "busy":
                status = "cancelled"
            children.append(
                {
                    "task_id": task.get("task_id", thread_id),
                    "thread_id": thread_id,
                    "run_id": task.get("run_id"),
                    "agent_name": task.get("agent_name")
                    or (child.get("metadata") or {}).get("subagent_name", "unknown"),
                    "status": status,
                    "engine_status": remote,
                }
            )
        return {"status": parent["status"], "values": values, "children": children}

    async def _cancel_runs(self, thread_id: str) -> None:
        for run in await self._runs(thread_id):
            if run["status"] in _ACTIVE:
                try:
                    await self.client.runs.cancel(thread_id, run["run_id"], wait=True)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 409:
                        raise

    async def cancel_tree(self, work_id: str) -> bool:
        await self._cancel_runs(work_id)
        children = await self._children(work_id)
        for child in children:
            await self._cancel_runs(child["thread_id"])
        # A completion notification can race with cancellation; inspect again.
        await self._cancel_runs(work_id)
        latest = await self._children(work_id)
        for child in latest:
            await self._cancel_runs(child["thread_id"])
        for thread_id in [work_id, *(child["thread_id"] for child in latest)]:
            if any(run["status"] in _ACTIVE for run in await self._runs(thread_id)):
                return False
        return True
