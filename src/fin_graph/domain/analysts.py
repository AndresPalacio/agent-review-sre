"""Canonical analyst identities shared by compatibility surfaces and orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.domain.tools import Domain


class AnalystId(StrEnum):
    ASWATH_DAMODARAN = "aswath_damodaran"
    BEN_GRAHAM = "ben_graham"
    BILL_ACKMAN = "bill_ackman"
    CATHIE_WOOD = "cathie_wood"
    CHARLIE_MUNGER = "charlie_munger"
    MICHAEL_BURRY = "michael_burry"
    MOHNISH_PABRAI = "mohnish_pabrai"
    NASSIM_TALEB = "nassim_taleb"
    PETER_LYNCH = "peter_lynch"
    PHIL_FISHER = "phil_fisher"
    RAKESH_JHUNJHUNWALA = "rakesh_jhunjhunwala"
    STANLEY_DRUCKENMILLER = "stanley_druckenmiller"
    WARREN_BUFFETT = "warren_buffett"
    TECHNICAL_ANALYST = "technical_analyst"
    FUNDAMENTALS_ANALYST = "fundamentals_analyst"
    GROWTH_ANALYST = "growth_analyst"
    NEWS_SENTIMENT_ANALYST = "news_sentiment_analyst"
    SENTIMENT_ANALYST = "sentiment_analyst"
    VALUATION_ANALYST = "valuation_analyst"


class AnalystDefinition(BaseModel):
    """Frozen metadata only; prompts and scorer callables live behind stable keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AnalystId
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    investing_style: str = Field(min_length=1)
    domains: tuple[Domain, ...] = Field(min_length=1)
    order: int = Field(ge=0)
    prompt_key: str = Field(min_length=1)
    scorer_key: str = Field(min_length=1)


def _definition(
    analyst_id: AnalystId,
    display_name: str,
    description: str,
    investing_style: str,
    domains: tuple[Domain, ...],
    order: int,
    scorer_key: str,
) -> AnalystDefinition:
    return AnalystDefinition(
        id=analyst_id,
        display_name=display_name,
        description=description,
        investing_style=investing_style,
        domains=domains,
        order=order,
        prompt_key=analyst_id.value,
        scorer_key=scorer_key,
    )


_CATALOG = (
    _definition(
        AnalystId.ASWATH_DAMODARAN,
        "Aswath Damodaran",
        "The Dean of Valuation",
        "Focuses on intrinsic value and financial metrics to assess investment "
        "opportunities through rigorous valuation analysis.",
        (Domain.VALUATION, Domain.FUNDAMENTALS),
        0,
        "damodaran",
    ),
    _definition(
        AnalystId.BEN_GRAHAM,
        "Ben Graham",
        "The Father of Value Investing",
        "Emphasizes a margin of safety and invests in undervalued companies with strong "
        "fundamentals through systematic value analysis.",
        (Domain.GRAHAM, Domain.FUNDAMENTALS, Domain.VALUATION),
        1,
        "graham",
    ),
    _definition(
        AnalystId.BILL_ACKMAN,
        "Bill Ackman",
        "The Activist Investor",
        "Seeks to influence management and unlock value through strategic activism and "
        "contrarian investment positions.",
        (Domain.FUNDAMENTALS, Domain.VALUATION, Domain.SENTIMENT),
        2,
        "ackman",
    ),
    _definition(
        AnalystId.CATHIE_WOOD,
        "Cathie Wood",
        "The Queen of Growth Investing",
        "Focuses on disruptive innovation and growth, investing in companies that are "
        "leading technological advancements and market disruption.",
        (Domain.FUNDAMENTALS, Domain.VALUATION, Domain.SENTIMENT),
        3,
        "wood",
    ),
    _definition(
        AnalystId.CHARLIE_MUNGER,
        "Charlie Munger",
        "The Rational Thinker",
        "Advocates for value investing with a focus on quality businesses and long-term "
        "growth through rational decision-making.",
        (Domain.FUNDAMENTALS, Domain.VALUATION),
        4,
        "munger",
    ),
    _definition(
        AnalystId.MICHAEL_BURRY,
        "Michael Burry",
        "The Big Short Contrarian",
        "Makes contrarian bets, often shorting overvalued markets and investing in "
        "undervalued assets through deep fundamental analysis.",
        (Domain.FUNDAMENTALS, Domain.VALUATION, Domain.SENTIMENT),
        5,
        "burry",
    ),
    _definition(
        AnalystId.MOHNISH_PABRAI,
        "Mohnish Pabrai",
        "The Dhandho Investor",
        "Focuses on value investing and long-term growth through fundamental analysis "
        "and a margin of safety.",
        (Domain.FUNDAMENTALS, Domain.VALUATION),
        6,
        "pabrai",
    ),
    _definition(
        AnalystId.NASSIM_TALEB,
        "Nassim Taleb",
        "The Black Swan Risk Analyst",
        "Focuses on tail risk, antifragility, and asymmetric payoffs. Uses barbell "
        "strategy, avoids fragile companies via negativa, and seeks convex positions "
        "with limited downside and unlimited upside.",
        (Domain.TECHNICALS, Domain.FUNDAMENTALS),
        7,
        "taleb",
    ),
    _definition(
        AnalystId.PETER_LYNCH,
        "Peter Lynch",
        "The 10-Bagger Investor",
        "Invests in companies with understandable business models and strong growth "
        "potential using the 'buy what you know' strategy.",
        (Domain.FUNDAMENTALS, Domain.VALUATION, Domain.SENTIMENT),
        8,
        "lynch",
    ),
    _definition(
        AnalystId.PHIL_FISHER,
        "Phil Fisher",
        "The Scuttlebutt Investor",
        "Emphasizes investing in companies with strong management and innovative "
        "products, focusing on long-term growth through scuttlebutt research.",
        (Domain.FUNDAMENTALS, Domain.SENTIMENT),
        9,
        "fisher",
    ),
    _definition(
        AnalystId.RAKESH_JHUNJHUNWALA,
        "Rakesh Jhunjhunwala",
        "The Big Bull Of India",
        "Leverages macroeconomic insights to invest in high-growth sectors, particularly "
        "within emerging markets and domestic opportunities.",
        (Domain.FUNDAMENTALS, Domain.TECHNICALS, Domain.SENTIMENT),
        10,
        "jhunjhunwala",
    ),
    _definition(
        AnalystId.STANLEY_DRUCKENMILLER,
        "Stanley Druckenmiller",
        "The Macro Investor",
        "Focuses on macroeconomic trends, making large bets on currencies, commodities, "
        "and interest rates through top-down analysis.",
        (Domain.FUNDAMENTALS, Domain.TECHNICALS, Domain.SENTIMENT),
        11,
        "druckenmiller",
    ),
    _definition(
        AnalystId.WARREN_BUFFETT,
        "Warren Buffett",
        "The Oracle of Omaha",
        "Seeks companies with strong fundamentals and competitive advantages through "
        "value investing and long-term ownership.",
        (Domain.BUFFETT, Domain.FUNDAMENTALS, Domain.VALUATION),
        12,
        "buffett",
    ),
    _definition(
        AnalystId.TECHNICAL_ANALYST,
        "Technical Analyst",
        "Chart Pattern Specialist",
        "Focuses on chart patterns and market trends to make investment decisions, often "
        "using technical indicators and price action analysis.",
        (Domain.TECHNICALS,),
        13,
        "technicals",
    ),
    _definition(
        AnalystId.FUNDAMENTALS_ANALYST,
        "Fundamentals Analyst",
        "Financial Statement Specialist",
        "Delves into financial statements and economic indicators to assess the intrinsic "
        "value of companies through fundamental analysis.",
        (Domain.FUNDAMENTALS,),
        14,
        "fundamentals",
    ),
    _definition(
        AnalystId.GROWTH_ANALYST,
        "Growth Analyst",
        "Growth Specialist",
        "Analyzes growth trends and valuation to identify growth opportunities through "
        "growth analysis.",
        (Domain.FUNDAMENTALS, Domain.VALUATION),
        15,
        "growth",
    ),
    _definition(
        AnalystId.NEWS_SENTIMENT_ANALYST,
        "News Sentiment Analyst",
        "News Sentiment Specialist",
        "Analyzes news sentiment to predict market movements and identify opportunities "
        "through news analysis.",
        (Domain.SENTIMENT,),
        16,
        "news",
    ),
    _definition(
        AnalystId.SENTIMENT_ANALYST,
        "Sentiment Analyst",
        "Market Sentiment Specialist",
        "Gauges market sentiment and investor behavior to predict market movements and "
        "identify opportunities through behavioral analysis.",
        (Domain.SENTIMENT,),
        17,
        "sentiment",
    ),
    _definition(
        AnalystId.VALUATION_ANALYST,
        "Valuation Analyst",
        "Company Valuation Specialist",
        "Specializes in determining the fair value of companies, using various valuation "
        "models and financial metrics for investment decisions.",
        (Domain.VALUATION,),
        18,
        "valuation",
    ),
)

_BY_ID = {definition.id: definition for definition in _CATALOG}
if len(_BY_ID) != len(_CATALOG) or tuple(item.order for item in _CATALOG) != tuple(range(19)):
    raise RuntimeError("canonical analyst catalog contains duplicate ids or invalid ordering")


def analyst_catalog() -> tuple[AnalystDefinition, ...]:
    """Return all nineteen legacy identities in their public reference order."""

    return _CATALOG


def resolve_analysts(ids: Sequence[str] | None) -> tuple[AnalystDefinition, ...]:
    """Resolve a selection, treating no selection as all analysts in reference order."""

    if not ids:
        return _CATALOG
    resolved: list[AnalystDefinition] = []
    seen: set[AnalystId] = set()
    for raw_id in ids:
        value = raw_id.value if isinstance(raw_id, AnalystId) else str(raw_id)
        try:
            analyst_id = AnalystId(value)
        except ValueError as exc:
            raise ValueError(f"unknown analyst: {value}") from exc
        if analyst_id in seen:
            raise ValueError(f"duplicate analyst: {analyst_id.value}")
        seen.add(analyst_id)
        resolved.append(_BY_ID[analyst_id])
    return tuple(resolved)
