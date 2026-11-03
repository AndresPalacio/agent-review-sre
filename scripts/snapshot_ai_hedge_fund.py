"""Record a safe, content-addressed manifest of an AI Hedge Fund source tree."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from fin_graph.conformance.reference_app.snapshot import build_reference_snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="read-only reference root")
    parser.add_argument("--output", type=Path, required=True, help="manifest destination")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve(strict=True)
    output = arguments.output.resolve(strict=False)
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        parser.error("--output must be outside the reference root")

    snapshot = build_reference_snapshot(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
