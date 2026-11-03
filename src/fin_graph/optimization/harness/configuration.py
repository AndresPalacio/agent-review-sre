"""Validate the upstream experiment against the narrower fin-graph analysis contract."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from better_harness.core import Experiment, load_experiment
from pydantic import BaseModel, ConfigDict, Field

from fin_graph.optimization.harness.dataset import DatasetManifest, load_manifest
from fin_graph.optimization.harness.surfaces import (
    FILE_SURFACES,
    PROMPT_MODULE,
    SURFACES,
    current_values,
    validate_values,
)


class EvalOptions(BaseModel):
    """``[fin]`` budgets; the defaults fit a 3B local model, which needs minutes per case."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    repeat: int = Field(default=1, ge=1, le=20)
    case_timeout: float = Field(default=900, gt=0, le=3600)
    proposer_timeout: float = Field(default=1800, gt=0, le=7200)
    split_timeout: float = Field(default=7200, gt=0, le=28800)
    max_case_tokens: int = Field(default=200000, ge=1, le=2000000)
    max_case_latency: float = Field(default=600, gt=0, le=3600)


@dataclass(frozen=True)
class HarnessConfig:
    experiment: Experiment
    dataset: DatasetManifest
    options: EvalOptions


def validate_config(config: HarnessConfig) -> None:
    experiment = config.experiment
    if experiment.better_agent_deepagents_root is not None:
        raise ValueError("use the installed Deep Agents package for bounded proposer execution")
    baseline = {name: s.base_value for name, s in experiment.surfaces.items()}
    validate_values(baseline, complete=True)
    production = current_values()
    for name, surface in experiment.surfaces.items():
        if name in SURFACES:
            expected = ("module_attr", f"{PROMPT_MODULE}:{SURFACES[name]}", f"{name}.txt")
            # Upstream strips values; the baseline must be what production runs.
            if surface.base_value != production[name].strip():
                raise ValueError(f"baseline prompt differs from production: {name}")
        else:
            expected = ("workspace_file", FILE_SURFACES[name], Path(FILE_SURFACES[name]).name)
        if (surface.kind, surface.target, surface.filename) != expected:
            raise ValueError("surface must match the fixed production allowlist")
    assignments = {(c.nodeid, c.split, c.stratum) for c in config.dataset.cases}
    if assignments != {(c.case_id, c.split, c.stratum) for c in experiment.cases}:
        raise ValueError("experiment cases differ from the frozen split manifest")
    if (
        not 1 <= experiment.max_iterations <= 20
        or not 1 <= experiment.better_agent_max_turns <= 100
    ):
        raise ValueError("iteration or proposer turn limit outside supported range")
    if experiment.runner != "pytest":
        raise ValueError("the fin-graph adapter requires a pytest-compatible experiment")
    if Path(experiment.runner_config["project_root"]) != experiment.workspace_root:
        raise ValueError("project_root must equal workspace_root")
    if not (experiment.workspace_root / "pyproject.toml").is_file():
        raise ValueError("workspace must be a fin-graph source checkout")
    for model in (experiment.model, experiment.better_agent_model):
        if model != "scripted" and (":" not in model or not all(model.split(":", 1))):
            raise ValueError("models must use provider:model syntax")


def load_config(
    path: Path,
    *,
    model: str | None = None,
    proposer_model: str | None = None,
    repeat: int | None = None,
) -> HarnessConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    options = dict(raw.get("fin", {}))
    if repeat is not None:
        options["repeat"] = repeat
    experiment = load_experiment(path, model_override=model)
    if proposer_model is not None:
        experiment = replace(experiment, better_agent_model=proposer_model)
    config = HarnessConfig(experiment, load_manifest(), EvalOptions.model_validate(options))
    validate_config(config)
    return config
