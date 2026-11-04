"""Point-in-time windows shared by financial analyses."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

#: The history the analysts read, widened in this order by ``expanded``: 1, 3 and 5 years.
ANALYSIS_LOOKBACKS_DAYS = (365, 1095, 1825)


class AnalysisWindow(BaseModel):
    """The closed day range ``[start, as_of]`` an analysis reads, point in time.

    Nothing after ``as_of`` is ever read, so a replay or a backtest sees what an analyst
    could have seen that day.
    """

    model_config = ConfigDict(frozen=True)

    as_of: date
    lookback_days: int = ANALYSIS_LOOKBACKS_DAYS[0]

    @model_validator(mode="after")
    def _positive(self) -> Self:
        if self.lookback_days < 1:
            raise ValueError("an analysis window needs at least one day of history")
        return self

    @property
    def start(self) -> date:
        """The first day inside the window."""
        return self.as_of - timedelta(days=self.lookback_days)

    def contains(self, day: date) -> bool:
        """Whether ``day`` falls inside the window (both ends included)."""
        return self.start <= day <= self.as_of

    def expanded(self) -> AnalysisWindow:
        """The next longer lookback (1y → 3y → 5y); ``self`` once at five years."""
        longer = [days for days in ANALYSIS_LOOKBACKS_DAYS if days > self.lookback_days]
        if not longer:
            return self
        return AnalysisWindow(as_of=self.as_of, lookback_days=longer[0])
