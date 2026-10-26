"""Build the synthetic equity evaluation scenarios from company archetypes, reproducibly.

Each archetype is a small, internally consistent company: statements that add up, ratios that
follow from them, a seeded price path whose last close matches the market cap, insider filings
and headlines worded for the sentiment lexicon. The expected answer of every scenario is part
of its design and is checked against the deterministic analysts by the test suite.

    uv run python scripts/build_equity_scenarios.py            # rewrite the synthetic scenarios
    uv run python scripts/build_equity_scenarios.py --check    # fail if a file would change

The three ``*-2024-03`` seed scenarios are copied from ai-hedge-fund's recorded API fixtures
(MIT, see NOTICE) and are not regenerated here.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "fin_graph" / "evals" / "data" / "equities"
AS_OF = date(2024, 6, 28)


@dataclass(frozen=True)
class Company:
    """Fundamentals, valuation inputs, price path and the sentiment flow of one ticker."""

    ticker: str
    revenue: float
    revenue_growth: float
    gross_margin: float
    operating_margin: float
    net_margin: float
    equity: float
    debt: float
    cash: float
    current_assets: float
    current_liabilities: float
    total_assets: float
    shares: float
    price: float
    capex_pct: float = 0.04
    da_pct: float = 0.04
    fcf_conversion: float = 1.0
    dividend_payout: float = 0.2
    buyback_pct: float = 0.0
    margin_trend: float = 0.0
    earnings_growth: float | None = None
    drift: float = 0.0003
    volatility: float = 0.015
    seed: int = 1
    volume_trend: float = 0.0
    #: Multiplies the last bar's volume: momentum needs above-average volume on the last day.
    last_volume_boost: float = 1.0
    insider_buys: int = 3
    insider_sells: int = 3
    headlines_positive: int = 2
    headlines_negative: int = 2
    headlines_neutral: int = 4
    interest_coverage: float | None = 20.0
    omit: tuple[str, ...] = ()

    @property
    def market_cap(self) -> float:
        return self.price * self.shares


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    companies: tuple[Company, ...]
    answers: dict[str, dict[str, Any]]
    horizon: str = "medium"
    difficulty: int = 1
    adversarial_signals: tuple[str, ...] = ()
    question: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# -- generation --------------------------------------------------------------------------


def _lcg(seed: int) -> Any:
    state = seed

    def rnd() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) % (2**31)
        return state / 2**31

    return rnd


def prices(company: Company, days: int = 420) -> list[dict[str, Any]]:
    """A seeded geometric walk ending exactly at ``company.price`` on AS_OF."""
    rnd = _lcg(company.seed)
    path = [1.0]
    for _ in range(days - 1):
        u1, u2 = max(rnd(), 1e-12), rnd()
        z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        path.append(path[-1] * math.exp(company.drift + company.volatility * z))
    scale = company.price / path[-1]
    start = AS_OF - timedelta(days=days - 1)
    bars = []
    previous = path[0] * scale
    for index, value in enumerate(path):
        close = value * scale
        open_ = previous
        spread = 0.004 + 0.003 * rnd()
        volume = int(1_000_000 * (0.6 + rnd()) * (1 + company.volume_trend * index / days))
        if index == days - 1:
            volume = int(volume * company.last_volume_boost)
        bars.append(
            {
                "time": f"{(start + timedelta(days=index)).isoformat()}T00:00:00Z",
                "open": round(open_, 4),
                "high": round(max(open_, close) * (1 + spread), 4),
                "low": round(min(open_, close) * (1 - spread), 4),
                "close": round(close, 4),
                "volume": volume,
            }
        )
        previous = close
    return bars


def _year_facts(company: Company, years_back: int) -> dict[str, float]:
    factor = (1 + company.revenue_growth) ** -years_back
    margin_shift = company.margin_trend * years_back
    revenue = company.revenue * factor
    net_margin = company.net_margin - margin_shift
    gross_margin = company.gross_margin - margin_shift
    operating_margin = company.operating_margin - margin_shift
    net_income = revenue * net_margin
    shares = company.shares * (1 + company.buyback_pct) ** years_back
    equity = company.equity * (1 + company.revenue_growth * 0.6) ** -years_back
    da = revenue * company.da_pct
    capex = revenue * company.capex_pct
    return {
        "revenue": revenue,
        "net_income": net_income,
        "gross_profit": revenue * gross_margin,
        "gross_margin": gross_margin,
        "operating_income": revenue * operating_margin,
        "ebit": revenue * operating_margin,
        "ebitda": revenue * operating_margin + da,
        "depreciation_and_amortization": da,
        "capital_expenditure": -capex,
        "free_cash_flow": (net_income + da - capex) * company.fcf_conversion,
        "working_capital": company.current_assets - company.current_liabilities,
        "total_debt": company.debt,
        "cash_and_equivalents": company.cash,
        "total_assets": company.total_assets * factor,
        "total_liabilities": company.total_assets * factor - equity,
        "current_assets": company.current_assets * factor,
        "current_liabilities": company.current_liabilities * factor,
        "shareholders_equity": equity,
        "outstanding_shares": shares,
        "earnings_per_share": net_income / shares,
        "book_value_per_share": equity / shares,
        "dividends_and_other_cash_distributions": -net_income * company.dividend_payout
        if net_income > 0
        else 0.0,
        "issuance_or_purchase_of_equity_shares": -company.market_cap * company.buyback_pct,
        "interest_expense": revenue * operating_margin / (company.interest_coverage or 1.0),
    }


def line_items(company: Company) -> list[dict[str, Any]]:
    rows = []
    for back in range(6):
        facts = _year_facts(company, back)
        rows.append(
            {
                "report_period": f"{AS_OF.year - 1 - back}-12-31",
                "period": "annual",
                "currency": "USD",
                **{key: round(value, 2) for key, value in facts.items()},
            }
        )
    return rows


def metrics(company: Company) -> list[dict[str, Any]]:
    """A current TTM snapshot and nine trailing quarterly snapshots."""
    rows = []
    quarter_ends = [date(2024, 3, 31)]
    for _ in range(9):
        last = quarter_ends[-1]
        month = last.month - 3 or 12
        year = last.year - (1 if last.month <= 3 else 0)
        day = 31 if month in (3, 12) else 30
        quarter_ends.append(date(year, month, day))
    for index, end in enumerate(quarter_ends):
        back = index / 4
        facts = _year_facts(company, int(back))
        drift = (1 + company.revenue_growth) ** -back
        net_income = facts["net_income"]
        market_cap = company.market_cap * (1 + 0.02 * math.sin(index))
        ev = market_cap + company.debt - company.cash
        ebitda = facts["ebitda"]
        growth = (
            company.earnings_growth
            if company.earnings_growth is not None
            else company.revenue_growth
        )
        rows.append(
            {
                "report_period": end.isoformat(),
                "period": "ttm",
                "currency": "USD",
                "market_cap": round(market_cap, 2),
                "enterprise_value": round(ev, 2),
                "price_to_earnings_ratio": round(market_cap / net_income, 4)
                if net_income > 0
                else None,
                "price_to_book_ratio": round(market_cap / facts["shareholders_equity"], 4)
                if facts["shareholders_equity"] > 0
                else None,
                "price_to_sales_ratio": round(market_cap / facts["revenue"], 4),
                "enterprise_value_to_ebitda_ratio": round(ev / ebitda, 4) if ebitda > 0 else None,
                "enterprise_value_to_revenue_ratio": round(ev / facts["revenue"], 4),
                "free_cash_flow_yield": round(facts["free_cash_flow"] / market_cap, 4),
                "gross_margin": round(facts["gross_margin"], 4),
                "operating_margin": round(facts["operating_income"] / facts["revenue"], 4),
                "net_margin": round(facts["net_income"] / facts["revenue"], 4),
                "return_on_equity": round(net_income / facts["shareholders_equity"], 4)
                if facts["shareholders_equity"] > 0
                else None,
                "return_on_assets": round(net_income / facts["total_assets"], 4),
                "return_on_invested_capital": round(
                    net_income / max(facts["shareholders_equity"] + company.debt, 1.0), 4
                ),
                "asset_turnover": round(facts["revenue"] / facts["total_assets"], 4),
                "current_ratio": round(facts["current_assets"] / facts["current_liabilities"], 4),
                "debt_to_equity": round(company.debt / facts["shareholders_equity"], 4)
                if facts["shareholders_equity"] > 0
                else None,
                "interest_coverage": company.interest_coverage,
                "revenue_growth": round(company.revenue_growth * drift**0, 4),
                "earnings_growth": round(growth, 4),
                "book_value_growth": round(company.revenue_growth * 0.6, 4),
                "earnings_per_share": round(facts["earnings_per_share"], 4),
                "book_value_per_share": round(facts["book_value_per_share"], 4),
                "free_cash_flow_per_share": round(
                    facts["free_cash_flow"] / facts["outstanding_shares"], 4
                ),
            }
        )
    return rows


_POSITIVE = (
    "{name} beats estimates and raises guidance",
    "{name} shares surge on record quarter",
    "Analyst upgrades {name} on strong demand",
    "{name} announces buyback after strong results",
)
_NEGATIVE = (
    "{name} misses estimates, cuts guidance",
    "{name} shares plunge after downgrade",
    "{name} faces lawsuit and SEC probe",
    "{name} warns of weak demand, announces layoffs",
)
_NEUTRAL = (
    "{name} to present at industry conference",
    "{name} schedules annual shareholder meeting",
    "What to watch in {name} this week",
    "{name} names new board member",
)


def news(company: Company) -> list[dict[str, Any]]:
    items = []
    groups = (
        (_POSITIVE, company.headlines_positive),
        (_NEGATIVE, company.headlines_negative),
        (_NEUTRAL, company.headlines_neutral),
    )
    index = 0
    for templates, count in groups:
        for position in range(count):
            moment = datetime(2024, 6, 27, 14) - timedelta(days=index * 3)
            items.append(
                {
                    "title": templates[position % len(templates)].format(name=company.ticker),
                    "source": "Synthetic Wire",
                    "date": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "url": f"https://news.example/{company.ticker.lower()}/{index}",
                }
            )
            index += 1
    return items


def insider_trades(company: Company) -> list[dict[str, Any]]:
    trades = []
    for index in range(company.insider_buys + company.insider_sells):
        sale = index >= company.insider_buys
        day = AS_OF - timedelta(days=7 + index * 11)
        shares = -25_000 if sale else 4_000
        trades.append(
            {
                "filing_date": day.isoformat(),
                "transaction_date": day.isoformat(),
                "name": f"Insider {index + 1}",
                "title": "Director" if index % 2 else "Officer",
                "transaction_shares": shares,
                "transaction_price_per_share": round(company.price, 2),
                "transaction_value": round(abs(shares) * company.price, 2),
            }
        )
    return trades


def company_files(company: Company) -> dict[str, Any]:
    files: dict[str, Any] = {
        "prices.json": {"prices": prices(company)},
        "financial_metrics.json": {"financial_metrics": metrics(company)},
        "line_items.json": {"search_results": line_items(company)},
        "insider_trades.json": {"insider_trades": insider_trades(company)},
        "news.json": {"news": news(company)},
        "market_cap.json": round(company.market_cap, 2),
    }
    for name in company.omit:
        files.pop(name, None)
    return files


# -- the archetypes ---------------------------------------------------------------------

BASE = Company(
    ticker="BASE",
    revenue=10e9,
    revenue_growth=0.05,
    gross_margin=0.40,
    operating_margin=0.15,
    net_margin=0.10,
    equity=6e9,
    debt=3e9,
    cash=1e9,
    current_assets=4e9,
    current_liabilities=2.5e9,
    total_assets=12e9,
    shares=1e9,
    price=8.0,
)

NO_EDGE = replace(BASE, ticker="FAIR", seed=11, drift=0.0001, volatility=0.012)
DEEP_VALUE = replace(
    BASE,
    ticker="DEEP",
    revenue=2.0e9,
    revenue_growth=0.03,
    gross_margin=0.30,
    operating_margin=0.10,
    net_margin=0.07,
    equity=1.4e9,
    debt=0.1e9,
    cash=0.5e9,
    current_assets=1.3e9,
    current_liabilities=0.4e9,
    total_assets=1.9e9,
    shares=100e6,
    price=5.5,
    seed=21,
    drift=0.0,
    volatility=0.012,
    dividend_payout=0.4,
)
COMPOUNDER = replace(
    BASE,
    ticker="MOAT",
    revenue=20e9,
    revenue_growth=0.12,
    gross_margin=0.62,
    operating_margin=0.35,
    net_margin=0.28,
    equity=14e9,
    debt=2e9,
    cash=6e9,
    current_assets=12e9,
    current_liabilities=5e9,
    total_assets=24e9,
    shares=1e9,
    price=70.0,
    buyback_pct=0.02,
    margin_trend=-0.01,
    seed=31,
    drift=0.0006,
    dividend_payout=0.3,
)
OVERVALUED = replace(
    BASE,
    ticker="HYPE",
    revenue=3e9,
    revenue_growth=0.04,
    gross_margin=0.50,
    operating_margin=0.12,
    net_margin=0.08,
    equity=2e9,
    debt=1e9,
    cash=0.5e9,
    current_assets=1.5e9,
    current_liabilities=1.2e9,
    total_assets=4e9,
    shares=500e6,
    price=120.0,
    seed=41,
    drift=0.0002,
    volatility=0.02,
    dividend_payout=0.0,
)
DETERIORATING = replace(
    BASE,
    ticker="FADE",
    revenue_growth=-0.08,
    margin_trend=0.02,
    gross_margin=0.30,
    operating_margin=0.06,
    net_margin=0.03,
    earnings_growth=-0.25,
    price=4.0,
    seed=51,
    drift=-0.0004,
    dividend_payout=0.0,
)
DISTRESSED = replace(
    BASE,
    ticker="DEBT",
    revenue=0.9e9,
    revenue_growth=-0.05,
    gross_margin=0.13,
    operating_margin=-0.02,
    net_margin=-0.09,
    equity=0.05e9,
    debt=1.4e9,
    cash=0.05e9,
    current_assets=0.3e9,
    current_liabilities=0.42e9,
    total_assets=1.8e9,
    shares=200e6,
    price=1.25,
    seed=61,
    volatility=0.03,
    interest_coverage=0.4,
    dividend_payout=0.0,
)
MOMENTUM = replace(
    BASE,
    ticker="RUNR",
    seed=71,
    drift=0.0030,
    volatility=0.008,
    volume_trend=0.8,
    last_volume_boost=1.8,
)
# Not "DROP": a ticker that is a sentiment word would colour its own neutral headlines.
BREAKDOWN = replace(
    BASE,
    ticker="BRKD",
    seed=81,
    drift=-0.0035,
    volatility=0.009,
    volume_trend=0.8,
    last_volume_boost=1.8,
)
INSIDER_RED_HERRING = replace(
    DEEP_VALUE, ticker="SOLD", seed=91, insider_buys=0, insider_sells=9, headlines_negative=1
)
NEGATIVE_NEWS = replace(
    BASE,
    ticker="GLUM",
    seed=101,
    insider_buys=0,
    insider_sells=8,
    headlines_positive=0,
    headlines_negative=9,
    headlines_neutral=1,
)
MISSING = replace(BASE, ticker="THIN", seed=111, omit=("line_items.json", "financial_metrics.json"))
GARP = replace(
    BASE,
    ticker="GRWX",
    revenue_growth=0.22,
    earnings_growth=0.25,
    gross_margin=0.45,
    operating_margin=0.18,
    net_margin=0.13,
    price=24.0,
    seed=121,
    drift=0.0003,
)


def _answer(signal: str, category: str, **extra: Any) -> dict[str, Any]:
    return {"expected_signal": signal, "thesis_category": category, **extra}


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "000-no-edge",
        "Fairly priced, average business: nothing to act on.",
        (NO_EDGE,),
        {
            "FAIR": _answer(
                "neutral",
                "no_edge",
                equivalent_categories=["mixed_signals"],
                forbidden_categories=["deep_value", "overvalued"],
            )
        },
    ),
    Scenario(
        "001-deep-value",
        "Net-net: current assets minus all liabilities exceed the market cap.",
        (DEEP_VALUE,),
        {
            "DEEP": _answer(
                "bullish",
                "deep_value",
                required_keywords=["margin of safety", "graham"],
                forbidden_categories=["overvalued", "balance_sheet_risk"],
                expected_action="buy",
            )
        },
    ),
    Scenario(
        "002-quality-compounder",
        "High and stable ROE, expanding margins and buybacks.",
        (COMPOUNDER,),
        {
            "MOAT": _answer(
                "bullish",
                "quality_compounder",
                equivalent_categories=["deep_value", "garp"],
                forbidden_categories=["balance_sheet_risk", "deteriorating_fundamentals"],
            )
        },
        horizon="long",
    ),
    Scenario(
        "003-overvalued",
        "Average business priced far above every intrinsic value estimate.",
        (OVERVALUED,),
        {
            "HYPE": _answer(
                "bearish",
                "overvalued",
                forbidden_categories=["deep_value", "quality_compounder"],
                expected_action="hold",
            )
        },
    ),
    Scenario(
        "004-deteriorating-fundamentals",
        "Shrinking revenue and margins year after year.",
        (DETERIORATING,),
        {
            "FADE": _answer(
                "bearish",
                "deteriorating_fundamentals",
                equivalent_categories=["overvalued", "momentum"],
                forbidden_categories=["quality_compounder", "garp"],
            )
        },
    ),
    Scenario(
        "005-balance-sheet-risk",
        "Liabilities near total assets, losses and weak liquidity.",
        (DISTRESSED,),
        {"DEBT": _answer("bearish", "balance_sheet_risk", forbidden_categories=["deep_value"])},
        adversarial_signals=("low_price_to_sales",),
    ),
    Scenario(
        "006-momentum",
        "A steady uptrend confirmed by rising volume, average fundamentals.",
        (MOMENTUM,),
        {
            "RUNR": _answer(
                "bullish",
                "momentum",
                equivalent_categories=["garp"],
                forbidden_categories=["balance_sheet_risk", "negative_sentiment"],
            )
        },
        horizon="short",
    ),
    Scenario(
        "007-momentum-breakdown",
        "A downtrend with volume: price action turns bearish.",
        (BREAKDOWN,),
        {
            "BRKD": _answer(
                "bearish",
                "momentum",
                equivalent_categories=["deteriorating_fundamentals", "negative_sentiment"],
                forbidden_categories=["deep_value"],
            )
        },
        horizon="short",
    ),
    Scenario(
        "008-insider-selling-red-herring",
        "Deep value despite heavy insider selling: the sales must not flip the thesis.",
        (INSIDER_RED_HERRING,),
        {"SOLD": _answer("bullish", "deep_value", forbidden_categories=["negative_sentiment"])},
        difficulty=2,
        adversarial_signals=("insider_selling",),
    ),
    Scenario(
        "009-negative-sentiment",
        "Average fundamentals, a stream of negative headlines and insider sales.",
        (NEGATIVE_NEWS,),
        {
            "GLUM": _answer(
                "bearish",
                "negative_sentiment",
                equivalent_categories=["mixed_signals"],
                forbidden_categories=["deep_value"],
            )
        },
    ),
    Scenario(
        "010-missing-data",
        "No statements and no ratios: the analysis must say so, not guess.",
        (MISSING,),
        {
            "THIN": _answer(
                "neutral",
                "no_edge",
                equivalent_categories=["unknown", "momentum"],
                forbidden_categories=["deep_value", "overvalued", "quality_compounder"],
            )
        },
        difficulty=2,
        adversarial_signals=("missing_fundamentals",),
    ),
    Scenario(
        "011-garp",
        "Growth above 20% at a reasonable multiple.",
        (GARP,),
        {
            "GRWX": _answer(
                "bullish",
                "garp",
                equivalent_categories=["quality_compounder", "deep_value"],
                forbidden_categories=["overvalued", "balance_sheet_risk"],
            )
        },
    ),
    Scenario(
        "012-two-tickers",
        "One request, one cheap and one expensive stock: theses must not bleed into each other.",
        (DEEP_VALUE, OVERVALUED),
        {
            "DEEP": _answer("bullish", "deep_value", forbidden_categories=["overvalued"]),
            "HYPE": _answer("bearish", "overvalued", forbidden_categories=["deep_value"]),
        },
        difficulty=2,
    ),
)


def _trajectory(horizon: str) -> list[str]:
    """The compute tools of the analysts a horizon runs (short skips Buffett and Graham)."""
    analysts = ["fundamentals", "valuation", "technicals", "sentiment"]
    if horizon != "short":
        analysts += ["buffett", "graham"]
    return [f"compute_{name}" for name in analysts]


def build(check: bool) -> list[Path]:
    """Write every synthetic scenario; with ``check``, only report files that would change."""
    changed: list[Path] = []
    for scenario in SCENARIOS:
        folder = DATA / scenario.scenario_id
        documents: dict[Path, str] = {
            folder / "scenario.yml": yaml.safe_dump(
                {
                    "scenario_id": scenario.scenario_id,
                    "description": scenario.description,
                    "tickers": [company.ticker for company in scenario.companies],
                    "as_of": AS_OF.isoformat(),
                    "horizon": scenario.horizon,
                    "question": scenario.question,
                    "scenario_difficulty": scenario.difficulty,
                    "adversarial_signals": list(scenario.adversarial_signals),
                    "synthetic": True,
                    **scenario.extra,
                },
                sort_keys=False,
                allow_unicode=True,
            ),
            folder / "answer.yml": yaml.safe_dump(
                {
                    "tickers": scenario.answers,
                    "optimal_trajectory": _trajectory(scenario.horizon),
                    "required_queries": ["compute_valuation", "compute_fundamentals"],
                    "max_waves": 2,
                },
                sort_keys=False,
                allow_unicode=True,
            ),
        }
        for company in scenario.companies:
            for name, content in company_files(company).items():
                documents[folder / company.ticker / name] = json.dumps(content, indent=1) + "\n"
        for path, text in documents.items():
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current == text:
                continue
            changed.append(path)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if a scenario would change")
    args = parser.parse_args()
    changed = build(args.check)
    for path in changed:
        print(("would change " if args.check else "wrote ") + str(path.relative_to(ROOT)))
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    sys.exit(main())
