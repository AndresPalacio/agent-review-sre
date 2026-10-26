from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("better_harness")
CONFIG = Path(__file__).resolve().parents[2] / "harness/fin.toml"
PLANNING = "src/fin_graph/graphs/analysis/strategy/planning.py"
CASE = "equities/000-no-edge"


def test_file_candidates_run_in_a_copy_and_leave_checkout_untouched(tmp_path):
    from better_harness.patching import build_baseline_variant, build_variant

    from fin_graph.harness.configuration import load_config
    from fin_graph.harness.execution import execute_variant

    config = load_config(CONFIG, model="scripted", repeat=1)
    original = (config.experiment.workspace_root / PLANNING).read_bytes()
    baseline = build_baseline_variant(config.experiment)
    candidate = build_variant(
        experiment=config.experiment,
        label="candidate",
        values=baseline.values | {"planning": "raise RuntimeError('candidate-marker')\n"},
    )
    with pytest.raises(RuntimeError, match="worker"):
        execute_variant(config, candidate, [CASE], tmp_path)
    assert "candidate-marker" in (tmp_path / "worker.log").read_text(encoding="utf-8")
    assert (config.experiment.workspace_root / PLANNING).read_bytes() == original


def test_worker_times_out_instead_of_hanging_on_candidate_code(tmp_path):
    from better_harness.patching import build_baseline_variant, build_variant

    from fin_graph.harness.configuration import load_config
    from fin_graph.harness.execution import execute_variant

    config = load_config(CONFIG, model="scripted", repeat=1)
    config = replace(config, options=config.options.model_copy(update={"split_timeout": 0.4}))
    baseline = build_baseline_variant(config.experiment)
    candidate = build_variant(
        experiment=config.experiment,
        label="candidate",
        values=baseline.values | {"planning": "while True:\n    pass\n"},
    )
    with pytest.raises(TimeoutError):
        execute_variant(config, candidate, [CASE], tmp_path)


def test_isolated_worker_returns_real_trace_artifacts(tmp_path):
    from better_harness.patching import build_baseline_variant

    from fin_graph.harness.configuration import load_config
    from fin_graph.harness.execution import execute_variant

    config = load_config(CONFIG, model="scripted", repeat=1)
    result = execute_variant(config, build_baseline_variant(config.experiment), [CASE], tmp_path)
    [record] = result["runs"]
    assert record["status"] == "completed" and record["trace"]
    assert record["trajectory"]["passed"], record["trajectory"]
    assert Path(record["trace_artifact"]).is_file()
    assert record["harness"]["mode"] == "analysis"


def test_invalid_python_is_rejected_before_execution():
    from fin_graph.harness.surfaces import validate_values

    with pytest.raises(ValueError, match="Python"):
        validate_values({"tools": "def broken("})


def test_candidates_use_resolved_dotenv_budgets_without_production_variant_pin(
    tmp_path, monkeypatch
):
    from better_harness.patching import build_baseline_variant, build_variant

    from fin_graph.harness.configuration import load_config
    from fin_graph.harness.execution import execute_variant
    from fin_graph.harness.profile import resolve_analysis_profile

    config = load_config(CONFIG, model="scripted", repeat=1)
    baseline = build_baseline_variant(config.experiment)
    production_variant = resolve_analysis_profile().variant
    monkeypatch.setenv("FIN_ANALYSIS_EXPECTED_VARIANT", production_variant)
    (tmp_path / ".env").write_text("FIN_ANALYSIS_MAX_WAVES=1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    candidate = build_variant(
        experiment=config.experiment,
        label="runtime-probe",
        values=baseline.values
        | {"narrative": baseline.values["narrative"] + "\nName the margin of safety."},
    )
    result = execute_variant(config, candidate, [CASE], tmp_path / "run")
    record = result["runs"][0]
    assert record["status"] == "completed", record.get("error")
    assert record["harness"]["variant"] != production_variant
    assert record["harness"]["limits"]["max_waves"] == 1
    assert resolve_analysis_profile().variant == production_variant


def test_production_refuses_an_unreviewed_variant(monkeypatch):
    from fin_graph.harness.profile import resolve_analysis_profile

    monkeypatch.setenv("FIN_ANALYSIS_EXPECTED_VARIANT", "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="FIN_ANALYSIS_EXPECTED_VARIANT"):
        resolve_analysis_profile()
    assert resolve_analysis_profile(verify_variant_pin=False).variant.startswith("sha256:")


def test_baseline_worker_keeps_dotenv_runtime_budget(tmp_path, monkeypatch):
    from better_harness.patching import build_baseline_variant

    from fin_graph.harness.configuration import load_config
    from fin_graph.harness.execution import execute_variant

    config = load_config(CONFIG, model="scripted", repeat=1)
    (tmp_path / ".env").write_text("FIN_ANALYSIS_MAX_WAVES=1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = execute_variant(
        config, build_baseline_variant(config.experiment), [CASE], tmp_path / "run"
    )
    assert result["runs"][0]["harness"]["limits"]["max_waves"] == 1
