"""NYSE regular trading hours: when a paper market order may fill and which day it counts for.

Regular session 09:30-16:00 America/New_York, Monday to Friday, without the exchange holidays.
Holidays follow the NYSE rules: fixed dates observed on Friday when they fall on a Saturday and
on Monday when they fall on a Sunday, with one exception: New Year's Day on a Saturday is not
observed. Juneteenth counts from 2022. Early closes (13:00 on some eves) are not modelled: an
order placed after 13:00 on those days fills at the next open in practice, and here it fills
at the paper quote, which is the last close.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Monday = 0) of the month; ``n = -1`` is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date | None:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def nyse_holidays(year: int) -> frozenset[date]:
    """The full-day NYSE closures of ``year``."""
    fixed = [date(year, 7, 4), date(year, 12, 25)]
    if year >= 2022:
        fixed.append(date(year, 6, 19))
    days = {observed for day in fixed if (observed := _observed(day)) is not None}
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # a Saturday New Year's Day is not observed on Friday
        days.add(new_year + timedelta(days=1) if new_year.weekday() == 6 else new_year)
    days |= {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    }
    return frozenset(days)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def exchange_time(now: datetime) -> datetime:
    """``now`` in New York time; a naive value is taken as UTC."""
    aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return aware.astimezone(EXCHANGE_TZ)


def session_date(now: datetime) -> date:
    """The exchange calendar day ``now`` belongs to (daily order limits count by it)."""
    return exchange_time(now).date()


def is_market_open(now: datetime) -> bool:
    """Whether ``now`` falls inside a regular session."""
    local = exchange_time(now)
    return is_trading_day(local.date()) and OPEN <= local.time() < CLOSE


def next_open(now: datetime) -> datetime:
    """The start of the next regular session at or after ``now`` (UTC)."""
    local = exchange_time(now)
    day = local.date()
    if local.time() >= OPEN:
        day += timedelta(days=1)
    if is_market_open(now):
        return now.astimezone(UTC)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return datetime.combine(day, OPEN, tzinfo=EXCHANGE_TZ).astimezone(UTC)
