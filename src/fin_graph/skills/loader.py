"""Path-bounded loader for bundled or filesystem-backed skill catalogs."""

from __future__ import annotations

import hashlib
import re
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath, PureWindowsPath

from fin_graph.skills.models import SkillRecord, SkillResource

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
_SKILL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_MIME_TYPES = {
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".txt": "text/plain",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def bundled_finance_root() -> Traversable:
    return resources.files("fin_graph.skills").joinpath("finance")


def _metadata(document: str) -> dict[str, str]:
    match = _FRONTMATTER.match(document)
    if match is None:
        raise ValueError("SKILL.md must start with bounded frontmatter")
    values: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"invalid skill frontmatter line: {raw_line!r}")
        key, raw_value = line.split(":", 1)
        key, value = key.strip(), raw_value.strip()
        if not key or key in values:
            raise ValueError(f"invalid or duplicate skill frontmatter key: {key!r}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _is_symlink(item: Traversable) -> bool:
    return isinstance(item, Path) and item.is_symlink()


def _walk_files(directory: Traversable, prefix: str = "") -> tuple[tuple[str, Traversable], ...]:
    files: list[tuple[str, Traversable]] = []
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        relative = f"{prefix}/{child.name}" if prefix else child.name
        if _is_symlink(child):
            raise ValueError(f"skill resource may not be a symlink: {relative}")
        if child.is_dir():
            files.extend(_walk_files(child, relative))
        elif child.is_file():
            files.append((relative, child))
    return tuple(files)


def _read_utf8(item: Traversable, label: str) -> str:
    try:
        return item.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"skill resource is not valid UTF-8: {label}") from exc


def load_skill_catalog(root: Traversable) -> tuple[SkillRecord, ...]:
    """Discover direct child skills without importing or executing support scripts."""

    if not root.is_dir():
        raise FileNotFoundError(f"skill root does not exist: {root}")
    records: list[SkillRecord] = []
    seen: set[str] = set()
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        if _is_symlink(directory):
            raise ValueError(f"skill directory may not be a symlink: {directory.name}")
        if not directory.is_dir():
            continue
        skill_file = directory.joinpath("SKILL.md")
        if not skill_file.is_file():
            raise ValueError(f"skill {directory.name!r} has no SKILL.md")
        document = _read_utf8(skill_file, f"{directory.name}/SKILL.md")
        metadata = _metadata(document)
        skill_id = metadata.get("name", "")
        description = metadata.get("description", "")
        category = metadata.get("category", "")
        if not _SKILL_ID.fullmatch(skill_id):
            raise ValueError(f"invalid skill id: {skill_id!r}")
        if skill_id != directory.name:
            raise ValueError(
                f"skill id {skill_id!r} does not match directory {directory.name!r}"
            )
        if not description:
            raise ValueError(f"skill {skill_id!r} requires a description")
        if not category:
            raise ValueError(f"skill {skill_id!r} requires a category")
        if skill_id in seen:
            raise ValueError(f"duplicate skill id: {skill_id}")
        seen.add(skill_id)
        support = tuple(path for path, _ in _walk_files(directory) if path != "SKILL.md")
        records.append(
            SkillRecord(
                id=skill_id,
                title=skill_id,
                description=description,
                category=category,
                root=str(directory),
                resources=support,
            )
        )
    return tuple(sorted(records, key=lambda item: item.id))


def _safe_resource_path(resource_path: str) -> tuple[str, ...]:
    if not resource_path or "\x00" in resource_path or "\\" in resource_path:
        raise ValueError("invalid skill resource path")
    raw_parts = resource_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("skill resource traversal is forbidden")
    posix = PurePosixPath(resource_path)
    windows = PureWindowsPath(resource_path)
    if posix.is_absolute() or windows.is_absolute():
        raise ValueError("absolute skill resource paths are forbidden")
    return posix.parts


def _resolve_skill(
    skill_id: str, root: Traversable
) -> tuple[SkillRecord, Traversable]:
    records = load_skill_catalog(root)
    try:
        record = next(item for item in records if item.id == skill_id)
    except StopIteration as exc:
        raise KeyError(f"Unknown skill: {skill_id}") from exc
    return record, root.joinpath(skill_id)


def read_skill_document(skill_id: str, *, root: Traversable | None = None) -> str:
    selected_root = root or bundled_finance_root()
    _, directory = _resolve_skill(skill_id, selected_root)
    return _read_utf8(directory.joinpath("SKILL.md"), f"{skill_id}/SKILL.md")


def read_skill_resource(
    skill_id: str,
    resource_path: str,
    max_bytes: int,
    *,
    root: Traversable | None = None,
) -> SkillResource:
    """Read one catalogued UTF-8 resource as inert text, never executable code."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    parts = _safe_resource_path(resource_path)
    selected_root = root or bundled_finance_root()
    record, directory = _resolve_skill(skill_id, selected_root)
    normalized = "/".join(parts)
    if normalized not in record.resources:
        raise FileNotFoundError(resource_path)
    candidate = directory.joinpath(*parts)
    if isinstance(directory, Path) and isinstance(candidate, Path):
        if candidate.is_symlink():
            raise ValueError("skill resource symlinks are forbidden")
        try:
            candidate.resolve(strict=True).relative_to(directory.resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError("skill resource is outside its skill directory") from exc
    with candidate.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"skill resource exceeds {max_bytes} bytes")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("skill resource is not valid UTF-8") from exc
    mime_type = _MIME_TYPES.get(PurePosixPath(normalized).suffix.lower(), "text/plain")
    return SkillResource(
        skill_id=skill_id,
        path=normalized,
        mime_type=mime_type,
        content=content,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
