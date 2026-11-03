"""License-scope audit for assets named by a frozen reference snapshot."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fin_graph.conformance.reference_app.snapshot import ReferenceFile, ReferenceSnapshot

_LICENSE_NAMES = frozenset({"license", "license.md", "license.txt"})
_REDISTRIBUTABLE = frozenset({"MIT", "Apache-2.0"})
_VIBE_NOTICE = "THIRD_PARTY_NOTICES_VIBE.md"
_VIBE_SCOPE = "app/backend/skills/finance"
_VIBE_COPYRIGHT = "Copyright (c) 2026 Vibe-Trading Contributors"


class AssetLicense(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    origin: str
    license_id: str
    redistributable: bool
    notice: str


class AssetInventory(BaseModel):
    """The single authoritative, scope-aware classification of frozen assets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    redistributable_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    assets: tuple[AssetLicense, ...]

    @model_validator(mode="after")
    def validate_inventory(self) -> AssetInventory:
        paths = [asset.path for asset in self.assets]
        if len(paths) != len(set(paths)):
            raise ValueError("asset inventory contains duplicate paths")
        allowed = sum(asset.redistributable for asset in self.assets)
        if allowed != self.redistributable_count:
            raise ValueError("asset inventory redistributable count does not match assets")
        if len(self.assets) - allowed != self.excluded_count:
            raise ValueError("asset inventory excluded count does not match assets")
        for asset in self.assets:
            expected = asset.license_id in _REDISTRIBUTABLE
            if asset.redistributable is not expected:
                raise ValueError(
                    f"asset redistributable flag disagrees with verified license: {asset.path}"
                )
        return self


class _LicenseScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    directory: str
    document: str
    license_id: str
    origin: str
    notice: str | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_path(root: Path, entry: ReferenceFile) -> Path:
    candidate = (root / Path(*PurePosixPath(entry.path).parts)).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"snapshot path escapes audit root: {entry.path}") from exc
    if not candidate.is_file():
        raise ValueError(f"snapshot path is not a file: {entry.path}")
    if _sha256(candidate) != entry.sha256:
        raise ValueError(f"snapshot hash mismatch: {entry.path}")
    return candidate


def _license_id(text: str) -> str:
    lowered = text.lower()
    if "mit license" in lowered and "permission is hereby granted" in lowered:
        return "MIT"
    if "apache license" in lowered and "version 2.0" in lowered:
        return "Apache-2.0"
    return "UNKNOWN"


def _covers(scope: str, path: str) -> bool:
    return not scope or path == scope or path.startswith(f"{scope}/")


def _vibe_license_scope(verified: dict[str, Path]) -> _LicenseScope | None:
    document = verified.get(_VIBE_NOTICE)
    if document is None:
        return None
    text = document.read_text(encoding="utf-8", errors="replace")
    required = (
        "Project: Vibe-Trading",
        "`app/backend/skills/finance/`",
        _VIBE_COPYRIGHT,
        "License: MIT",
    )
    if any(marker not in text for marker in required) or _license_id(text) != "MIT":
        raise ValueError(f"invalid explicit Vibe-Trading provenance document: {_VIBE_NOTICE}")
    return _LicenseScope(
        directory=_VIBE_SCOPE,
        document=_VIBE_NOTICE,
        license_id="MIT",
        origin="Vibe-Trading",
        notice=f"Covered by {_VIBE_NOTICE} (MIT); preserve {_VIBE_COPYRIGHT}.",
    )


def audit_assets(root: Path, snapshot: ReferenceSnapshot) -> tuple[AssetLicense, ...]:
    """Classify every frozen asset using only verified explicit license documents."""

    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError(f"audit root is not a directory: {root}")

    verified: dict[str, Path] = {
        entry.path: _verified_path(resolved_root, entry) for entry in snapshot.files
    }
    scopes: list[_LicenseScope] = []
    for entry in snapshot.files:
        pure_path = PurePosixPath(entry.path)
        if pure_path.name.lower() not in _LICENSE_NAMES:
            continue
        directory = "" if str(pure_path.parent) == "." else pure_path.parent.as_posix()
        text = verified[entry.path].read_text(encoding="utf-8", errors="replace")
        scopes.append(
            _LicenseScope(
                directory=directory,
                document=entry.path,
                license_id=_license_id(text),
                origin="AI Hedge Fund" if not directory else f"AI Hedge Fund/{directory}",
            )
        )
    vibe_scope = _vibe_license_scope(verified)
    if vibe_scope is not None:
        scopes.append(vibe_scope)

    assets: list[AssetLicense] = []
    for entry in snapshot.files:
        applicable = [scope for scope in scopes if _covers(scope.directory, entry.path)]
        scope = max(
            applicable,
            key=lambda item: len(PurePosixPath(item.directory).parts),
            default=None,
        )
        if scope is None:
            assets.append(
                AssetLicense(
                    path=entry.path,
                    origin="AI Hedge Fund",
                    license_id="UNKNOWN",
                    redistributable=False,
                    notice="No recognized license document covers this path; excluded.",
                )
            )
            continue
        redistributable = scope.license_id in _REDISTRIBUTABLE
        notice = scope.notice or (
            f"Covered by {scope.document} ({scope.license_id}); preserve its license terms."
            if redistributable
            else f"Unrecognized license document {scope.document}; excluded."
        )
        assets.append(
            AssetLicense(
                path=entry.path,
                origin=scope.origin,
                license_id=scope.license_id,
                redistributable=redistributable,
                notice=notice,
            )
        )
    return tuple(assets)
