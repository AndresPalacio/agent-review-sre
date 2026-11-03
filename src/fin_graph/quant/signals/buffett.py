"""Warren Buffett-style structured alpha persona (educational approximation)."""

from __future__ import annotations

from fin_graph.quant.signals.llm_agent import LLMAlphaModel


class BuffettAlphaModel(LLMAlphaModel):
    @property
    def name(self) -> str:
        return "buffett"

    def system_prompt(self) -> str:
        return (
            "You are an educational approximation of Warren Buffett evaluating one company as "
            "a long-term owner. Use only the supplied filed snapshot. Assess moat, stable "
            "returns, margins, capital allocation, leverage, valuation, and ten-year prospects. "
            "Never invent data or use knowledge after as_of. Return the required structured "
            "signal, confidence, and concise reasoning."
        )
