"""Official Deep Agents proposer, with only train data in its workspace."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from better_harness.agent import (
    ProposerWorkspace,
    build_proposer_workspace,
    invoke_deepagents_proposer,
)
from better_harness.core import Proposal, RunLayout, SplitResult, Variant
from better_harness.patching import build_variant

from fin_graph.evals.scenarios import load_scenario
from fin_graph.evals.trajectory import contract_for
from fin_graph.harness.configuration import HarnessConfig, load_config
from fin_graph.harness.runner import write_json
from fin_graph.harness.surfaces import validate_values
from fin_graph.safety.secrets import redact_secrets


def prepare_workspace(
    config: HarnessConfig,
    current: Variant,
    train: SplitResult,
    layout: RunLayout,
    iteration: int,
) -> ProposerWorkspace:
    workspace = build_proposer_workspace(
        experiment=config.experiment,
        current=current,
        train_result=train,
        layout=layout,
        iteration=iteration,
    )
    for case in config.dataset.select("train"):
        scenario = load_scenario(*case.scenario.split("/", 1))
        write_json(
            workspace.root / "train_data" / f"{case.scenario.replace('/', '--')}.json",
            {
                "scenario": scenario.key,
                "alert": scenario.alert,
                "metadata": scenario.metadata,
                "evidence": scenario.evidence,
                "reference": scenario.answer.model_dump(mode="json"),
                "execution_contract": contract_for(scenario).model_dump(mode="json"),
            },
        )
    task = workspace.root / "task.md"
    with task.open("a", encoding="utf-8") as handle:
        handle.write(
            "\nThe /train_data directory contains train observations and independent references.\n"
            "Use general investigation rules, never memorize scenario ids or reference answers.\n"
            "Edit only the nine files listed in surface_manifest.json. "
            "The production runtime consumes all of them. Tools prepare_tools(task, tools) "
            "may select/order tools and change descriptions via model_copy, but must preserve "
            "adapter functions, schemas and metadata. planning.refine_plan(plan, alert, available) "
            "runs before fixed graph constraints. skills.md is loaded into specialist prompts. "
            "registration.build_middleware(task) installs additional AgentMiddleware instances "
            "from middleware.py alongside the fixed safety stack. "
            "Safety, graph limits, schemas, adapters, corpus and graders are NOT editable.\n"
            "A pass requires correct category, required keywords/sources/queries, "
            "valid citations, execution contracts and measured budgets. Inspect train traces "
            "for actual delegation, arguments, call order and errors. Code is tested in fresh "
            "Python processes; preserve interfaces and avoid side effects.\n"
        )
    return workspace


def propose_in_process(
    config: HarnessConfig,
    current: Variant,
    train: SplitResult,
    layout: RunLayout,
    iteration: int,
) -> tuple[Proposal, Variant]:
    workspace = prepare_workspace(config, current, train, layout, iteration)
    message = invoke_deepagents_proposer(experiment=config.experiment, workspace=workspace)
    values = {}
    for name, path in workspace.surface_files.items():
        if path.is_symlink() or not path.resolve().is_relative_to(workspace.current_dir.resolve()):
            raise ValueError("surface escaped proposer workspace")
        if path.stat().st_size > 384000:
            raise ValueError("surface too large")
        values[name] = path.read_text(encoding="utf-8")
    validate_values(values, complete=True)
    proposal = Proposal(
        changed_surfaces=tuple(
            sorted(name for name in values if values[name] != current.values[name])
        ),
        workspace_dir=str(workspace.root),
        summary=workspace.proposal_file.read_text(encoding="utf-8")[:24000],
        final_message=message,
    )
    variant = build_variant(
        experiment=config.experiment, label=f"iter-{iteration:03d}", values=values
    )
    write_json(
        layout.iteration_dir(iteration) / "candidate.json",
        {
            "proposal": proposal.to_dict(),
            "values": values,
        },
    )
    return proposal, variant


def propose_candidate(
    config: HarnessConfig,
    current: Variant,
    train: SplitResult,
    layout: RunLayout,
    iteration: int,
) -> tuple[Proposal, Variant]:
    """A killable child process bounds outer-agent wall time on Windows and Linux."""
    directory = layout.iteration_dir(iteration)
    directory.mkdir(parents=True, exist_ok=True)
    current.save(directory / "input-variant.json")
    train.save(directory / "input-train.json")
    command = [
        sys.executable,
        "-m",
        "fin_graph.harness.proposer",
        str(config.experiment.path),
        "--root",
        str(layout.root),
        "--iteration",
        str(iteration),
        "--model",
        config.experiment.model,
        "--proposer-model",
        config.experiment.better_agent_model,
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["LANGSMITH_TRACING"] = "false"
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=config.options.proposer_timeout,
        check=False,
    )
    (directory / "proposer.log").write_text(
        redact_secrets(completed.stdout + "\n" + completed.stderr),
        encoding="utf-8",
    )
    if completed.returncode:
        raise RuntimeError("outer proposer failed; see proposer.log")
    payload = json.loads((directory / "candidate.json").read_text(encoding="utf-8"))
    values = payload["values"]
    validate_values(values, complete=True)
    proposal = Proposal(
        **(
            payload["proposal"]
            | {
                "changed_surfaces": tuple(
                    sorted(name for name in values if values[name] != current.values[name])
                ),
            }
        )
    )
    return proposal, build_variant(
        experiment=config.experiment,
        label=f"iter-{iteration:03d}",
        values=values,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--proposer-model", required=True)
    args = parser.parse_args()
    config = load_config(args.config, model=args.model, proposer_model=args.proposer_model)
    layout = RunLayout(args.root)
    directory = layout.iteration_dir(args.iteration)
    propose_in_process(
        config,
        Variant.load(directory / "input-variant.json"),
        SplitResult.load(directory / "input-train.json"),
        layout,
        args.iteration,
    )


if __name__ == "__main__":
    main()
