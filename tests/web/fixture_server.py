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
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from sre_graph.agents.testing.scripted_model import ScriptedChatModel
from sre_graph.config.settings import Settings
from sre_graph.domain.alerts.noise import AlertExtraction
from sre_graph.evals.fixtures import FixtureBackend
from sre_graph.evals.runner import ScriptedServices
from sre_graph.evals.scenarios import load_scenario
from sre_graph.graphs.chat.graph import make_chat_graph
from sre_graph.graphs.investigation.graph import make_investigation_graph
from sre_graph.graphs.remediation.graph import ActionCatalog, ActionSpec, make_remediation_graph
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.memory.service import IncidentMemory
from sre_graph.safety.approval import ApprovalPolicy
from sre_graph.server.app import create_app
from sre_graph.server.config import ServerConfig
from sre_graph.server.manager import RunManager
from sre_graph.server.storage import Base, Storage


class ScaleArgs(BaseModel):
    namespace: str = Field(default="production", min_length=1)
    replicas: int = Field(default=2, ge=1, le=10)


class BrowserServices(ScriptedServices):
    """Structured offline reasoning; recorded tools still execute through an agent."""

    async def extract(self, text: str) -> AlertExtraction:
        return AlertExtraction(is_noise=False, alert_name=text[:120])


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
    storage = Storage(engine)
    for role in ("viewer", "operator", "approver", "admin"):
        await storage.put_key(f"{role}-key", role)
    backend = FixtureBackend(load_scenario("eks", "001-oomkilled-crashloop"))
    investigation = make_investigation_graph(
        BrowserServices(backend.scenario),
        backend.registry,
        clock=lambda: backend.scenario.reference_time,
        checkpointer=InMemorySaver(),
    )
    manager = RunManager(investigation, storage, lambda: backend.context)
    writes: list[dict[str, Any]] = []
    preparations: list[dict[str, Any]] = []

    async def prepare(args: ScaleArgs, context: RunContext) -> dict[str, Any]:
        value = {**args.model_dump(), "description": "Simulación offline: escalar deployment"}
        preparations.append(value)
        return value

    async def execute(args: ScaleArgs, key: str, context: RunContext) -> dict[str, Any]:
        value = {**args.model_dump(), "idempotency_key": key, "actor": context.actor}
        writes.append(value)
        return value

    async def verify(args: ScaleArgs, context: RunContext) -> bool:
        return any(item["replicas"] == args.replicas for item in writes)

    catalog = ActionCatalog(
        [ActionSpec("fixture_scale", ScaleArgs, prepare, execute, verify, rollback="Escalar a 1")],
        ledger=storage,
    )
    policy = ApprovalPolicy(environment="prod")
    remediation = make_remediation_graph(catalog, policy, checkpointer=InMemorySaver())
    store = InMemoryStore()
    chat = make_chat_graph(
        ScriptedChatModel(
            responses=[
                AIMessage(
                    content="Consulta offline recibida; revisa la evidencia de la investigación."
                )
                for _ in range(50)
            ]
        ),
        backend.registry,
        backend.context,
        IncidentMemory(store),
        catalog,
        policy=policy,
        checkpointer=InMemorySaver(),
        store=store,
        settings=settings,
    )
    app = create_app(
        manager=manager,
        config=config,
        ui_dir=ui_dir,
        registry=IntegrationRegistry([], env_file=None),
        action_catalog=catalog,
        chat_graph=chat,
        remediation_graph=remediation,
        approval_policy=policy,
    )
    app.state.fixture_engine = engine

    @app.get("/__fixture/state")
    async def state(thread_id: str | None = None) -> dict[str, Any]:
        return {
            "writes": writes,
            "preparations": preparations,
            "approvals": await storage.approvals(thread_id) if thread_id else [],
            "scenario": backend.scenario.key,
            "alert": backend.scenario.alert,
        }

    return app


async def main() -> None:
    with TemporaryDirectory(prefix="sre-graph-browser-") as directory:
        app = await create_fixture_app(Path(directory) / "fixture.sqlite")
        try:
            await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000)).serve()
        finally:
            await app.state.fixture_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
