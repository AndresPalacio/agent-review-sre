from __future__ import annotations

from datetime import UTC, datetime

from fin_graph.domain.evidence import EvidenceItem, evidence_id, merge_evidence
from fin_graph.domain.tools import Domain, EvidenceType


def test_the_same_call_gets_the_same_id_whatever_the_argument_order() -> None:
    first = evidence_id("get_prices", {"ticker": "AAPL", "start": "2026-01-01"})
    second = evidence_id("get_prices", {"start": "2026-01-01", "ticker": "AAPL"})

    assert first == second
    assert first.startswith("ev_")
    assert len(first) == 13


def test_different_calls_get_different_ids() -> None:
    prices = evidence_id("get_prices", {"ticker": "AAPL"})

    assert prices != evidence_id("get_prices", {"ticker": "MSFT"})
    assert prices != evidence_id("get_financials", {"ticker": "AAPL"})


def test_merging_appends_new_ids_and_refreshes_repeated_ones_in_place() -> None:
    current = [{"id": "ev_1", "summary": "old"}, {"id": "ev_2", "summary": "two"}]
    new = [{"id": "ev_3", "summary": "three"}, {"id": "ev_1", "summary": "fresh"}]

    merged = merge_evidence(current, new)

    assert [item["id"] for item in merged] == ["ev_1", "ev_2", "ev_3"]
    assert merged[0]["summary"] == "fresh"


def test_merging_tolerates_an_empty_side() -> None:
    assert merge_evidence(None, [{"id": "ev_1"}]) == [{"id": "ev_1"}]
    assert merge_evidence([{"id": "ev_1"}], None) == [{"id": "ev_1"}]


def test_an_evidence_item_round_trips_through_json() -> None:
    item = EvidenceItem(
        id=evidence_id("get_prices", {"ticker": "AAPL"}),
        integration="market_data",
        tool="get_prices",
        evidence_type=EvidenceType.PRICES,
        domains=[Domain.FUNDAMENTALS],
        summary="AAPL closed at 189.20",
        args={"ticker": "AAPL"},
        data=[{"date": "2026-10-07", "close": 189.2}],
        collected_at=datetime(2026, 10, 7, 10, 6, tzinfo=UTC),
    )

    assert EvidenceItem.model_validate(item.model_dump(mode="json")) == item
