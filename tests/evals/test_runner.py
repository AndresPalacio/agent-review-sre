from __future__ import annotations

import json
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import Field
from typer.testing import CliRunner

from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.cli.main import app
from sre_graph.domain.alerts.parsing import parse_alert
from sre_graph.domain.investigation import SpecialistTask
from sre_graph.domain.tools import Domain
from sre_graph.domain.window import resolve_window
from sre_graph.evals.fixtures import FixtureBackend
from sre_graph.evals.runner import run_suite
from sre_graph.evals.scenarios import load_scenario


class WindowReadingModel(ScriptedChatModel):
    """Provider double that issues real tools using only the model's incident window."""

    windows: list[dict] = Field(default_factory=list)
    observations: list[dict] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        payload = json.loads(
            next(item.content for item in messages if isinstance(item, HumanMessage))
        )
        if "available_domains" in payload:
            response = tool_call_message(
                "InvestigationPlan",
                {
                    "hypotheses": [],
                    "tasks": [{"domain": "metrics", "objective": "Inspect ReplicaLag"}],
                },
                "plan",
            )
        elif "task" in payload:
            observations = [item for item in messages if isinstance(item, ToolMessage)]
            if observations:
                self.observations.append(json.loads(observations[-1].content))
                response = tool_call_message(
                    "SpecialistFinding",
                    {"domain": "metrics", "summary": "Metric read", "confidence": 0.0},
                    "finding",
                )
            else:
                window = payload["window"]
                self.windows.append(window)
                response = tool_call_message(
                    "aws_metric_data",
                    {
                        "namespace": "AWS/RDS",
                        "metric_name": "ReplicaLag",
                        "start": int(datetime.fromisoformat(window["since"]).timestamp()),
                        "end": int(datetime.fromisoformat(window["until"]).timestamp()),
                    },
                    "read",
                )
        else:
            response = tool_call_message(
                "Diagnosis",
                {
                    "triage_summary": "Metric inspected",
                    "status": {
                        "confirmed": "metric",
                        "open": "cause",
                        "next": "review",
                        "owner": "SRE",
                    },
                    "root_cause": "Unknown",
                    "category": "unknown",
                    "confidence": 0.0,
                },
                "diagnosis",
            )
        self._queue.append(response)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


async def test_non_scripted_evaluation_delivers_historical_samples_to_the_model(
    tmp_path, monkeypatch
) -> None:
    scenario = load_scenario("rds_postgres", "001-replication-lag")
    model = WindowReadingModel(responses=[])
    monkeypatch.setattr("sre_graph.evals.runner.load_suite", lambda suite: [scenario])
    monkeypatch.setattr("sre_graph.evals.runner._chat_model", lambda name: model)

    result = await run_suite("rds", "offline:window-reader", 1, tmp_path)

    assert result["runs"][0]["status"] == "completed", result["runs"]
    assert model.observations
    assert model.observations[0]["MetricDataResults"][0]["Values"] == [
        1.25,
        1.21,
        74.0,
        151.0,
        263.0,
        388.0,
        491.0,
        582.0,
        664.0,
        738.0,
        812.0,
        889.0,
        962.0,
        1047.0,
        1140.0,
    ]
    assert model.windows[0]["until"].startswith("2026-03-26T11:35:")


async def test_scripted_fixture_reader_uses_the_graph_window() -> None:
    from sre_graph.evals.runner import ScriptedServices

    scenario = load_scenario("rds_postgres", "001-replication-lag")
    backend = FixtureBackend(scenario)
    _, evidence = await ScriptedServices(scenario).investigate(
        SpecialistTask(domain=Domain.METRICS, objective="Inspect recorded metric"),
        parse_alert(scenario.alert),
        resolve_window(None, now=datetime(2026, 10, 8, tzinfo=UTC)),
        backend.registry.tools(backend.context),
        backend.context,
    )

    assert evidence[0].data["MetricDataResults"][0]["Values"] == []


async def test_scripted_smoke_runs_graph_offline_and_writes_artifacts(tmp_path) -> None:
    result = await run_suite("smoke", "scripted", 2, tmp_path)
    assert result["baseline_eligible"] is False
    assert len(result["runs"]) == 6
    assert all(run["status"] == "completed" for run in result["runs"])
    assert all(run["score"]["llm_judge_status"] == "not_run" for run in result["runs"])
    assert all(run["deliveries"] == [] for run in result["runs"])
    assert (tmp_path / "results.json").is_file()
    assert "scripted" in (tmp_path / "report.md").read_text(encoding="utf-8")


async def test_runner_records_blocked_run_without_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await run_suite("smoke", "openai:gpt-4.1-mini", 1, tmp_path)
    assert len(result["runs"]) == 3
    assert all(run["status"] == "blocked" for run in result["runs"])
    assert all(run["score"] is None for run in result["runs"])
    assert result["baseline_eligible"] is False


async def test_judge_model_needs_credentials_and_scripted_mode_rejects_it(
    tmp_path, monkeypatch
) -> None:
    import pytest

    with pytest.raises(ValueError, match="scripted"):
        await run_suite("smoke", "scripted", 1, tmp_path, judge_model="openai:gpt-4.1-mini")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await run_suite(
        "smoke", "anthropic:some-model", 1, tmp_path, judge_model="openai:gpt-4.1-mini"
    )
    assert all(run["status"] == "blocked" for run in result["runs"])


async def test_one_graph_failure_does_not_abort_other_scenarios(tmp_path, monkeypatch) -> None:
    from sre_graph.evals.runner import ScriptedServices

    original = ScriptedServices.investigate

    async def fail_one(self, *args, **kwargs):
        if self.scenario.scenario_id == "001-oomkilled-crashloop":
            raise RuntimeError("simulated fixture read failure")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ScriptedServices, "investigate", fail_one)
    result = await run_suite("smoke", "scripted", 1, tmp_path)
    assert [run["status"] for run in result["runs"]] == ["completed", "failed", "completed"]


def test_eval_cli_run_smoke(tmp_path) -> None:
    command = CliRunner().invoke(
        app,
        ["eval", "run", "--suite", "smoke", "--model", "scripted", "--output-dir", str(tmp_path)],
    )
    assert command.exit_code == 0, command.output
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["suite"] == "smoke"


async def test_scripted_all_is_wiring_only_and_covers_every_case(tmp_path):
    result = await run_suite("all", "scripted", 1, tmp_path)
    assert len(result["runs"]) == 36
    assert all(run["status"] == "completed" for run in result["runs"]), result["runs"]
    assert result["baseline_eligible"] is False
    assert len(result["dataset_fingerprint"]) == 64


def test_dataset_fingerprint_detects_corpus_change_with_same_identity():
    from dataclasses import replace

    from sre_graph.evals.runner import dataset_fingerprint

    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    changed = replace(scenario, alert={**scenario.alert, "extra_observation": "changed"})
    assert changed.key == scenario.key
    assert dataset_fingerprint([scenario]) != dataset_fingerprint([changed])
