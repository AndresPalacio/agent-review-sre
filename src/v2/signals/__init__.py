"""Historical alpha-model registry backed entirely by native implementations."""

from fin_graph.quant.signals.arima import ARIMAForecastModel
from fin_graph.quant.signals.base import AlphaModel, QuantModel
from fin_graph.quant.signals.buffett import BuffettAlphaModel as BuffettAgent
from fin_graph.quant.signals.candlestick import CandlestickPatternModel
from fin_graph.quant.signals.llm_agent import LLMAlphaModel as LLMAgent
from fin_graph.quant.signals.lstm import LSTMForecastModel
from fin_graph.quant.signals.monte_carlo import MonteCarloAlphaModel as MonteCarloForecastModel
from fin_graph.quant.signals.news_sentiment import NewsSentimentModel
from fin_graph.quant.signals.pead import PEADModel
from fin_graph.quant.signals.technical_ml import TechnicalMomentumModel
from fin_graph.quant.signals.technical_scan import TechnicalScanModel
from fin_graph.quant.signals.trainable import (
    ForecastingAlphaModel as ForecastingQuantModel,
)
from fin_graph.quant.signals.trainable import TrainableAlphaModel as TrainableQuantModel

ALPHA_MODEL_REGISTRY: dict[str, type[AlphaModel]] = {
    "pead": PEADModel,
    "buffett": BuffettAgent,
    "candlestick": CandlestickPatternModel,
    "technical_ml": TechnicalMomentumModel,
    "technical_scan": TechnicalScanModel,
    "news_sentiment": NewsSentimentModel,
    "arima": ARIMAForecastModel,
    "lstm": LSTMForecastModel,
    "montecarlo": MonteCarloForecastModel,
}
TRAINABLE_MODEL_REGISTRY = {
    key: value
    for key, value in ALPHA_MODEL_REGISTRY.items()
    if issubclass(value, (TrainableQuantModel, ForecastingQuantModel))
}

__all__ = [
    "ALPHA_MODEL_REGISTRY",
    "TRAINABLE_MODEL_REGISTRY",
    "ARIMAForecastModel",
    "AlphaModel",
    "BuffettAgent",
    "CandlestickPatternModel",
    "ForecastingQuantModel",
    "LLMAgent",
    "LSTMForecastModel",
    "MonteCarloForecastModel",
    "NewsSentimentModel",
    "PEADModel",
    "QuantModel",
    "TechnicalMomentumModel",
    "TechnicalScanModel",
    "TrainableQuantModel",
]
