"""The incident time window every time-aware query shares."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
