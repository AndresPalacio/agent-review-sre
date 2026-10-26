import json

from typer.testing import CliRunner

from fin_graph.cli.main import app


def test_record_replay_cli(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    answer = tmp_path / "answer.json"
    snapshot.write_text(
        json.dumps(
            {
                "alert": {
                    "source": "generic",
                    "title": "service failed",
                    "fingerprint": "recorded-fingerprint",
                },
                "incident_window": {
                    "since": "2026-10-08T00:00:00Z",
                    "until": "2026-10-08T01:00:00Z",
                    "source": "override",
                },
                "evidence": [
                    {
                        "id": "e1",
                        "integration": "prometheus",
                        "tool": "read_metrics",
                        "args": {"service": "app"},
                        "data": {"count": 1},
                        "domains": ["metrics"],
                        "evidence_type": "metrics",
                        "summary": "count",
                        "collected_at": "2026-10-08T00:10:00Z",
                    }
                ],
            }
        )
    )
    answer.write_text('{"root_cause_category":"unknown"}')
    directory = tmp_path / "recorded"
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "record",
            str(directory),
            "--snapshot-file",
            str(snapshot),
            "--answer-file",
            str(answer),
            "--reviewer",
            "operator",
        ],
    )
    assert result.exit_code == 0, result.output
    output = tmp_path / "results.json"
    result = CliRunner().invoke(app, ["eval", "replay", str(directory), "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["baseline_eligible"] is False


def test_compare_scripted_refused_cli(tmp_path):
    path = tmp_path / "result.json"
    path.write_text('{"model":"scripted","baseline_eligible":false}')
    result = CliRunner().invoke(app, ["eval", "compare", str(path), str(path)])
    assert result.exit_code == 2
