from __future__ import annotations

from langsmith.utils import tracing_is_enabled

from fin_graph.config.tracing import disable_external_tracing


def test_disabling_beats_a_tracing_environment(tracing_enabled_by_environment: None) -> None:
    disable_external_tracing()

    assert not tracing_is_enabled()
