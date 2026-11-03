"""Run release acceptance for one AI Hedge Fund persistence backend."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from fin_graph.acceptance import run_acceptance, write_acceptance_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--database-url", default=os.getenv("FIN_GRAPH_TEST_DATABASE_URL"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/acceptance"))
    args = parser.parse_args()
    try:
        report = asyncio.run(
            run_acceptance(
                backend=args.backend,
                data_dir=args.data_dir,
                database_url=args.database_url,
            )
        )
        json_path, markdown_path = write_acceptance_report(report, args.output)
    except Exception as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        return 1
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "backend": report["backend"],
                "public_digest": report["public_digest"],
                "json": str(json_path),
                "markdown": str(markdown_path),
            },
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
