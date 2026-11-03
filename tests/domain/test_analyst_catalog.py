from __future__ import annotations

import pytest

from fin_graph.domain.analysts import AnalystId, analyst_catalog, resolve_analysts
from fin_graph.domain.tools import Domain

EXPECTED = (
    ("aswath_damodaran", "Aswath Damodaran"),
    ("ben_graham", "Ben Graham"),
    ("bill_ackman", "Bill Ackman"),
    ("cathie_wood", "Cathie Wood"),
    ("charlie_munger", "Charlie Munger"),
    ("michael_burry", "Michael Burry"),
    ("mohnish_pabrai", "Mohnish Pabrai"),
    ("nassim_taleb", "Nassim Taleb"),
    ("peter_lynch", "Peter Lynch"),
    ("phil_fisher", "Phil Fisher"),
    ("rakesh_jhunjhunwala", "Rakesh Jhunjhunwala"),
    ("stanley_druckenmiller", "Stanley Druckenmiller"),
    ("warren_buffett", "Warren Buffett"),
    ("technical_analyst", "Technical Analyst"),
    ("fundamentals_analyst", "Fundamentals Analyst"),
    ("growth_analyst", "Growth Analyst"),
    ("news_sentiment_analyst", "News Sentiment Analyst"),
    ("sentiment_analyst", "Sentiment Analyst"),
    ("valuation_analyst", "Valuation Analyst"),
)


def test_catalog_exactly_matches_reference_keys_display_names_and_order() -> None:
    catalog = analyst_catalog()

    assert (
        tuple((definition.id.value, definition.display_name) for definition in catalog) == EXPECTED
    )
    assert tuple(definition.order for definition in catalog) == tuple(range(19))
    assert len({definition.id for definition in catalog}) == 19
    assert len({definition.order for definition in catalog}) == 19
    assert all(definition.prompt_key and definition.scorer_key for definition in catalog)
    assert all(definition.domains for definition in catalog)


def test_no_selection_resolves_every_analyst_in_reference_order() -> None:
    assert resolve_analysts(None) == analyst_catalog()
    assert resolve_analysts(()) == analyst_catalog()


def test_explicit_selection_preserves_caller_order() -> None:
    selected = resolve_analysts([AnalystId.VALUATION_ANALYST, "ben_graham", "aswath_damodaran"])

    assert [definition.id.value for definition in selected] == [
        "valuation_analyst",
        "ben_graham",
        "aswath_damodaran",
    ]


def test_duplicate_and_unknown_analyst_ids_are_public_validation_errors() -> None:
    with pytest.raises(ValueError, match="duplicate analyst: ben_graham"):
        resolve_analysts(["ben_graham", "ben_graham"])
    with pytest.raises(ValueError, match="unknown analyst: secret_alpha"):
        resolve_analysts(["secret_alpha"])


@pytest.mark.parametrize(
    ("analyst_id", "domain"),
    [
        (AnalystId.FUNDAMENTALS_ANALYST, Domain.FUNDAMENTALS),
        (AnalystId.VALUATION_ANALYST, Domain.VALUATION),
        (AnalystId.TECHNICAL_ANALYST, Domain.TECHNICALS),
        (AnalystId.SENTIMENT_ANALYST, Domain.SENTIMENT),
        (AnalystId.WARREN_BUFFETT, Domain.BUFFETT),
        (AnalystId.BEN_GRAHAM, Domain.GRAHAM),
    ],
)
def test_existing_domain_analysts_are_addressable_in_catalog(
    analyst_id: AnalystId,
    domain: Domain,
) -> None:
    [definition] = resolve_analysts([analyst_id])
    assert domain in definition.domains
