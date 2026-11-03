from __future__ import annotations

import json
from pathlib import Path

from tests.compat.oracle import assert_fixture_provenance

COMPAT = Path(__file__).parent


def test_every_recorded_v1_fixture_has_frozen_source_provenance() -> None:
    manifest = json.loads((COMPAT / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((COMPAT / "reference-snapshot.json").read_text(encoding="utf-8"))
    assert_fixture_provenance(manifest)

    recorded = {
        str(path.relative_to(COMPAT / "fixtures")).replace("\\", "/")
        for path in (COMPAT / "fixtures" / "v1").rglob("*.json")
    }
    entries = {entry["fixture"]: entry for entry in manifest["fixtures"]}
    assert entries.keys() >= recorded
    assert {entry["snapshot_digest"] for entry in entries.values()} == {snapshot["root_digest"]}

    frozen_hashes = {entry["path"]: entry["sha256"] for entry in snapshot["files"]}
    for entry in entries.values():
        assert entry["source_hashes"]
        for source_file, digest in entry["source_hashes"].items():
            assert frozen_hashes[source_file] == digest
