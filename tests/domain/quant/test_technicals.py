from __future__ import annotations

import math

import pytest
from tests.domain.quant.synthetic import TECHNICAL_CASES, bars, raw_bars, reference

from fin_graph.domain.quant import series, technicals
from fin_graph.domain.quant.common import Signal

_INDICATORS = {
    "ema_21_last": lambda c, h, lo: series.last(series.ewm_mean(c, 21, adjust=False)),
    "rsi_14_last": lambda c, h, lo: series.last(series.rsi(c, 14)),
    "bb_upper_last": lambda c, h, lo: series.last(series.bollinger(c)[0]),
    "bb_lower_last": lambda c, h, lo: series.last(series.bollinger(c)[1]),
    "adx_last": lambda c, h, lo: series.last(series.adx(h, lo, c, 14)),
    "atr_last": lambda c, h, lo: series.last(series.atr(h, lo, c, 14)),
    "skew_last": lambda c, h, lo: series.last(series.rolling_skew(series.pct_change(c), 63)),
    "kurt_last": lambda c, h, lo: series.last(series.rolling_kurt(series.pct_change(c), 63)),
}


def _close_enough(mine: float | None, theirs: float | None) -> bool:
    if theirs is None or (isinstance(theirs, float) and math.isnan(theirs)):
        return mine is None
    return mine is not None and math.isclose(mine, theirs, rel_tol=1e-9, abs_tol=1e-9)


@pytest.mark.parametrize("case", sorted(TECHNICAL_CASES))
@pytest.mark.parametrize("indicator", sorted(_INDICATORS))
def test_indicators_match_pandas(case: str, indicator: str) -> None:
    prices = bars(**TECHNICAL_CASES[case])
    close = [bar.close for bar in prices]
    high = [bar.high for bar in prices]
    low = [bar.low for bar in prices]

    mine = _INDICATORS[indicator](close, high, low)

    assert _close_enough(mine, reference()["technicals"][case][indicator])


@pytest.mark.parametrize("case", sorted(TECHNICAL_CASES))
@pytest.mark.parametrize("strategy", ["trend", "mean_reversion", "momentum", "volatility"])
def test_strategies_match_the_original(case: str, strategy: str) -> None:
    expected = reference()["technicals"][case][strategy]

    component = getattr(technicals, strategy)(bars(**TECHNICAL_CASES[case]))

    assert component.signal.value == expected["signal"]
    assert component.confidence == round(min(max(expected["confidence"], 0), 1) * 100)
    for name, value in expected["metrics"].items():
        mine = component.metrics[name]
        # The original reports a missing indicator as 0.0; the port reports None.
        if mine is None and value == 0.0:
            continue
        assert _close_enough(mine, value), name


def test_the_original_hurst_is_always_zero_and_the_port_fixes_it() -> None:
    walk = bars()

    assert abs(reference()["technicals"]["walk"]["hurst_original"]) < 1e-12
    hurst = series.hurst_exponent([bar.close for bar in walk])
    assert hurst is not None and 0.4 < hurst < 0.6


def _integrated(phi: float) -> list[float]:
    """Prices whose increments follow AR(1) with coefficient ``phi`` over seeded noise."""
    noise = [math.log(bar["close"] / bar["open"]) for bar in raw_bars(n=600, seed=5)]
    prices, increment = [100.0], 0.0
    for shock in noise:
        increment = phi * increment + shock
        prices.append(prices[-1] + increment * 100)
    return prices


def test_hurst_separates_persistent_from_anti_persistent_series() -> None:
    walk, persistent, reverting = _integrated(0.0), _integrated(0.8), _integrated(-0.8)

    assert 0.45 < (series.hurst_exponent(walk) or 0) < 0.55
    assert (series.hurst_exponent(persistent) or 0) > 0.7
    assert (series.hurst_exponent(reverting) or 1) < 0.4
    assert series.hurst_exponent([1.0, 2.0]) is None


def test_ensemble_on_short_history_reports_every_gap() -> None:
    score = technicals.score_technicals("X", bars(n=40))

    assert {gap.split()[0] for gap in score.data_gaps} == set(technicals.REQUIRED_BARS)
    assert technicals.score_technicals("X", bars(n=1)).degraded


def test_ensemble_weights_strategy_confidence() -> None:
    score = technicals.score_technicals("AAPL", bars(n=320, drift=-0.004, vol=0.01))

    assert score.signal is Signal.BEARISH
    assert 0 < score.confidence <= 100
    assert [item.name for item in score.components] == list(technicals.WEIGHTS)


def test_ewm_adjusted_skips_missing_values_like_pandas() -> None:
    values = [1.0, series.NAN, 3.0]

    smoothed = series.ewm_mean(values, 3, adjust=True)

    # pandas: ewm(span=3).mean() → [1.0, 1.0, 2.6]
    assert smoothed[0] == 1.0 and smoothed[1] == 1.0
    assert math.isclose(smoothed[2], 2.6)
