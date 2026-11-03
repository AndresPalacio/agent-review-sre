"""Write the license inventory for a frozen AI Hedge Fund snapshot."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from fin_graph.compat.licensing import AssetInventory, audit_assets
from fin_graph.compat.reference import ReferenceSnapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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

    snapshot = ReferenceSnapshot.model_validate_json(arguments.snapshot.read_text(encoding="utf-8"))
    assets = audit_assets(root, snapshot)
    inventory = AssetInventory(
        schema_version=1,
        snapshot_digest=snapshot.root_digest,
        redistributable_count=sum(asset.redistributable for asset in assets),
        excluded_count=sum(not asset.redistributable for asset in assets),
        assets=assets,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(inventory.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
