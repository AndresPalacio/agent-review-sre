"""Strict differential comparison for frozen compatibility contracts."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

_DEFAULT_ABS_TOL = 1e-9
_DEFAULT_REL_TOL = 1e-7
_VOLATILE_VALUE = "<volatile>"
_PATH_PATTERN = re.compile(r'^\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\]|\["(?:[^"\\]|\\.)*"\])*$')


def _validated_path(path: str) -> str:
    if "*" in path or ".." in path:
        raise ValueError("JSON paths may not contain wildcards or recursive descent")
    if not _PATH_PATTERN.fullmatch(path):
        raise ValueError(f"unsupported JSON path: {path}")
    return path


class ComparisonRule(BaseModel):
    """An exact-path override for one comparison node."""

    model_config = ConfigDict(frozen=True)

    path: str
    mode: Literal["exact", "float", "volatile"]
    abs_tol: float = Field(default=0, ge=0)
    rel_tol: float = Field(default=0, ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validated_path(value)


class ContractMismatch(BaseModel):
    """One candidate difference at a stable JSON path."""

    model_config = ConfigDict(frozen=True)

    path: str
    reference: JsonValue
    candidate: JsonValue
    reason: str


def _child_path(parent: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    if key.isidentifier() and key.isascii():
        return f"{parent}.{key}"
    encoded = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    return f"{parent}[{encoded}]"


def _strict_equal(reference: JsonValue, candidate: JsonValue) -> bool:
    if type(reference) is not type(candidate):
        return False
    if isinstance(reference, dict) and isinstance(candidate, dict):
        return reference.keys() == candidate.keys() and all(
            _strict_equal(reference[key], candidate[key]) for key in reference
        )
    if isinstance(reference, list) and isinstance(candidate, list):
        return len(reference) == len(candidate) and all(
            _strict_equal(left, right) for left, right in zip(reference, candidate, strict=True)
        )
    return reference == candidate


def normalize_contract(value: JsonValue, volatile_paths: frozenset[str]) -> JsonValue:
    """Return a copy with only explicitly named volatile nodes replaced."""

    for path in volatile_paths:
        _validated_path(path)

    def normalize(current: JsonValue, path: str) -> JsonValue:
        if path in volatile_paths:
            return _VOLATILE_VALUE
        if isinstance(current, dict):
            return {key: normalize(child, _child_path(path, key)) for key, child in current.items()}
        if isinstance(current, list):
            return [
                normalize(child, _child_path(path, index)) for index, child in enumerate(current)
            ]
        return current

    return normalize(value, "$")


def compare_contract(
    reference: JsonValue,
    candidate: JsonValue,
    rules: Sequence[ComparisonRule],
) -> list[ContractMismatch]:
    """Compare JSON-compatible values without hiding unlisted business differences."""

    rules_by_path: dict[str, ComparisonRule] = {}
    for rule in rules:
        if rule.path in rules_by_path:
            raise ValueError(f"duplicate comparison rule for {rule.path}")
        rules_by_path[rule.path] = rule

    mismatches: list[ContractMismatch] = []

    def mismatch(path: str, left: JsonValue, right: JsonValue, reason: str) -> None:
        mismatches.append(
            ContractMismatch(path=path, reference=left, candidate=right, reason=reason)
        )

    def compare(left: JsonValue, right: JsonValue, path: str) -> None:
        rule = rules_by_path.get(path)
        if rule is not None:
            if rule.mode == "volatile":
                return
            if rule.mode == "exact":
                if not _strict_equal(left, right):
                    mismatch(path, left, right, "values differ")
                return
            if (
                isinstance(left, bool)
                or isinstance(right, bool)
                or not isinstance(left, (int, float))
                or not isinstance(right, (int, float))
            ):
                mismatch(path, left, right, "float rule requires numeric values")
                return
            if not math.isfinite(left) or not math.isfinite(right):
                mismatch(path, left, right, "non-finite float")
                return
            if not math.isclose(left, right, abs_tol=rule.abs_tol, rel_tol=rule.rel_tol):
                mismatch(path, left, right, "float tolerance exceeded")
            return

        if type(left) is not type(right):
            mismatch(path, left, right, "types differ")
            return
        if isinstance(left, dict) and isinstance(right, dict):
            left_keys = set(left)
            right_keys = set(right)
            for key in sorted(left_keys | right_keys):
                child_path = _child_path(path, key)
                if key not in right:
                    mismatch(child_path, left[key], None, "missing candidate field")
                elif key not in left:
                    mismatch(child_path, None, right[key], "unexpected candidate field")
                else:
                    compare(left[key], right[key], child_path)
            return
        if isinstance(left, list) and isinstance(right, list):
            shared = min(len(left), len(right))
            for index in range(shared):
                compare(left[index], right[index], _child_path(path, index))
            for index in range(shared, len(left)):
                mismatch(_child_path(path, index), left[index], None, "missing candidate item")
            for index in range(shared, len(right)):
                mismatch(_child_path(path, index), None, right[index], "unexpected candidate item")
            return
        if isinstance(left, float) and isinstance(right, float):
            if not math.isfinite(left) or not math.isfinite(right):
                mismatch(path, left, right, "non-finite float")
            elif not math.isclose(
                left,
                right,
                abs_tol=_DEFAULT_ABS_TOL,
                rel_tol=_DEFAULT_REL_TOL,
            ):
                mismatch(path, left, right, "float tolerance exceeded")
            return
        if left != right:
            mismatch(path, left, right, "values differ")

    compare(reference, candidate, "$")
    return mismatches
