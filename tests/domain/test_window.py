from __future__ import annotations

from datetime import date

import pytest

from fin_graph.domain.window import ANALYSIS_LOOKBACKS_DAYS, AnalysisWindow

AS_OF = date(2026, 10, 7)


def test_analysis_window_defaults_to_one_year() -> None:
    window = AnalysisWindow(as_of=AS_OF)

    assert window.lookback_days == 365
    assert window.start == date(2025, 10, 7)
    assert window.contains(window.start)
    assert window.contains(AS_OF)


def test_analysis_window_expands_from_one_to_three_to_five_years() -> None:
    one_year = AnalysisWindow(as_of=AS_OF)
    three_years = one_year.expanded()
    five_years = three_years.expanded()

    assert ANALYSIS_LOOKBACKS_DAYS == (365, 1095, 1825)
    assert three_years.lookback_days == 1095
    assert five_years.lookback_days == 1825
    assert five_years.expanded() is five_years


def test_analysis_window_rejects_nonpositive_history() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        AnalysisWindow(as_of=AS_OF, lookback_days=0)


def test_analysis_window_round_trips_through_json() -> None:
    window = AnalysisWindow(as_of=AS_OF, lookback_days=1095)

    assert AnalysisWindow.model_validate(window.model_dump(mode="json")) == window
