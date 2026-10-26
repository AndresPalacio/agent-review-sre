"""The time windows queries share: the incident window and the point-in-time analysis window."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_LOOKBACK = timedelta(minutes=120)
DEFAULT_FORWARD_BUFFER = timedelta(minutes=10)
MAX_LOOKBACK = timedelta(days=7)
MIN_LOOKBACK = timedelta(minutes=1)


class WindowSource(StrEnum):
    """Where the window's end came from."""

    ALERT = "alert"
    DEFAULT = "default"
    OVERRIDE = "override"


class IncidentWindow(BaseModel):
    """A half-open ``[since, until)`` interval in UTC.

    Evidence stamped exactly at ``since`` is inside; at ``until`` it is not,
    matching the range semantics of Loki, Prometheus and Datadog queries.
    """

    model_config = ConfigDict(frozen=True)

    since: datetime
    until: datetime
    source: WindowSource

    @model_validator(mode="after")
    def _utc_and_ordered(self) -> Self:
        if self.since.tzinfo is None or self.until.tzinfo is None:
            raise ValueError("an incident window needs timezone-aware datetimes")
        since, until = self.since.astimezone(UTC), self.until.astimezone(UTC)
        if since >= until:
            raise ValueError("an incident window needs since < until")
        object.__setattr__(self, "since", since)
        object.__setattr__(self, "until", until)
        return self

    @property
    def lookback(self) -> timedelta:
        """How much time the window covers."""
        return self.until - self.since

    def contains(self, moment: datetime) -> bool:
        """Whether ``moment`` falls inside the window (half-open)."""
        return self.since <= moment.astimezone(UTC) < self.until

    def expanded(self, factor: float = 2.0) -> IncidentWindow:
        """The same window reaching ``factor`` times further back, at most 7 days.

        ``until`` stays put: widening admits the lookback guess was too short,
        not that the incident moved. Returns ``self`` when already at the cap.
        """
        if factor <= 1.0:
            raise ValueError("expanded() needs a factor greater than 1")
        lookback = min(self.lookback * factor, MAX_LOOKBACK)
        if lookback <= self.lookback:
            return self
        return IncidentWindow(since=self.until - lookback, until=self.until, source=self.source)


def resolve_window(
    started_at: datetime | None,
    *,
    now: datetime,
    lookback: timedelta = DEFAULT_LOOKBACK,
    forward_buffer: timedelta = DEFAULT_FORWARD_BUFFER,
) -> IncidentWindow:
    """The window for an alert that started at ``started_at`` (None: unknown start).

    With a start, the window ends ``forward_buffer`` after it (never after
    ``now``, which absorbs clock skew) and reaches ``lookback`` back. Without
    one it ends now. ``lookback`` is kept between 1 minute and 7 days.
    """
    current = (now if now.tzinfo else now.replace(tzinfo=UTC)).astimezone(UTC)
    reach = min(max(lookback, MIN_LOOKBACK), MAX_LOOKBACK)
    if started_at is None:
        return IncidentWindow(since=current - reach, until=current, source=WindowSource.DEFAULT)
    start = (started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)).astimezone(UTC)
    until = min(start + max(forward_buffer, timedelta(0)), current)
    return IncidentWindow(since=until - reach, until=until, source=WindowSource.ALERT)


#: The history the analysts read, widened in this order by ``refine``: 1, 3 and 5 years.
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
