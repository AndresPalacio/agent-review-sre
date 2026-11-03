"""Deterministic release acceptance for the AI Hedge Fund replacement runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import tempfile
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx

from fin_graph.backtesting import BacktestArtifactStore, BacktestJobManager, BacktestRunOutput
from fin_graph.backtesting.models import BacktestEvent, BacktestEventKind
from fin_graph.compat.ai_hedge_fund.schemas import BacktestRequest, HedgeFundRequest
from fin_graph.compat.contracts import JsonObject
from fin_graph.compat.storage import CompatibilityStore, ConcurrentUpdateError, RunStatus
from fin_graph.domain.fills import OrderStatus
from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.server.compat.events import InternalEvent
from fin_graph.server.factory import RuntimeProfile, create_app

REQUIRED_CHECKS = (
    "health",
    "catalogs",
    "flow_crud",
    "fixture_analysis",
    "v1_backtest",
    "v2_backtest",
    "job_cancellation",
    "key_metadata",
    "paper_order",
    "flow_cas",
    "run_cas",
    "cycle_sequence",
    "restart_persistence",
    "capabilities",
)


class _FixtureHedgeFundRunner:
    async def run(
        self,
        request: HedgeFundRequest,
        emit: Callable[[InternalEvent], Awaitable[None]],
    ) -> dict[str, object]:
        await emit(
            InternalEvent(
                kind="progress",
                payload={
                    "agent": "valuation_analyst",
                    "ticker": request.tickers[0],
                    "status": "complete",
                    "timestamp": None,
                    "analysis": "deterministic acceptance fixture",
                },
            )
        )
        return {
            "decisions": {request.tickers[0]: {"action": "hold", "quantity": 0}},
            "analyst_signals": {"valuation_analyst": {request.tickers[0]: {"signal": "neutral"}}},
            "current_prices": {request.tickers[0]: 100.0},
        }

    async def backtest(
        self,
        request: BacktestRequest,
        emit: Callable[[InternalEvent], Awaitable[None]],
        cancellation: object,
    ) -> dict[str, object]:
        del emit, cancellation
        return {
            "results": [
                {
                    "date": request.start_date,
                    "portfolio_value": request.initial_capital,
                }
            ],
            "performance_metrics": {
                "total_return": 0.0,
                "sharpe_ratio": None,
                "max_drawdown": 0.0,
            },
        }


async def _strategy_runner(
    request: JsonObject,
    *,
    event_sink: Callable[[BacktestEvent], Awaitable[None]],
    cancellation: asyncio.Event,
) -> BacktestRunOutput:
    if request.get("symbols") == ["SLOW"]:
        await cancellation.wait()
    else:
        await event_sink(
            BacktestEvent(
                sequence=0,
                kind=BacktestEventKind.PROGRESS,
                progress=0.5,
                message="deterministic fixture",
            )
        )
    symbol = str(cast(list[object], request["symbols"])[0])
    return BacktestRunOutput(
        result={
            "metrics": {"total_return": 0.1, "trade_count": 1},
            "equity_curve": [],
            "trades": [],
            "signal_summary": [],
            "data_coverage": {},
            "warnings": [],
        },
        signals=(
            {
                "date": "2025-01-02",
                "symbol": symbol,
                "raw_signal": 0.5,
                "applied_weight": 0.0,
            },
            {
                "date": "2025-01-03",
                "symbol": symbol,
                "raw_signal": 0.7,
                "applied_weight": 0.5,
            },
        ),
    )


def _flow_body(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": "release acceptance fixture",
        "nodes": [
            {"id": "input", "type": "stock-analyzer-node", "data": {"name": "Input"}},
            {"id": "output", "type": "json-output-node", "data": {"name": "Output"}},
        ],
        "edges": [{"id": "edge", "source": "input", "target": "output"}],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "data": {"fixture": True},
        "is_template": False,
        "tags": ["acceptance"],
    }


def _hedge_body() -> dict[str, object]:
    return {
        "tickers": ["AAPL"],
        "graph_nodes": [
            {"id": "stock-analyzer-node", "type": "stock-analyzer-node"},
            {"id": "valuation_analyst", "type": "agent-node"},
            {"id": "portfolio_manager", "type": "portfolio-manager-node"},
        ],
        "graph_edges": [
            {"id": "a", "source": "stock-analyzer-node", "target": "valuation_analyst"},
            {"id": "b", "source": "valuation_analyst", "target": "portfolio_manager"},
        ],
        "start_date": "2025-01-02",
        "end_date": "2025-01-10",
        "initial_cash": 10_000,
    }


def _strategy_body(symbol: str = "AAPL") -> dict[str, object]:
    return {
        "algorithm_key": "sma_crossover",
        "parameters": {"fast_window": 2, "slow_window": 5},
        "symbols": [symbol],
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "initial_capital": 10_000,
    }


def _sse_events(response: httpx.Response) -> list[str]:
    return [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]


async def _wait_job(client: httpx.AsyncClient, run_id: str, terminal: set[str]) -> dict[str, Any]:
    for _ in range(300):
        response = await client.get(f"/v2/backtests/{run_id}")
        response.raise_for_status()
        body = response.json()
        if body["status"] in terminal:
            return cast(dict[str, Any], body)
        await asyncio.sleep(0.01)
    raise TimeoutError(f"backtest job did not reach {sorted(terminal)}")


def _profile(
    backend: str,
    *,
    data_dir: Path | None,
    database_url: str | None,
    bootstrap_admin_key: str,
) -> RuntimeProfile:
    if backend == "sqlite":
        if data_dir is None:
            raise ValueError("data_dir is required for SQLite acceptance")
        return RuntimeProfile.local(data_dir=data_dir)
    if backend == "postgres":
        value = (database_url or os.getenv("FIN_GRAPH_TEST_DATABASE_URL") or "").strip()
        if not value:
            raise ValueError("database_url is required for PostgreSQL acceptance")
        return RuntimeProfile.distributed(
            database_url=value,
            bootstrap_admin_key=bootstrap_admin_key,
        )
    raise ValueError("backend must be 'sqlite' or 'postgres'")


async def _run_profile(
    profile: RuntimeProfile,
    *,
    artifact_dir: Path,
    acceptance_secret: str,
) -> tuple[dict[str, object], list[dict[str, object]], str, str]:
    token = uuid4().hex[:12]
    flow_name = f"Acceptance {token}"
    provider = f"Acceptance {token}"
    order_id = f"acceptance-{token}"
    checks: dict[str, bool] = {}
    semantic: dict[str, object] = {}
    app = create_app(profile)
    flow_store_id = ""
    flow_version = 0

    async with app.router.lifespan_context(app):
        store = cast(CompatibilityStore, app.state.compatibility_store)
        app.state.compatibility_hedge_fund = _FixtureHedgeFundRunner()
        app.state.compatibility_backtest_manager = BacktestJobManager(
            store,
            BacktestArtifactStore(artifact_dir),
            {"strategy": _strategy_runner},
        )
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://acceptance",
            timeout=30,
        ) as client:
            health = await client.get("/health")
            checks["health"] = health.status_code == 200 and health.json()["status"] == "ok"

            models = (await client.get("/language-models/")).json()["models"]
            agents = (await client.get("/hedge-fund/agents")).json()["agents"]
            algorithms = (await client.get("/v2/algorithms")).json()
            algorithm_keys = [item["key"] for item in algorithms]
            checks["catalogs"] = (
                len(models) >= 7
                and len(agents) == 19
                and algorithm_keys
                == [
                    "sma_crossover",
                    "rsi_mean_reversion",
                    "momentum",
                ]
            )

            created = await client.post("/flows/", json=_flow_body(flow_name))
            created.raise_for_status()
            public_flow_id = created.json()["id"]
            updated = await client.put(
                f"/flows/{public_flow_id}",
                json={"description": "updated acceptance fixture", "tags": ["release"]},
            )
            fetched = await client.get(f"/flows/{public_flow_id}")
            checks["flow_crud"] = (
                updated.status_code == 200
                and fetched.status_code == 200
                and fetched.json()["tags"] == ["release"]
            )
            flow = next(item for item in await store.list_flows() if item.name == flow_name)

            async def update_flow(label: str) -> bool:
                try:
                    await store.update_flow(
                        flow.id,
                        expected_version=flow.version,
                        definition={"winner": label},
                    )
                except ConcurrentUpdateError:
                    return False
                return True

            flow_outcomes = await asyncio.gather(update_flow("one"), update_flow("two"))
            checks["flow_cas"] = sum(flow_outcomes) == 1
            flow = cast(Any, await store.get_flow(flow.id))
            flow_store_id = flow.id
            flow_version = flow.version

            run = await store.create_flow_run(flow.id, flow.active_revision, {"tickers": ["AAPL"]})
            run_outcomes = await asyncio.gather(
                store.transition_flow_run(
                    run.id,
                    expected_version=run.version,
                    status=RunStatus.SUCCEEDED,
                    result={"decision": "hold"},
                ),
                store.transition_flow_run(
                    run.id,
                    expected_version=run.version,
                    status=RunStatus.CANCELLED,
                ),
            )
            checks["run_cas"] = sum(item is not None for item in run_outcomes) == 1
            await asyncio.gather(
                *(store.append_run_cycle(run.id, f"fixture-{index}", {}) for index in range(12))
            )
            cycles = await store.list_run_cycles(run.id)
            checks["cycle_sequence"] = [item.sequence for item in cycles] == list(range(1, 13))

            analysis = await client.post("/hedge-fund/run", json=_hedge_body())
            analysis_events = _sse_events(analysis)
            checks["fixture_analysis"] = analysis_events == ["start", "progress", "complete"]
            v1 = await client.post("/hedge-fund/backtest", json=_hedge_body())
            checks["v1_backtest"] = _sse_events(v1) == ["start", "complete"]

            fast = await client.post("/v2/backtests", json=_strategy_body())
            fast.raise_for_status()
            fast_terminal = await _wait_job(client, fast.json()["run_id"], {"complete", "failed"})
            signals = await client.get(f"/v2/backtests/{fast.json()['run_id']}/signals")
            checks["v2_backtest"] = (
                fast_terminal["status"] == "complete"
                and fast_terminal["result"]["metrics"]["total_return"] == 0.1
                and signals.status_code == 200
                and signals.json()["total"] == 2
            )

            slow = await client.post("/v2/backtests", json=_strategy_body("SLOW"))
            slow.raise_for_status()
            cancelled = await client.post(f"/v2/backtests/{slow.json()['run_id']}/cancel")
            cancelled.raise_for_status()
            slow_terminal = await _wait_job(client, slow.json()["run_id"], {"cancelled", "failed"})
            checks["job_cancellation"] = slow_terminal["status"] == "cancelled"

            key = await client.post(
                "/api-keys/",
                json={
                    "provider": provider,
                    "key_value": acceptance_secret,
                    "description": "acceptance only",
                    "is_active": True,
                },
            )
            listed = await client.get("/api-keys/")
            checks["key_metadata"] = (
                key.status_code == 200
                and acceptance_secret not in key.text
                and any(
                    item["description"] == "acceptance only" and "key_value" not in item
                    for item in listed.json()
                )
            )
            await client.delete(f"/api-keys/{provider}")

            broker = app.state.paper_broker

            async def quote(_ticker: str) -> float:
                return 10.0

            broker.quotes = quote
            broker.market_open = lambda _now: True
            execution = await broker.place(
                ProposedOrder(
                    client_order_id=order_id,
                    ticker="AAPL",
                    action=OrderAction.BUY,
                    quantity=1,
                    reference_price=10.0,
                    as_of=date(2025, 1, 2),
                    reasoning="acceptance fixture",
                )
            )
            checks["paper_order"] = execution.status is OrderStatus.FILLED
            checks["capabilities"] = app.state.runtime_capabilities == {
                "backend": profile.backend,
                "distributed_locks": profile.backend == "postgres",
                "multi_worker": profile.backend == "postgres",
            }

            semantic = {
                "health": "ok",
                "catalogs": {
                    "remote_models": 7,
                    "agents": len(agents),
                    "algorithms": algorithm_keys,
                },
                "flow": {"revisions": 3, "cas_winners": 1, "cycle_count": len(cycles)},
                "analysis": {"events": analysis_events, "decision": "hold"},
                "v1_backtest": {"events": _sse_events(v1), "total_return": 0.0},
                "v2_backtest": {
                    "status": fast_terminal["status"],
                    "total_return": 0.1,
                    "signals": signals.json()["total"],
                },
                "cancellation": slow_terminal["status"],
                "key_metadata": "redacted",
                "paper_order": execution.status.value,
            }

    restarted = create_app(profile)
    async with restarted.router.lifespan_context(restarted):
        restarted_store = cast(CompatibilityStore, restarted.state.compatibility_store)
        persisted_flow = await restarted_store.get_flow(flow_store_id)
        persisted_order = await restarted.state.paper_broker.execution(order_id)
        checks["restart_persistence"] = (
            persisted_flow is not None
            and persisted_flow.version == flow_version
            and persisted_order is not None
            and persisted_order.status is OrderStatus.FILLED
        )
        if persisted_flow is not None:
            await restarted_store.delete_flow(
                persisted_flow.id, expected_version=persisted_flow.version
            )

    check_rows = [
        {"name": name, "passed": bool(checks.get(name, False))} for name in REQUIRED_CHECKS
    ]
    canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return semantic, check_rows, digest, profile.backend


async def run_acceptance(
    *,
    backend: str,
    data_dir: Path | None = None,
    database_url: str | None = None,
    acceptance_secret: str | None = None,
) -> dict[str, object]:
    """Run one backend and return redacted, machine-readable release evidence."""

    secret = acceptance_secret or secrets.token_urlsafe(32)
    bootstrap = "acceptance-bootstrap-" + secrets.token_urlsafe(32)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if backend == "sqlite" and data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="fin-graph-acceptance-")
        data_dir = Path(temporary.name) / "runtime"
    artifacts = (
        Path(data_dir) / "acceptance-artifacts"
        if data_dir is not None
        else Path(tempfile.mkdtemp(prefix="fin-graph-acceptance-artifacts-"))
    )
    profile = _profile(
        backend,
        data_dir=data_dir,
        database_url=database_url,
        bootstrap_admin_key=bootstrap,
    )
    try:
        semantic, checks, digest, actual_backend = await _run_profile(
            profile,
            artifact_dir=artifacts,
            acceptance_secret=secret,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()
    capabilities = {
        "backend": actual_backend,
        "distributed_locks": actual_backend == "postgres",
        "multi_worker": actual_backend == "postgres",
    }
    return {
        "schema_version": 1,
        "backend": actual_backend,
        "passed": all(item["passed"] for item in checks),
        "capabilities": capabilities,
        "checks": checks,
        "public_digest": digest,
        "public_summary": semantic,
    }


def write_acceptance_report(report: dict[str, object], output: Path) -> tuple[Path, Path]:
    """Write the same redacted evidence as JSON and readable Markdown."""

    output.mkdir(parents=True, exist_ok=True)
    backend = str(report["backend"])
    json_path = output / f"acceptance-{backend}.json"
    markdown_path = output / f"acceptance-{backend}.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    label = "SQLite" if backend == "sqlite" else "PostgreSQL"
    rows = "\n".join(
        f"| `{item['name']}` | {'PASS' if item['passed'] else 'FAIL'} |"
        for item in cast(list[dict[str, object]], report["checks"])
    )
    markdown_path.write_text(
        "\n".join(
            [
                "# AI Hedge Fund runtime acceptance",
                "",
                f"- Backend: {label}",
                f"- Result: {'PASS' if report['passed'] else 'FAIL'}",
                f"- Public digest: `{report['public_digest']}`",
                "",
                "| Check | Result |",
                "|---|---|",
                rows,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, markdown_path


__all__ = ["REQUIRED_CHECKS", "run_acceptance", "write_acceptance_report"]
