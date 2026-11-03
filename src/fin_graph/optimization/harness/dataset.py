"""Frozen, family-aware train/holdout/scorecard assignment of the equity scenarios.

Two strata keep every split balanced: ``fundamental`` cases turn on statements and valuation,
``market`` cases on prices, sentiment or missing data. A family groups scenarios that share a
company's data (``012-two-tickers`` reuses the deep-value and overvalued companies), so a split
never sees a company another split was tuned on.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.evals.market.scenarios import dataset_fingerprint, load_equity_suite

Split = Literal["train", "holdout", "scorecard"]
Stratum = Literal["fundamental", "market"]
SPLITS: tuple[Split, ...] = ("train", "holdout", "scorecard")
STRATA: tuple[Stratum, ...] = ("fundamental", "market")
DEFAULT_MANIFEST = Path(__file__).with_name("dataset.json")


class CaseAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scenario: str
    split: Split
    stratum: Stratum
    family: str = Field(min_length=1)

    @property
    def nodeid(self) -> str:
        return f"evals/harness/{self.split}/test_analysis.py::test_analysis[{self.scenario}]"


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[2]
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: tuple[CaseAssignment, ...]

    def select(self, split: Split) -> tuple[CaseAssignment, ...]:
        return tuple(case for case in self.cases if case.split == split)

    @property
    def fingerprint(self) -> str:
        value = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()


def _company_digests(scenario_key: str) -> set[str]:
    """One digest per ticker folder: identical company data must stay in one split."""
    scenario = next(item for item in load_equity_suite("all") if item.key == scenario_key)
    digests = set()
    for folder in sorted(path for path in scenario.directory.iterdir() if path.is_dir()):
        digest = hashlib.sha256()
        for path in sorted(folder.iterdir()):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        digests.add(digest.hexdigest())
    return digests


def validate_manifest(value: Any) -> DatasetManifest:
    manifest = DatasetManifest.model_validate(value)
    keys = [case.scenario for case in manifest.cases]
    if len(keys) != len(set(keys)):
        raise ValueError("scenario assignments must be unique")
    scenarios = load_equity_suite("all")
    if set(keys) != {scenario.key for scenario in scenarios}:
        raise ValueError("manifest must cover the complete corpus exactly once")
    if manifest.dataset_fingerprint != dataset_fingerprint(scenarios):
        raise ValueError("dataset fingerprint changed; review and version the manifest")
    families: dict[str, set[str]] = {}
    company_splits: dict[str, set[str]] = {}
    for case in manifest.cases:
        families.setdefault(case.family, set()).add(case.split)
        for digest in _company_digests(case.scenario):
            company_splits.setdefault(digest, set()).add(case.split)
    if any(len(splits) > 1 for splits in families.values()):
        raise ValueError("a company family crosses splits")
    if any(len(splits) > 1 for splits in company_splits.values()):
        raise ValueError("identical company data crosses splits")
    for split in SPLITS:
        if {case.stratum for case in manifest.select(split)} != set(STRATA):
            raise ValueError(f"{split} must contain fundamental and market cases")
    return manifest


def load_manifest(path: Path = DEFAULT_MANIFEST) -> DatasetManifest:
    return validate_manifest(json.loads(path.read_text(encoding="utf-8")))
