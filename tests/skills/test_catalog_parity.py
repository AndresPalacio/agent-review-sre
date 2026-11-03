from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path

from fin_graph.skills.catalog import finance_skill_catalog, finance_skill_roots, load_skill
from fin_graph.skills.loader import load_skill_catalog

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_PREFIX = "app/backend/skills/finance/"


def _reference_files() -> dict[str, dict[str, object]]:
    snapshot = json.loads(
        (ROOT / "tests" / "compat" / "reference-snapshot.json").read_text(encoding="utf-8")
    )
    return {
        item["path"][len(REFERENCE_PREFIX) :]: item
        for item in snapshot["files"]
        if item["path"].startswith(REFERENCE_PREFIX)
    }


def test_every_licensed_finance_skill_and_resource_is_bundled_byte_for_byte() -> None:
    root = resources.files("fin_graph.skills").joinpath("finance")
    catalog = load_skill_catalog(root)
    expected = _reference_files()
    actual: dict[str, bytes] = {}
    for skill in catalog:
        skill_file = root.joinpath(skill.id, "SKILL.md")
        actual[f"{skill.id}/SKILL.md"] = skill_file.read_bytes()
        for resource_path in skill.resources:
            actual[f"{skill.id}/{resource_path}"] = root.joinpath(
                skill.id, *resource_path.split("/")
            ).read_bytes()

    assert len(catalog) == 71
    assert set(actual) == set(expected)
    for path, payload in actual.items():
        assert len(payload) == expected[path]["size"]
        assert hashlib.sha256(payload).hexdigest() == expected[path]["sha256"]


def test_catalog_order_metadata_and_deepagents_roots_are_stable() -> None:
    first = finance_skill_catalog()
    second = finance_skill_catalog()

    assert first is second
    assert [skill.id for skill in first] == sorted(skill.id for skill in first)
    assert all(skill.title == skill.id for skill in first)
    assert all(skill.description and skill.category for skill in first)
    assert all(list(skill.resources) == sorted(skill.resources) for skill in first)
    roots = finance_skill_roots()
    assert len(roots) == 71
    assert all(Path(path).name in {skill.id for skill in first} for path in roots)


def test_legacy_progressive_disclosure_extends_to_full_finance_catalog() -> None:
    summary = load_skill("adr-hshare")
    full = load_skill("adr-hshare", full=True)

    assert "cross-listing" in summary
    assert full.startswith("---\nname: adr-hshare")
    assert len(full) > len(summary)

