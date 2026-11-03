from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_PROVENANCE_FIELDS = frozenset({"snapshot_digest", "source_file", "recorder_version", "license"})


def assert_fixture_provenance(manifest: Mapping[str, Any]) -> None:
    fixtures = manifest.get("fixtures")
    assert isinstance(fixtures, list)
    for entry in fixtures:
        assert isinstance(entry, dict)
        assert entry.keys() >= _PROVENANCE_FIELDS
        assert all(entry[field] for field in _PROVENANCE_FIELDS)
