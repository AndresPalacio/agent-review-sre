from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from sklearn.preprocessing import StandardScaler
from tests.quant.signals.test_trainable_models import Data, request, synthetic_prices

from fin_graph.quant.artifacts import ArtifactStore
from fin_graph.quant.signals.arima import ARIMAForecastModel
from fin_graph.quant.signals.lstm import (
    DependencyUnavailableError,
    LSTMForecastModel,
    build_windows,
    recursive_forecast,
)
from fin_graph.quant.signals.technical_ml import TechnicalMomentumModel
from fin_graph.quant.signals.technical_scan import TechnicalScanModel

REFERENCE = json.loads(
    (Path(__file__).parents[2] / "compat/fixtures/v2/signals/trainable/reference.json").read_text(
        encoding="utf-8"
    )
)


def reference_payload(signal):
    payload = signal.model_dump(mode="json")
    payload["date"] = payload.pop("as_of")
    payload["metadata"].pop("seed", None)
    return payload


async def test_arima_artifact_forecast_is_point_in_time_and_repeatable(tmp_path: Path) -> None:
    prices = synthetic_prices(180)
    data = Data(prices)
    model = ARIMAForecastModel(min_history_days=80, forecast_horizon_days=5)
    artifact = await model.train(request(prices, seed=11), data, ArtifactStore(tmp_path))

    first = await model.predict("CASE", prices[-1].day, data, artifact=artifact)
    second = await model.predict("CASE", prices[-1].day, data, artifact=artifact)

    assert first == second
    assert len(first.metadata["forecast_path"]) == 5
    assert first.metadata["trained_through"] <= prices[-1].day.isoformat()
    assert first.metadata["last_close"] == prices[-1].adj_close
    assert -1 <= first.value <= 1


@pytest.mark.parametrize(
    ("key", "model"),
    [
        (
            "technical_ml",
            TechnicalMomentumModel(min_history_days=100, n_estimators=30),
        ),
        (
            "technical_scan",
            TechnicalScanModel(min_history_days=100, n_estimators=10),
        ),
        ("arima", ARIMAForecastModel(min_history_days=100)),
    ],
)
async def test_recorded_reference_parity(tmp_path: Path, key: str, model) -> None:
    prices = synthetic_prices(220, adjusted=False)
    data = Data(prices)
    artifact = await model.train(request(prices), data, ArtifactStore(tmp_path / key))
    signal = await model.predict("CASE", prices[-1].day, data, artifact=artifact)

    assert reference_payload(signal) == REFERENCE[key]


def test_lstm_windows_use_scaled_sequences_and_scaled_targets() -> None:
    closes = np.arange(1.0, 9.0)
    scaler = StandardScaler().fit(closes.reshape(-1, 1))
    inputs, targets = build_windows(closes, 3, scaler)

    assert inputs.shape == (5, 3, 1)
    assert targets.shape == (5,)
    assert targets[0] == pytest.approx(scaler.transform([[4.0]])[0, 0])


def test_recursive_lstm_forecast_feeds_predictions_back() -> None:
    class IncrementModel:
        def predict(self, values, verbose=0):
            return np.asarray([[values[0, -1, 0] + 1]])

    scaler = StandardScaler().fit(np.asarray([[0.0], [1.0]]))
    result = recursive_forecast(
        IncrementModel(), scaler, np.asarray([0.0, 1.0]), window=2, horizon=3
    )

    assert result == pytest.approx([1.5, 2.0, 2.5])


async def test_lstm_dependency_error_is_typed_and_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fin_graph.quant.signals.lstm as module

    def unavailable():
        raise DependencyUnavailableError("install fin-graph[ml]")

    monkeypatch.setattr(module, "load_tensorflow", unavailable)
    prices = synthetic_prices(100)

    with pytest.raises(DependencyUnavailableError, match=r"\[ml\]"):
        await LSTMForecastModel(min_history_days=60, lookback_window=10, epochs=1).train(
            request(prices), Data(prices), ArtifactStore(tmp_path)
        )

    assert not [path for path in tmp_path.rglob("*") if path.is_file()]  # noqa: ASYNC240


async def test_lstm_ml_profile_trains_and_publishes_portable_bundle(tmp_path: Path) -> None:
    pytest.importorskip("tensorflow")
    prices = synthetic_prices(100, adjusted=False)
    data = Data(prices)
    model = LSTMForecastModel(
        min_history_days=60,
        forecast_horizon_days=2,
        lookback_window=10,
        units=2,
        epochs=1,
        batch_size=8,
        dropout=0,
    )

    artifact = await model.train(request(prices, seed=13), data, ArtifactStore(tmp_path))
    signal = await model.predict("CASE", prices[-1].day, data, artifact=artifact)

    assert artifact.dependency_versions["tensorflow"] != "unavailable"
    assert len(signal.metadata["forecast_path"]) == 2
    assert signal.metadata["seed"] == 13
