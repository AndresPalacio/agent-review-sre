"""Structured LLM alpha model over authoritative point-in-time fundamentals."""

from __future__ import annotations

from datetime import date
from typing import Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from fin_graph.llm.structured import structured_call
from fin_graph.quant.data import MarketDataClient
from fin_graph.quant.features import InsufficientData, build_fundamental_snapshot
from fin_graph.quant.signals.base import AlphaModel, Signal


class AlphaNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0, le=100)
    reasoning: str


class LLMAlphaModel(AlphaModel):
    def __init__(self, model: BaseChatModel) -> None:
        self.model = model

    def system_prompt(self) -> str:
        raise NotImplementedError

    async def predict(self, ticker: str, as_of: date, data: MarketDataClient) -> Signal:
        try:
            snapshot = await build_fundamental_snapshot(ticker, as_of, data)
        except InsufficientData as exc:
            return self._abstain(ticker, as_of, str(exc))

        result = await structured_call(
            self.model,
            AlphaNarrative,
            self.system_prompt(),
            snapshot.model_dump(mode="json"),
            lambda reason: AlphaNarrative(
                signal="neutral", confidence=0, reasoning=f"abstained: {reason}"
            ),
        )
        if result.degraded:
            return self._abstain(ticker, as_of, result.value.reasoning)
        sign = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}[result.value.signal]
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=sign * result.value.confidence / 100,
            reasoning=result.value.reasoning,
            metadata={
                "signal": result.value.signal,
                "confidence": result.value.confidence,
                "snapshot_hash": snapshot.content_hash,
                "structured_source": result.source,
                "abstained": False,
            },
        )

    def _abstain(self, ticker: str, as_of: date, reason: str) -> Signal:
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=0,
            reasoning=f"abstained: {reason}",
            metadata={"abstained": True, "abstain_reason": reason},
        )
