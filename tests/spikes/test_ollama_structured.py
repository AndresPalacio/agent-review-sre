"""Spike: llama3.2:3b on a local Ollama returns a small finance schema without the fallback.

Opt-in like the Postgres tests: set ``FIN_GRAPH_TEST_OLLAMA_URL`` (for example
``http://localhost:11434``). The test skips when the server or the model is missing.
"""

from __future__ import annotations

import os
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, Field

from fin_graph.config.settings import Settings
from fin_graph.llm.models import ModelRole, get_model
from fin_graph.llm.structured import structured_call

pytestmark = pytest.mark.ollama

# Read at import time: the autouse fixture clears OLLAMA_BASE_URL before each test.
_URL = os.environ.get("FIN_GRAPH_TEST_OLLAMA_URL", "")
_MODEL = "llama3.2:3b"


class TickerSignal(BaseModel):
    ticker: str = Field(description="Ticker symbol exactly as given")
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(ge=0, le=100, description="0-100")
    reasoning: str = Field(description="One sentence citing the metrics")


def _require_ollama() -> None:
    if not _URL:
        pytest.skip("FIN_GRAPH_TEST_OLLAMA_URL is not set")
    try:
        tags = httpx.get(f"{_URL.rstrip('/')}/api/tags", timeout=3).json()
    except (httpx.HTTPError, ValueError) as error:
        pytest.skip(f"Ollama is not reachable at {_URL}: {error}")
    if _MODEL not in {item.get("name") for item in tags.get("models", [])}:
        pytest.skip(f"{_MODEL} is not pulled on {_URL}")


async def test_llama_3b_returns_the_schema_natively() -> None:
    _require_ollama()
    settings = Settings(_env_file=None, llm_provider="ollama", ollama_base_url=_URL)
    model = get_model(ModelRole.WORKER, settings)

    result = await structured_call(
        model,
        TickerSignal,
        "You are a fundamentals analyst. Classify the ticker from the metrics given as data.",
        {
            "ticker": "AAPL",
            "return_on_equity": 1.47,
            "net_margin": 0.24,
            "debt_to_equity": 1.8,
            "revenue_growth_yoy": 0.02,
            "price_to_earnings": 33.5,
        },
        lambda reason: TickerSignal(
            ticker="AAPL", signal="neutral", confidence=0, reasoning=f"fallback: {reason}"
        ),
    )

    assert not result.degraded, result.errors
    assert result.value.ticker == "AAPL"
