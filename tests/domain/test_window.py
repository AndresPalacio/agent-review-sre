from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sre_graph.domain.window import (
    DEFAULT_LOOKBACK,
    MAX_LOOKBACK,
    IncidentWindow,
    WindowSource,
    resolve_window,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
STARTED = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def test_an_alert_start_anchors_the_window_with_a_forward_buffer() -> None:
    window = resolve_window(STARTED, now=NOW)

    assert window.until == STARTED + timedelta(minutes=10)
    assert window.since == window.until - DEFAULT_LOOKBACK
    assert window.source is WindowSource.ALERT


def test_without_a_start_the_window_ends_now() -> None:
    window = resolve_window(None, now=NOW)

    assert (window.since, window.until, window.source) == (
        NOW - DEFAULT_LOOKBACK,
        NOW,
        WindowSource.DEFAULT,
    )


def test_a_start_in_the_future_is_clamped_to_now() -> None:
    window = resolve_window(NOW + timedelta(hours=1), now=NOW)

    assert window.until == NOW


def test_lookback_is_kept_between_one_minute_and_seven_days() -> None:
    assert resolve_window(None, now=NOW, lookback=timedelta(days=30)).lookback == MAX_LOOKBACK
    assert resolve_window(None, now=NOW, lookback=timedelta(0)).lookback == timedelta(minutes=1)


def test_naive_inputs_are_read_as_utc() -> None:
    window = resolve_window(STARTED.replace(tzinfo=None), now=NOW.replace(tzinfo=None))

    assert window.until == STARTED + timedelta(minutes=10)


def test_other_timezones_are_normalized_to_utc() -> None:
    bogota = timezone(timedelta(hours=-5))
    window = IncidentWindow(
        since=datetime(2026, 10, 7, 5, 0, tzinfo=bogota),
        until=datetime(2026, 10, 7, 7, 0, tzinfo=bogota),
        source=WindowSource.OVERRIDE,
    )

    assert window.since == STARTED
    assert window.since.tzinfo == UTC


@pytest.mark.parametrize(
    ("since", "until"),
    [(NOW, NOW), (NOW, NOW - timedelta(minutes=1)), (NOW.replace(tzinfo=None), NOW + timedelta(1))],
)
def test_empty_inverted_or_naive_windows_are_rejected(since: datetime, until: datetime) -> None:
    with pytest.raises(ValueError):
        IncidentWindow(since=since, until=until, source=WindowSource.OVERRIDE)


def test_the_window_is_half_open() -> None:
    window = resolve_window(STARTED, now=NOW)

    assert window.contains(window.since)
    assert not window.contains(window.until)


def test_expanding_moves_only_the_start_and_stops_at_seven_days() -> None:
    window = resolve_window(STARTED, now=NOW)

    wider = window.expanded()

    assert wider.until == window.until
    assert wider.lookback == 2 * window.lookback
    capped = resolve_window(None, now=NOW, lookback=MAX_LOOKBACK)
    assert capped.expanded() is capped


def test_expanding_by_one_or_less_is_an_error() -> None:
    with pytest.raises(ValueError, match="greater than 1"):
        resolve_window(None, now=NOW).expanded(1.0)


def test_the_window_round_trips_through_json() -> None:
    window = resolve_window(STARTED, now=NOW)

    assert IncidentWindow.model_validate(window.model_dump(mode="json")) == window
