"""Private evaluation subprocess entry point; code and data come from the source copy."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fin_graph.evals.market.runner import run_equity_suite
from fin_graph.evals.market.scenarios import load_equity_case
from fin_graph.optimization.harness.surfaces import apply_surfaces
from fin_graph.workflows.analysis.graph import AnalysisLimits


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    scenarios = [load_equity_case(key) for key in request["scenarios"]]
    with apply_surfaces(request["prompts"]):
        asyncio.run(
            run_equity_suite(
                "harness",
                request["model"],
                request["repeat"],
                Path(request["output"]),
                scenarios=scenarios,
                case_timeout=request["case_timeout"],
                limits=AnalysisLimits(**request["runtime_limits"]),
            )
        )


if __name__ == "__main__":
    main()
