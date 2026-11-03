"""Fail closed unless every compatibility contract has fresh executable evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RULES = {
    "exact",
    "schema",
    "semantic",
    "float-tolerance",
    "runtime-digest",
    "security-deviation",
}


def _owner_file(root: Path, owner: str) -> tuple[Path | None, list[str]]:
    path_text, separator, node_text = owner.partition("::")
    if not separator or not path_text or not node_text:
        return None, []
    candidate = (root / path_text).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, []
    return candidate, node_text.split("::")


def _pytest_owner_exists(root: Path, owner: str) -> tuple[bool, Path | None]:
    path, nodes = _owner_file(root, owner)
    if path is None or not path.is_file() or path.suffix != ".py" or not nodes:
        return False, path
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return False, path
    body: list[ast.stmt] = tree.body
    for index, raw_name in enumerate(nodes):
        name = raw_name.split("[", 1)[0]
        matches = [
            item
            for item in body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and item.name == name
        ]
        if not matches:
            return False, path
        selected = matches[0]
        if index < len(nodes) - 1:
            if not isinstance(selected, ast.ClassDef):
                return False, path
            body = selected.body
    return True, path


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def validate_manifest(manifest: dict[str, Any], root: Path) -> list[str]:
    """Return all closure errors; an empty list means the manifest is releasable."""

    errors: list[str] = []
    snapshot = _text(manifest.get("snapshot_digest"))
    if not _DIGEST.fullmatch(snapshot):
        errors.append("manifest snapshot_digest must be a SHA-256 digest")

    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        errors.append("manifest fixtures must be a non-empty list")
    else:
        for index, fixture in enumerate(fixtures):
            if not isinstance(fixture, dict):
                errors.append(f"fixture {index} is not an object")
            elif fixture.get("snapshot_digest") != snapshot:
                errors.append(f"fixture {index} has a stale reference digest")

    contracts = manifest.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        errors.append("manifest contracts must be a non-empty list")
        return errors

    ids: set[str] = set()
    owners: set[str] = set()
    required = {
        "id",
        "status",
        "owner",
        "reference_digest",
        "candidate_digest",
        "comparison_rule",
        "evidence_command",
    }
    for index, contract in enumerate(contracts):
        label = f"contract {index}"
        if not isinstance(contract, dict):
            errors.append(f"{label} is not an object")
            continue
        missing = sorted(required - contract.keys())
        if missing:
            errors.append(f"{label} is missing fields: {', '.join(missing)}")
            continue
        contract_id = _text(contract.get("id"))
        label = f"contract {contract_id or index}"
        if not contract_id or contract_id in ids:
            errors.append(f"{label} has a blank or duplicate id")
        ids.add(contract_id)

        status = _text(contract.get("status"))
        if status not in {"verified", "excluded"}:
            errors.append(f"{label} has invalid status {status!r}")

        owner = _text(contract.get("owner"))
        if owner in owners:
            errors.append(f"{label} has duplicate owner {owner!r}")
        owners.add(owner)
        owner_exists, owner_path = _pytest_owner_exists(root, owner)
        if not owner_exists:
            errors.append(f"{label} pytest owner does not exist: {owner!r}")

        reference = _text(contract.get("reference_digest"))
        if reference != snapshot:
            errors.append(f"{label} has a stale reference digest")

        candidate = _text(contract.get("candidate_digest"))
        if not _DIGEST.fullmatch(candidate):
            errors.append(f"{label} candidate_digest must be SHA-256")
        elif owner_path is not None and owner_path.is_file():
            actual = hashlib.sha256(owner_path.read_bytes()).hexdigest()
            if candidate != actual:
                errors.append(f"{label} has a stale candidate digest")

        rule = _text(contract.get("comparison_rule"))
        if rule not in _RULES:
            errors.append(f"{label} has unsupported comparison rule {rule!r}")

        command = _text(contract.get("evidence_command"))
        if not command.startswith("uv run ") or " pytest " not in command or owner not in command:
            errors.append(f"{label} needs an executable pytest command containing its owner")

        if status == "excluded":
            exclusion = contract.get("exclusion")
            approved = (
                isinstance(exclusion, dict)
                and exclusion.get("approved_by_design") is True
                and bool(_text(exclusion.get("rationale")).strip())
                and _text(exclusion.get("design_reference")).startswith("docs/")
            )
            if not approved:
                errors.append(f"{label} has an unapproved exclusion")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).parents[1]
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=default_root / "tests" / "compat" / "fixtures" / "manifest.json",
    )
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"manifest cannot be read: {type(exc).__name__}", file=sys.stderr)
        return 1
    if not isinstance(manifest, dict):
        print("manifest root must be an object", file=sys.stderr)
        return 1
    errors = validate_manifest(manifest, args.root.resolve())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    contracts = manifest["contracts"]
    excluded = sum(item["status"] == "excluded" for item in contracts)
    print(
        json.dumps(
            {
                "contracts": len(contracts),
                "excluded": excluded,
                "status": "closed",
                "verified": len(contracts) - excluded,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
