"""Frozen, family-aware train/holdout/scorecard assignment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sre_graph.evals.runner import dataset_fingerprint
from sre_graph.evals.scenarios import load_suite

Split = Literal["train", "holdout", "scorecard"]
SPLITS: tuple[Split, ...] = ("train", "holdout", "scorecard")
DEFAULT_MANIFEST = Path(__file__).with_name("dataset.json")


class CaseAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scenario: str
    split: Split
    stratum: Literal["eks", "rds"]
    family: str = Field(min_length=1)

    @property
    def nodeid(self) -> str:
        return f"evals/harness/{self.split}/test_rca.py::test_rca[{self.scenario}]"


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: tuple[CaseAssignment, ...]

    def select(self, split: Split) -> tuple[CaseAssignment, ...]:
        return tuple(case for case in self.cases if case.split == split)

    @property
    def fingerprint(self) -> str:
        value = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()


def validate_manifest(value: Any) -> DatasetManifest:
    manifest = DatasetManifest.model_validate(value)
    keys = [case.scenario for case in manifest.cases]
    if len(keys) != len(set(keys)):
        raise ValueError("scenario assignments must be unique")
    scenarios = load_suite("all")
    if set(keys) != {scenario.key for scenario in scenarios}:
        raise ValueError("manifest must cover the complete corpus exactly once")
    if manifest.dataset_fingerprint != dataset_fingerprint(scenarios):
        raise ValueError("dataset fingerprint changed; review and version the manifest")
    families: dict[str, set[str]] = {}
    evidence_splits: dict[str, set[str]] = {}
    by_id = {scenario.key: scenario for scenario in scenarios}
    for case in manifest.cases:
        expected_stratum = "eks" if by_id[case.scenario].suite == "eks" else "rds"
        if case.stratum != expected_stratum:
            raise ValueError("incorrect case stratum")
        families.setdefault(case.family, set()).add(case.split)
        # Exact observation duplicates must stay together even if someone renames a family.
        evidence = json.dumps(by_id[case.scenario].evidence, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(evidence.encode()).hexdigest()
        evidence_splits.setdefault(digest, set()).add(case.split)
    if any(len(splits) > 1 for splits in families.values()):
        raise ValueError("incident family crosses splits")
    if any(len(splits) > 1 for splits in evidence_splits.values()):
        raise ValueError("duplicate evidence crosses splits")
    for split in SPLITS:
        if {case.stratum for case in manifest.select(split)} != {"eks", "rds"}:
            raise ValueError(f"{split} must contain EKS and RDS cases")
    return manifest


def load_manifest(path: Path = DEFAULT_MANIFEST) -> DatasetManifest:
    return validate_manifest(json.loads(path.read_text(encoding="utf-8")))
