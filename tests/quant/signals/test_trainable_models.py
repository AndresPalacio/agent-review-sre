from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from fin_graph.domain.market import Price
from fin_graph.quant.artifacts import (
    ArtifactCompatibilityError,
    ArtifactCorruptionError,
    ArtifactStore,
    TrainingRequest,
)
from fin_graph.quant.signals.technical_ml import TechnicalMomentumModel
from fin_graph.quant.signals.technical_scan import TechnicalScanModel
from fin_graph.quant.signals.trainable import TrainingDataError


class Data:
    def __init__(self, prices: list[Price]) -> None:
        self.rows = prices

    async def prices(self, ticker: str, start: date, end: date):
        return [row for row in self.rows if start <= row.day <= end]

    async def financial_metrics(self, ticker: str, as_of: date, **kwargs):
        return []

    async def line_items(self, ticker: str, names, as_of: date, **kwargs):
        return []

    async def insider_trades(self, ticker: str, as_of: date, **kwargs):
        return []

    async def company_news(self, ticker: str, as_of: date, **kwargs):
        return []

    async def market_cap(self, ticker: str, as_of: date):
        return None


def synthetic_prices(count: int, *, adjusted: bool = True) -> list[Price]:
    first = date(2024, 1, 1)
    result = []
    for index in range(count):
        close = 100 + index * 0.05 + 3 * math.sin(index / 10)
        result.append(
            Price(
                time=datetime.combine(first + timedelta(days=index), datetime.min.time(), UTC),
                open=close - 0.1,
                high=close + 0.3,
                low=close - 0.3,
                close=close * (2 if adjusted else 1),
                adj_close=close if adjusted else None,
                volume=1_000_000 + index,
            )
        )
    return result


def request(prices: list[Price], *, seed: int = 42) -> TrainingRequest:
    return TrainingRequest(
        ticker="CASE",
        start_date=prices[0].day,
        end_date=prices[-1].day,
        seed=seed,
        parameters={},
    )


@pytest.mark.parametrize(
    "model",
    [
        TechnicalMomentumModel(min_history_days=80, n_estimators=30),
        TechnicalScanModel(min_history_days=80, n_estimators=10),
    ],
)
async def test_train_publish_load_and_predict_are_repeatable(tmp_path: Path, model) -> None:
    prices = synthetic_prices(180)
    data = Data(prices)
    store = ArtifactStore(tmp_path / model.name)

    artifact = await model.train(request(prices, seed=7), data, store)
    first = await model.predict("CASE", prices[-1].day, data, artifact=artifact)
    second = await model.predict("CASE", prices[-1].day, data, artifact=artifact)

    assert first == second
    assert -1 <= first.value <= 1
    assert first.metadata["trained_through"] == prices[-1].day.isoformat()
    assert artifact.seed == 7
    assert artifact.model_name == model.name
    assert artifact.feature_names
    assert Path(artifact.payload_path).is_file()  # noqa: ASYNC240
    assert Path(artifact.manifest_path).is_file()  # noqa: ASYNC240


async def test_training_rejects_nonfinite_data_without_partial_artifact(tmp_path: Path) -> None:
    prices = synthetic_prices(180)
    prices[100] = prices[100].model_copy(update={"adj_close": math.nan})
    store = ArtifactStore(tmp_path)

    with pytest.raises(TrainingDataError, match="finite"):
        await TechnicalMomentumModel(min_history_days=80).train(
            request(prices), Data(prices), store
        )

    assert not [path for path in tmp_path.rglob("*") if path.is_file()]  # noqa: ASYNC240


async def test_corrupt_artifact_fails_loud(tmp_path: Path) -> None:
    prices = synthetic_prices(180)
    store = ArtifactStore(tmp_path)
    artifact = await TechnicalMomentumModel(min_history_days=80, n_estimators=10).train(
        request(prices), Data(prices), store
    )
    Path(artifact.payload_path).write_bytes(b"not-a-joblib-artifact")  # noqa: ASYNC240

    with pytest.raises(ArtifactCorruptionError):
        ArtifactStore.load(artifact)


async def test_incompatible_artifact_is_rejected_before_prediction(tmp_path: Path) -> None:
    prices = synthetic_prices(180)
    data = Data(prices)
    artifact = await TechnicalMomentumModel(min_history_days=80, n_estimators=10).train(
        request(prices), data, ArtifactStore(tmp_path)
    )

    with pytest.raises(ArtifactCompatibilityError, match="mismatch"):
        await TechnicalScanModel(min_history_days=80).predict(
            "CASE", prices[-1].day, data, artifact=artifact
        )


async def test_insufficient_samples_fail_training_but_predict_abstains(tmp_path: Path) -> None:
    prices = synthetic_prices(40)
    model = TechnicalMomentumModel(min_history_days=80, n_estimators=10)

    with pytest.raises(TrainingDataError, match="insufficient"):
        await model.train(request(prices), Data(prices), ArtifactStore(tmp_path))

    signal = await model.predict("CASE", prices[-1].day, Data(prices))
    assert signal.value == 0
