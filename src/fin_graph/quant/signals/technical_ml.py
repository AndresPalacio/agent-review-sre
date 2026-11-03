"""Random-forest next-session direction alpha model."""

from __future__ import annotations

from typing import Any

from fin_graph.quant.signals.base import JsonValue
from fin_graph.quant.signals.trainable import TrainableAlphaModel


class TechnicalMomentumModel(TrainableAlphaModel):
    feature_columns = (
        "sma_10",
        "sma_50",
        "ema_12",
        "ema_26",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_diff",
        "atr_14",
        "bb_pband",
        "bb_wband",
        "obv",
    )

    def __init__(self, *, n_estimators: int = 200, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.n_estimators = n_estimators

    @property
    def name(self) -> str:
        return "technical_ml"

    def _fit_and_score(
        self, x_train: Any, y_train: Any, x_test: Any, y_test: Any, seed: int
    ) -> tuple[Any, float | None, float | None]:
        from sklearn.ensemble import RandomForestClassifier  # type: ignore[import-untyped]
        from sklearn.metrics import (  # type: ignore[import-untyped]
            accuracy_score,
            precision_score,
        )

        model = RandomForestClassifier(
            n_estimators=self.n_estimators,
            max_depth=6,
            min_samples_leaf=20,
            random_state=seed,
            n_jobs=1,
        )
        model.fit(x_train, y_train)
        accuracy = precision = None
        if len(x_test) >= self.minimum_test_rows:
            predicted = model.predict(x_test)
            accuracy = round(float(accuracy_score(y_test, predicted)), 4)
            precision = round(float(precision_score(y_test, predicted, zero_division=0)), 4)
        return model, accuracy, precision

    def _predict_probability(self, bundle: Any, latest: Any) -> float | None:
        classes = list(bundle.classes_)
        if 1 not in classes:
            return None
        return float(bundle.predict_proba(latest)[0, classes.index(1)])

    def _top_features(self, bundle: Any) -> list[tuple[str, float]]:
        return sorted(
            zip(self.feature_columns, bundle.feature_importances_, strict=True),
            key=lambda item: -item[1],
        )

    def _parameters(self, requested: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {"n_estimators": self.n_estimators, **super()._parameters(requested)}
