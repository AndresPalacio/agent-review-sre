"""Official Deep Agents proposer, with only train data in its workspace.

The outer agent is upstream's (its system prompt, workspace and file backend); fin-graph only
builds the model, so a local ``ollama:`` proposer gets a context window that fits the task.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from better_harness.agent import (
    DEFAULT_SYSTEM_PROMPT,
    ProposerWorkspace,
    build_proposer_workspace,
)
from better_harness.core import Proposal, RunLayout, SplitResult, Variant
from better_harness.patching import build_variant

from fin_graph.agents.optimizer.proposer import make_optimizer_agent
from fin_graph.config.settings import Settings
from fin_graph.evals.market.scenarios import load_equity_case
from fin_graph.evals.market.trajectory import contract_for
from fin_graph.harness.configuration import HarnessConfig, load_config
from fin_graph.harness.runner import write_json
from fin_graph.harness.surfaces import validate_values
from fin_graph.llm.models import build_chat_model, parse_model_spec
from fin_graph.safety.secrets import redact_secrets

#: Room for the system prompt, task file and the surface files a proposer reads and rewrites.
PROPOSER_NUM_CTX = 16384
PROPOSER_NUM_PREDICT = 4096
_START = (
    "Read /task.md first. Then inspect the current surface files, visible history, and failing "
    "train cases, edit only /current, and finish by updating /proposal.md."
)


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
        scenario = load_equity_case(case.scenario)
        write_json(
            workspace.root / "train_data" / f"{case.scenario.replace('/', '--')}.json",
            {
                "scenario": scenario.key,
                "request": scenario.request(),
                "metadata": scenario.metadata,
                "reference": scenario.answer.model_dump(mode="json"),
                "execution_contract": contract_for(scenario).model_dump(mode="json"),
            },
        )
    task = workspace.root / "task.md"
    with task.open("a", encoding="utf-8") as handle:
        handle.write(
            "\nThe /train_data directory holds the train requests, their metadata, independent "
            "reference answers and execution contracts.\n"
            "Write general analysis rules; never memorize scenario ids, tickers or answers.\n"
            "Edit only the eleven files listed in surface_manifest.json. With a small local "
            "model the graph runs in tool_plan mode: the analysts' compute tools, the stance "
            "and the category are deterministic, and the model writes the analyst summaries "
            "(analyst.txt), the Spanish thesis narrative (narrative.txt), the claim judge "
            "(judge.txt) and the portfolio choice (portfolio.txt). tools.py "
            "prepare_tools(task, tools) may order tools and change descriptions via "
            "model_copy, but must keep every tool, its function, schema and metadata. "
            "plan.txt, synthesis.txt, skills.md, planning.py refine_plan(plan, request, "
            "available), middleware.py and registration.py build_middleware(task) run only in "
            "react mode with larger models. Safety, trading limits, graph limits, schemas, the "
            "quant analysts, market data, corpus and graders are NOT editable.\n"
            "A pass needs, per ticker, the expected stance and category, no forbidden "
            "category, every required keyword in the thesis text (alternatives separated by "
            "'|'), a verified thesis, and the expected action when the reference names one; "
            "per run, valid citations, the execution contract and the latency and token "
            "budgets. Verification drops a claim whose numbers are not in the evidence it "
            "cites. Code is tested in fresh Python processes; preserve interfaces and avoid "
            "side effects.\n"
        )
    return workspace


def run_outer_agent(config: HarnessConfig, workspace: ProposerWorkspace) -> str | None:
    """Upstream's outer Deep Agent on ``workspace``, with a model built by fin-graph."""
    from langchain_core.messages import HumanMessage

    experiment = config.experiment
    spec = parse_model_spec(experiment.better_agent_model)
    settings = Settings(
        _env_file=None,
        llm_provider=spec.provider,
        llm_num_ctx=PROPOSER_NUM_CTX,
        llm_num_predict=PROPOSER_NUM_PREDICT,
    )
    system = DEFAULT_SYSTEM_PROMPT
    if experiment.better_agent_system_prompt:
        system = experiment.better_agent_system_prompt.strip() + "\n\n" + system
    agent = make_optimizer_agent(
        model=build_chat_model(spec, settings, temperature=0),
        system_prompt=system,
        workspace_root=workspace.root,
    )
    result: dict[str, Any] = agent.invoke(
        {"messages": [HumanMessage(content=_START)]},
        config={"recursion_limit": experiment.better_agent_max_turns},
    )
    for message in reversed(result.get("messages", [])):
        if getattr(message, "type", None) == "ai":
            return redact_secrets(str(message.content))[:24000]
    return None


def propose_in_process(
    config: HarnessConfig,
    current: Variant,
    train: SplitResult,
    layout: RunLayout,
    iteration: int,
) -> tuple[Proposal, Variant]:
    workspace = prepare_workspace(config, current, train, layout, iteration)
    message = run_outer_agent(config, workspace)
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
