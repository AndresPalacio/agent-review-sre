import json

import httpx
from langgraph_sdk.client import LangGraphClient

from fin_graph.work.engine import AgentServerEngine


async def test_dispatch_uses_context_metadata_and_enqueue_and_recovers_by_command():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/runs") and request.method == "GET":
            return httpx.Response(
                200, json=[{"run_id": "r1", "metadata": {"work_command_id": "c1"}}]
            )
        return httpx.Response(200, json={"run_id": "r1", "thread_id": "w1"})

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as http:
        engine = AgentServerEngine(client=LangGraphClient(http))
        await engine.ensure_thread("w1", {"actor": "ana"})
        assert await engine.submit("w1", "update", "c1", {"revision": 2}, enqueue=True) == "r1"
        payload = json.loads(requests[-1].content)
        assert payload["context"] == {"revision": 2}
        assert payload["metadata"]["work_command_id"] == "c1"
        assert payload["multitask_strategy"] == "enqueue"
        assert "config" not in payload
        assert await engine.find_run("w1", "c1") == "r1"


async def test_snapshot_discovers_child_created_before_parent_checkpoint_and_cancels_it():
    cancelled = []

    def handler(request):
        path = request.url.path
        if path.endswith("/threads/search"):
            assert json.loads(request.content)["metadata"] == {"parent_thread_id": "w1"}
            return httpx.Response(
                200,
                json=[
                    {
                        "thread_id": "child",
                        "status": "busy",
                        "metadata": {"subagent_name": "technicals"},
                    }
                ],
            )
        if path.endswith("/state"):
            return httpx.Response(200, json={"values": {"async_tasks": {}, "work_revision": 1}})
        if path.endswith("/cancel"):
            cancelled.append(path)
            return httpx.Response(200, json={})
        if path.endswith("/runs"):
            active = [] if cancelled or "/w1/" in path else [{"run_id": "cr", "status": "running"}]
            return httpx.Response(200, json=active)
        return httpx.Response(200, json={"thread_id": "w1", "status": "idle"})

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as http:
        engine = AgentServerEngine(client=LangGraphClient(http))
        snap = await engine.snapshot("w1")
        assert snap["children"][0]["thread_id"] == "child"
        assert snap["children"][0]["status"] == "running"
        assert await engine.cancel_tree("w1")
        assert cancelled == ["/threads/child/runs/cr/cancel"]


async def test_finished_untracked_child_requires_recovery_instead_of_running_forever():
    def handler(request):
        if request.url.path.endswith("/threads/search"):
            return httpx.Response(200, json=[{"thread_id": "orphan", "status": "idle"}])
        if request.url.path.endswith("/state"):
            return httpx.Response(200, json={"values": {"async_tasks": {}}})
        return httpx.Response(200, json={"status": "idle"})

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as http:
        engine = AgentServerEngine(client=LangGraphClient(http))
        snapshot = await engine.snapshot("w")
        assert snapshot["children"][0]["status"] == "recovery_required"


async def test_finished_tracked_child_exposes_pending_parent_notification():
    def handler(request):
        if request.url.path.endswith("/threads/search"):
            return httpx.Response(200, json=[{"thread_id": "child", "status": "idle"}])
        if request.url.path.endswith("/state"):
            return httpx.Response(
                200,
                json={
                    "values": {
                        "async_tasks": {
                            "child": {
                                "thread_id": "child",
                                "task_id": "child",
                                "run_id": "r",
                                "status": "running",
                            }
                        }
                    }
                },
            )
        return httpx.Response(200, json={"status": "idle"})

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as http:
        snapshot = await AgentServerEngine(client=LangGraphClient(http)).snapshot("w")
        assert snapshot["children"][0]["status"] == "awaiting_parent"
