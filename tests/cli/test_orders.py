from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from fin_graph.integrations.broker.paper import runtime
from fin_graph.integrations.context import RunContext

QUOTES: dict[str, float] = {"MSFT": 400.0}


@pytest.fixture(autouse=True)
def _open_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixed quote and an open market, whatever the real clock says."""

    async def quote(ticker: str) -> float | None:
        return QUOTES.get(ticker)

    original = runtime.paper_broker

    def broker(*args: Any, **kwargs: Any) -> Any:
        built = original(*args, **kwargs)
        built.market_open = lambda now: True
        return built

    monkeypatch.setattr(runtime, "context_quotes", lambda context: quote)
    monkeypatch.setattr(runtime, "paper_broker", broker)


def cli(*args: str) -> Any:
    from fin_graph.cli.main import app

    return CliRunner().invoke(app, list(args))


def test_an_order_is_proposed_approved_filled_and_shown_in_the_portfolio() -> None:
    proposed = cli("orders", "new", "--ticker", "msft", "--action", "buy", "--quantity", "2")
    assert proposed.exit_code == 0, proposed.output
    thread = proposed.output.splitlines()[0]
    assert thread.startswith("order-ord_") and "Esperando aprobación" in proposed.output

    listed = cli("orders", "pending")
    assert thread in listed.output and "Comprar 2 MSFT" in listed.output

    decided = cli("orders", "decide", thread, "approve")
    assert decided.exit_code == 0, decided.output
    assert decided.output.startswith("filled")

    orders = json.loads(cli("orders", "list", "--json").output)
    assert [item["status"] for item in orders] == ["filled"]
    book = cli("portfolio")
    assert "MSFT" in book.output and "Cartera paper" in book.output
    assert "No hay órdenes pendientes" in cli("orders", "pending").output


def test_a_rejected_or_edited_order_is_decided_by_the_local_approver() -> None:
    first = cli("orders", "new", "--ticker", "MSFT", "--action", "buy", "--quantity", "1")
    second = cli("orders", "new", "--ticker", "MSFT", "--action", "buy", "--quantity", "5")
    rejected = cli("orders", "decide", first.output.splitlines()[0], "reject")
    edited = cli("orders", "decide", second.output.splitlines()[0], "edit", "--quantity", "3")

    assert rejected.output.startswith("rejected")
    assert edited.output.startswith("filled")
    [order] = json.loads(cli("orders", "list", "--json").output)
    assert order["order"]["quantity"] == 3
    assert cli("orders", "decide", "order-x", "maybe").exit_code != 0


def test_analyze_propose_queues_each_proposed_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.cli.test_analyze import FakeGraph, _state
    from tests.domain.test_analysis_report import _report

    from fin_graph.runtime import analysis as analysis_runtime

    order = _report().sections[0].order
    assert order is not None
    monkeypatch.setitem(QUOTES, order.ticker, order.reference_price)
    graph = FakeGraph(_state())
    monkeypatch.setattr(analysis_runtime, "make_analysis_runtime", lambda: (graph, RunContext()))

    result = cli("analyze", "--tickers", "DEEP", "--propose")

    assert result.exit_code == 0, result.output
    assert "order-ord_" in result.output and "Esperando aprobación" in result.output
    assert "order-ord_" in cli("orders", "pending").output
    clash = cli("analyze", "--tickers", "DEEP", "--propose", "--cash", "10")
    assert clash.exit_code != 0


def test_a_quote_far_from_the_proposal_blocks_the_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.cli.test_analyze import FakeGraph, _state

    from fin_graph.runtime import analysis as analysis_runtime

    monkeypatch.setitem(QUOTES, "DEEP", 0.5)
    graph = FakeGraph(_state())
    monkeypatch.setattr(analysis_runtime, "make_analysis_runtime", lambda: (graph, RunContext()))

    result = cli("analyze", "--tickers", "DEEP", "--propose")

    assert "blocked: price_drift" in result.output
    assert "No hay órdenes pendientes" in cli("orders", "pending").output
