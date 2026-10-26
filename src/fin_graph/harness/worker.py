"""Private evaluation subprocess entry point; code and data come from the source copy."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fin_graph.evals.runner import run_suite
from fin_graph.evals.scenarios import load_scenario
from fin_graph.graphs.investigation.graph import InvestigationLimits
from fin_graph.harness.surfaces import apply_surfaces


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    scenarios = [load_scenario(*key.split("/", 1)) for key in request["scenarios"]]
    with apply_surfaces(request["prompts"]):
        asyncio.run(
            run_suite(
                "harness",
                request["model"],
                request["repeat"],
                Path(request["output"]),
                scenarios=scenarios,
                case_timeout=request["case_timeout"],
                judge_model=request.get("judge_model"),
                runtime_limits=InvestigationLimits(**request["runtime_limits"]),
            )
        )


if __name__ == "__main__":
    main()
