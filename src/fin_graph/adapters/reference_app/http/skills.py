"""Read-only legacy routes over the inert bundled finance-skill catalog."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from fin_graph.skills import SkillRecord, finance_skill_catalog, read_skill_resource
from fin_graph.skills.loader import read_skill_document

router = APIRouter(prefix="/v2/skills")
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n", re.DOTALL)
_MAX_RESOURCE_BYTES = 512 * 1024


def _record(skill_id: str) -> SkillRecord:
    try:
        return next(item for item in finance_skill_catalog() if item.id == skill_id)
    except StopIteration as exc:
        raise HTTPException(404, f"Unknown skill: {skill_id}") from exc


def _summary(record: SkillRecord) -> dict[str, object]:
    return {
        "skill_id": record.id,
        "name": record.title,
        "description": record.description,
        "category": record.category,
        "resources": list(record.resources),
    }


@router.get("")
async def list_skills() -> list[dict[str, object]]:
    return [_summary(record) for record in finance_skill_catalog()]


@router.get("/{skill_id}/resources/{resource_path:path}")
async def skill_resource(skill_id: str, resource_path: str) -> dict[str, object]:
    _record(skill_id)
    try:
        resource = read_skill_resource(skill_id, resource_path, _MAX_RESOURCE_BYTES)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Skill resource not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "skill_id": skill_id,
        "resource_path": resource.path,
        "content": resource.content,
    }


@router.get("/{skill_id}")
async def skill_document(skill_id: str) -> dict[str, object]:
    record = _record(skill_id)
    try:
        document = read_skill_document(skill_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, f"Unknown skill: {skill_id}") from exc
    body = _FRONTMATTER.sub("", document, count=1).strip()
    return {**_summary(record), "body": body}
