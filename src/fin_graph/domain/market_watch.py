"""Market-watch rules: when a watched ticker moved enough to deserve a fresh analysis.

Three deterministic triggers on daily closes:

- **Daily move**: the last close against the one before, above ``max_daily_move_pct``.
- **Drawdown**: the last close against the highest close of the past year (252 sessions).
- **Volatility spike**: the standard deviation of the last 20 daily returns over that of the
  120 before them, above ``volatility_spike_ratio``. It needs 140 returns.

A trigger never trades: it starts an analysis, whose orders still need a human.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import datetime
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fin_graph.domain.market import Price
from fin_graph.domain.requests.model import normalize_ticker

WatchStatus = Literal["triggered", "normal", "unknown"]
YEAR_SESSIONS = 252
_RECENT, _BASE = 20, 120


class WatchRule(BaseModel):
    """The thresholds for one ticker."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    ticker: str
    max_daily_move_pct: float = Field(default=5.0, gt=0, le=100)
    max_drawdown_pct: float = Field(default=15.0, gt=0, le=100)
    volatility_spike_ratio: float = Field(default=1.8, gt=1, le=20)

    @field_validator("ticker")
    @classmethod
    def symbol(cls, value: str) -> str:
        return normalize_ticker(value)


class WatchResult(BaseModel):
    """What the rule saw; ``reasons`` names every threshold that was crossed."""

    ticker: str
    status: WatchStatus
    close: float | None = None
    daily_move_pct: float | None = None
    drawdown_pct: float | None = None
    volatility_ratio: float | None = None
    reasons: list[str] = Field(default_factory=list)


def _returns(closes: Sequence[float]) -> list[float]:
    return [now / before - 1 for before, now in pairwise(closes) if before > 0]


def assess(rule: WatchRule, prices: Sequence[Price]) -> WatchResult:
    """Evaluate ``rule`` on ``prices``; fewer than two closes is ``unknown``."""
    closes = [item.close for item in sorted(prices, key=lambda item: item.time) if item.close > 0]
    if len(closes) < 2:
        return WatchResult(ticker=rule.ticker, status="unknown", reasons=["not enough prices"])
    last, previous = closes[-1], closes[-2]
    move = (last / previous - 1) * 100
    peak = max(closes[-YEAR_SESSIONS:])
    drawdown = (1 - last / peak) * 100 if peak > 0 else 0.0
    returns = _returns(closes)
    ratio = None
    if len(returns) >= _RECENT + _BASE:
        recent = statistics.pstdev(returns[-_RECENT:])
        base = statistics.pstdev(returns[-(_RECENT + _BASE) : -_RECENT])
        ratio = recent / base if base > 0 else None
    reasons = []
    if abs(move) >= rule.max_daily_move_pct:
        reasons.append(f"daily move {move:+.1f}% (limit ±{rule.max_daily_move_pct:g}%)")
    if drawdown >= rule.max_drawdown_pct:
        reasons.append(f"drawdown {drawdown:.1f}% from the 1-year high")
    if ratio is not None and math.isfinite(ratio) and ratio >= rule.volatility_spike_ratio:
        reasons.append(f"volatility x{ratio:.1f} against its 6-month level")
    return WatchResult(
        ticker=rule.ticker,
        status="triggered" if reasons else "normal",
        close=round(last, 4),
        daily_move_pct=round(move, 2),
        drawdown_pct=round(drawdown, 2),
        volatility_ratio=round(ratio, 2) if ratio is not None else None,
        reasons=reasons,
    )


def watch_question(results: Sequence[WatchResult]) -> str:
    """The analysis question a trigger asks: what moved and why it matters."""
    moved = "; ".join(f"{item.ticker}: {', '.join(item.reasons)}" for item in results)
    return f"Vigilancia de mercado: {moved}. ¿Cambia la tesis?"


def watch_digest(results: Sequence[WatchResult], now: datetime) -> str:
    """A short Spanish summary of a day of market-watch checks."""
    if not results:
        return f"Vigilancia {now:%Y-%m-%d}: sin resultados."
    triggered = [item for item in results if item.status == "triggered"]
    unknown = [item.ticker for item in results if item.status == "unknown"]
    lines = [
        f"Vigilancia {now:%Y-%m-%d}: {len(triggered)} alertas de {len(results)} comprobaciones."
    ]
    lines += [f"- {item.ticker}: {', '.join(item.reasons)}" for item in triggered]
    if unknown:
        lines.append(f"Sin datos: {', '.join(sorted(set(unknown)))}.")
    return "\n".join(lines)
