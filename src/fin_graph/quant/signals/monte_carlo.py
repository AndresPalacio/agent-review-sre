"""Fixed-seed GBM forecaster with the frozen v2 signal contract."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, cast

from fin_graph.quant.data import MarketDataClient, adjusted_close
from fin_graph.quant.signals.base import QuantModel, Signal

_CALENDAR_DAYS_PER_TRADING_DAY = 1.6
_FETCH_PADDING_DAYS = 60
_TEST_FRACTION = 0.2
_MIN_TEST_ROWS = 5
_RELIABILITY_RELATIVE_RMSE_CEILING = 0.03


class MonteCarloAlphaModel(QuantModel):
    """Reference-compatible point-in-time Monte Carlo price forecast."""

    def __init__(
        self,
        *,
        n_sims: int = 10_000,
        seed: int = 42,
        forecast_horizon_days: int = 5,
        retrain_interval_days: int = 5,
        min_history_days: int = 150,
        signal_scale: float = 25.0,
        simulations: int | None = None,
        horizon_days: int | None = None,
    ) -> None:
        # The aliases keep the first replacement preview source-compatible.
        if simulations is not None:
            n_sims = simulations
        if horizon_days is not None:
            forecast_horizon_days = horizon_days
        if n_sims <= 0 or forecast_horizon_days <= 0 or min_history_days <= 1:
            raise ValueError("simulation, horizon, and history sizes must be positive")
        self.n_sims = n_sims
        self.seed = seed
        self.forecast_horizon_days = forecast_horizon_days
        self.retrain_interval_days = retrain_interval_days
        self.min_history_days = min_history_days
        self.signal_scale = signal_scale
        self._cache: dict[str, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "montecarlo"

    async def predict(self, ticker: str, as_of: date, data: MarketDataClient) -> Signal:
        import numpy as np

        window_days = int(self.min_history_days * _CALENDAR_DAYS_PER_TRADING_DAY)
        window_days += _FETCH_PADDING_DAYS
        prices = await data.prices(ticker, as_of - timedelta(days=window_days), as_of)
        prices = sorted(prices, key=lambda item: item.time)
        closes = np.asarray([adjusted_close(item) for item in prices], dtype=float)
        if len(closes) < self.min_history_days:
            return self._neutral(ticker, as_of)
        if not np.isfinite(closes).all() or (closes <= 0).any():
            raise ValueError("price history must contain only finite positive closes")

        record = self._cache.get(ticker)
        if record is None or not self._is_fresh(as_of, record["trained_through"]):
            record = self._train(as_of, closes)
            self._cache[ticker] = record

        last_close = float(closes[-1])
        if record["trained_through"] == as_of:
            forecast_path = record["forecast_path"]
        else:
            forecast_path = self._mean_path(
                record["model_bundle"], last_close, self.forecast_horizon_days
            )
        if not forecast_path:
            return self._neutral(ticker, as_of)

        expected_return = (forecast_path[-1] - last_close) / last_close
        value = math.tanh(expected_return * self.signal_scale)
        baseline = float(record["historical_avg_return"])
        edge = expected_return - baseline
        label = self._confidence_label(value)
        rmse = record["rmse"]
        relative_rmse = rmse / last_close if rmse is not None else None
        reliable = relative_rmse is not None and relative_rmse <= _RELIABILITY_RELATIVE_RMSE_CEILING
        rmse_note = f", holdout RMSE {rmse:.2f}" if rmse is not None else ""
        reliability_note = (
            ""
            if relative_rmse is None or reliable
            else f" -- high forecast uncertainty (holdout error {relative_rmse:.1%} of price)"
        )
        reasoning = (
            f"[{label}] {self.forecast_horizon_days}-day forecast "
            f"{forecast_path[0]:.2f} -> {forecast_path[-1]:.2f} "
            f"({expected_return:+.2%} vs last close {last_close:.2f}, "
            f"baseline {baseline:+.2%}, edge {edge:+.2%}); trained on "
            f"{record['n_train']} rows through {as_of.isoformat()}{rmse_note}"
            f"{reliability_note}"
        )
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=value,
            reasoning=reasoning,
            components={"expected_return": expected_return},
            metadata={
                "forecast_horizon_days": self.forecast_horizon_days,
                "trained_through": record["trained_through"].isoformat(),
                "n_training_rows": record["n_train"],
                "n_test_rows": record["n_test"],
                "rmse": rmse,
                "last_close": last_close,
                "forecast_path": [round(float(item), 4) for item in forecast_path],
                "expected_return": round(expected_return, 6),
                "confidence_label": label,
                "historical_avg_return": round(baseline, 6),
                "edge_vs_baseline": round(edge, 6),
                "reliable": reliable,
                "seed": self.seed,
                "simulations": self.n_sims,
            },
        )

    def _train(self, as_of: date, closes: Any) -> dict[str, Any]:
        import numpy as np

        split_index = int(len(closes) * (1 - _TEST_FRACTION))
        train = closes[:split_index]
        test = closes[split_index:]
        bundle = self._estimate_moments(train)
        rmse = None
        if len(test) >= _MIN_TEST_ROWS:
            holdout = self._mean_path(bundle, float(train[-1]), len(test))
            rmse = float(np.sqrt(np.mean((np.asarray(holdout) - test) ** 2)))
        forecast = self._mean_path(
            bundle, float(test[-1] if len(test) else train[-1]), self.forecast_horizon_days
        )
        return {
            "trained_through": as_of,
            "model_bundle": bundle,
            "n_train": len(train),
            "n_test": len(test),
            "rmse": rmse,
            "forecast_path": forecast,
            "historical_avg_return": self._historical_average_return(closes),
        }

    @staticmethod
    def _estimate_moments(closes: Any) -> dict[str, float]:
        import numpy as np

        log_returns = np.diff(np.log(closes))
        return {
            "mu": float(log_returns.mean()),
            "sigma": float(log_returns.std(ddof=1)),
        }

    def _mean_path(self, bundle: dict[str, float], anchor: float, horizon: int) -> list[float]:
        import numpy as np

        generator = np.random.default_rng(self.seed)
        shocks = generator.standard_normal((self.n_sims, horizon))
        increments = bundle["mu"] - 0.5 * bundle["sigma"] ** 2
        paths = anchor * np.exp(np.cumsum(increments + bundle["sigma"] * shocks, axis=1))
        return cast(list[float], paths.mean(axis=0).tolist())

    def _historical_average_return(self, closes: Any) -> float:
        horizon = self.forecast_horizon_days
        if len(closes) <= horizon:
            return 0.0
        returns = (closes[horizon:] - closes[:-horizon]) / closes[:-horizon]
        return float(returns.mean())

    def _is_fresh(self, as_of: date, trained_through: date) -> bool:
        days = (as_of - trained_through).days
        return 0 <= days <= self.retrain_interval_days

    def _neutral(self, ticker: str, as_of: date) -> Signal:
        return Signal(model_name=self.name, ticker=ticker, as_of=as_of, value=0)

    @staticmethod
    def _confidence_label(value: float) -> str:
        magnitude = abs(value)
        if magnitude < 0.15:
            return "no signal"
        if magnitude < 0.4:
            return "weak"
        if magnitude < 0.7:
            return "moderate"
        return "strong"
