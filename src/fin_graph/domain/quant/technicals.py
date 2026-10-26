"""Technicals: trend, mean reversion, momentum, volatility regime and return statistics.

Ported from ai-hedge-fund ``src/agents/technicals.py`` (MIT) with the same windows, thresholds
and ensemble weights, in plain Python. Differences: the Hurst exponent is computed correctly
(see :func:`series.hurst_exponent`), and an indicator without enough history is reported as
``None`` plus a data gap instead of a silent ``0.0``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from operator import sub, truediv

from fin_graph.domain.market import Price
from fin_graph.domain.quant import series
from fin_graph.domain.quant.common import (
    Component,
    QuantScore,
    Signal,
    no_data,
    weighted_combination,
)
from fin_graph.domain.tools import Domain

WEIGHTS = {
    "trend": 0.25,
    "mean_reversion": 0.20,
    "momentum": 0.25,
    "volatility": 0.15,
    "stat_arb": 0.15,
}
#: Bars each strategy needs before its last value is defined.
REQUIRED_BARS = {
    "trend": 55,
    "mean_reversion": 50,
    "momentum": 127,
    "volatility": 84,
    "stat_arb": 64,
}
TRADING_DAYS = 252


def _closes(prices: Sequence[Price]) -> list[float]:
    return [bar.close for bar in prices]


def _highs(prices: Sequence[Price]) -> list[float]:
    return [bar.high for bar in prices]


def _lows(prices: Sequence[Price]) -> list[float]:
    return [bar.low for bar in prices]


def _component(
    name: str, signal: Signal, confidence: float, metrics: dict[str, float | None]
) -> Component:
    return Component(
        name=name,
        signal=signal,
        confidence=round(min(max(confidence, 0.0), 1.0) * 100),
        metrics=metrics,
    )


def trend(prices: Sequence[Price]) -> Component:
    """EMA 8 > 21 > 55 is an uptrend, the reverse a downtrend; ADX/100 is the confidence."""
    close = _closes(prices)
    ema8, ema21, ema55 = (series.ewm_mean(close, span, adjust=False) for span in (8, 21, 55))
    strength_raw = series.last(series.adx(_highs(prices), _lows(prices), close, 14))
    strength = strength_raw / 100 if strength_raw is not None else None
    short_up, medium_up = ema8[-1] > ema21[-1], ema21[-1] > ema55[-1]
    if short_up and medium_up:
        signal, confidence = Signal.BULLISH, strength or 0.0
    elif not short_up and not medium_up:
        signal, confidence = Signal.BEARISH, strength or 0.0
    else:
        signal, confidence = Signal.NEUTRAL, 0.5
    return _component(
        "trend",
        signal,
        confidence,
        {
            "ema_8": series.last(ema8),
            "ema_21": series.last(ema21),
            "ema_55": series.last(ema55),
            "adx": strength_raw,
            "trend_strength": strength,
        },
    )


def mean_reversion(prices: Sequence[Price]) -> Component:
    """A 50-day z-score beyond ±2 with price at the matching Bollinger extreme."""
    close = _closes(prices)
    mean50, std50 = series.rolling_mean(close, 50), series.rolling_std(close, 50)
    z = series.last(series.combine(series.combine(close, mean50, sub), std50, truediv))
    upper, lower = series.bollinger(close)
    band = series.last(
        series.combine(
            series.combine(close, lower, sub),
            series.combine(upper, lower, sub),
            truediv,
        )
    )
    if z is not None and band is not None and z < -2 and band < 0.2:
        signal, confidence = Signal.BULLISH, min(abs(z) / 4, 1.0)
    elif z is not None and band is not None and z > 2 and band > 0.8:
        signal, confidence = Signal.BEARISH, min(abs(z) / 4, 1.0)
    else:
        signal, confidence = Signal.NEUTRAL, 0.5
    return _component(
        "mean_reversion",
        signal,
        confidence,
        {
            "z_score": z,
            "price_vs_bb": band,
            "rsi_14": series.last(series.rsi(close, 14)),
            "rsi_28": series.last(series.rsi(close, 28)),
        },
    )


def momentum(prices: Sequence[Price]) -> Component:
    """Weighted 1/3/6-month return sums beyond ±5%, confirmed by above-average volume."""
    close = _closes(prices)
    returns = series.pct_change(close)
    m1, m3, m6 = (series.last(series.rolling_sum(returns, window)) for window in (21, 63, 126))
    volume = [float(bar.volume) for bar in prices]
    volume_ratio = series.last(series.combine(volume, series.rolling_mean(volume, 21), truediv))
    score = None
    if m1 is not None and m3 is not None and m6 is not None:
        score = 0.4 * m1 + 0.3 * m3 + 0.3 * m6
    confirmed = volume_ratio is not None and volume_ratio > 1.0
    if score is not None and score > 0.05 and confirmed:
        signal, confidence = Signal.BULLISH, min(abs(score) * 5, 1.0)
    elif score is not None and score < -0.05 and confirmed:
        signal, confidence = Signal.BEARISH, min(abs(score) * 5, 1.0)
    else:
        signal, confidence = Signal.NEUTRAL, 0.5
    return _component(
        "momentum",
        signal,
        confidence,
        {
            "momentum_1m": m1,
            "momentum_3m": m3,
            "momentum_6m": m6,
            "momentum_score": score,
            "volume_momentum": volume_ratio,
        },
    )


def volatility(prices: Sequence[Price]) -> Component:
    """A compressed volatility regime leans bullish, an expanded one bearish."""
    close = _closes(prices)
    returns = series.pct_change(close)
    hist = [value * math.sqrt(TRADING_DAYS) for value in series.rolling_std(returns, 21)]
    hist_mean = series.rolling_mean(hist, 63)
    regime = series.last(series.combine(hist, hist_mean, truediv))
    z = series.last(
        series.combine(
            series.combine(hist, hist_mean, sub),
            series.rolling_std(hist, 63),
            truediv,
        )
    )
    atr_ratio = series.last(
        series.combine(
            series.atr(_highs(prices), _lows(prices), close),
            close,
            truediv,
        )
    )
    if regime is not None and z is not None and regime < 0.8 and z < -1:
        signal, confidence = Signal.BULLISH, min(abs(z) / 3, 1.0)
    elif regime is not None and z is not None and regime > 1.2 and z > 1:
        signal, confidence = Signal.BEARISH, min(abs(z) / 3, 1.0)
    else:
        signal, confidence = Signal.NEUTRAL, 0.5
    return _component(
        "volatility",
        signal,
        confidence,
        {
            "historical_volatility": series.last(hist),
            "volatility_regime": regime,
            "volatility_z_score": z,
            "atr_ratio": atr_ratio,
        },
    )


def stat_arb(prices: Sequence[Price]) -> Component:
    """A mean-reverting series (Hurst < 0.4) leans the way its 63-day return skew points."""
    close = _closes(prices)
    returns = series.pct_change(close)
    skew = series.last(series.rolling_skew(returns, 63))
    kurt = series.last(series.rolling_kurt(returns, 63))
    hurst = series.hurst_exponent(close)
    if hurst is not None and skew is not None and hurst < 0.4 and skew > 1:
        signal, confidence = Signal.BULLISH, (0.5 - hurst) * 2
    elif hurst is not None and skew is not None and hurst < 0.4 and skew < -1:
        signal, confidence = Signal.BEARISH, (0.5 - hurst) * 2
    else:
        signal, confidence = Signal.NEUTRAL, 0.5
    return _component(
        "stat_arb",
        signal,
        confidence,
        {"hurst_exponent": hurst, "skewness": skew, "kurtosis": kurt},
    )


def score_technicals(ticker: str, prices: Sequence[Price]) -> QuantScore:
    """Weighted ensemble of the five strategies over bars sorted oldest first."""
    if len(prices) < 2:
        return no_data(Domain.TECHNICALS, ticker, "fewer than two price bars")
    components = [
        trend(prices),
        mean_reversion(prices),
        momentum(prices),
        volatility(prices),
        stat_arb(prices),
    ]
    signal, strength = weighted_combination(
        (item.signal, WEIGHTS[item.name], (item.confidence or 0) / 100) for item in components
    )
    gaps = [
        f"{name} needs {needed} bars, got {len(prices)}"
        for name, needed in REQUIRED_BARS.items()
        if len(prices) < needed
    ]
    return QuantScore(
        analyst=Domain.TECHNICALS,
        ticker=ticker,
        signal=signal,
        confidence=round(strength * 100),
        components=components,
        data_gaps=gaps,
    )
