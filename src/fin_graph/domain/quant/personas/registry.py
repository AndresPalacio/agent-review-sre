"""One typed registry for every scorer key in the canonical analyst catalog."""

from __future__ import annotations

from collections.abc import Callable

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.buffett import score_buffett
from fin_graph.domain.quant.common import QuantScore
from fin_graph.domain.quant.fundamentals import score_fundamentals
from fin_graph.domain.quant.graham import score_graham
from fin_graph.domain.quant.personas.ackman import score_ackman
from fin_graph.domain.quant.personas.burry import score_burry
from fin_graph.domain.quant.personas.damodaran import score_damodaran
from fin_graph.domain.quant.personas.druckenmiller import score_druckenmiller
from fin_graph.domain.quant.personas.fisher import score_fisher
from fin_graph.domain.quant.personas.growth import score_growth
from fin_graph.domain.quant.personas.jhunjhunwala import score_jhunjhunwala
from fin_graph.domain.quant.personas.lynch import score_lynch
from fin_graph.domain.quant.personas.munger import score_munger
from fin_graph.domain.quant.personas.news import score_news
from fin_graph.domain.quant.personas.pabrai import score_pabrai
from fin_graph.domain.quant.personas.taleb import score_taleb
from fin_graph.domain.quant.personas.wood import score_wood
from fin_graph.domain.quant.sentiment import score_sentiment
from fin_graph.domain.quant.technicals import score_technicals
from fin_graph.domain.quant.valuation import score_valuation

type PersonaScorer = Callable[[CompanySnapshot], QuantScore]


def _fundamentals(snapshot: CompanySnapshot) -> QuantScore:
    return score_fundamentals(snapshot.ticker, snapshot.metrics)


def _valuation(snapshot: CompanySnapshot) -> QuantScore:
    return score_valuation(
        snapshot.ticker,
        snapshot.metrics,
        snapshot.line_items,
        snapshot.market_cap,
    )


def _technicals(snapshot: CompanySnapshot) -> QuantScore:
    return score_technicals(snapshot.ticker, snapshot.prices)


def _sentiment(snapshot: CompanySnapshot) -> QuantScore:
    return score_sentiment(snapshot.ticker, snapshot.insider_trades, snapshot.news)


def _buffett(snapshot: CompanySnapshot) -> QuantScore:
    return score_buffett(
        snapshot.ticker,
        snapshot.metrics,
        snapshot.line_items,
        snapshot.market_cap,
    )


def _graham(snapshot: CompanySnapshot) -> QuantScore:
    return score_graham(
        snapshot.ticker,
        snapshot.metrics,
        snapshot.line_items,
        snapshot.market_cap,
    )


_SCORERS: dict[str, PersonaScorer] = {
    "damodaran": score_damodaran,
    "graham": _graham,
    "ackman": score_ackman,
    "wood": score_wood,
    "munger": score_munger,
    "burry": score_burry,
    "pabrai": score_pabrai,
    "taleb": score_taleb,
    "lynch": score_lynch,
    "fisher": score_fisher,
    "jhunjhunwala": score_jhunjhunwala,
    "druckenmiller": score_druckenmiller,
    "buffett": _buffett,
    "technicals": _technicals,
    "fundamentals": _fundamentals,
    "growth": score_growth,
    "news": score_news,
    "sentiment": _sentiment,
    "valuation": _valuation,
}


def registered_scorer_keys() -> frozenset[str]:
    return frozenset(_SCORERS)


def scorer_for(key: str) -> PersonaScorer:
    try:
        return _SCORERS[key]
    except KeyError as exc:
        raise ValueError(f"unknown persona scorer: {key}") from exc
