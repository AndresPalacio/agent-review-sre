from __future__ import annotations

from datetime import date

import pytest
from langgraph.store.memory import InMemoryStore

from fin_graph.memory.theses import ThesisMemory, stance_was_right, thesis_key


async def _memory() -> ThesisMemory:
    memory = ThesisMemory(InMemoryStore())
    for run, ticker, stance, day, price in (
        ("r1", "AAPL", "bullish", date(2024, 1, 2), 100.0),
        ("r2", "AAPL", "bearish", date(2024, 2, 1), 120.0),
        ("r3", "MSFT", "neutral", date(2024, 2, 1), 400.0),
    ):
        await memory.save_thesis(
            thesis_key(run, ticker),
            ticker=ticker,
            as_of=day,
            stance=stance,
            category="deep_value" if stance == "bullish" else "no_edge",
            confidence=70,
            entry_price=price,
            summary="sk-ant-secret0123456789abcdefghij in a summary",
            evidence_ids=["ev_1"],
        )
    return memory


async def test_theses_are_searched_newest_first_and_redacted() -> None:
    memory = await _memory()

    aapl = await memory.search_theses(ticker="aapl")
    value = await memory.search_theses(category="deep_value")

    assert [item["as_of"] for item in aapl] == ["2024-02-01", "2024-01-02"]
    assert [item["ticker"] for item in value] == ["AAPL"]
    assert "sk-ant-secret" not in aapl[0]["summary"]


async def test_outcomes_build_a_track_record() -> None:
    memory = await _memory()

    win = await memory.record_outcome("r1:AAPL", exit_price=130.0, as_of=date(2024, 6, 1))
    loss = await memory.record_outcome("r2:AAPL", exit_price=150.0, as_of=date(2024, 6, 1))
    await memory.record_outcome("r3:MSFT", exit_price=410.0, as_of=date(2024, 6, 1))
    record = await memory.track_record()

    assert win == {"as_of": "2024-06-01", "exit_price": 130.0, "return": 0.3, "right": True}
    assert loss["right"] is False
    assert record["closed"] == 3 and record["hit_rate"] == pytest.approx(0.667)
    assert record["by_stance"]["bearish"] == {"closed": 1, "hit_rate": 0.0, "avg_return": 0.25}


async def test_outcomes_are_validated() -> None:
    memory = await _memory()

    with pytest.raises(KeyError):
        await memory.record_outcome("missing:AAPL", exit_price=1.0, as_of=date(2024, 6, 1))
    with pytest.raises(ValueError, match="predate"):
        await memory.record_outcome("r1:AAPL", exit_price=1.0, as_of=date(2023, 1, 1))


async def test_investor_profile_defaults_and_updates() -> None:
    memory = ThesisMemory(InMemoryStore())

    assert await memory.profile() == {"risk_profile": "moderate", "watchlist": []}
    await memory.save_profile(risk_profile="conservative", watchlist=["aapl", "$msft", "AAPL"])
    assert await memory.profile() == {"risk_profile": "conservative", "watchlist": ["AAPL", "MSFT"]}


def test_stance_scoring() -> None:
    assert stance_was_right("bullish", 0.1) and stance_was_right("bearish", -0.1)
    assert stance_was_right("neutral", 0.02) and not stance_was_right("neutral", 0.2)
