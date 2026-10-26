from __future__ import annotations

from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from fin_graph.domain.alerts.parsing import text_alert
from fin_graph.domain.diagnosis import unknown_diagnosis
from fin_graph.domain.report import build_report
from fin_graph.integrations.context import RunContext


class FakeGraph:
    def __init__(self) -> None:
        self.inputs: list[dict[str, Any]] = []

    async def astream(self, value: dict[str, Any], **kwargs: Any) -> Any:
        self.inputs.append(value)
        yield "custom", {"type": "stage", "data": {"stage": "intake"}}
        alert = text_alert("API CPU high")
        report = build_report(alert, unknown_diagnosis(alert, "fixture"), [])
        yield "values", {"report": report.model_dump(mode="json")}


def test_cli_investigate_text_streams_progress_and_renders_report(monkeypatch: Any) -> None:
    from fin_graph.cli import main

    graph = FakeGraph()
    monkeypatch.setattr(main, "make_runtime", lambda: (graph, RunContext()))
    result = CliRunner().invoke(main.app, ["investigate", "--text", "API CPU high"])
    assert result.exit_code == 0, result.output
    assert "intake" in result.output
    assert "API CPU high" in result.output
    assert graph.inputs[0]["user_requested"] is True
    assert graph.inputs[0]["destinations"] == {}


def test_cli_file_and_text_flags_are_exclusive(tmp_path: Path, monkeypatch: Any) -> None:
    from fin_graph.cli import main

    graph = FakeGraph()
    monkeypatch.setattr(main, "make_runtime", lambda: (graph, RunContext()))
    path = tmp_path / "alert.json"
    path.write_text('{"title":"CPU high"}', encoding="utf-8")
    runner = CliRunner()

    missing = runner.invoke(main.app, ["investigate"])
    both = runner.invoke(main.app, ["investigate", "--text", "CPU", "--file", str(path)])
    valid = runner.invoke(main.app, ["investigate", "--file", str(path)])

    assert missing.exit_code != 0
    assert both.exit_code != 0
    assert valid.exit_code == 0, valid.output
    assert graph.inputs[-1]["raw_alert"] == '{"title":"CPU high"}'
