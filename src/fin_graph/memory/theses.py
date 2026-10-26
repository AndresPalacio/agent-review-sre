"""Verified investment theses per ticker in LangGraph Store, with their realised track record.

A thesis is saved only after it passed verification. ``record_outcome`` later closes it at an
exit price, and ``track_record`` reports how often each stance was right. That is the feedback a
thesis review needs; it is never investment advice. The investor profile (risk tolerance and
watchlist) is stored beside the theses.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from langgraph.store.base import BaseStore

from fin_graph.domain.requests.model import normalize_ticker
from fin_graph.safety.secrets import redact_secrets

THESES = ("org", "theses")
PROFILE = ("org", "investor_profile")
#: A neutral thesis is right when the price moved less than this either way.
NEUTRAL_BAND = 0.05

type RiskProfile = Literal["conservative", "moderate", "aggressive"]


def thesis_key(run_id: str, ticker: str) -> str:
    """One thesis per run and ticker."""
    key = f"{run_id}:{normalize_ticker(ticker)}"
    if not run_id or len(key) > 160:
        raise ValueError("invalid thesis id")
    return key


def stance_was_right(stance: str, realised_return: float) -> bool:
    """Bullish needs a gain, bearish a loss, neutral a move inside ±5%."""
    if stance == "bullish":
        return realised_return > 0
    if stance == "bearish":
        return realised_return < 0
    return abs(realised_return) < NEUTRAL_BAND


class ThesisMemory:
    def __init__(self, store: BaseStore) -> None:
        self.store = store

    async def save_thesis(
        self,
        thesis_id: str,
        *,
        ticker: str,
        as_of: date,
        stance: str,
        category: str,
        confidence: float,
        entry_price: float | None,
        summary: str,
        evidence_ids: list[str],
    ) -> None:
        if not thesis_id or len(thesis_id) > 160:
            raise ValueError("invalid thesis id")
        await self.store.aput(
            THESES,
            thesis_id,
            {
                "id": thesis_id,
                "ticker": normalize_ticker(ticker),
                "as_of": as_of.isoformat(),
                "stance": stance,
                "category": category[:64],
                "confidence": round(float(confidence), 1),
                "entry_price": entry_price,
                "summary": redact_secrets(summary)[:1500],
                "evidence_ids": evidence_ids[:20],
                "outcome": None,
            },
        )

    async def search_theses(
        self, *, ticker: str | None = None, category: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Theses for a ticker and/or category, newest first."""
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        symbol = normalize_ticker(ticker) if ticker else None
        items = await self.store.asearch(THESES, limit=500)
        matches = [
            item.value
            for item in items
            if (symbol is None or item.value.get("ticker") == symbol)
            and (category is None or item.value.get("category") == category)
        ]
        matches.sort(
            key=lambda value: (str(value.get("as_of")), str(value.get("id"))), reverse=True
        )
        return matches[:limit]

    async def record_outcome(
        self, thesis_id: str, *, exit_price: float, as_of: date
    ) -> dict[str, Any]:
        """Close a thesis at ``exit_price``; returns the realised return and whether it held."""
        item = await self.store.aget(THESES, thesis_id)
        if item is None:
            raise KeyError(thesis_id)
        value = dict(item.value)
        entry = value.get("entry_price")
        if not isinstance(entry, int | float) or entry <= 0 or exit_price <= 0:
            raise ValueError("an outcome needs positive entry and exit prices")
        if as_of.isoformat() < str(value["as_of"]):
            raise ValueError("an outcome cannot predate its thesis")
        realised = exit_price / entry - 1
        value["outcome"] = {
            "as_of": as_of.isoformat(),
            "exit_price": exit_price,
            "return": round(realised, 4),
            "right": stance_was_right(str(value["stance"]), realised),
        }
        await self.store.aput(THESES, thesis_id, value)
        return dict(value["outcome"])

    async def track_record(self, *, ticker: str | None = None) -> dict[str, Any]:
        """Hit rate and average return per stance over the closed theses."""
        theses = await self.search_theses(ticker=ticker, limit=50)
        closed = [item for item in theses if item.get("outcome")]
        by_stance: dict[str, list[dict[str, Any]]] = {}
        for item in closed:
            by_stance.setdefault(str(item["stance"]), []).append(item["outcome"])
        return {
            "theses": len(theses),
            "closed": len(closed),
            "hit_rate": round(sum(item["outcome"]["right"] for item in closed) / len(closed), 3)
            if closed
            else None,
            "by_stance": {
                stance: {
                    "closed": len(outcomes),
                    "hit_rate": round(sum(o["right"] for o in outcomes) / len(outcomes), 3),
                    "avg_return": round(sum(o["return"] for o in outcomes) / len(outcomes), 4),
                }
                for stance, outcomes in sorted(by_stance.items())
            },
        }

    async def save_profile(self, *, risk_profile: RiskProfile, watchlist: list[str]) -> None:
        tickers = list(dict.fromkeys(normalize_ticker(item) for item in watchlist))[:50]
        await self.store.aput(
            PROFILE, "default", {"risk_profile": risk_profile, "watchlist": tickers}
        )

    async def profile(self) -> dict[str, Any]:
        item = await self.store.aget(PROFILE, "default")
        return dict(item.value) if item else {"risk_profile": "moderate", "watchlist": []}
