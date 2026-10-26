import pytest

from fin_graph.evals.recording import StrictReplay, load_recording, record_scenario


def snapshot():
    return {
        "alert": {
            "source": "generic",
            "title": "checkout latency",
            "severity": "warning",
            "fingerprint": "recorded-fingerprint",
        },
        "incident_window": {"start": "2026-10-08T00:00:00Z", "end": "2026-10-08T01:00:00Z"},
        "evidence": [
            {
                "id": "ev1",
                "tool": "read_metrics",
                "args": {"service": "checkout"},
                "data": {"value": 2, "token": "secret-value"},
                "integration": "prometheus",
                "domains": ["metrics"],
                "evidence_type": "metrics",
                "collected_at": "2026-10-08T00:05:00Z",
                "summary": "reading",
            }
        ],
    }


def test_record_roundtrip_and_redaction(tmp_path):
    path = tmp_path / "recording"
    record_scenario(snapshot(), {"root_cause_category": "unknown"}, path, reviewer="operator")
    recording = load_recording(path)
    assert "secret-value" not in (path / "scenario.json").read_text()
    replay = StrictReplay(recording)
    assert replay.call("read_metrics", {"service": "checkout"})["value"] == 2
    with pytest.raises(ValueError):
        replay.call("read_metrics", {"service": "other"})
    with pytest.raises(ValueError):
        record_scenario(snapshot(), {"root_cause_category": "unknown"}, path, reviewer="operator")


@pytest.mark.asyncio
async def test_replay_executes_tools_without_provider(tmp_path):
    path = tmp_path / "recording"
    record_scenario(snapshot(), {"root_cause_category": "unknown"}, path, reviewer="operator")
    replay = StrictReplay(load_recording(path))
    tool = replay.tools()[0]
    assert (await tool.ainvoke({"service": "checkout"}))["value"] == 2
    with pytest.raises(ValueError):
        await tool.ainvoke({"service": "other"})


def test_requires_actual_args_results_review(tmp_path):
    data = snapshot()
    del data["evidence"][0]["args"]
    with pytest.raises(ValueError):
        record_scenario(
            data, {"root_cause_category": "unknown"}, tmp_path / "x", reviewer="operator"
        )
    with pytest.raises(ValueError):
        record_scenario(snapshot(), {}, tmp_path / "y", reviewer="")


@pytest.mark.asyncio
async def test_replay_graph_results_are_explicitly_ineligible(tmp_path):
    from fin_graph.evals.recording import replay_graph

    path = tmp_path / "graph-recording"
    record_scenario(snapshot(), {"root_cause_category": "unknown"}, path, reviewer="operator")
    result = await replay_graph(load_recording(path))
    assert result["model"] == "scripted"
    assert result["baseline_eligible"] is False
    assert result["runs"][0]["status"] == "completed"
    assert result["runs"][0]["calls_executed"] >= 1


@pytest.mark.parametrize(
    "change", [{"domains": ["invalid"]}, {"collected_at": "no-date"}, {"evidence_type": "invalid"}]
)
def test_malformed_call_metadata_rejected_before_write(tmp_path, change):
    data = snapshot()
    data["evidence"][0].update(change)
    with pytest.raises(ValueError):
        record_scenario(
            data, {"root_cause_category": "unknown"}, tmp_path / "bad", reviewer="operator"
        )
    assert not (tmp_path / "bad").exists()


def test_bad_window_rejected_before_write(tmp_path):
    data = snapshot()
    data["incident_window"]["end"] = data["incident_window"]["start"]
    with pytest.raises(ValueError):
        record_scenario(
            data, {"root_cause_category": "unknown"}, tmp_path / "bad", reviewer="operator"
        )


@pytest.mark.asyncio
async def test_replay_name_cannot_be_overridden_in_arguments(tmp_path):
    path = tmp_path / "recording"
    record_scenario(snapshot(), {"root_cause_category": "unknown"}, path, reviewer="operator")
    tool = StrictReplay(load_recording(path)).tools()[0]
    with pytest.raises(ValueError):
        await tool.ainvoke({"service": "checkout", "_name": "read_metrics"})


@pytest.mark.asyncio
async def test_graph_replay_preserves_normalized_alert_and_exact_initial_window(tmp_path):
    from fin_graph.domain.investigation import InvestigationPlan
    from fin_graph.evals.recording import replay_graph
    from fin_graph.graphs.investigation.services import UnavailableServices

    data = snapshot()
    data["alert"].update(
        source="cloudwatch",
        fingerprint="exact-id",
        summary="actual summary",
        service="checkout",
        namespace="application",
        cluster="production",
        alert_count=7,
    )
    data["incident_window"] = {
        "since": "2026-10-07T00:00:00Z",
        "until": "2026-10-08T01:00:00Z",
        "source": "override",
    }
    path = tmp_path / "recording"
    record_scenario(data, {"root_cause_category": "unknown"}, path, reviewer="operator")
    seen = []

    class CaptureServices(UnavailableServices):
        async def plan(self, alert, window, *_):
            seen.append((alert, window))
            return InvestigationPlan(hypotheses=[], tasks=[])

    await replay_graph(load_recording(path), services=CaptureServices())
    assert seen[0][0].source.value == "cloudwatch"
    assert seen[0][0].fingerprint == "exact-id"
    assert seen[0][0].summary == "actual summary"
    assert seen[0][0].namespace == "application"
    assert seen[0][0].alert_count == 7
    assert seen[0][1].model_dump(mode="json") == data["incident_window"]
