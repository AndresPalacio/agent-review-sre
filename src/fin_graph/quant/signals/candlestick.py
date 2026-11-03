"""Pure-Python port of the v2 classic candlestick pattern catalogue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from fin_graph.domain.market import Price
from fin_graph.quant.data import MarketDataClient
from fin_graph.quant.signals.base import QuantModel, Signal

_SINGLE = 0.5
_MULTI = 0.8
_TREND_LOOKBACK = 5
_TREND_THRESHOLD = 0.01

# The reference calls Doji an indecision observation; these are its eighteen directional patterns.
PATTERN_NAMES = frozenset(
    {
        "Hammer",
        "Hanging Man",
        "Inverted Hammer",
        "Shooting Star",
        "Marubozu",
        "Spinning Top",
        "Bullish Belt-hold",
        "Bearish Belt-hold",
        "Bullish Engulfing",
        "Bearish Engulfing",
        "Harami",
        "Harami Cross",
        "Piercing Pattern",
        "Dark Cloud Cover",
        "Morning Star",
        "Evening Star",
        "Three White Soldiers",
        "Three Black Crows",
    }
)


@dataclass(frozen=True)
class _Bar:
    open: float
    high: float
    low: float
    close: float


def _adjusted(price: Price) -> _Bar:
    ratio = price.adj_close / price.close if price.adj_close is not None and price.close else 1.0
    return _Bar(
        open=price.open * ratio,
        high=price.high * ratio,
        low=price.low * ratio,
        close=price.adj_close if price.adj_close is not None else price.close,
    )


class CandlestickPatternModel(QuantModel):
    def __init__(self, *, min_bars: int = 10) -> None:
        if min_bars < 3:
            raise ValueError("min_bars must be at least three")
        self.min_bars = min_bars

    @property
    def name(self) -> str:
        return "candlestick"

    async def predict(self, ticker: str, as_of: date, data: MarketDataClient) -> Signal:
        prices = await data.prices(ticker, as_of - timedelta(days=60), as_of)
        if len(prices) < self.min_bars:
            return Signal(model_name=self.name, ticker=ticker, as_of=as_of, value=0)
        bars = [_adjusted(item) for item in sorted(prices, key=lambda item: item.time)]
        matches = self.detect_patterns(bars)
        if not matches:
            return Signal(model_name=self.name, ticker=ticker, as_of=as_of, value=0)
        value = sum(item[1] for item in matches) / len(matches)
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=value,
            reasoning="; ".join(
                f"{name} ({'bullish' if score > 0 else 'bearish' if score < 0 else 'neutral'})"
                for name, score in matches
            ),
            metadata={"patterns": [name for name, _ in matches]},
        )

    def detect_patterns(self, bars: list[_Bar]) -> list[tuple[str, float]]:
        detectors: tuple[Callable[[list[_Bar]], tuple[str, float] | None], ...] = (
            self._doji,
            self._single_bar,
            self._marubozu,
            self._spinning_top,
            self._belt_hold,
            self._engulfing,
            self._harami,
            self._piercing_or_dark_cloud,
            self._star,
            self._three_soldiers_or_crows,
        )
        return [match for detector in detectors if (match := detector(bars)) is not None]

    @staticmethod
    def _doji(bars: list[_Bar]) -> tuple[str, float] | None:
        bar = bars[-1]
        span = bar.high - bar.low
        return ("Doji", 0.0) if span > 0 and abs(bar.close - bar.open) <= 0.1 * span else None

    def _single_bar(self, bars: list[_Bar]) -> tuple[str, float] | None:
        bar = bars[-1]
        span = bar.high - bar.low
        body = abs(bar.close - bar.open)
        if span <= 0 or body <= 0 or body > 0.4 * span:
            return None
        upper = bar.high - max(bar.open, bar.close)
        lower = min(bar.open, bar.close) - bar.low
        trend = self._prior_trend(bars, len(bars) - 1)
        if lower >= 2 * body and upper <= 0.3 * body:
            if trend == "down":
                return ("Hammer", _SINGLE)
            if trend == "up":
                return ("Hanging Man", -_SINGLE)
        if upper >= 2 * body and lower <= 0.3 * body:
            if trend == "down":
                return ("Inverted Hammer", _SINGLE)
            if trend == "up":
                return ("Shooting Star", -_SINGLE)
        return None

    @staticmethod
    def _marubozu(bars: list[_Bar]) -> tuple[str, float] | None:
        bar = bars[-1]
        span = bar.high - bar.low
        if span <= 0 or abs(bar.close - bar.open) < 0.9 * span:
            return None
        if bar.close > bar.open:
            return ("Marubozu", _SINGLE)
        if bar.close < bar.open:
            return ("Marubozu", -_SINGLE)
        return None

    @staticmethod
    def _spinning_top(bars: list[_Bar]) -> tuple[str, float] | None:
        bar = bars[-1]
        span = bar.high - bar.low
        body = abs(bar.close - bar.open)
        if span <= 0 or not 0.1 * span < body <= 0.3 * span:
            return None
        upper = bar.high - max(bar.open, bar.close)
        lower = min(bar.open, bar.close) - bar.low
        return ("Spinning Top", 0.0) if upper >= body and lower >= body else None

    @staticmethod
    def _belt_hold(bars: list[_Bar]) -> tuple[str, float] | None:
        bar = bars[-1]
        span = bar.high - bar.low
        body = abs(bar.close - bar.open)
        if span <= 0 or body < 0.6 * span:
            return None
        upper = bar.high - max(bar.open, bar.close)
        lower = min(bar.open, bar.close) - bar.low
        if bar.close > bar.open and lower <= 0.05 * span < upper:
            return ("Bullish Belt-hold", _SINGLE)
        if bar.close < bar.open and upper <= 0.05 * span < lower:
            return ("Bearish Belt-hold", -_SINGLE)
        return None

    @staticmethod
    def _engulfing(bars: list[_Bar]) -> tuple[str, float] | None:
        first, second = bars[-2:]
        if (
            first.close < first.open
            and second.close > second.open
            and second.open <= first.close
            and second.close >= first.open
        ):
            return ("Bullish Engulfing", _MULTI)
        if (
            first.close > first.open
            and second.close < second.open
            and second.open >= first.close
            and second.close <= first.open
        ):
            return ("Bearish Engulfing", -_MULTI)
        return None

    @staticmethod
    def _harami(bars: list[_Bar]) -> tuple[str, float] | None:
        first, second = bars[-2:]
        first_low, first_high = sorted((first.open, first.close))
        second_low, second_high = sorted((second.open, second.close))
        if not (second_low > first_low and second_high < first_high):
            return None
        span = second.high - second.low
        name = (
            "Harami Cross"
            if span > 0 and abs(second.close - second.open) <= 0.1 * span
            else "Harami"
        )
        if first.close < first.open:
            return (name, _MULTI)
        if first.close > first.open:
            return (name, -_MULTI)
        return None

    @staticmethod
    def _piercing_or_dark_cloud(bars: list[_Bar]) -> tuple[str, float] | None:
        first, second = bars[-2:]
        midpoint = (first.open + first.close) / 2
        if (
            first.close < first.open
            and second.close > second.open
            and second.open < first.close
            and midpoint < second.close < first.open
        ):
            return ("Piercing Pattern", _MULTI)
        if (
            first.close > first.open
            and second.close < second.open
            and second.open > first.close
            and first.open < second.close < midpoint
        ):
            return ("Dark Cloud Cover", -_MULTI)
        return None

    @staticmethod
    def _star(bars: list[_Bar]) -> tuple[str, float] | None:
        first, middle, last = bars[-3:]
        span = first.high - first.low
        first_body = abs(first.close - first.open)
        middle_body = abs(middle.close - middle.open)
        if span <= 0 or first_body < 0.6 * span or middle_body > 0.3 * first_body:
            return None
        midpoint = (first.open + first.close) / 2
        if first.close < first.open and last.close > last.open and last.close > midpoint:
            return ("Morning Star", _MULTI)
        if first.close > first.open and last.close < last.open and last.close < midpoint:
            return ("Evening Star", -_MULTI)
        return None

    @staticmethod
    def _three_soldiers_or_crows(bars: list[_Bar]) -> tuple[str, float] | None:
        first, second, third = bars[-3:]
        if (
            first.close > first.open
            and second.close > second.open
            and third.close > third.open
            and first.close < second.close < third.close
            and first.open < second.open < first.close
            and second.open < third.open < second.close
        ):
            return ("Three White Soldiers", _MULTI)
        if (
            first.close < first.open
            and second.close < second.open
            and third.close < third.open
            and first.close > second.close > third.close
            and first.open > second.open > first.close
            and second.open > third.open > second.close
        ):
            return ("Three Black Crows", -_MULTI)
        return None

    @staticmethod
    def _prior_trend(bars: list[_Bar], before: int) -> str:
        base_index = before - _TREND_LOOKBACK
        if base_index < 0:
            return "flat"
        base = bars[base_index].close
        recent = bars[before - 1].close if before else base
        if base <= 0:
            return "flat"
        change = (recent - base) / base
        if change <= -_TREND_THRESHOLD:
            return "down"
        if change >= _TREND_THRESHOLD:
            return "up"
        return "flat"
