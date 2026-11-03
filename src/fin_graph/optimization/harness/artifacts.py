"""Inspect and export selected harnesses for review without adopting them."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from better_harness.core import RunLayout, Variant

from fin_graph.optimization.harness.surfaces import FILE_SURFACES, SURFACES, validate_values

PROMPTS_FILE = "src/fin_graph/workflows/analysis/prompts.py"


def save_source_baseline(workspace: Path, directory: Path) -> None:
    """Capture actual source for portable review diffs, including formatting and comments."""
    for relative in (*FILE_SURFACES.values(), PROMPTS_FILE):
        destination = directory / "source-baseline" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((workspace / relative).read_bytes())


def inspect_run(directory: Path) -> dict[str, Any]:
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    key = summary.get("selected_variant")
    if key is not None and (
        not isinstance(key, str) or not re.fullmatch(r"baseline|iter-\d{3}", key)
    ):
        raise ValueError("invalid selected variant")
    return dict(summary)


def _files(variant: Variant) -> dict[str, str]:
    validate_values(variant.values, complete=True)
    values = {target: variant.values[name] for name, target in FILE_SURFACES.items()}
    values[PROMPTS_FILE] = (
        '"""Investigation prompts exported for review by better-harness."""\n\n'
        + "\n".join(
            f"{attribute} = {variant.values[name]!r}" for name, attribute in SURFACES.items()
        )
        + "\n"
    )
    return values


def _render_prompts(source: str, values: dict[str, str]) -> str:
    lines = source.splitlines(keepends=True)
    changed: list[tuple[int, int, str]] = []
    by_attribute = {attribute: values[name] for name, attribute in SURFACES.items()}
    found = set()
    for node in ast.parse(source).body:
        if (
            not isinstance(node, ast.Assign)
            or len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
        ):
            continue
        name = node.targets[0].id
        if name not in by_attribute:
            continue
        found.add(name)
        if ast.literal_eval(node.value) != by_attribute[name]:
            ending = "\r\n" if lines[node.lineno - 1].endswith("\r\n") else "\n"
            changed.append(
                (
                    node.lineno - 1,
                    node.end_lineno or node.lineno,
                    f"{name} = {by_attribute[name]!r}{ending}",
                )
            )
    if found != set(by_attribute):
        raise ValueError("captured prompt source does not contain the expected assignments")
    for start, end, replacement in sorted(changed, reverse=True):
        lines[start:end] = [replacement]
    return "".join(lines)


def export_variant(directory: Path, destination: Path) -> dict[str, Any]:
    summary = inspect_run(directory)
    key = summary.get("selected_variant")
    if key is None:
        raise ValueError("run has no selected variant to export")
    layout = RunLayout(directory.resolve())
    variant = Variant.load(layout.variant_path(key))
    after = _files(variant)
    before = {
        target: (directory / "source-baseline" / target).read_bytes().decode("utf-8")
        for target in after
    }
    after[PROMPTS_FILE] = _render_prompts(before[PROMPTS_FILE], variant.values)
    for target in FILE_SURFACES.values():
        # Upstream strips base_file trailing whitespace; keep unchanged source byte-for-byte.
        if (
            before[target].replace("\r\n", "\n").strip()
            == after[target].replace("\r\n", "\n").strip()
        ):
            after[target] = before[target]
    destination.mkdir(parents=True, exist_ok=False)
    diff: list[str] = []
    for target, text in after.items():
        path = destination / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        for line in difflib.unified_diff(
            before[target].splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"a/{target}",
            tofile=f"b/{target}",
        ):
            diff.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    manifest = {
        "run": str(directory.resolve()),
        "variant": key,
        "status": summary.get("status"),
        "promotable": summary.get("promotable", False),
        "adopted": False,
        "files": {
            target: hashlib.sha256(text.encode()).hexdigest() for target, text in after.items()
        },
    }
    (destination / "changes.diff").write_text("".join(diff), encoding="utf-8", newline="")
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
