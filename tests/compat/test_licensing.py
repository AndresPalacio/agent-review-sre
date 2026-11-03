from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from fin_graph.compat.licensing import AssetInventory, audit_assets
from fin_graph.compat.reference import ReferenceSnapshot, build_reference_snapshot

ROOT = Path(__file__).resolve().parents[2]
MIT = """MIT License

Copyright (c) Example

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction.
"""
APACHE = """Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/
"""
VIBE_NOTICE = """# Vibe-Trading third-party notice

The finance skill catalog under `app/backend/skills/finance/` is vendored from:

- Project: Vibe-Trading
- Source directory: `agent/src/skills/`
- Copyright: Copyright (c) 2026 Vibe-Trading Contributors
- License: MIT

## MIT License
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_audit_allows_explicit_mit_and_nested_apache_with_notices(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    _write(root / "LICENSE", MIT)
    _write(root / "src" / "main.py", "print('root')\n")
    _write(root / "vendor" / "LICENSE", APACHE)
    _write(root / "vendor" / "nested" / "resource.txt", "licensed resource")
    snapshot = build_reference_snapshot(root)

    assets = {asset.path: asset for asset in audit_assets(root, snapshot)}

    assert assets["src/main.py"].license_id == "MIT"
    assert assets["src/main.py"].redistributable is True
    assert "LICENSE" in assets["src/main.py"].notice
    assert assets["vendor/nested/resource.txt"].license_id == "Apache-2.0"
    assert assets["vendor/nested/resource.txt"].redistributable is True
    assert "vendor/LICENSE" in assets["vendor/nested/resource.txt"].notice
    assert all(entry.license_id is None for entry in snapshot.files)


def test_audit_uses_verified_vibe_notice_for_finance_skill_provenance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "reference"
    _write(root / "LICENSE", MIT)
    _write(root / "THIRD_PARTY_NOTICES_VIBE.md", VIBE_NOTICE)
    skill_path = "app/backend/skills/finance/value-investing/SKILL.md"
    _write(root / skill_path, "# Value investing\n")

    snapshot = build_reference_snapshot(root)
    assets = {asset.path: asset for asset in audit_assets(root, snapshot)}

    skill = assets[skill_path]
    assert skill.origin == "Vibe-Trading"
    assert skill.license_id == "MIT"
    assert skill.redistributable is True
    assert "Vibe-Trading Contributors" in skill.notice
    assert "THIRD_PARTY_NOTICES_VIBE.md" in skill.notice


def test_audit_excludes_missing_and_unknown_license_scopes(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    _write(missing_root / "src" / "unlicensed.py", "pass\n")
    missing = audit_assets(missing_root, build_reference_snapshot(missing_root))

    unknown_root = tmp_path / "unknown"
    _write(unknown_root / "LICENSE", MIT)
    _write(unknown_root / "safe.py", "pass\n")
    _write(unknown_root / "vendor" / "LICENSE", "All rights reserved")
    _write(unknown_root / "vendor" / "nested" / "blocked.txt", "blocked")
    unknown = {
        asset.path: asset
        for asset in audit_assets(unknown_root, build_reference_snapshot(unknown_root))
    }

    assert len(missing) == 1
    assert missing[0].license_id == "UNKNOWN"
    assert missing[0].redistributable is False
    assert unknown["safe.py"].redistributable is True
    assert unknown["vendor/nested/blocked.txt"].license_id == "UNKNOWN"
    assert unknown["vendor/nested/blocked.txt"].redistributable is False


def test_audit_cli_writes_metadata_outside_reference_only(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    output = tmp_path / "third-party-assets.json"
    snapshot_path = tmp_path / "snapshot.json"
    _write(root / "LICENSE", MIT)
    _write(root / "src" / "main.py", "pass\n")
    snapshot = build_reference_snapshot(root)
    snapshot_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    script = ROOT / "scripts" / "audit_ai_hedge_fund_assets.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(root),
            "--snapshot",
            str(snapshot_path),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(root),
            "--snapshot",
            str(snapshot_path),
            "--output",
            str(root / "inventory.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    inventory = json.loads(output.read_text(encoding="utf-8"))
    assert inventory["snapshot_digest"] == snapshot.root_digest
    assert inventory["assets"][0]["notice"]
    assert refused.returncode != 0
    assert "outside the reference root" in refused.stderr


def test_checked_in_inventory_matches_clean_snapshot_and_explicit_origins() -> None:
    snapshot = ReferenceSnapshot.model_validate_json(
        (ROOT / "tests" / "compat" / "reference-snapshot.json").read_text(encoding="utf-8")
    )
    inventory = AssetInventory.model_validate_json(
        (ROOT / "docs" / "compatibility" / "third-party-assets.json").read_text(encoding="utf-8")
    )
    paths = {entry.path for entry in snapshot.files}
    private_paths = {
        path
        for path in paths
        if path.startswith((".ai_hedge_fund/", ".v2_cache/"))
        or path == ".claude/settings.local.json"
        or path.endswith(
            tuple(
                f"{suffix}-{sidecar}"
                for suffix in (".db", ".sqlite", ".sqlite3")
                for sidecar in ("journal", "shm", "wal")
            )
        )
    }

    assert private_paths == set()
    assert inventory.snapshot_digest == snapshot.root_digest
    assert {asset.path for asset in inventory.assets} == paths
    assert all(entry.license_id is None for entry in snapshot.files)
    assert all(
        asset.origin == "Vibe-Trading"
        for asset in inventory.assets
        if asset.path.startswith("app/backend/skills/finance/")
    )


def test_built_wheel_keeps_heavy_packages_optional_and_app_is_complete(tmp_path: Path) -> None:
    destination = tmp_path / "dist"
    completed = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(destination)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    wheel = next(destination.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]

    def enabled_for(extra: str) -> set[str]:
        environment = {"extra": extra}
        return {
            canonicalize_name(requirement.name)
            for requirement in requirements
            if requirement.marker is None or requirement.marker.evaluate(environment)
        }

    base = enabled_for("")
    quant = enabled_for("quant")
    ml = enabled_for("ml")
    app = enabled_for("app")
    quant_expected = {
        "numpy",
        "pandas",
        "scipy",
        "scikit-learn",
        "statsmodels",
        "ta",
        "vadersentiment",
        "joblib",
        "matplotlib",
    }
    agents_expected = {"deepagents", "langgraph-sdk", "openai", "mcp"}

    assert base.isdisjoint({"numpy", "pandas", "tensorflow"})
    assert quant >= quant_expected
    assert ml >= {"tensorflow"}
    assert app >= quant_expected | {"tensorflow"} | agents_expected
