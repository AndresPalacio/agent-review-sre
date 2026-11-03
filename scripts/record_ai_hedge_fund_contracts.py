"""Record one explicit JSON contract fixture with frozen source provenance."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from fin_graph.conformance.reference_app.licensing import AssetInventory
from fin_graph.conformance.reference_app.snapshot import ReferenceSnapshot

RECORDER_VERSION = "1"
_FIXTURES_ROOT = Path("tests/compat/fixtures")
_FIXTURE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--asset-inventory", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--input", type=Path, required=True)
    return parser


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if not _FIXTURE_NAME.fullmatch(arguments.name):
        parser.error("--name must be a lowercase fixture name without path separators")
    if arguments.input.suffix.lower() != ".json":
        parser.error("--input must name an explicit JSON input, never private or database state")
    if arguments.snapshot.suffix.lower() != ".json":
        parser.error("--snapshot must be a JSON reference manifest")
    if arguments.asset_inventory.suffix.lower() != ".json":
        parser.error("--asset-inventory must be a JSON license inventory")

    try:
        snapshot = ReferenceSnapshot.model_validate(_read_json(arguments.snapshot))
        inventory = AssetInventory.model_validate(_read_json(arguments.asset_inventory))
        payload = _read_json(arguments.input)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    source = next((item for item in snapshot.files if item.path == arguments.source_file), None)
    if source is None:
        parser.error("--source-file is not present in the frozen reference snapshot")
    if inventory.snapshot_digest != snapshot.root_digest:
        parser.error("--asset-inventory belongs to a different reference snapshot")
    asset = next((item for item in inventory.assets if item.path == source.path), None)
    if asset is None:
        parser.error("--source-file is missing from the scope-aware asset inventory")
    if not asset.redistributable:
        parser.error("--source-file is not redistributable under its verified license scope")

    fixtures_root = (_FIXTURES_ROOT).resolve()
    fixtures_root.mkdir(parents=True, exist_ok=True)
    fixture_name = f"{arguments.name}.json"
    fixture_path = fixtures_root / fixture_name
    manifest_path = fixtures_root / "manifest.json"
    if fixture_path.exists():
        parser.error(f"fixture already exists: {arguments.name}")

    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        if manifest.get("snapshot_digest") != snapshot.root_digest:
            parser.error("fixture manifest belongs to a different reference snapshot")
    else:
        manifest = {
            "schema_version": 1,
            "recorder_version": RECORDER_VERSION,
            "snapshot_digest": snapshot.root_digest,
            "fixtures": [],
        }

    entries = manifest.get("fixtures")
    if not isinstance(entries, list):
        parser.error("fixture manifest has an invalid fixtures collection")
    if any(isinstance(entry, dict) and entry.get("name") == arguments.name for entry in entries):
        parser.error(f"fixture name already recorded: {arguments.name}")

    entries.append(
        {
            "fixture": fixture_name,
            "license": asset.license_id,
            "name": arguments.name,
            "origin": asset.origin,
            "recorder_version": RECORDER_VERSION,
            "snapshot_digest": snapshot.root_digest,
            "source_file": source.path,
        }
    )
    entries.sort(key=lambda entry: str(entry.get("name", "")))
    _write_json(fixture_path, payload)
    _write_json(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
