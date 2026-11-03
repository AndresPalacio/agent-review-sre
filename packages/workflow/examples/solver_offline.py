"""Run one native DeepAgents solver case with local tools and no provider calls.

From packages/workflow: uv run --extra agents python examples/solver_offline.py
"""

import asyncio
import json

from solver_benchmark import cases, run_benchmark


async def main() -> None:
    case = next(case for case in cases() if case.id == "config-wrong-diagnosis")
    report = await run_benchmark((case,))
    print(json.dumps(report.as_dict(), indent=2))
    solver = next(row for row in report.rows if row.arm == "solver")
    if not solver.verified or not solver.environment_passed:
        raise SystemExit("Offline solver did not produce a verified configuration repair.")


if __name__ == "__main__":
    asyncio.run(main())
