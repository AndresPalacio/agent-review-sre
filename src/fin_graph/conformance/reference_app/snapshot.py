"""Safe, content-addressed snapshots of a local reference source tree."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".cache",
        ".ai_hedge_fund",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".v2_cache",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)
_PRIVATE_SUFFIXES = frozenset({".db", ".pyc", ".pyo", ".sqlite", ".sqlite3"})
_PRIVATE_DATABASE_SIDECARS = tuple(
    f"{database_suffix}-{sidecar}"
    for database_suffix in (".db", ".sqlite", ".sqlite3")
    for sidecar in ("journal", "shm", "wal")
)
_SAFE_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})


class ReferenceFile(BaseModel):
    """One distributable candidate in the frozen source inventory."""

    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str
    size: int
    license_id: str | None


class ReferenceSnapshot(BaseModel):
    """A deterministic manifest of a reference tree without private state."""

    model_config = ConfigDict(frozen=True)

    schema_version: int
    files: tuple[ReferenceFile, ...]
    root_digest: str


def _is_excluded(relative: Path) -> bool:
    lowered_parts = tuple(part.lower() for part in relative.parts)
    if any(part in _EXCLUDED_DIRECTORIES for part in lowered_parts[:-1]):
        return True

    name = lowered_parts[-1]
    if name in _EXCLUDED_DIRECTORIES:
        return True
    if name == ".env" or (name.startswith(".env.") and name not in _SAFE_ENV_TEMPLATES):
        return True
    if lowered_parts == (".claude", "settings.local.json"):
        return True
    if name.endswith(_PRIVATE_DATABASE_SIDECARS):
        return True
    return Path(name).suffix in _PRIVATE_SUFFIXES


def _inside_root(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes reference root: {path}") from exc
    return resolved


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _aggregate_digest(files: tuple[ReferenceFile, ...]) -> str:
    digest = hashlib.sha256()
    for entry in files:
        payload = json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_reference_snapshot(root: Path) -> ReferenceSnapshot:
    """Build a stable manifest while rejecting links that escape *root*."""

    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError(f"reference root is not a directory: {root}")

    entries: list[ReferenceFile] = []
    for current, directory_names, file_names in os.walk(resolved_root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            candidate = current_path / name
            relative = candidate.relative_to(resolved_root)
            if _is_excluded(relative):
                continue
            _inside_root(candidate, resolved_root)
            kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in sorted(file_names):
            candidate = current_path / name
            relative = candidate.relative_to(resolved_root)
            if _is_excluded(relative):
                continue
            resolved_file = _inside_root(candidate, resolved_root)
            if not resolved_file.is_file():
                continue
            sha256, size = _hash_file(resolved_file)
            entries.append(
                ReferenceFile(
                    path=relative.as_posix(),
                    sha256=sha256,
                    size=size,
                    # Licensing is deliberately classified later from verified,
                    # scope-aware documents. A snapshot is only an inventory.
                    license_id=None,
                )
            )

    files = tuple(sorted(entries, key=lambda entry: entry.path))
    return ReferenceSnapshot(
        schema_version=1,
        files=files,
        root_digest=_aggregate_digest(files),
    )
