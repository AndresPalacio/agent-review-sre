"""Allowlisted production surfaces; code edits run only in copied workspaces."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from sre_graph.graphs.investigation import prompts

SURFACES = {
    "plan": "PLAN_PROMPT",
    "specialist": "SPECIALIST_PROMPT",
    "synthesis": "SYNTHESIS_PROMPT",
    "judge": "JUDGE_PROMPT",
}
PROMPT_MODULE = "sre_graph.graphs.investigation.prompts"
_STRATEGY = "src/sre_graph/graphs/investigation/strategy/"
FILE_SURFACES = {
    "tools": _STRATEGY + "tools.py",
    "skills": _STRATEGY + "skills.md",
    "planning": _STRATEGY + "planning.py",
    "middleware": _STRATEGY + "middleware.py",
    "middleware_registration": _STRATEGY + "registration.py",
}
ALL_SURFACES = set(SURFACES) | set(FILE_SURFACES)


def validate_values(values: Mapping[str, str], *, complete: bool = False) -> None:
    if not set(values) <= ALL_SURFACES or (complete and set(values) != ALL_SURFACES):
        raise ValueError("unknown or missing harness surface")
    for name, value in values.items():
        limit = 96000 if name in FILE_SURFACES and name != "skills" else 24000
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"invalid surface value: {name}")
        if name in FILE_SURFACES and FILE_SURFACES[name].endswith(".py"):
            try:
                ast.parse(value, filename=FILE_SURFACES[name])
                compile(value, FILE_SURFACES[name], "exec")
            except (SyntaxError, ValueError) as exc:
                raise ValueError(f"invalid Python surface: {name}") from exc


def current_values() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    return {name: str(getattr(prompts, attribute)) for name, attribute in SURFACES.items()} | {
        name: (root / target.removeprefix("src/sre_graph/")).read_text(encoding="utf-8")
        for name, target in FILE_SURFACES.items()
    }


@contextmanager
def apply_surfaces(values: Mapping[str, str]) -> Iterator[None]:
    """Only use in the standalone harness process, never in a concurrent API server."""
    validate_values(values)
    if set(values) - set(SURFACES):
        raise ValueError("file surfaces require an isolated worker")
    originals = {name: str(getattr(prompts, attr)) for name, attr in SURFACES.items()}
    try:
        for name, value in values.items():
            setattr(prompts, SURFACES[name], value)
        yield
    finally:
        for name, value in originals.items():
            setattr(prompts, SURFACES[name], value)
