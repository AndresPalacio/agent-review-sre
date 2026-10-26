"""Load the 36 attributed, immutable OpenSRE evaluation scenarios."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from fin_graph.domain.alerts.parsing import parse_alert
from fin_graph.domain.window import DEFAULT_FORWARD_BUFFER

DATA_ROOT = Path(__file__).with_name("data")
SOURCE_SUITES = ("eks", "rds_postgres", "rds_upstream")
SMOKE = (
    ("eks", "000-healthy"),
    ("eks", "001-oomkilled-crashloop"),
    ("rds_postgres", "001-replication-lag"),
)
_SAFE_ID = re.compile(r"[0-9]{3}-[a-z0-9-]+\Z")


class Answer(BaseModel):
    root_cause_category: str
    required_keywords: list[str] = Field(default_factory=list)
    forbidden_categories: list[str] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    required_evidence_sources: list[str] = Field(default_factory=list)
    equivalent_root_cause_categories: list[str] = Field(default_factory=list)
    optimal_trajectory: list[str] = Field(default_factory=list)
    required_queries: list[str] = Field(default_factory=list)
    ruling_out_keywords: list[str] = Field(default_factory=list)
    max_investigation_loops: int = 1
    model_response: str = ""
    golden_trajectory: dict[str, Any] | None = None


@dataclass(frozen=True)
class Scenario:
    suite: str
    scenario_id: str
    alert: dict[str, Any]
    metadata: dict[str, Any]
    answer: Answer
    evidence: dict[str, dict[str, Any]]

    @property
    def key(self) -> str:
        return f"{self.suite}/{self.scenario_id}"

    @property
    def reference_time(self) -> datetime:
        """Replay at the recorded query end, independently of expected RCA answers."""
        query_ends = [
            moment
            for source in self.evidence.values()
            for moment in _recorded_times({"end_time": source.get("end_time")})
        ]
        if query_ends:
            return max(query_ends)
        started_at = parse_alert(self.alert).started_at
        if started_at is not None:
            return started_at + DEFAULT_FORWARD_BUFFER
        observations = list(_recorded_times(self.evidence))
        if observations:
            # The graph uses half-open windows; include the last observation.
            return max(observations) + timedelta(seconds=1)
        raise ValueError(f"scenario has no recorded time: {self.key}")


def _recorded_times(value: Any, field: str = "") -> Iterator[datetime]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _recorded_times(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _recorded_times(item, field)
    elif isinstance(value, str) and field in {
        "timestamp",
        "timestamps",
        "ts",
        "date",
        "start_time",
        "end_time",
    }:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return
        if moment.tzinfo is not None:
            yield moment.astimezone(UTC)


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _yaml_object(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected YAML object: {path.name}")
    return value


def _file(directory: Path, base: Path | None, name: str) -> Path:
    for candidate in (directory / name, base / name if base else None):
        if candidate is not None and candidate.is_file():
            return candidate
    raise FileNotFoundError(f"missing scenario fixture: {name}")


def _cloudwatch(directory: Path, base: Path | None) -> dict[str, Any]:
    for candidate in (directory, base):
        if candidate is None:
            continue
        combined = candidate / "aws_cloudwatch_metrics.json"
        if combined.is_file():
            return _json_object(combined)
        envelope = candidate / "aws_cloudwatch_metrics_envelope.json"
        if envelope.is_file():
            data = _json_object(envelope)
            files = sorted(
                candidate.glob("aws_cloudwatch_metrics_*.json"), key=lambda path: path.name
            )
            data["metric_data_results"] = [_json_object(path) for path in files if path != envelope]
            return data
    raise FileNotFoundError("missing scenario fixture: aws_cloudwatch_metrics")


def load_scenario(suite: str, scenario_id: str) -> Scenario:
    """Load one scenario, rejecting paths outside the three bundled suites."""
    if suite not in SOURCE_SUITES or not _SAFE_ID.fullmatch(scenario_id):
        raise ValueError("invalid scenario suite or id")
    directory = DATA_ROOT / suite / scenario_id
    if not directory.is_dir():
        raise FileNotFoundError(f"scenario does not exist: {suite}/{scenario_id}")
    metadata = _yaml_object(directory / "scenario.yml")
    base: Path | None = None
    base_id = metadata.pop("base", None)
    if base_id is not None:
        if not isinstance(base_id, str) or not _SAFE_ID.fullmatch(base_id):
            raise ValueError("invalid scenario base")
        base = DATA_ROOT / suite / base_id
        base_metadata = _yaml_object(base / "scenario.yml")
        if "base" in base_metadata:
            raise ValueError("chained scenario inheritance is not supported")
        metadata = base_metadata | metadata
    if metadata.get("scenario_id") != scenario_id:
        raise ValueError("scenario id disagrees with directory")
    sources = metadata.get("available_evidence")
    if not isinstance(sources, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[a-z0-9_]+", item) for item in sources
    ):
        raise ValueError("invalid available_evidence")
    evidence = {
        source: (
            _cloudwatch(directory, base)
            if source == "aws_cloudwatch_metrics"
            else _json_object(_file(directory, base, f"{source}.json"))
        )
        for source in sources
    }
    return Scenario(
        suite=suite,
        scenario_id=scenario_id,
        alert=_json_object(_file(directory, base, "alert.json")),
        metadata=metadata,
        answer=Answer.model_validate(_yaml_object(directory / "answer.yml")),
        evidence=evidence,
    )


def load_suite(suite: str) -> list[Scenario]:
    """Select a stable fixture suite, including one cross-system RDS scenario."""
    if suite == "smoke":
        return [load_scenario(group, scenario_id) for group, scenario_id in SMOKE]
    groups = {
        "eks": ("eks",),
        "rds": ("rds_postgres", "rds_upstream"),
        "all": SOURCE_SUITES,
    }.get(suite)
    if groups is None:
        raise ValueError("unknown evaluation suite")
    return [
        load_scenario(group, directory.name)
        for group in groups
        for directory in sorted((DATA_ROOT / group).iterdir())
        if directory.is_dir() and _SAFE_ID.fullmatch(directory.name)
    ]
