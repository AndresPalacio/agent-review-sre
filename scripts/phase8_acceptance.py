"""Local acceptance evidence: no sockets, provider clients, or outbound delivery."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.evals.market.runner import run_context
from fin_graph.evals.market.scenarios import load_equity_scenario
from fin_graph.harness.runtime import make_analysis_graph
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS
from fin_graph.integrations.context import RunContext
from fin_graph.server.app import create_app
from fin_graph.server.config import ServerConfig
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, Storage
from fin_graph.workflows.analysis.services import UnavailableAnalysisServices


class FixtureTools:
    """The production analytics tools over the acceptance scenario's recorded market data."""

    def tools(self, context: RunContext) -> list[BaseTool]:
        return list(ANALYTICS_TOOLS)


class MeasuredGraph:
    """Gate the actual graph after its first state for reproducible queue pressure."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.release = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.calls: Counter[str] = Counter()
        self.changed = asyncio.Event()

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return await self.graph.aget_state(config)

    async def astream(self, value: Any, **kwargs: Any) -> AsyncIterator[Any]:
        thread = kwargs["config"]["configurable"]["thread_id"]
        self.calls[thread] += 1
        self.active += 1
        self.changed.set()
        self.peak = max(self.peak, self.active)
        held = False
        try:
            async for event in self.graph.astream(value, **kwargs):
                yield event
                if event[0] == "values" and not held:
                    held = True
                    await self.release.wait()
        finally:
            self.active -= 1


async def _wait_started(graph: MeasuredGraph, minimum: int) -> None:
    async with asyncio.timeout(10):
        while graph.active < minimum:
            graph.changed.clear()
            if graph.active < minimum:
                await graph.changed.wait()


async def load_check(*, burst: int = 32, pending: int = 8, concurrent: int = 2) -> dict[str, Any]:
    if not 1 <= concurrent <= pending < burst <= 512:
        raise ValueError("require 1 <= concurrent <= pending < burst <= 512")
    started = time.perf_counter()
    criteria: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="fin-graph-acceptance-") as temporary:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(temporary) / 'runs.sqlite'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            storage = Storage(engine)
            await storage.put_key("offline-acceptance", "operator")
            scenario = load_equity_scenario("001-deep-value")
            graph = MeasuredGraph(
                make_analysis_graph(
                    UnavailableAnalysisServices(), FixtureTools(), checkpointer=InMemorySaver()
                )
            )
            manager = RunManager(
                graph,
                storage,
                lambda: run_context(scenario),
                max_concurrent=concurrent,
                max_pending=pending,
            )
            config = ServerConfig(
                _env_file=None, max_http_requests=512, stream_fallback_seconds=0.05
            )
            app = create_app(
                manager=manager,
                config=config,
            )
            headers = {"x-api-key": "offline-acceptance"}
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://offline", timeout=30
                ) as client,
            ):
                request_start = time.perf_counter()
                responses = await asyncio.gather(
                    *(
                        client.post(
                            "/v1/analyses",
                            json={
                                "request": {
                                    **scenario.request(),
                                    "question": f"Carga financiera offline número {i}",
                                }
                            },
                            headers=headers,
                        )
                        for i in range(burst)
                    )
                )
                burst_seconds = time.perf_counter() - request_start
                accepted = [r.json() for r in responses if r.status_code == 202]
                overloaded = [r for r in responses if r.status_code == 429]
                criteria["deterministic_admission"] = (
                    len(accepted) == pending and len(overloaded) == burst - pending
                )
                criteria["retry_after"] = all(
                    r.headers.get("Retry-After") == "1" for r in overloaded
                )
                criteria["no_rejected_run_rows"] = (
                    len(await storage.list_runs(limit=512)) == pending
                )
                await _wait_started(graph, concurrent)
                graph.release.set()
                await asyncio.wait_for(
                    asyncio.gather(*(manager.wait(r["run_id"]) for r in accepted)), 30
                )
                criteria["bounded_execution"] = graph.peak == concurrent
                criteria["one_execution_per_accepted_thread"] = all(
                    graph.calls[r["thread_id"]] == 1 for r in accepted
                )
                statuses = [await storage.get_run(r["run_id"]) for r in accepted]
                criteria["all_accepted_completed"] = all(
                    r is not None and r["status"] == "completed" for r in statuses
                )
                details = [
                    await client.get(f"/v1/analyses/{r['thread_id']}", headers=headers)
                    for r in accepted
                ]
                criteria["real_graph_reports"] = all(
                    r.status_code == 200 and bool(r.json()["report"]) for r in details
                )
                thread = accepted[0]["thread_id"]
                durable = await storage.events(thread, 0)
                replay = await client.get(f"/v1/threads/{thread}/stream", headers=headers)
                ids = [
                    int(line[4:]) for line in replay.text.splitlines() if line.startswith("id: ")
                ]
                criteria["sse_durable_replay"] = ids == [e["id"] for e in durable] and len(
                    set(ids)
                ) == len(ids)
                resumed_stream = await client.get(
                    f"/v1/threads/{thread}/stream",
                    headers={**headers, "Last-Event-ID": str(ids[0])},
                )
                remaining = [
                    int(line[4:])
                    for line in resumed_stream.text.splitlines()
                    if line.startswith("id: ")
                ]
                criteria["sse_cursor_replay"] = remaining == ids[1:]
                criteria["no_sse_subscriber_leak"] = not app.state.events.subscribers
                graph.release.clear()
                interrupted = (
                    await client.post(
                        "/v1/analyses",
                        json={
                            "request": {
                                **scenario.request(),
                                "question": "Análisis financiero interrumpido",
                            }
                        },
                        headers=headers,
                    )
                ).json()
                await _wait_started(graph, 1)
                await manager.stop()
                criteria["cancellation_releases_capacity"] = (
                    manager.pending_count == 0 and graph.active == 0
                )
                cancelled_run = await storage.get_run(interrupted["run_id"])
                criteria["cancelled_run_resumable"] = (
                    cancelled_run is not None and cancelled_run["status"] == "interrupted"
                )
                await manager.start()
                graph.release.set()
                resumed = await client.post(
                    f"/v1/threads/{interrupted['thread_id']}/resume", headers=headers
                )
                criteria["resume_accepted"] = resumed.status_code == 202
                if resumed.status_code == 202:
                    await manager.wait(resumed.json()["run_id"])
                    resumed_run = await storage.get_run(resumed.json()["run_id"])
                    criteria["resume_completed"] = (
                        resumed_run is not None and resumed_run["status"] == "completed"
                    )
                else:
                    criteria["resume_completed"] = False
                criteria["capacity_recovered"] = manager.pending_count == 0
                body = await client.post(
                    "/v1/analyses", content=b"x" * (1048576 + 1), headers=headers
                )
                criteria["oversized_body_rejected"] = body.status_code == 413
                measurements = {
                    "burst": burst,
                    "pending_limit": pending,
                    "execution_limit": concurrent,
                    "accepted": len(accepted),
                    "overloaded": len(overloaded),
                    "peak_executing": graph.peak,
                    "burst_seconds": round(burst_seconds, 4),
                    "total_seconds": round(time.perf_counter() - started, 4),
                    "durable_sse_events": len(ids),
                }
            criteria["shutdown_releases_capacity"] = (
                manager.pending_count == 0 and not manager.tasks
            )
        finally:
            await engine.dispose()
    return {
        "mode": "offline_load",
        "passed": all(criteria.values()),
        "criteria": criteria,
        "measurements": measurements,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "transport": "in-process ASGI",
            "storage": "temporary SQLite",
            "graph": "actual analysis graph, deterministic unavailable-model fallbacks",
        },
        "limitations": [
            "No LLM quality or provider verification",
            "SSE checks completed durable replay; ASGITransport buffers the response. "
            "Live network streaming is not measured",
            "No Postgres failover or production capacity claim",
        ],
    }


def preflight() -> dict[str, Any]:
    names = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DATABASE_URL",
        "BOOTSTRAP_ADMIN_KEY",
        "KUBECONFIG",
        "PROMETHEUS_URL",
        "SLACK_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "MCP_CONFIG",
    ]
    environment = {name: bool(os.environ.get(name, "").strip()) for name in names}
    tools = {name: shutil.which(name) is not None for name in ["docker", "kubectl", "kind", "helm"]}
    missing = [name for name, present in tools.items() if not present]
    if not environment["OPENAI_API_KEY"] and not environment["ANTHROPIC_API_KEY"]:
        missing.append("LLM credential (OPENAI_API_KEY or ANTHROPIC_API_KEY)")
    missing += [name for name in ["DATABASE_URL", "BOOTSTRAP_ADMIN_KEY"] if not environment[name]]
    return {
        "mode": "preflight",
        "validation": "presence_only",
        "passed": not missing,
        "environment": environment,
        "tools": tools,
        "missing": missing,
        "limitations": [
            "Does not read .env or credential files",
            "Does not connect to services, validate credentials, or send messages",
            "CLI PATH presence does not establish daemon/cluster readiness",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load")
    load.add_argument("--burst", type=int, default=32)
    load.add_argument("--pending", type=int, default=8)
    load.add_argument("--concurrent", type=int, default=2)
    commands.add_parser("preflight")
    args = parser.parse_args()
    try:
        result = (
            preflight()
            if args.command == "preflight"
            else asyncio.run(
                load_check(burst=args.burst, pending=args.pending, concurrent=args.concurrent)
            )
        )
    except Exception as exc:
        result = {"passed": False, "error_type": type(exc).__name__, "mode": args.command}
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
