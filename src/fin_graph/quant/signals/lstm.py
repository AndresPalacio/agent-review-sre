"""Lazy TensorFlow LSTM forecaster with scaled sliding windows."""

from __future__ import annotations

from contextlib import suppress
from datetime import date
from typing import Any, cast

from fin_graph.quant.signals.trainable import ForecastingAlphaModel, dependency_versions


class DependencyUnavailableError(RuntimeError):
    """An explicitly optional model dependency is not installed."""


def load_tensorflow() -> Any:
    try:
        import tensorflow as tensorflow  # type: ignore[import-untyped]
    except (ImportError, OSError) as exc:
        raise DependencyUnavailableError(
            "TensorFlow is unavailable; install fin-graph[ml] or use the supported app container"
        ) from exc
    return tensorflow


def build_windows(closes: Any, window: int, scaler: Any) -> tuple[Any, Any]:
    import numpy as np

    values = np.asarray(closes, dtype=float)
    scaled = scaler.transform(values.reshape(-1, 1)).flatten()
    inputs = np.asarray([scaled[index : index + window] for index in range(len(scaled) - window)])
    targets = scaled[window:]
    return inputs.reshape(-1, window, 1), targets


def recursive_forecast(
    model: Any, scaler: Any, closes: Any, *, window: int, horizon: int
) -> list[float]:
    import numpy as np

    values = np.asarray(closes, dtype=float)
    history = list(scaler.transform(values[-window:].reshape(-1, 1)).flatten())
    forecast: list[float] = []
    for _ in range(horizon):
        inputs = np.asarray(history[-window:]).reshape(1, window, 1)
        prediction = float(model.predict(inputs, verbose=0)[0, 0])
        forecast.append(prediction)
        history.append(prediction)
    restored = scaler.inverse_transform(np.asarray(forecast).reshape(-1, 1)).flatten()
    return cast(list[float], restored.tolist())


class LSTMForecastModel(ForecastingAlphaModel):
    def __init__(
        self,
        *,
        lookback_window: int = 20,
        units: int = 50,
        epochs: int = 10,
        batch_size: int = 32,
        dropout: float = 0.2,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.lookback_window = lookback_window
        self.units = units
        self.epochs = epochs
        self.batch_size = batch_size
        self.dropout = dropout

    @property
    def name(self) -> str:
        return "lstm"

    def _fit_and_forecast(
        self, train: Any, test: Any, horizon: int, seed: int
    ) -> tuple[Any, float | None, list[float]]:
        import numpy as np
        from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

        tensorflow = load_tensorflow()
        tensorflow.keras.utils.set_random_seed(seed)
        with suppress(AttributeError, RuntimeError):
            tensorflow.config.experimental.enable_op_determinism()
        scaler = StandardScaler().fit(train.to_numpy().reshape(-1, 1))
        model = self._build_model(tensorflow)
        inputs, targets = build_windows(train.to_numpy(), self.lookback_window, scaler)
        if not len(inputs):
            raise ValueError("insufficient history for the configured LSTM lookback")
        model.fit(
            inputs,
            targets,
            epochs=self.epochs,
            batch_size=self.batch_size,
            verbose=0,
        )
        rmse = None
        if len(test) >= self.minimum_test_rows:
            holdout = recursive_forecast(
                model,
                scaler,
                train.to_numpy(),
                window=self.lookback_window,
                horizon=len(test),
            )
            rmse = float(np.sqrt(np.mean((np.asarray(holdout) - test.to_numpy()) ** 2)))
        full = np.concatenate([train.to_numpy(), test.to_numpy()])
        forecast = recursive_forecast(
            model, scaler, full, window=self.lookback_window, horizon=horizon
        )
        bundle = {
            "model_json": model.to_json(),
            "weights": model.get_weights(),
            "scaler": scaler,
        }
        return bundle, rmse, forecast

    def _extend_and_forecast(
        self, bundle: Any, frame: Any, trained_through: date, horizon: int, seed: int
    ) -> list[float]:
        tensorflow = load_tensorflow()
        model = tensorflow.keras.models.model_from_json(bundle["model_json"])
        model.set_weights(bundle["weights"])
        return recursive_forecast(
            model,
            bundle["scaler"],
            frame["close"].to_numpy(),
            window=self.lookback_window,
            horizon=horizon,
        )

    def _build_model(self, tensorflow: Any) -> Any:
        model = tensorflow.keras.Sequential(
            [
                tensorflow.keras.layers.Input(shape=(self.lookback_window, 1)),
                tensorflow.keras.layers.LSTM(self.units, return_sequences=True),
                tensorflow.keras.layers.Dropout(self.dropout),
                tensorflow.keras.layers.LSTM(self.units, return_sequences=True),
                tensorflow.keras.layers.Dropout(self.dropout),
                tensorflow.keras.layers.LSTM(self.units),
                tensorflow.keras.layers.Dropout(self.dropout),
                tensorflow.keras.layers.Dense(1),
            ]
        )
        model.compile(optimizer="adam", loss="mean_squared_error")
        return model

    def _dependency_versions(self) -> dict[str, str]:
        return dependency_versions("numpy", "pandas", "scikit-learn", "tensorflow", "joblib")
