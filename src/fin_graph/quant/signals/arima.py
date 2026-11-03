"""Point-in-time ARIMA price forecast alpha model."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from fin_graph.quant.signals.trainable import ForecastingAlphaModel, dependency_versions


class ARIMAForecastModel(ForecastingAlphaModel):
    def __init__(self, *, order: tuple[int, int, int] = (5, 1, 0), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.order = order

    @property
    def name(self) -> str:
        return "arima"

    def _fit_and_forecast(
        self, train: Any, test: Any, horizon: int, seed: int
    ) -> tuple[Any, float | None, list[float]]:
        import numpy as np
        from statsmodels.tsa.arima.model import ARIMA  # type: ignore[import-untyped]

        results = ARIMA(train.to_numpy(), order=self.order).fit()
        rmse = None
        if len(test) >= self.minimum_test_rows:
            holdout = results.get_forecast(steps=len(test)).predicted_mean
            rmse = float(np.sqrt(np.mean((holdout - test.to_numpy()) ** 2)))
        extended = results.append(test.to_numpy(), refit=False) if len(test) else results
        forecast = extended.get_forecast(steps=horizon).predicted_mean.tolist()
        return extended, rmse, cast(list[float], forecast)

    def _extend_and_forecast(
        self, bundle: Any, frame: Any, trained_through: date, horizon: int, seed: int
    ) -> list[float]:
        closes = frame.loc[frame["time"].dt.date > trained_through, "close"].to_numpy()
        extended = bundle.append(closes, refit=False) if len(closes) else bundle
        return cast(list[float], extended.get_forecast(steps=horizon).predicted_mean.tolist())

    def _dependency_versions(self) -> dict[str, str]:
        return dependency_versions("numpy", "pandas", "statsmodels", "joblib")
