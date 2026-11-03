"""A finance server for tests: the deterministic analysis graph over a recorded equity scenario.

No model is called: ``UnavailableAnalysisServices`` makes every model step take its fallback, so
an analysis of ``DEEP`` (scenario ``001-deep-value``, as of 2024-06-28) finishes in about a
second with a real report. The paper broker quotes a fixed price with the market open.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fin_graph.domain.portfolio import Portfolio
from fin_graph.evals.market.scenarios import load_equity_scenario
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS
from fin_graph.integrations.broker.paper.broker import PaperBroker
from fin_graph.integrations.context import RunContext
from fin_graph.persistence.paper_ledger import PaperLedgerStore, create_paper_tables
from fin_graph.runtime.analysis import make_analysis_graph
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.safety.trading_limits import TradingLimits
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, Storage
from fin_graph.trading.order_desk import OrderDesk
from fin_graph.workflows.analysis.services import UnavailableAnalysisServices
from fin_graph.workflows.orders.graph import make_order_graph

AS_OF = "2024-06-28"
REQUEST = {"tickers": ["DEEP"], "as_of": AS_OF, "horizon": "long"}
DEFAULT_KEYS = {
    "viewer-key": "viewer",
    "operator-key": "operator",
    "approver-key": "approver",
    "admin-key": "admin",
}


class FixtureTools:
    """The analytics tools; their data comes from the context's market fixtures."""

    def tools(self, context: RunContext) -> list[BaseTool]:
        return list(ANALYTICS_TOOLS)


async def make_storage(
    path: Path, keys: Mapping[str, str] = DEFAULT_KEYS
) -> tuple[AsyncEngine, Storage]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await create_paper_tables(engine)
    storage = Storage(engine)
    for key, role in keys.items():
        await storage.put_key(key, role)
    return engine, storage


def paper_broker(engine: AsyncEngine, price: float = 8.0, *, cash: float = 10_000.0) -> PaperBroker:
    async def quote(ticker: str) -> float | None:
        return price

    return PaperBroker(
        store=PaperLedgerStore(engine),
        quotes=quote,
        limits=TradingLimits(max_position_pct=1.0),
        initial=Portfolio(cash=cash),
        clock=lambda: datetime(2024, 6, 28, 14, 0, tzinfo=UTC),
        market_open=lambda now: True,
    )


def fixture_context(broker: PaperBroker | None = None) -> Callable[[], RunContext]:
    market = load_equity_scenario("001-deep-value").market()

    def factory() -> RunContext:
        clients: dict[str, Any] = {
            "analytics": AnalyticsEngine("fixtures"),
            "market_fixtures": market,
        }
        if broker is not None:
            clients["paper_broker"] = broker
        return RunContext(clients=clients)

    return factory


def analysis_manager(
    storage: Storage,
    context: Callable[[], RunContext],
    *,
    checkpointer: Any = None,
    **kwargs: Any,
) -> RunManager:
    graph = make_analysis_graph(
        UnavailableAnalysisServices(),
        FixtureTools(),
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
    )
    return RunManager(graph, storage, context, **kwargs)


def order_graph(broker: PaperBroker, policy: ApprovalPolicy | None = None) -> Any:
    return make_order_graph(
        OrderDesk(broker),
        policy or ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )


@asynccontextmanager
async def client_for(app: Any) -> AsyncIterator[Any]:
    import httpx

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client
