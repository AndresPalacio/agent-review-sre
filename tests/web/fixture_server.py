"""Offline browser fixture: production routes and real checkpointed graphs.

Run from the repository root: ``uv run python tests/web/fixture_server.py``.
Only this app exposes /__fixture; production never imports this module.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import uvicorn
from fastapi import FastAPI
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.server.finance_app import FixtureTools, fixture_context, paper_broker

from fin_graph.agents.testing.scripted_model import ScriptedChatModel
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
from fin_graph.server.config import ServerConfig
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, Storage


async def create_fixture_app(database: Path) -> FastAPI:
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    try:
        return await _build_fixture_app(engine, database.parent / "no-ui")
    except BaseException:
        await engine.dispose()
        raise


async def _build_fixture_app(engine: AsyncEngine, ui_dir: Path) -> FastAPI:
    # Trusted schema defaults only: BaseSettings __init__ parses ambient sources
    # even when every field was passed, so skip those sources entirely here.
    settings = Settings.model_construct(llm_fallback_models=(), mask_enabled=False)
    config = ServerConfig.model_construct()
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
                AIMessage(content="Consulta offline recibida; revisa el análisis financiero.")
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
        ui_dir=ui_dir,
        registry=registry,
        chat_graph=chat,
        order_graph=orders,
        approval_policy=policy,
        paper=broker,
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

    return app


async def main() -> None:
    with TemporaryDirectory(prefix="fin-graph-browser-") as directory:
        app = await create_fixture_app(Path(directory) / "fixture.sqlite")
        try:
            await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000)).serve()
        finally:
            await app.state.fixture_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
