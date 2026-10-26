"""Time-series arithmetic in plain Python, matching pandas semantics where the analysts rely on it.

Every function takes and returns lists aligned with the input; positions without enough history
are ``nan``, as pandas leaves them. Rolling windows need a full window (pandas ``min_periods``
defaults to the window), sample statistics use ``ddof=1``, and ``ewm_mean`` reproduces pandas'
``ewm(span=..., adjust=...)`` including how it skips missing values.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

NAN = float("nan")

type Series = list[float]


def is_nan(value: float) -> bool:
    return math.isnan(value)


def last(values: Sequence[float]) -> float | None:
    """The final value when it is a finite number, else ``None``."""
    if not values or not math.isfinite(values[-1]):
        return None
    return values[-1]


def diff(values: Sequence[float], lag: int = 1) -> Series:
    """``x[t] - x[t - lag]``."""
    return [NAN] * min(lag, len(values)) + [
        values[index] - values[index - lag] for index in range(lag, len(values))
    ]


def pct_change(values: Sequence[float]) -> Series:
    """Simple returns ``x[t] / x[t - 1] - 1``; ``nan`` after a zero price."""
    out: Series = [NAN] if values else []
    for index in range(1, len(values)):
        previous = values[index - 1]
        out.append(values[index] / previous - 1 if previous else NAN)
    return out


def _rolling(
    values: Sequence[float], window: int, reduce: Callable[[list[float]], float]
) -> Series:
    out: Series = []
    for index in range(len(values)):
        if index + 1 < window:
            out.append(NAN)
            continue
        chunk = list(values[index + 1 - window : index + 1])
        out.append(NAN if any(map(is_nan, chunk)) else reduce(chunk))
    return out


def _mean(chunk: list[float]) -> float:
    return sum(chunk) / len(chunk)


def _std(chunk: list[float]) -> float:
    if len(chunk) < 2:
        return NAN
    mean = _mean(chunk)
    return math.sqrt(sum((value - mean) ** 2 for value in chunk) / (len(chunk) - 1))


def _skew(chunk: list[float]) -> float:
    """Bias-corrected sample skewness (pandas ``skew``)."""
    n = len(chunk)
    if n < 3:
        return NAN
    mean = _mean(chunk)
    m2 = sum((value - mean) ** 2 for value in chunk) / n
    m3 = sum((value - mean) ** 3 for value in chunk) / n
    if m2 == 0:
        return 0.0
    return math.sqrt(n * (n - 1)) / (n - 2) * m3 / (m2 * math.sqrt(m2))


def _kurt(chunk: list[float]) -> float:
    """Bias-corrected excess kurtosis (pandas ``kurt``)."""
    n = len(chunk)
    if n < 4:
        return NAN
    mean = _mean(chunk)
    m2 = sum((value - mean) ** 2 for value in chunk) / n
    m4 = sum((value - mean) ** 4 for value in chunk) / n
    if m2 == 0:
        return 0.0
    g2 = m4 / m2**2 - 3
    return ((n + 1) * g2 + 6) * (n - 1) / ((n - 2) * (n - 3))


def rolling_mean(values: Sequence[float], window: int) -> Series:
    return _rolling(values, window, _mean)


def rolling_std(values: Sequence[float], window: int) -> Series:
    return _rolling(values, window, _std)


def _sum(chunk: list[float]) -> float:
    return float(sum(chunk))


def rolling_sum(values: Sequence[float], window: int) -> Series:
    return _rolling(values, window, _sum)


def rolling_skew(values: Sequence[float], window: int) -> Series:
    return _rolling(values, window, _skew)


def rolling_kurt(values: Sequence[float], window: int) -> Series:
    return _rolling(values, window, _kurt)


def ewm_mean(values: Sequence[float], span: int, *, adjust: bool) -> Series:
    """pandas ``ewm(span=span, adjust=adjust).mean()`` with the default ``ignore_na=False``."""
    alpha = 2 / (span + 1)
    out: Series = []
    if adjust:
        numerator = denominator = 0.0
        for value in values:
            numerator *= 1 - alpha
            denominator *= 1 - alpha
            if not is_nan(value):
                numerator += value
                denominator += 1
            out.append(numerator / denominator if denominator else NAN)
        return out
    current = NAN
    pending = 1.0
    for value in values:
        if is_nan(value):
            if not is_nan(current):
                pending *= 1 - alpha
            out.append(current)
            continue
        if is_nan(current):
            current = value
        else:
            # Absolute-position weights: skipped rows keep decaying the old mean.
            old_weight = pending * (1 - alpha)
            current = (old_weight * current + alpha * value) / (old_weight + alpha)
        pending = 1.0
        out.append(current)
    return out


def combine(
    left: Sequence[float], right: Sequence[float], op: Callable[[float, float], float]
) -> Series:
    """Element-wise ``op``; ``nan`` where either side is missing or ``op`` divides by zero."""
    out: Series = []
    for a, b in zip(left, right, strict=True):
        if is_nan(a) or is_nan(b):
            out.append(NAN)
            continue
        try:
            out.append(op(a, b))
        except ZeroDivisionError:
            out.append(NAN if a == 0 else math.copysign(math.inf, a) * math.copysign(1, b))
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> Series:
    """``max(high - low, |high - prev close|, |low - prev close|)``; the first bar is high - low."""
    out: Series = []
    for index in range(len(close)):
        spread = high[index] - low[index]
        if index == 0:
            out.append(spread)
            continue
        previous = close[index - 1]
        out.append(max(spread, abs(high[index] - previous), abs(low[index] - previous)))
    return out


def atr(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14
) -> Series:
    """Average true range: the ``period``-bar mean of the true range."""
    return rolling_mean(true_range(high, low, close), period)


def rsi(close: Sequence[float], period: int = 14) -> Series:
    """RSI over simple averages of gains and losses (ai-hedge-fund's variant, not Wilder's)."""
    delta = diff(close)
    gains = [value if value > 0 else 0.0 for value in delta]
    losses = [-value if value < 0 else 0.0 for value in delta]
    average_gain, average_loss = rolling_mean(gains, period), rolling_mean(losses, period)
    out: Series = []
    for gain, loss in zip(average_gain, average_loss, strict=True):
        if is_nan(gain) or is_nan(loss) or (gain == 0 and loss == 0):
            out.append(NAN)
        elif loss == 0:
            out.append(100.0)
        else:
            out.append(100 - 100 / (1 + gain / loss))
    return out


def adx(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14
) -> Series:
    """Average directional index smoothed with pandas' adjusted EWM, as ai-hedge-fund does."""
    tr = true_range(high, low, close)
    plus_dm: Series = [0.0]
    minus_dm: Series = [0.0]
    for index in range(1, len(close)):
        up = high[index] - high[index - 1]
        down = low[index - 1] - low[index]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    smoothed_tr = ewm_mean(tr, period, adjust=True)
    plus_di = combine(ewm_mean(plus_dm, period, adjust=True), smoothed_tr, lambda a, b: 100 * a / b)
    minus_di = combine(
        ewm_mean(minus_dm, period, adjust=True), smoothed_tr, lambda a, b: 100 * a / b
    )
    dx = combine(plus_di, minus_di, lambda a, b: 100 * abs(a - b) / (a + b))
    return ewm_mean(dx, period, adjust=True)


def bollinger(close: Sequence[float], window: int = 20) -> tuple[Series, Series]:
    """Upper and lower bands at two sample standard deviations."""
    mean, std = rolling_mean(close, window), rolling_std(close, window)
    upper = combine(mean, std, lambda m, s: m + 2 * s)
    lower = combine(mean, std, lambda m, s: m - 2 * s)
    return upper, lower


def linear_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Least-squares slope of ``ys`` on ``xs`` (``numpy.polyfit(..., 1)[0]``)."""
    n = len(xs)
    if n < 2:
        return NAN
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread == 0:
        return NAN
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / spread


def hurst_exponent(values: Sequence[float], max_lag: int = 20) -> float | None:
    """Hurst exponent from how the spread of lagged differences grows with the lag.

    Below 0.5 the series mean-reverts, near 0.5 it is a random walk, above 0.5 it trends.
    ai-hedge-fund subtracts two pandas slices, which align on their index and cancel to zero,
    and takes a square root that halves the slope; both are corrected here.
    """
    lags: list[float] = []
    spreads: list[float] = []
    for lag in range(2, max_lag):
        if lag >= len(values):
            break
        differences = [values[index] - values[index - lag] for index in range(lag, len(values))]
        mean = sum(differences) / len(differences)
        spread = math.sqrt(sum((value - mean) ** 2 for value in differences) / len(differences))
        lags.append(math.log(lag))
        spreads.append(math.log(max(spread, 1e-8)))
    if len(lags) < 3:
        return None
    slope = linear_slope(lags, spreads)
    return None if is_nan(slope) else slope
