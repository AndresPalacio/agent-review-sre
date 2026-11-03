"""License-scope audit for assets named by a frozen reference snapshot."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from fin_graph.compat.reference import ReferenceFile, ReferenceSnapshot

_LICENSE_NAMES = frozenset({"license", "license.md", "license.txt"})
_REDISTRIBUTABLE = frozenset({"MIT", "Apache-2.0"})


class AssetLicense(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    origin: str
    license_id: str
    redistributable: bool
    notice: str


class _LicenseScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    directory: str
    document: str
    license_id: str


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
            )
        )

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
        origin = "AI Hedge Fund" if not scope.directory else f"AI Hedge Fund/{scope.directory}"
        notice = (
            f"Covered by {scope.document} ({scope.license_id}); preserve its license terms."
            if redistributable
            else f"Unrecognized license document {scope.document}; excluded."
        )
        assets.append(
            AssetLicense(
                path=entry.path,
                origin=origin,
                license_id=scope.license_id,
                redistributable=redistributable,
                notice=notice,
            )
        )
    return tuple(assets)
