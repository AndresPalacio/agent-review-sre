"""Deterministic browser fixture for the compatibility and native finance surfaces.

Run from the repository root with ``uv run python tests/web/fixture_server.py``.
The fixture uses the production routers, durable compatibility store, paper broker,
order graph and static web build. Only deterministic model/data adapters are replaced.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import uvicorn
from cryptography.fernet import Fernet
from fastapi import FastAPI
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.server.finance_app import FixtureTools, fixture_context, paper_broker

from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel
from fin_graph.backtesting import (
    BacktestArtifactStore,
    BacktestEvent,
    BacktestEventKind,
    BacktestJobManager,
    BacktestRunOutput,
)
from fin_graph.compat.settings import CompatibilityProfile
from fin_graph.compat.storage import (
    CompatibilityStore,
    FernetSecretCipher,
    open_compat_sqlite,
)
from fin_graph.config.settings import Settings
from fin_graph.evals.market.scenarios import load_equity_scenario
from fin_graph.graphs.analysis.services import UnavailableAnalysisServices
from fin_graph.graphs.chat.graph import make_chat_graph
from fin_graph.graphs.orders.desk import OrderDesk
from fin_graph.graphs.orders.graph import make_order_graph
from fin_graph.harness.runtime import make_analysis_graph
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.memory.theses import ThesisMemory
from fin_graph.persistence.paper_ledger import create_paper_tables
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.server.app import create_app
from fin_graph.server.compat.events import InternalEvent
from fin_graph.server.config import ServerConfig
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, Storage


class FixtureOllama:
    """In-memory Ollama lifecycle with the same public contract."""

    def __init__(self) -> None:
        self.running = False
        self.models: list[str] = []
        self.progress: dict[str, dict[str, Any]] = {}

    async def check_ollama_status(self) -> dict[str, Any]:
        return {
            "installed": True,
            "running": self.running,
            "server_running": self.running,
            "available_models": list(self.models),
            "server_url": "http://fixture-ollama:11434",
            "error": None,
        }

    async def get_available_models(self) -> list[dict[str, str]]:
        return [
            {"display_name": model, "model_name": model, "provider": "Ollama"}
            for model in self.models
        ]

    async def start_server(self) -> dict[str, Any]:
        self.running = True
        return {"success": True, "message": "Ollama fixture started"}

    async def stop_server(self) -> dict[str, Any]:
        self.running = False
        return {"success": True, "message": "Ollama fixture stopped"}

    async def download_model(self, model_name: str) -> dict[str, Any]:
        if model_name not in self.models:
            self.models.append(model_name)
        self.progress[model_name] = {
            "status": "completed",
            "percentage": 100.0,
            "message": "Download complete",
        }
        return {"success": True, "message": f"Model {model_name} downloaded"}

    async def download_model_with_progress(self, model_name: str) -> AsyncIterator[str]:
        await self.download_model(model_name)
        yield 'data: {"status":"completed","percentage":100}\n\n'

    def get_download_progress(self, model_name: str) -> dict[str, Any] | None:
        return self.progress.get(model_name)

    def get_all_download_progress(self) -> dict[str, dict[str, Any]]:
        return dict(self.progress)

    def cancel_download(self, model_name: str) -> bool:
        return self.progress.pop(model_name, None) is not None

    async def delete_model(self, model_name: str) -> dict[str, Any]:
        if model_name in self.models:
            self.models.remove(model_name)
        return {"success": True, "message": f"Model {model_name} deleted"}

    async def get_recommended_models(self) -> list[dict[str, str]]:
        return [{"display_name": "Qwen 3 (8B)", "model_name": "qwen3:8b", "provider": "Alibaba"}]


class FixtureHedgeFund:
    """Small deterministic substitute for an external LLM while preserving SSE."""

    async def run(self, body: Any, emit: Any) -> dict[str, Any]:
        ticker = body.tickers[0]
        await emit(
            InternalEvent(
                kind="progress",
                payload={
                    "agent": "valuation_analyst",
                    "ticker": ticker,
                    "status": "Completed valuation",
                    "analysis": "Fixture intrinsic value is above market price.",
                },
            )
        )
        return {
            "decisions": {ticker: {"action": "buy", "quantity": 10, "confidence": 87.5}},
            "analyst_signals": {
                "valuation_analyst": {
                    "signal": "bullish",
                    "confidence": 87.5,
                    "reasoning": "Deterministic browser fixture",
                }
            },
        }

    async def backtest(self, body: Any, emit: Any, cancellation: asyncio.Event) -> dict[str, Any]:
        del cancellation
        await emit(
            InternalEvent(
                kind="progress",
                payload={
                    "agent": "backtest",
                    "status": f"Completed {body.start_date}",
                    "analysis": "Fixture backtest day",
                },
            )
        )
        return {
            "performance_metrics": {"sharpe_ratio": 1.4, "max_drawdown": -0.04},
            "final_portfolio": {"cash": body.initial_capital * 1.1},
            "total_days": 1,
        }


async def _algorithm_runner(
    request: dict[str, object], *, event_sink: Any, cancellation: asyncio.Event
) -> BacktestRunOutput:
    symbols = [str(value) for value in request.get("symbols", [])]
    if symbols == ["SLOW"]:
        await cancellation.wait()
    else:
        await event_sink(
            BacktestEvent(
                sequence=0,
                kind=BacktestEventKind.PROGRESS,
                progress=0.5,
                message="Calculating fixture portfolio",
            )
        )
    symbol = symbols[0] if symbols else "AAPL"
    return BacktestRunOutput(
        result={
            "metrics": {
                "total_return": 0.18,
                "sharpe_ratio": 1.42,
                "max_drawdown": -0.08,
                "trade_count": 1,
            },
            "equity_curve": [{"date": "2026-01-31", "equity": 118000.0, "cash": 20000.0}],
            "trades": [
                {
                    "symbol": symbol,
                    "side": "long",
                    "entry_date": "2026-01-02",
                    "entry_price": 100.0,
                    "quantity": 10,
                    "pnl": 1800.0,
                }
            ],
            "signal_summary": [
                {
                    "symbol": symbol,
                    "first_date": "2026-01-01",
                    "last_date": "2026-01-31",
                    "latest_raw_signal": 0.8,
                    "latest_applied_weight": 0.4,
                    "long_count": 8,
                    "short_count": 2,
                    "flat_count": 10,
                }
            ],
            "data_coverage": {
                symbol: {
                    "first_date": "2026-01-01",
                    "last_date": "2026-01-31",
                    "row_count": 20,
                }
            },
            "warnings": [],
        },
        signals=(),
    )


async def create_fixture_app(
    database: Path,
    compatibility_store: CompatibilityStore | None = None,
    artifact_root: Path | None = None,
) -> FastAPI:
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    compatibility_context: Any = None
    try:
        if compatibility_store is None:
            cipher = FernetSecretCipher(Fernet.generate_key())
            compatibility_context = open_compat_sqlite(
                database.with_name("compatibility.sqlite"), cipher
            )
            compatibility_store = await compatibility_context.__aenter__()
        app = await _build_fixture_app(
            engine,
            compatibility_store,
            artifact_root or database.parent / "artifacts",
        )
        if compatibility_context is not None:
            inherited_lifespan = app.router.lifespan_context

            @asynccontextmanager
            async def lifespan(application: FastAPI) -> AsyncIterator[None]:
                try:
                    async with inherited_lifespan(application):
                        yield
                finally:
                    await compatibility_context.__aexit__(None, None, None)

            app.router.lifespan_context = lifespan
        return app
    except BaseException:
        if compatibility_context is not None:
            await compatibility_context.__aexit__(None, None, None)
        await engine.dispose()
        raise


async def _build_fixture_app(
    engine: AsyncEngine,
    compatibility_store: CompatibilityStore,
    artifact_root: Path,
) -> FastAPI:
    settings = Settings.model_construct(llm_fallback_models=(), mask_enabled=False)
    config = ServerConfig.model_construct(
        cors_origins=("http://127.0.0.1:4174", "http://localhost:4174"),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await create_paper_tables(engine)
    storage = Storage(engine)
    for role in ("viewer", "operator", "approver", "admin"):
        await storage.put_key(f"{role}-key", role)
    scenario = load_equity_scenario("001-deep-value")
    broker = paper_broker(engine)
    context = fixture_context(broker)
    analysis = make_analysis_graph(
        UnavailableAnalysisServices(),
        FixtureTools(),
        checkpointer=InMemorySaver(),
        settings=settings,
    )
    manager = RunManager(analysis, storage, context)
    policy = ApprovalPolicy(environment="prod")
    orders = make_order_graph(OrderDesk(broker), policy, checkpointer=InMemorySaver())
    store = InMemoryStore()
    registry = IntegrationRegistry([], env_file=None)
    chat = make_chat_graph(
        ScriptedChatModel(
            responses=[
                AIMessage(content="Offline finance fixture received your question.")
                for _ in range(50)
            ]
        ),
        registry,
        context(),
        ThesisMemory(store),
        policy=policy,
        checkpointer=InMemorySaver(),
        store=store,
        settings=settings,
    )
    app = create_app(
        manager=manager,
        config=config,
        registry=registry,
        chat_graph=chat,
        order_graph=orders,
        approval_policy=policy,
        paper=broker,
        compatibility_store=compatibility_store,
        compatibility_profile=CompatibilityProfile(local=True),
        compatibility_export_dir=artifact_root / "exports",
        compatibility_ollama=FixtureOllama(),
        compatibility_hedge_fund=FixtureHedgeFund(),
    )
    app.state.compatibility_backtest_manager = BacktestJobManager(
        compatibility_store,
        BacktestArtifactStore(artifact_root / "backtests"),
        {"strategy": _algorithm_runner},
    )
    app.state.fixture_engine = engine

    @app.get("/__fixture/state")
    async def state(thread_id: str | None = None) -> dict[str, Any]:
        portfolio = await broker.portfolio()
        return {
            "portfolio": portfolio.model_dump(mode="json"),
            "orders": [item.model_dump(mode="json") for item in await broker.orders()],
            "approvals": await storage.approvals(thread_id) if thread_id else [],
            "scenario": scenario.key,
            "request": scenario.request(),
        }

    # The production SPA fallback is intentionally last for production routes.
    # This test-only endpoint is registered after app construction, so move it
    # ahead of that catch-all without changing production routing.
    app.router.routes.insert(0, app.router.routes.pop())

    return app


async def main() -> None:
    with TemporaryDirectory(prefix="fin-graph-browser-") as directory:
        root = Path(directory)
        cipher = FernetSecretCipher(Fernet.generate_key())
        async with open_compat_sqlite(root / "compatibility.sqlite", cipher) as compatibility:
            app = await create_fixture_app(
                root / "fixture.sqlite",
                compatibility,
                root / "artifacts",
            )
            try:
                await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000)).serve()
            finally:
                await app.state.fixture_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
