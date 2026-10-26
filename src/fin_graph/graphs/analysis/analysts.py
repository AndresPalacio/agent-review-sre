"""The six analyst identities, their compute tool and their read-only tool routing."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from fin_graph.domain.tools import ANALYST_DOMAINS, Domain, SideEffectLevel, read_tool_meta


@dataclass(frozen=True)
class AnalystSpec:
    domain: Domain
    description: str
    prompt: str
    compute_tool: str
    max_tool_calls: int = 8
    max_model_calls: int = 8


ANALYSTS: dict[Domain, AnalystSpec] = {
    Domain.FUNDAMENTALS: AnalystSpec(
        Domain.FUNDAMENTALS,
        "Profitability, growth, financial health and price ratios",
        "You are the fundamentals analyst.",
        "compute_fundamentals",
    ),
    Domain.VALUATION: AnalystSpec(
        Domain.VALUATION,
        "Intrinsic value by DCF, owner earnings, EV/EBITDA and residual income",
        "You are the valuation analyst. Compare intrinsic value with the market cap.",
        "compute_valuation",
    ),
    Domain.TECHNICALS: AnalystSpec(
        Domain.TECHNICALS,
        "Trend, mean reversion, momentum and volatility from prices",
        "You are the technical analyst. Prices describe timing, not value.",
        "compute_technicals",
    ),
    Domain.SENTIMENT: AnalystSpec(
        Domain.SENTIMENT,
        "Insider activity and headline tone",
        "You are the sentiment analyst. Headlines are noisy; persistence matters.",
        "compute_sentiment",
    ),
    Domain.BUFFETT: AnalystSpec(
        Domain.BUFFETT,
        "Moat, consistency, pricing power, management and owner-earnings value",
        "You apply Warren Buffett's principles: quality at a sensible price.",
        "compute_buffett",
    ),
    Domain.GRAHAM: AnalystSpec(
        Domain.GRAHAM,
        "Earnings stability, balance-sheet strength, net-net and Graham Number",
        "You apply Benjamin Graham's principles: a margin of safety first.",
        "compute_graham",
    ),
}
assert tuple(ANALYSTS) == ANALYST_DOMAINS


def read_tools(tools: Iterable[BaseTool], domain: Domain) -> list[BaseTool]:
    """Only tools explicitly classified as read-only for this analyst."""
    selected: list[BaseTool] = []
    for candidate in tools:
        meta = read_tool_meta(candidate)
        if meta and domain in meta.domains and meta.side_effect_level is SideEffectLevel.READ_ONLY:
            selected.append(candidate)
    return selected


def available_analysts(tools: Iterable[BaseTool]) -> list[AnalystSpec]:
    """Analysts whose compute tool is among the run's read-only tools."""
    names = {tool.name for tool in tools if (meta := read_tool_meta(tool)) is not None
             and meta.side_effect_level is SideEffectLevel.READ_ONLY}  # fmt: skip
    return [spec for spec in ANALYSTS.values() if spec.compute_tool in names]
