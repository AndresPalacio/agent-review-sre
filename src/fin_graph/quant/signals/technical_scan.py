"""Scaled bagged-KNN technical scan alpha model."""

from __future__ import annotations

from typing import Any

from fin_graph.quant.signals.base import JsonValue
from fin_graph.quant.signals.trainable import TrainableAlphaModel


class TechnicalScanModel(TrainableAlphaModel):
    feature_columns = ("rsi_14", "cci_20", "roc_12", "volume")

    def __init__(self, *, n_neighbors: int = 5, n_estimators: int = 20, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.n_neighbors = n_neighbors
        self.n_estimators = n_estimators

    @property
    def name(self) -> str:
        return "technical_scan"

    def _fit_and_score(
        self, x_train: Any, y_train: Any, x_test: Any, y_test: Any, seed: int
    ) -> tuple[Any, float | None, float | None]:
        from sklearn.ensemble import BaggingClassifier  # type: ignore[import-untyped]
        from sklearn.metrics import (  # type: ignore[import-untyped]
            accuracy_score,
            precision_score,
        )
        from sklearn.neighbors import KNeighborsClassifier  # type: ignore[import-untyped]
        from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

        scaler = StandardScaler().fit(x_train)
        classifier = BaggingClassifier(
            estimator=KNeighborsClassifier(n_neighbors=self.n_neighbors),
            n_estimators=self.n_estimators,
            random_state=seed,
            n_jobs=1,
        )
        classifier.fit(scaler.transform(x_train), y_train)
        accuracy = precision = None
        if len(x_test) >= self.minimum_test_rows:
            predicted = classifier.predict(scaler.transform(x_test))
            accuracy = round(float(accuracy_score(y_test, predicted)), 4)
            precision = round(float(precision_score(y_test, predicted, zero_division=0)), 4)
        return (scaler, classifier), accuracy, precision

    def _predict_probability(self, bundle: Any, latest: Any) -> float | None:
        scaler, classifier = bundle
        classes = list(classifier.classes_)
        if 1 not in classes:
            return None
        scaled = scaler.transform(latest)
        return float(classifier.predict_proba(scaled)[0, classes.index(1)])

    def _parameters(self, requested: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            "n_neighbors": self.n_neighbors,
            "n_estimators": self.n_estimators,
            **super()._parameters(requested),
        }
