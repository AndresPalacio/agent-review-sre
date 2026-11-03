"""Shared point-in-time training and forecasting machinery."""

from __future__ import annotations

import math
from abc import abstractmethod
from datetime import date, timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

from fin_graph.quant.artifacts import (
    ArtifactCompatibilityError,
    ArtifactStore,
    ModelArtifact,
    TrainingRequest,
)
from fin_graph.quant.data import MarketDataClient
from fin_graph.quant.signals.base import JsonValue, QuantModel, Signal

_CLASSIFIER_PADDING_DAYS = 120
_FORECAST_PADDING_DAYS = 60
_CALENDAR_DAYS_PER_TRADING_DAY = 1.6
_TEST_FRACTION = 0.2
_RELIABILITY_ACCURACY_FLOOR = 0.55
_RELIABILITY_RMSE_CEILING = 0.03


class TrainingDataError(ValueError):
    """Training input is absent, insufficient, or numerically unsafe."""


def dependency_versions(*packages: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for package in packages:
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "unavailable"
    return result


def _confidence_label(value: float) -> str:
    magnitude = abs(value)
    if magnitude < 0.15:
        return "no signal"
    if magnitude < 0.4:
        return "weak"
    if magnitude < 0.7:
        return "moderate"
    return "strong"


def build_technical_frame(prices: list[Any]) -> Any:
    import numpy as np
    import pandas as pd  # type: ignore[import-untyped]
    import ta  # type: ignore[import-untyped]

    rows: list[dict[str, object]] = []
    for price in prices:
        ratio = (
            price.adj_close / price.close if price.adj_close is not None and price.close else 1.0
        )
        rows.append(
            {
                "time": price.time,
                "open": price.open * ratio,
                "high": price.high * ratio,
                "low": price.low * ratio,
                "close": price.adj_close if price.adj_close is not None else price.close,
                "volume": price.volume,
            }
        )
    frame = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    if frame.empty:
        return frame
    numeric = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise TrainingDataError("price history must contain finite OHLCV values")
    close, high, low, volume = (
        frame["close"],
        frame["high"],
        frame["low"],
        frame["volume"],
    )
    frame["sma_10"] = ta.trend.SMAIndicator(close, window=10).sma_indicator()
    frame["sma_50"] = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    frame["ema_12"] = ta.trend.EMAIndicator(close, window=12).ema_indicator()
    frame["ema_26"] = ta.trend.EMAIndicator(close, window=26).ema_indicator()
    frame["rsi_14"] = ta.momentum.RSIIndicator(close, window=14).rsi()
    macd = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    frame["macd"] = macd.macd()
    frame["macd_signal"] = macd.macd_signal()
    frame["macd_diff"] = macd.macd_diff()
    frame["atr_14"] = ta.volatility.AverageTrueRange(
        high, low, close, window=14
    ).average_true_range()
    bands = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    frame["bb_pband"] = bands.bollinger_pband()
    frame["bb_wband"] = bands.bollinger_wband()
    frame["obv"] = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    frame["cci_20"] = ta.trend.CCIIndicator(high, low, close, window=20).cci()
    frame["roc_12"] = ta.momentum.ROCIndicator(close, window=12).roc()
    return frame


class TrainableAlphaModel(QuantModel):
    feature_columns: tuple[str, ...] = ()
    algorithm_version = "1"
    minimum_test_rows = 5

    def __init__(self, *, retrain_interval_days: int = 20, min_history_days: int = 250) -> None:
        self.retrain_interval_days = retrain_interval_days
        self.min_history_days = min_history_days
        self._cache: dict[str, dict[str, Any]] = {}

    async def train(
        self, request: TrainingRequest, data: MarketDataClient, store: ArtifactStore
    ) -> ModelArtifact:
        frame = await self._features(
            request.ticker, request.start_date, request.end_date, data, fail=True
        )
        record = self._train_record(frame, request.end_date, request.seed)
        payload = {"kind": "classifier", "record": record}
        return store.publish(
            model_name=self.name,
            request=request,
            trained_through=request.end_date,
            algorithm_version=self.algorithm_version,
            feature_names=self.feature_columns,
            dependency_versions=dependency_versions(
                "numpy", "pandas", "scikit-learn", "ta", "joblib"
            ),
            parameters=self._parameters(request.parameters),
            payload=payload,
            validator=self._validate_payload,
        )

    async def predict(
        self,
        ticker: str,
        as_of: date,
        data: MarketDataClient,
        *,
        artifact: ModelArtifact | None = None,
    ) -> Signal:
        start = as_of - timedelta(
            days=int(self.min_history_days * _CALENDAR_DAYS_PER_TRADING_DAY)
            + _CLASSIFIER_PADDING_DAYS
        )
        try:
            frame = await self._features(ticker, start, as_of, data, fail=False)
        except TrainingDataError:
            raise
        if frame is None:
            return self._neutral(ticker, as_of)
        if artifact is not None:
            self._check_artifact(artifact, ticker, as_of)
            payload = ArtifactStore.load(artifact)
            self._validate_payload(payload)
            record = cast(dict[str, Any], payload["record"])
        else:
            cached = self._cache.get(ticker)
            if cached is None or not self._fresh(as_of, cast(date, cached["trained_through"])):
                try:
                    record = self._train_record(frame, as_of, 42)
                except TrainingDataError:
                    return self._neutral(ticker, as_of)
                self._cache[ticker] = record
            else:
                record = cached
        bundle = record["model_bundle"]
        if bundle is None:
            return self._neutral(ticker, as_of)
        latest = frame.iloc[[-1]][list(self.feature_columns)]
        probability = self._predict_probability(bundle, latest)
        if probability is None:
            return self._neutral(ticker, as_of)
        value = max(-1.0, min(1.0, 2 * probability - 1))
        base_rate = float(record["base_rate_up"])
        edge = probability - base_rate
        accuracy = record["accuracy"]
        label = _confidence_label(value)
        reliable = accuracy is not None and accuracy >= _RELIABILITY_ACCURACY_FLOOR
        top = self._top_features(bundle)
        feature_note = ""
        if top:
            feature_note = "; top features: " + ", ".join(
                f"{name} ({importance:.2f})" for name, importance in top[:3]
            )
        accuracy_note = f", holdout accuracy {accuracy:.2f}" if accuracy is not None else ""
        reliability_note = (
            ""
            if accuracy is None or reliable
            else " -- low historical reliability, barely above a coin flip"
        )
        trained = cast(date, record["trained_through"])
        reasoning = (
            f"[{label}] P(up)={probability:.2f} next session "
            f"(baseline {base_rate:.2f}, edge {edge:+.2f}){feature_note} "
            f"(trained on {record['n_train']} rows through {trained.isoformat()}"
            f"{accuracy_note}){reliability_note}"
        )
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=value,
            reasoning=reasoning,
            metadata={
                "probability_up": probability,
                "trained_through": trained.isoformat(),
                "n_training_rows": record["n_train"],
                "accuracy": accuracy,
                "precision": record["precision"],
                "confidence_label": label,
                "historical_base_rate_up": base_rate,
                "edge_vs_base_rate": round(edge, 4),
                "reliable": reliable,
                "seed": artifact.seed if artifact is not None else 42,
            },
        )

    async def _features(
        self,
        ticker: str,
        start: date,
        end: date,
        data: MarketDataClient,
        *,
        fail: bool,
    ) -> Any | None:
        prices = await data.prices(ticker, start, end)
        if not prices:
            if fail:
                raise TrainingDataError("insufficient price history: no bars")
            return None
        frame = build_technical_frame(prices)
        frame = frame.dropna(subset=list(self.feature_columns)).reset_index(drop=True)
        if len(frame) < self.min_history_days:
            if fail:
                raise TrainingDataError(
                    f"insufficient feature history: {len(frame)} rows; need {self.min_history_days}"
                )
            return None
        import numpy as np

        values = frame[list(self.feature_columns)].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise TrainingDataError("features must be finite")
        return frame

    def _train_record(self, frame: Any, trained_through: date, seed: int) -> dict[str, Any]:
        import pandas as pd

        features = frame.iloc[:-1][list(self.feature_columns)]
        current = frame["close"].iloc[:-1].to_numpy()
        future = frame["close"].shift(-1).iloc[:-1].to_numpy()
        labels = pd.Series((future > current).astype(int), index=features.index)
        base_rate = float(labels.mean())
        if labels.nunique() < 2:
            raise TrainingDataError("degenerate label set")
        split = int(len(features) * (1 - _TEST_FRACTION))
        if split <= 0 or split >= len(features):
            raise TrainingDataError("insufficient rows for chronological holdout")
        bundle, accuracy, precision = self._fit_and_score(
            features.iloc[:split],
            labels.iloc[:split],
            features.iloc[split:],
            labels.iloc[split:],
            seed,
        )
        return {
            "trained_through": trained_through,
            "model_bundle": bundle,
            "n_train": split,
            "n_test": len(features) - split,
            "accuracy": accuracy,
            "precision": precision,
            "base_rate_up": base_rate,
        }

    def _parameters(self, requested: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            "retrain_interval_days": self.retrain_interval_days,
            "min_history_days": self.min_history_days,
            **requested,
        }

    def _check_artifact(self, artifact: ModelArtifact, ticker: str, as_of: date) -> None:
        if artifact.model_name != self.name or artifact.ticker != ticker:
            raise ArtifactCompatibilityError("artifact model/ticker mismatch")
        if artifact.trained_through > as_of:
            raise ArtifactCompatibilityError("artifact was trained after prediction date")
        if artifact.feature_names != self.feature_columns:
            raise ArtifactCompatibilityError("artifact feature schema mismatch")

    @staticmethod
    def _validate_payload(payload: Any) -> None:
        if not isinstance(payload, dict) or payload.get("kind") != "classifier":
            raise ArtifactCompatibilityError("invalid classifier artifact payload")
        record = payload.get("record")
        if not isinstance(record, dict) or "model_bundle" not in record:
            raise ArtifactCompatibilityError("classifier artifact has no model bundle")

    def _fresh(self, as_of: date, trained_through: date) -> bool:
        days = (as_of - trained_through).days
        return 0 <= days <= self.retrain_interval_days

    def _neutral(self, ticker: str, as_of: date) -> Signal:
        return Signal(model_name=self.name, ticker=ticker, as_of=as_of, value=0)

    @abstractmethod
    def _fit_and_score(
        self, x_train: Any, y_train: Any, x_test: Any, y_test: Any, seed: int
    ) -> tuple[Any, float | None, float | None]: ...

    @abstractmethod
    def _predict_probability(self, bundle: Any, latest: Any) -> float | None: ...

    def _top_features(self, bundle: Any) -> list[tuple[str, float]] | None:
        return None


class ForecastingAlphaModel(QuantModel):
    algorithm_version = "1"
    minimum_test_rows = 5

    def __init__(
        self,
        *,
        forecast_horizon_days: int = 5,
        retrain_interval_days: int = 5,
        min_history_days: int = 150,
        signal_scale: float = 25,
    ) -> None:
        self.forecast_horizon_days = forecast_horizon_days
        self.retrain_interval_days = retrain_interval_days
        self.min_history_days = min_history_days
        self.signal_scale = signal_scale
        self._cache: dict[str, dict[str, Any]] = {}

    async def train(
        self, request: TrainingRequest, data: MarketDataClient, store: ArtifactStore
    ) -> ModelArtifact:
        frame = await self._price_frame(
            request.ticker, request.start_date, request.end_date, data, fail=True
        )
        record = self._train_record(frame, request.end_date, request.seed)
        payload = {"kind": "forecast", "record": record}
        return store.publish(
            model_name=self.name,
            request=request,
            trained_through=request.end_date,
            algorithm_version=self.algorithm_version,
            feature_names=("adjusted_close",),
            dependency_versions=self._dependency_versions(),
            parameters={
                "forecast_horizon_days": self.forecast_horizon_days,
                "retrain_interval_days": self.retrain_interval_days,
                "min_history_days": self.min_history_days,
                "signal_scale": self.signal_scale,
                **request.parameters,
            },
            payload=payload,
            validator=self._validate_payload,
        )

    async def predict(
        self,
        ticker: str,
        as_of: date,
        data: MarketDataClient,
        *,
        artifact: ModelArtifact | None = None,
    ) -> Signal:
        start = as_of - timedelta(
            days=int(self.min_history_days * _CALENDAR_DAYS_PER_TRADING_DAY)
            + _FORECAST_PADDING_DAYS
        )
        frame = await self._price_frame(ticker, start, as_of, data, fail=False)
        if frame is None:
            return self._neutral(ticker, as_of)
        if artifact is not None:
            if artifact.model_name != self.name or artifact.ticker != ticker:
                raise ArtifactCompatibilityError("artifact model/ticker mismatch")
            if artifact.trained_through > as_of:
                raise ArtifactCompatibilityError("artifact was trained after prediction date")
            payload = ArtifactStore.load(artifact)
            self._validate_payload(payload)
            record = cast(dict[str, Any], payload["record"])
            used_seed = artifact.seed
        else:
            cached = self._cache.get(ticker)
            if cached is None or not self._fresh(as_of, cast(date, cached["trained_through"])):
                record = self._train_record(frame, as_of, 42)
                self._cache[ticker] = record
            else:
                record = cached
            used_seed = 42
        last_close = float(frame["close"].iloc[-1])
        trained = cast(date, record["trained_through"])
        if trained == as_of:
            forecast = cast(list[float], record["forecast_path"])
        else:
            forecast = self._extend_and_forecast(
                record["model_bundle"], frame, trained, self.forecast_horizon_days, used_seed
            )
        if not forecast or last_close <= 0:
            return self._neutral(ticker, as_of)
        expected_return = (forecast[-1] - last_close) / last_close
        value = math.tanh(expected_return * self.signal_scale)
        baseline = float(record["historical_avg_return"])
        edge = expected_return - baseline
        label = _confidence_label(value)
        rmse = cast(float | None, record["rmse"])
        relative_rmse = rmse / last_close if rmse is not None else None
        reliable = relative_rmse is not None and relative_rmse <= _RELIABILITY_RMSE_CEILING
        rmse_note = f", holdout RMSE {rmse:.2f}" if rmse is not None else ""
        reliability_note = (
            ""
            if relative_rmse is None or reliable
            else f" -- high forecast uncertainty (holdout error {relative_rmse:.1%} of price)"
        )
        reasoning = (
            f"[{label}] {self.forecast_horizon_days}-day forecast {forecast[0]:.2f} -> "
            f"{forecast[-1]:.2f} ({expected_return:+.2%} vs last close {last_close:.2f}, "
            f"baseline {baseline:+.2%}, edge {edge:+.2%}); trained on "
            f"{record['n_train']} rows through {trained.isoformat()}{rmse_note}{reliability_note}"
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
                "trained_through": trained.isoformat(),
                "n_training_rows": record["n_train"],
                "n_test_rows": record["n_test"],
                "rmse": rmse,
                "last_close": last_close,
                "forecast_path": [round(float(item), 4) for item in forecast],
                "expected_return": round(expected_return, 6),
                "confidence_label": label,
                "historical_avg_return": round(baseline, 6),
                "edge_vs_baseline": round(edge, 6),
                "reliable": reliable,
                "seed": used_seed,
            },
        )

    async def _price_frame(
        self,
        ticker: str,
        start: date,
        end: date,
        data: MarketDataClient,
        *,
        fail: bool,
    ) -> Any | None:
        import numpy as np
        import pandas as pd

        prices = sorted(await data.prices(ticker, start, end), key=lambda item: item.time)
        if len(prices) < self.min_history_days:
            if fail:
                raise TrainingDataError(
                    f"insufficient price history: {len(prices)} rows; need {self.min_history_days}"
                )
            return None
        frame = pd.DataFrame(
            {
                "time": [item.time for item in prices],
                "close": [
                    item.adj_close if item.adj_close is not None else item.close for item in prices
                ],
            }
        )
        values = frame["close"].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise TrainingDataError("forecast closes must be finite and positive")
        return frame

    def _train_record(self, frame: Any, trained_through: date, seed: int) -> dict[str, Any]:
        closes = frame["close"].reset_index(drop=True)
        split = int(len(closes) * (1 - _TEST_FRACTION))
        train = closes.iloc[:split].reset_index(drop=True)
        test = closes.iloc[split:].reset_index(drop=True)
        bundle, rmse, forecast = self._fit_and_forecast(
            train, test, self.forecast_horizon_days, seed
        )
        horizon = self.forecast_horizon_days
        shifted = closes.shift(-horizon)
        returns = ((shifted - closes) / closes).dropna()
        baseline = float(returns.mean()) if len(returns) else 0.0
        return {
            "trained_through": trained_through,
            "model_bundle": bundle,
            "n_train": len(train),
            "n_test": len(test),
            "rmse": rmse,
            "forecast_path": forecast,
            "historical_avg_return": baseline,
        }

    @staticmethod
    def _validate_payload(payload: Any) -> None:
        if not isinstance(payload, dict) or payload.get("kind") != "forecast":
            raise ArtifactCompatibilityError("invalid forecast artifact payload")
        record = payload.get("record")
        if not isinstance(record, dict) or "forecast_path" not in record:
            raise ArtifactCompatibilityError("forecast artifact has no forecast path")

    def _fresh(self, as_of: date, trained_through: date) -> bool:
        days = (as_of - trained_through).days
        return 0 <= days <= self.retrain_interval_days

    def _neutral(self, ticker: str, as_of: date) -> Signal:
        return Signal(model_name=self.name, ticker=ticker, as_of=as_of, value=0)

    @abstractmethod
    def _fit_and_forecast(
        self, train: Any, test: Any, horizon: int, seed: int
    ) -> tuple[Any, float | None, list[float]]: ...

    @abstractmethod
    def _extend_and_forecast(
        self, bundle: Any, frame: Any, trained_through: date, horizon: int, seed: int
    ) -> list[float]: ...

    @abstractmethod
    def _dependency_versions(self) -> dict[str, str]: ...
