"""Runtime profiles for a zero-setup local app or distributed Postgres server."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from platformdirs import user_data_dir
from pydantic import SecretStr
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.adapters.reference_app.settings import ReferenceAppProfile
from fin_graph.config.settings import PersistenceBackend, Settings
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.broker.paper.runtime import context_quotes, paper_broker
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import MarketDataClient
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.persistence.paper_ledger import PaperLedgerStore, create_paper_tables
from fin_graph.persistence.reference_app import FernetSecretCipher, open_reference_sqlite
from fin_graph.runtime.analysis import _analysis_services, make_analysis_graph
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.server.app import create_app as create_native_app
from fin_graph.server.config import ServerConfig
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, Storage

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeProfile:
    """Everything needed to choose one coherent server persistence/security mode."""

    backend: str
    host: str
    data_dir: Path | None = None
    database_url: str | None = None
    bootstrap_admin_key: str | None = None
    reference_app: ReferenceAppProfile = field(default_factory=ReferenceAppProfile)

    @classmethod
    def local(
        cls,
        *,
        data_dir: Path | None = None,
        host: str = "127.0.0.1",
        allow_insecure_remote: bool | None = None,
    ) -> RuntimeProfile:
        environment = ReferenceAppProfile.from_env(os.environ)
        allow_remote = (
            environment.allow_insecure_remote
            if allow_insecure_remote is None
            else allow_insecure_remote
        )
        reference_app = ReferenceAppProfile(
            local=True,
            enabled=environment.enabled,
            allow_insecure_remote=allow_remote,
            trusted_proxies=environment.trusted_proxies,
        )
        reference_app.validate_bind(host)
        root = Path(
            data_dir or os.getenv("FIN_GRAPH_DATA_DIR") or user_data_dir("fin-graph", "fin-graph")
        ).expanduser()
        return cls(
            backend="sqlite",
            host=host,
            data_dir=root.resolve(),
            reference_app=reference_app,
        )

    @classmethod
    def distributed(
        cls,
        *,
        database_url: str,
        bootstrap_admin_key: str,
        host: str = "0.0.0.0",
    ) -> RuntimeProfile:
        if not database_url.strip():
            raise ValueError("database_url must not be blank")
        if len(bootstrap_admin_key) < 32:
            raise ValueError("bootstrap_admin_key must contain at least 32 characters")
        return cls(
            backend="postgres",
            host=host,
            database_url=database_url,
            bootstrap_admin_key=bootstrap_admin_key,
            reference_app=ReferenceAppProfile(
                local=False,
                enabled=True,
                allow_insecure_remote=False,
            ),
        )

    @classmethod
    def from_env(cls) -> RuntimeProfile:
        options = ServerConfig()
        if options.database_url is not None:
            bootstrap = options.bootstrap_admin_key
            if bootstrap is None:
                raise ValueError("BOOTSTRAP_ADMIN_KEY is required with DATABASE_URL")
            return cls.distributed(
                database_url=options.database_url.get_secret_value(),
                bootstrap_admin_key=bootstrap.get_secret_value(),
                host=os.getenv("FIN_GRAPH_HOST", "0.0.0.0"),
            )
        return cls.local(host=os.getenv("FIN_GRAPH_HOST", "127.0.0.1"))


def _read_or_create(path: Path, create: Callable[[], str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        value = create()
        try:
            with path.open("x", encoding="ascii") as handle:
                handle.write(value)
        except FileExistsError:
            return path.read_text(encoding="ascii").strip()
        with suppress(OSError):
            path.chmod(0o600)
        return value


def _local_model_graphs(
    settings: Settings,
    registry: IntegrationRegistry,
    context: RunContext,
) -> tuple[object | None, object | None, object | None]:
    from fin_graph.llm.models import ModelRole, get_model, missing_credentials

    if missing_credentials(settings.llm_provider):
        return None, None, None
    try:
        from langgraph.store.memory import InMemoryStore

        from fin_graph.adapters.reference_app.runtime import make_reference_analysis_supervisor
        from fin_graph.agents.chat.supervisor import make_chat_graph
        from fin_graph.memory.theses import ThesisMemory
        from fin_graph.workflows.analysis.snapshots import MarketSnapshotServices

        model = get_model(ModelRole.REASONING, settings)
        reference_app_graph = make_reference_analysis_supervisor(
            model,
            registry,
            context,
            MarketSnapshotServices(),
            checkpointer=False,
        )
        memory_store = InMemoryStore()
        chat = make_chat_graph(
            model,
            registry,
            context,
            ThesisMemory(memory_store),
            policy=ApprovalPolicy.from_settings(settings),
            analysis=reference_app_graph,
            checkpointer=InMemorySaver(),
            store=memory_store,
            settings=settings,
        )
        return reference_app_graph, chat, model
    except (ImportError, TypeError, ValueError):
        _LOGGER.warning("optional local chat/DeepAgents runtime is unavailable")
        return None, None, None


def _create_local_app(profile: RuntimeProfile) -> FastAPI:
    assert profile.data_dir is not None
    root = profile.data_dir
    runtime_database = root / "runtime.sqlite"
    reference_app_database = root / "compatibility.sqlite"
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(runtime_database)),
        connect_args={"timeout": 30},
    )
    storage = Storage(engine)
    settings = Settings(
        _env_file=".env",
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(root / "checkpoints.sqlite"),
        paper_ledger_path=str(runtime_database),
    )
    registry = default_registry()
    base_context = registry.run_context()
    broker = paper_broker(settings, PaperLedgerStore(engine), context_quotes(base_context))

    def context_factory() -> RunContext:
        current = registry.run_context()
        return RunContext(
            clients={**current.clients, "paper_broker": broker},
            unavailable=current.unavailable,
            actor=current.actor,
            role=current.role,
        )

    graph = make_analysis_graph(
        _analysis_services(settings),
        registry,
        checkpointer=InMemorySaver(),
        settings=settings,
    )
    manager = RunManager(graph, storage, context_factory)
    reference_app_graph, chat_graph, model = _local_model_graphs(
        settings, registry, context_factory()
    )
    application = create_native_app(
        manager=manager,
        registry=registry,
        chat_graph=chat_graph,
        approval_policy=ApprovalPolicy.from_settings(settings),
        paper=broker,
        reference_app_profile=profile.reference_app,
        reference_app_export_dir=root / "outputs",
        config=ServerConfig(_env_file=None),
    )
    application.state.runtime_profile = profile
    application.state.runtime_capabilities = {
        "backend": "sqlite",
        "distributed_locks": False,
        "multi_worker": False,
    }
    application.state.reference_app_analysis_graph = reference_app_graph
    application.state.reference_app_quant_llm = model
    application.state.reference_app_quant_artifact_dir = root / "models"
    application.state.reference_app_backtest_artifact_dir = root / "backtests"
    if reference_app_graph is not None:
        from fin_graph.adapters.reference_app.http.hedge_fund import make_v1_backtest_runner

        application.state.reference_app_backtest_runner = make_v1_backtest_runner(
            reference_app_graph, context_factory
        )
    analytics = base_context.clients.get("analytics")
    if isinstance(analytics, AnalyticsEngine):
        candidate = base_context.clients.get(analytics.data_integration)
        if isinstance(candidate, MarketDataClient):
            application.state.reference_app_quant_data = candidate

    inherited_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        root.mkdir(parents=True, exist_ok=True)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await create_paper_tables(engine)
        local_api_key = _read_or_create(root / "local-api.key", lambda: secrets.token_urlsafe(32))
        await storage.put_key(local_api_key, "admin")
        cipher_key = _read_or_create(
            root / "compatibility.key", lambda: Fernet.generate_key().decode("ascii")
        )
        try:
            async with open_reference_sqlite(
                reference_app_database, FernetSecretCipher(cipher_key)
            ) as reference_app_store:
                app.state.reference_app_store = reference_app_store
                app.state.local_api_key_path = root / "local-api.key"
                async with inherited_lifespan(app):
                    yield
        finally:
            backtests = getattr(app.state, "reference_app_backtest_manager", None)
            if backtests is not None:
                await backtests.close()
            await engine.dispose()

    application.router.lifespan_context = lifespan
    return application


def create_app(runtime: RuntimeProfile) -> FastAPI:
    """Create the correct application without mixing local/distributed guarantees."""

    if runtime.backend == "sqlite":
        return _create_local_app(runtime)
    if runtime.backend != "postgres" or runtime.database_url is None:
        raise ValueError(f"unsupported runtime backend: {runtime.backend}")
    application = create_native_app(
        config=ServerConfig(
            _env_file=None,
            database_url=SecretStr(runtime.database_url),
            bootstrap_admin_key=SecretStr(runtime.bootstrap_admin_key or ""),
        ),
        reference_app_profile=runtime.reference_app,
    )
    application.state.runtime_profile = runtime
    application.state.runtime_capabilities = {
        "backend": "postgres",
        "distributed_locks": True,
        "multi_worker": True,
    }
    return application


create_runtime_app = create_app

__all__ = ["RuntimeProfile", "create_app", "create_runtime_app"]
