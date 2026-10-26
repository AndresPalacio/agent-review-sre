"""Thesis categories: the one taxonomy the thesis, the report, memory and the evals share.

Each category names why a stock is interesting, or why not, and the stance it implies. A thesis
whose category implies one stance and argues the other is inconsistent, and verification
penalises it. ``no_edge`` plays the role ``healthy`` played in incident review: nothing
distinctive to act on.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from fin_graph.domain.quant.common import Signal


class CategoryGroup(StrEnum):
    """What kind of evidence a category rests on."""

    VALUE = "value"
    QUALITY = "quality"
    GROWTH = "growth"
    PRICE_ACTION = "price_action"
    BALANCE_SHEET = "balance_sheet"
    SENTIMENT = "sentiment"
    GENERIC = "generic"


@dataclass(frozen=True)
class ThesisCategory:
    """A stable name, its group, the stance it implies (None: any) and a one-line meaning."""

    name: str
    group: CategoryGroup
    stance: Signal | None
    description: str


NO_EDGE = "no_edge"
MIXED_SIGNALS = "mixed_signals"
UNKNOWN = "unknown"

CATEGORIES: tuple[ThesisCategory, ...] = (
    ThesisCategory(
        "deep_value",
        CategoryGroup.VALUE,
        Signal.BULLISH,
        "Price well below a conservative intrinsic value (Graham Number, net-net, DCF) "
        "with a balance sheet that can wait for the gap to close.",
    ),
    ThesisCategory(
        "quality_compounder",
        CategoryGroup.QUALITY,
        Signal.BULLISH,
        "High and stable returns on capital, durable margins and a moat, at a fair price.",
    ),
    ThesisCategory(
        "garp",
        CategoryGroup.GROWTH,
        Signal.BULLISH,
        "Growth above 10% a year bought at a reasonable multiple.",
    ),
    ThesisCategory(
        "momentum",
        CategoryGroup.PRICE_ACTION,
        None,
        "Trend and momentum aligned and confirmed by volume; the stance follows the trend.",
    ),
    ThesisCategory(
        "overvalued",
        CategoryGroup.VALUE,
        Signal.BEARISH,
        "Price well above intrinsic value on most methods, leaving no margin of safety.",
    ),
    ThesisCategory(
        "deteriorating_fundamentals",
        CategoryGroup.GROWTH,
        Signal.BEARISH,
        "Revenue, earnings or margins shrinking period after period.",
    ),
    ThesisCategory(
        "balance_sheet_risk",
        CategoryGroup.BALANCE_SHEET,
        Signal.BEARISH,
        "Leverage, weak liquidity or negative equity that could force dilution or distress.",
    ),
    ThesisCategory(
        "negative_sentiment",
        CategoryGroup.SENTIMENT,
        Signal.BEARISH,
        "Insider selling and negative headlines dominate the recent flow.",
    ),
    ThesisCategory(
        MIXED_SIGNALS,
        CategoryGroup.GENERIC,
        Signal.NEUTRAL,
        "Analysts disagree with conviction; no side has the evidence to win.",
    ),
    ThesisCategory(
        NO_EDGE,
        CategoryGroup.GENERIC,
        Signal.NEUTRAL,
        "Fairly priced, nothing distinctive: no reason to act either way.",
    ),
    ThesisCategory(
        UNKNOWN,
        CategoryGroup.GENERIC,
        None,
        "Not enough data to form a thesis.",
    ),
)
_BY_NAME = {entry.name: entry for entry in CATEGORIES}
CATEGORY_NAMES: frozenset[str] = frozenset(_BY_NAME)
_ALIASES = {
    "value": "deep_value",
    "undervalued": "deep_value",
    "net_net": "deep_value",
    "quality": "quality_compounder",
    "compounder": "quality_compounder",
    "growth_at_a_reasonable_price": "garp",
    "growth": "garp",
    "trend": "momentum",
    "trend_following": "momentum",
    "expensive": "overvalued",
    "overpriced": "overvalued",
    "declining_fundamentals": "deteriorating_fundamentals",
    "deteriorating": "deteriorating_fundamentals",
    "leverage": "balance_sheet_risk",
    "distress": "balance_sheet_risk",
    "negative_news": "negative_sentiment",
    "insider_selling": "negative_sentiment",
    "mixed": MIXED_SIGNALS,
    "neutral": NO_EDGE,
    "fair_value": NO_EDGE,
    "fairly_valued": NO_EDGE,
    "hold": NO_EDGE,
}
_SEPARATORS = re.compile(r"[\s\-/]+")

#: Phrases that strongly signal a group; two hits are needed to call a mismatch.
_GROUP_SIGNALS: dict[CategoryGroup, tuple[str, ...]] = {
    CategoryGroup.VALUE: (
        "intrinsic value", "margin of safety", "graham number", "net-net", "ncav", "discount to",
        "dcf", "undervalued", "overvalued", "owner earnings",
    ),
    CategoryGroup.PRICE_ACTION: (
        "moving average", "ema", "rsi", "momentum", "breakout", "uptrend", "downtrend", "adx",
        "bollinger",
    ),
    CategoryGroup.BALANCE_SHEET: (
        "debt-to-equity", "debt to equity", "leverage", "current ratio", "liquidity",
        "negative equity", "interest coverage", "liabilities",
    ),
    CategoryGroup.SENTIMENT: (
        "insider selling", "insider sales", "insiders sold", "headlines", "news flow",
        "negative news",
    ),
}  # fmt: skip
_MIN_SIGNAL_HITS = 2
_GROUP_PATTERNS = {
    group: re.compile(r"\b(?:" + "|".join(map(re.escape, phrases)) + r")\b")
    for group, phrases in _GROUP_SIGNALS.items()
}


def category(name: str) -> ThesisCategory | None:
    """The category called ``name``, if it exists."""
    return _BY_NAME.get(name)


def normalize_category(raw: str, allowed: Collection[str] | None = None) -> str:
    """Map a model's label onto an allowed category, or ``unknown``; fails closed."""
    names = CATEGORY_NAMES if allowed is None else frozenset(allowed) & CATEGORY_NAMES
    cleaned = raw.strip()
    token = _SEPARATORS.sub("_", cleaned.lower()).strip("_")
    for candidate in (cleaned, token, _ALIASES.get(token, "")):
        if candidate in names:
            return candidate
    return UNKNOWN


def render_categories(names: Collection[str] | None = None) -> str:
    """The taxonomy as a prompt section: ``- name (stance): description`` grouped."""
    wanted = CATEGORY_NAMES if names is None else frozenset(names)
    lines: list[str] = []
    for group in CategoryGroup:
        members = [entry for entry in CATEGORIES if entry.group is group and entry.name in wanted]
        if members:
            lines.append(f"### {group.value}")
            lines.extend(
                f"- {entry.name} ({entry.stance.value if entry.stance else 'any stance'}): "
                f"{entry.description}"
                for entry in members
            )
    return "\n".join(lines)


def stance_mismatch(category_name: str, stance: Signal) -> str | None:
    """Why ``stance`` contradicts the category, or None when they agree."""
    entry = _BY_NAME.get(category_name)
    if entry is None or entry.stance is None or entry.stance is stance:
        return None
    return f"{entry.name!r} implies a {entry.stance.value} stance, not {stance.value}"


def category_text_mismatch(summary: str, category_name: str) -> str | None:
    """Why the summary reads as another group, or None when it agrees with the category."""
    entry = _BY_NAME.get(category_name.strip())
    if entry is None or entry.group not in _GROUP_SIGNALS:
        return None
    text = summary.lower()
    scores = {group: len(pattern.findall(text)) for group, pattern in _GROUP_PATTERNS.items()}
    best = max(scores, key=lambda group: scores[group])
    if scores[best] < _MIN_SIGNAL_HITS or best is entry.group or scores[entry.group] >= 1:
        return None
    return (
        f"the summary reads as {best.value} ({scores[best]} phrase hits) "
        f"but {entry.name!r} is {entry.group.value}"
    )
