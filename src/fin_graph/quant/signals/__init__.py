"""Lazy registry of every public v2 alpha-model key."""

from __future__ import annotations

from importlib import import_module
from types import MappingProxyType

from fin_graph.quant.signals.base import AlphaModel, AlphaModelFactory, Signal


def _lazy(module: str, class_name: str) -> AlphaModelFactory:
    def create(**kwargs: object) -> AlphaModel:
        imported = import_module(f"fin_graph.quant.signals.{module}")
        model = getattr(imported, class_name)(**kwargs)
        if not isinstance(model, AlphaModel):
            raise TypeError(f"{class_name} is not an AlphaModel")
        return model

    return create


_REGISTRY = MappingProxyType(
    {
        "pead": _lazy("pead", "PEADModel"),
        "buffett": _lazy("buffett", "BuffettAlphaModel"),
        "candlestick": _lazy("candlestick", "CandlestickPatternModel"),
        "technical_ml": _lazy("technical_ml", "TechnicalMomentumModel"),
        "technical_scan": _lazy("technical_scan", "TechnicalScanModel"),
        "news_sentiment": _lazy("news_sentiment", "NewsSentimentModel"),
        "arima": _lazy("arima", "ARIMAForecastModel"),
        "lstm": _lazy("lstm", "LSTMForecastModel"),
        "montecarlo": _lazy("monte_carlo", "MonteCarloAlphaModel"),
    }
)


def alpha_model_registry() -> MappingProxyType[str, AlphaModelFactory]:
    return _REGISTRY


__all__ = ["AlphaModel", "Signal", "alpha_model_registry"]
