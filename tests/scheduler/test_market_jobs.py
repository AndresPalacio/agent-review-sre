from __future__ import annotations

import math
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from langgraph.store.memory import InMemoryStore

from fin_graph.domain.market import Price
from fin_graph.domain.market_watch import WatchRule, assess, watch_digest
from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.domain.portfolio import Portfolio
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.broker.paper.broker import PaperBroker
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import StaticMarketData
from fin_graph.memory.theses import ThesisMemory
from fin_graph.persistence.paper_ledger import open_sqlite_paper_ledger
from fin_graph.safety.trading_limits import TradingLimits
from fin_graph.scheduler.market import MarketJobs

NOW = datetime(2024, 6, 28, 14, 0, tzinfo=UTC)


TODAY = NOW.date()


def bars(closes: list[float], end: date = TODAY) -> list[Price]:
    start = end - timedelta(days=len(closes) - 1)
    return [
        Price(
            time=datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=UTC),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1,
        )
        for i, c in enumerate(closes)
    ]


def calm(n: int, start: float = 100.0) -> list[float]:
    return [start * (1 + 0.002 * math.sin(i)) for i in range(n)]


def test_each_rule_names_the_threshold_it_crossed() -> None:
    rule = WatchRule(ticker="aapl")
    steady = assess(rule, bars(calm(200)))
    jump = assess(rule, bars([*calm(199), 110.0]))
    slump = assess(rule, bars([*calm(150), *[80.0 + 0.01 * i for i in range(50)]]))
    choppy = calm(180) + [100 * (1 + (0.04 if i % 2 else -0.04)) for i in range(20)]
    spike = assess(rule.model_copy(update={"max_daily_move_pct": 50}), bars(choppy))

    assert rule.ticker == "AAPL" and steady.status == "normal"
    assert jump.status == "triggered" and "daily move +" in jump.reasons[0]
    assert slump.status == "triggered" and any("drawdown" in r for r in slump.reasons)
    assert any("volatility" in r for r in spike.reasons)
    assert assess(rule, bars([100.0])).status == "unknown"


def test_the_digest_lists_alerts_and_missing_data() -> None:
    rule = WatchRule(ticker="MSFT")
    results = [assess(rule, bars([*calm(199), 110.0])), assess(rule, [])]

    text = watch_digest(results, NOW)

    assert "1 alertas de 2" in text and "MSFT: daily move" in text and "Sin datos: MSFT" in text


class Storage:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, dict[str, Any]]] = []
        self.claimed: set[str] = set()

    async def record_watch_result(
        self, schedule_id: str, status: str, details: dict[str, Any], checked_at: datetime
    ) -> None:
        self.results.append((schedule_id, status, details))

    async def recent_watch_results(self, since: datetime, *, limit: int = 100) -> list[Any]:
        return [{"details": details} for _, _, details in self.results]

    async def claim_webhook_run(self, source, fingerprint, thread_id, run_id, now, window):
        if fingerprint in self.claimed:
            return False, "earlier"
        self.claimed.add(fingerprint)
        return True, thread_id

    async def set_status(self, run_id: str, status: str, error: str | None = None) -> None:
        pass

    async def release_thread_fingerprints(self, thread_id: str) -> None:
        pass


class Manager:
    def __init__(self) -> None:
        self.runs: list[tuple[Any, str | None]] = []

    def schedule_existing(self, run_id, thread, raw_request, source_hint, **kwargs):
        self.runs.append((raw_request, source_hint))
        return {"run_id": run_id}


@pytest.fixture
async def broker(tmp_path: Path) -> AsyncIterator[PaperBroker]:
    async def quote(ticker: str) -> float | None:
        return {"MSFT": 440.0, "AAPL": 150.0}.get(ticker)

    async with open_sqlite_paper_ledger(tmp_path / "paper.sqlite") as store:
        yield PaperBroker(
            store=store,
            quotes=quote,
            limits=TradingLimits(max_position_pct=1.0),
            initial=Portfolio(cash=10_000.0),
            clock=lambda: NOW,
            market_open=lambda now: True,
        )


def jobs(broker: PaperBroker | None = None, memory: ThesisMemory | None = None, sent=None):
    market = StaticMarketData(prices={"MSFT": bars([*calm(199), 110.0]), "KO": bars(calm(200))})
    context = RunContext(
        clients={"analytics": AnalyticsEngine("fixtures"), "market_fixtures": market}
    )

    async def send(text: str, destinations: dict[str, str], key: str) -> None:
        if sent is not None:
            sent.append((text, key))

    return MarketJobs(
        Storage(),
        Manager(),
        lambda: context,
        broker=broker,
        memory=memory,
        send_text=send,
        now=lambda: NOW,
    )


async def test_a_trigger_starts_one_deduplicated_analysis_of_the_moved_tickers() -> None:
    watch = jobs()
    definition = {
        "kind": "market_watch",
        "interval_minutes": 60,
        "rules": [{"ticker": "MSFT"}, {"ticker": "KO"}],
    }

    await watch.market_watch("watch-1", definition)
    await watch.market_watch("watch-1", definition)

    [(request, source)] = watch.manager.runs
    assert request["tickers"] == ["MSFT"] and "Vigilancia de mercado" in request["question"]
    assert source == "price_alert"
    assert [status for _, status, _ in watch.storage.results] == ["triggered", "triggered"]


async def test_market_watch_closes_each_price_context() -> None:
    class CloseableClient:
        def __init__(self) -> None:
            self.closes = 0

        async def aclose(self) -> None:
            self.closes += 1

    market = StaticMarketData(prices={"KO": bars(calm(200))})
    closeable = CloseableClient()

    def context_factory() -> RunContext:
        return RunContext(
            clients={"analytics": AnalyticsEngine("fixtures"), "market_fixtures": market},
            owned_clients=(closeable,),
        )

    watch = MarketJobs(Storage(), Manager(), context_factory, now=lambda: NOW)
    await watch.market_watch("watch-close", {"kind": "market_watch", "rules": [{"ticker": "KO"}]})

    assert closeable.closes == 1


async def test_the_digest_reports_the_paper_book_and_queued_orders(broker: PaperBroker) -> None:
    await broker.place(
        ProposedOrder(
            client_order_id="ord_1",
            ticker="MSFT",
            action=OrderAction.BUY,
            quantity=10,
            reference_price=440.0,
            as_of=NOW.date(),
        )
    )
    sent: list[tuple[str, str]] = []

    await jobs(broker, sent=sent).portfolio_digest(
        "digest", {"destinations": {"slack": "C1"}, "timezone": "UTC"}
    )

    [(text, key)] = sent
    assert "MSFT: 10 a 440.00" in text and "no es asesoría financiera" in text
    assert len(key) == 64


async def test_thesis_review_closes_old_theses_at_todays_price(broker: PaperBroker) -> None:
    memory = ThesisMemory(InMemoryStore())
    await memory.save_thesis(
        "run-1:AAPL",
        ticker="AAPL",
        as_of=date(2024, 3, 1),
        stance="bullish",
        category="quality_compounder",
        confidence=60,
        entry_price=120.0,
        summary="x",
        evidence_ids=[],
    )
    sent: list[tuple[str, str]] = []

    await jobs(broker, memory, sent).thesis_review(
        "review", {"after_days": 30, "destinations": {"slack": "C1"}}
    )

    [(text, _)] = sent
    assert "AAPL bullish: +25.0% (acierto)" in text and "acierto histórico 100%" in text
    assert (await memory.track_record())["closed"] == 1
