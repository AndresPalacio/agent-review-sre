from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from fin_graph.integrations.context import RunContext


class FakeGraph:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.inputs: list[dict[str, Any]] = []

    async def astream(self, value: dict[str, Any], **kwargs: Any) -> Any:
        self.inputs.append(value)
        yield "custom", {"type": "stage", "data": {"stage": "intake"}}
        yield "values", self.state


def _state() -> dict[str, Any]:
    from tests.domain.test_analysis_report import _report

    return {"report": _report().model_dump(mode="json"), "is_noise": False}


def _invoke(monkeypatch: Any, args: list[str], state: dict[str, Any] | None = None) -> Any:
    from fin_graph.cli import main
    from fin_graph.runtime import analysis as runtime

    graph = FakeGraph(_state() if state is None else state)
    monkeypatch.setattr(runtime, "make_analysis_runtime", lambda: (graph, RunContext()))
    return CliRunner().invoke(main.app, ["analyze", *args]), graph


def test_analyze_tickers_streams_and_renders_the_report(monkeypatch: Any) -> None:
    result, graph = _invoke(
        monkeypatch, ["--tickers", "deep,dist", "--as-of", "2024-03-08", "--cash", "5000"]
    )

    assert result.exit_code == 0, result.output
    assert "intake" in result.output and "Análisis DEEP" in result.output
    sent = graph.inputs[0]
    assert sent["raw_request"] == {
        "tickers": "deep,dist",
        "horizon": "medium",
        "as_of": "2024-03-08",
    }
    assert sent["source_hint"] == "cli" and sent["portfolio"] == {"cash": 5000.0}


def test_analyze_text_and_json_output(monkeypatch: Any) -> None:
    result, graph = _invoke(monkeypatch, ["--text", "¿cómo ves DEEP?", "--json"])

    assert result.exit_code == 0, result.output
    assert '"sections"' in result.output and "intake" not in result.output
    assert graph.inputs[0]["raw_request"] == "¿cómo ves DEEP?"


def test_analyze_validates_its_options(monkeypatch: Any) -> None:
    both, _ = _invoke(monkeypatch, ["--tickers", "AAPL", "--text", "x"])
    horizon, _ = _invoke(monkeypatch, ["--tickers", "AAPL", "--horizon", "forever"])
    noise, _ = _invoke(monkeypatch, ["--text", "hola"], state={"is_noise": True})

    assert both.exit_code != 0 and horizon.exit_code != 0
    assert noise.exit_code == 1 and "ningún ticker" in noise.output
