from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("better_harness")


def make_run(tmp_path):
    from better_harness.core import RunLayout
    from better_harness.patching import build_baseline_variant, build_variant

    from sre_graph.harness.artifacts import save_source_baseline
    from sre_graph.harness.configuration import load_config

    config = load_config(Path(__file__).resolve().parents[2] / "harness/sre.toml", model="scripted")
    layout = RunLayout(tmp_path / "run")
    layout.root.mkdir()
    save_source_baseline(config.experiment.workspace_root, layout.root)
    baseline = build_baseline_variant(config.experiment)
    candidate = build_variant(
        experiment=config.experiment,
        label="iter-001",
        values=baseline.values
        | {"skills": "Inspect evidence chronology.\n", "plan": "Check chronology."},
    )
    baseline.save(layout.variant_path(baseline.key))
    candidate.save(layout.variant_path(candidate.key))
    (layout.root / "summary.json").write_text(
        json.dumps({"selected_variant": candidate.key, "promotable": False, "status": "completed"}),
        encoding="utf-8",
    )
    return layout.root


def test_export_contains_reviewable_files_diff_and_qualification(tmp_path):
    from sre_graph.harness.artifacts import export_variant

    run = make_run(tmp_path)
    destination = tmp_path / "review"
    result = export_variant(run, destination)
    assert result["promotable"] is False
    assert (
        destination / "src/sre_graph/graphs/investigation/strategy/skills.md"
    ).read_text() == "Inspect evidence chronology.\n"
    assert "+Inspect evidence chronology." in (destination / "changes.diff").read_text()
    assert (destination / "src/sre_graph/graphs/investigation/prompts.py").is_file()
    import hashlib

    for relative, digest in result["files"].items():
        assert hashlib.sha256((destination / relative).read_bytes()).hexdigest() == digest
    tools = "src/sre_graph/graphs/investigation/strategy/tools.py"
    assert (destination / tools).read_bytes() == (run / "source-baseline" / tools).read_bytes()
    with pytest.raises(FileExistsError):
        export_variant(run, destination)


def test_inspection_rejects_variant_path_traversal(tmp_path):
    from sre_graph.harness.artifacts import inspect_run

    run = make_run(tmp_path)
    (run / "summary.json").write_text(json.dumps({"selected_variant": "../../secret"}))
    with pytest.raises(ValueError, match="variant"):
        inspect_run(run)


def test_exported_patch_applies_to_the_captured_source(tmp_path):
    import shutil
    import subprocess

    from sre_graph.harness.artifacts import export_variant

    run = make_run(tmp_path)
    export = tmp_path / "review"
    export_variant(run, export)
    checkout = tmp_path / "checkout"
    shutil.copytree(run / "source-baseline", checkout)
    result = subprocess.run(
        ["git", "apply", "--check", str(export / "changes.diff")],
        cwd=checkout,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cli_exposes_contracts_inspection_and_export():
    from typer.testing import CliRunner

    from sre_graph.cli.main import app

    cli = CliRunner()
    help_text = cli.invoke(app, ["harness", "--help"]).output
    assert "inspect" in help_text and "export" in help_text and "contract" in help_text
    result = cli.invoke(app, ["harness", "contract", "eks/002-image-pull-backoff"])
    assert result.exit_code == 0, result.output
    assert "specialist_kubernetes" in result.output and "k8s_list_events" in result.output
