"""Separate chat from analysis requests: a deterministic gate, then the intake model schema."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from fin_graph.domain.requests.model import AnalysisRequest, Horizon, RequestSource, unique_tickers

_TRIVIAL_MESSAGES = frozenset(
    {
        "ok", "okay", "k", "thanks", "thank you", "thx", "ty", "hi", "hello", "hey", "hola",
        "gracias", "vale", "listo", "yes", "no", "si", "sí", "got it", "cool", "nice",
        "great", "perfect", "ack", "done", "buenas", "buenos dias", "buenas tardes",
    }
)  # fmt: skip
_NOT_WORDS = re.compile(r"[\W_]+")


def is_trivial_message(text: str) -> bool:
    """True for an empty message, bare punctuation/emoji, or a one-word ack or greeting."""
    words = _NOT_WORDS.sub(" ", text).strip().lower()
    return not words or words in _TRIVIAL_MESSAGES


class RequestExtraction(BaseModel):
    """What the intake model reads out of a message that named no ticker the parser knew."""

    is_noise: bool = Field(
        description=(
            "True only for greetings, thanks or chat that asks for no analysis. Any question "
            "about a company, stock, fund or market is not noise."
        )
    )
    tickers: list[str] = Field(
        default_factory=list, description="Ticker symbols, for example AAPL or BRK-B."
    )
    horizon: Horizon = Field(default=Horizon.MEDIUM, description="Investment horizon asked for.")
    question: str = Field(default="", description="The user's question in one sentence.")


def request_from_extraction(
    extraction: RequestExtraction, text: str, *, now: datetime | None = None
) -> AnalysisRequest | None:
    """The request the model found, or ``None`` when it found noise or no valid ticker."""
    if extraction.is_noise:
        return None
    try:
        tickers = unique_tickers(extraction.tickers)
    except ValueError:
        return None
    if not tickers:
        return None
    return AnalysisRequest(
        tickers=tickers[:10],
        as_of=(now or datetime.now(UTC)).astimezone(UTC).date(),
        source=RequestSource.TEXT,
        horizon=extraction.horizon,
        question=(extraction.question or text).strip()[:2000],
    )
