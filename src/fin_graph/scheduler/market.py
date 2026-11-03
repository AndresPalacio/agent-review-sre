"""Scheduled finance jobs: market watch, the daily paper-portfolio digest and thesis review.

None of them trades. A market-watch trigger starts an analysis, whose proposed orders still wait
for a human; the digest and the review only read the paper book and the thesis memory.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from fin_graph.domain.fills import OrderStatus
from fin_graph.domain.market_watch import WatchResult
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.broker.paper.broker import PaperBroker
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import MarketDataClient
from fin_graph.memory.theses import ThesisMemory
from fin_graph.workflows.market_watch.graph import make_market_watch_graph

SendText = Callable[[str, dict[str, str], str], Awaitable[None]]


class WatchStorage(Protocol):
    async def record_watch_result(
        self, schedule_id: str, status: str, details: dict[str, Any], checked_at: datetime
    ) -> None: ...

    async def recent_watch_results(
        self, since: datetime, *, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    async def claim_webhook_run(
        self,
        source: str,
        fingerprint: str,
        thread_id: str,
        run_id: str,
        now: datetime,
        window: timedelta,
    ) -> tuple[bool, str]: ...

    async def set_status(self, run_id: str, status: str, error: str | None = None) -> None: ...

    async def release_thread_fingerprints(self, thread_id: str) -> None: ...


class AnalysisManager(Protocol):
    def schedule_existing(
        self,
        run_id: str,
        thread: str,
        raw_request: str | dict[str, Any],
        source_hint: str | None,
        *,
        destinations: dict[str, str] | None = None,
        user_requested: bool = True,
    ) -> dict[str, Any]: ...


def market_client(context_factory: Callable[[], RunContext]) -> Callable[[], MarketDataClient]:
    """The data client the analytics engine reads, resolved for every run."""

    def resolve() -> MarketDataClient:
        context = context_factory()
        engine = context.client("analytics", AnalyticsEngine)
        client = context.client(engine.data_integration, object)
        if not isinstance(client, MarketDataClient):
            raise TypeError(f"{engine.data_integration} is not a market data client")
        return client

    return resolve


def _day_key(kind: str, schedule_id: str, day: date) -> str:
    return hashlib.sha256(f"{kind}:{schedule_id}:{day.isoformat()}".encode()).hexdigest()


class MarketJobs:
    """The handlers ``SchedulerService`` calls for the finance schedule kinds."""

    def __init__(
        self,
        storage: WatchStorage,
        manager: AnalysisManager,
        context_factory: Callable[[], RunContext],
        *,
        broker: PaperBroker | None = None,
        memory: ThesisMemory | None = None,
        send_text: SendText | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.storage = storage
        self.manager = manager
        self.broker = broker
        self.memory = memory
        self.send_text = send_text
        self.now = now
        self.graph = make_market_watch_graph(market_client(context_factory), clock=now)

    async def market_watch(self, schedule_id: str, definition: dict[str, Any]) -> None:
        """Check the rules; a trigger starts one deduplicated analysis of the moved tickers."""
        result = await self.graph.ainvoke({"rules": definition["rules"]})
        results = [WatchResult.model_validate(item) for item in result["results"]]
        status = (
            "triggered"
            if any(item.status == "triggered" for item in results)
            else "unknown"
            if any(item.status == "unknown" for item in results)
            else "normal"
        )
        moment = self.now()
        await self.storage.record_watch_result(
            schedule_id, status, {"results": result["results"]}, moment
        )
        if not result["escalate"]:
            return
        moved = sorted(item.ticker for item in results if item.status == "triggered")
        fingerprint = hashlib.sha256(f"{schedule_id}:{','.join(moved)}".encode()).hexdigest()
        run_id, thread_id = str(uuid4()), str(uuid4())
        claimed, _ = await self.storage.claim_webhook_run(
            "market_watch",
            fingerprint,
            thread_id,
            run_id,
            moment,
            timedelta(minutes=definition["interval_minutes"]),
        )
        if not claimed:
            return
        try:
            self.manager.schedule_existing(
                run_id,
                thread_id,
                {"tickers": moved, "question": result["question"]},
                "price_alert",
                destinations=definition.get("destinations", {}),
                user_requested=False,
            )
        except Exception:
            await self.storage.set_status(run_id, "failed", "schedule_unavailable")
            await self.storage.release_thread_fingerprints(thread_id)
            raise

    async def portfolio_digest(self, schedule_id: str, definition: dict[str, Any]) -> None:
        """Once a day: cash, positions with open P&L, net value and the orders still waiting."""
        if self.broker is None:
            raise RuntimeError("the paper broker is unavailable")
        book = await self.broker.portfolio()
        quotes = {ticker: await self.broker.quotes(ticker) for ticker in book.positions}
        prices = {ticker: price for ticker, price in quotes.items() if price}
        lines = [f"Cartera paper · valor neto {book.net_liquidation_value(prices):,.2f}"]
        lines.append(f"Efectivo {book.cash:,.2f} {book.currency}; garantía {book.margin_used:,.2f}")
        for ticker, held in sorted(book.positions.items()):
            price = quotes.get(ticker)
            if held.long:
                pnl = (price - held.long_cost_basis) * held.long if price else None
                lines.append(_line(ticker, held.long, held.long_cost_basis, price, pnl))
            if held.short:
                pnl = (held.short_cost_basis - price) * held.short if price else None
                lines.append(
                    _line(f"{ticker} corto", held.short, held.short_cost_basis, price, pnl)
                )
        queued = await self.broker.orders(status=OrderStatus.QUEUED)
        if queued:
            lines.append(f"Órdenes en cola para la apertura: {len(queued)}")
        lines.append("Simulación paper con fines educativos; no es asesoría financiera.")
        await self._send(definition, "\n".join(lines), "portfolio_digest", schedule_id)

    async def thesis_review(self, schedule_id: str, definition: dict[str, Any]) -> None:
        """Close the theses older than ``after_days`` at today's price and report the record."""
        if self.memory is None or self.broker is None:
            raise RuntimeError("thesis memory or quotes are unavailable")
        cutoff = self.now().date() - timedelta(days=int(definition.get("after_days", 30)))
        closed = []
        for thesis in await self.memory.search_theses(limit=50):
            if thesis.get("outcome") or str(thesis.get("as_of")) > cutoff.isoformat():
                continue
            if not isinstance(thesis.get("entry_price"), int | float):
                continue
            price = await self.broker.quotes(str(thesis["ticker"]))
            if not price:
                continue
            outcome = await self.memory.record_outcome(
                str(thesis["id"]), exit_price=price, as_of=self.now().date()
            )
            closed.append(
                f"- {thesis['ticker']} {thesis['stance']}: {outcome['return']:+.1%} "
                f"({'acierto' if outcome['right'] else 'fallo'})"
            )
        record = await self.memory.track_record()
        hit = record["hit_rate"]
        lines = [
            f"Revisión de tesis: {len(closed)} cerradas hoy; "
            f"acierto histórico {hit:.0%} sobre {record['closed']}."
            if hit is not None
            else f"Revisión de tesis: {len(closed)} cerradas hoy; aún sin historial.",
            *closed,
        ]
        await self._send(definition, "\n".join(lines), "thesis_review", schedule_id)

    async def watch_digest(self, schedule_id: str, definition: dict[str, Any]) -> None:
        """The last day of market-watch results, summarized."""
        rows = await self.storage.recent_watch_results(self.now() - timedelta(days=1), limit=100)
        recent = [item for row in rows for item in row.get("details", {}).get("results", [])]
        result = await self.graph.ainvoke({"rules": [], "recent_results": recent})
        await self._send(definition, result["digest"], "watch_digest", schedule_id)

    async def _send(
        self, definition: dict[str, Any], text: str, kind: str, schedule_id: str
    ) -> None:
        destinations: dict[str, str] = definition.get("destinations", {})
        if not destinations:
            return
        if self.send_text is None:
            raise RuntimeError("digest delivery is unavailable")
        local_day = self.now().astimezone(ZoneInfo(definition.get("timezone", "UTC"))).date()
        await self.send_text(text, destinations, _day_key(kind, schedule_id, local_day))


def _line(label: str, shares: int, cost: float, price: float | None, pnl: float | None) -> str:
    shown = f"{price:,.2f}" if price else "sin precio"
    gain = f"{pnl:+,.2f}" if pnl is not None else "—"
    return f"- {label}: {shares} a {cost:,.2f} → {shown} (P&L {gain})"
