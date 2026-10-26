"""Production configuration, independent of optional optimization dependencies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from fin_graph.graphs.analysis.graph import AnalysisLimits
from fin_graph.graphs.investigation.graph import InvestigationLimits
from fin_graph.harness.surfaces import current_values


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIN_HARNESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )
    max_waves: int = Field(default=3, ge=1, le=3)
    max_tasks_per_wave: int = Field(default=5, ge=1, le=5)
    max_total_tasks: int = Field(default=15, ge=1, le=15)
    deadline_seconds: int = Field(default=180, ge=1, le=1800)
    expected_variant: str | None = None


class AnalysisRuntimeSettings(BaseSettings):
    """``FIN_ANALYSIS_*`` budgets; a 3B local model needs a longer deadline than a hosted one."""

    model_config = SettingsConfigDict(
        env_prefix="FIN_ANALYSIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )
    max_waves: int = Field(default=2, ge=1, le=3)
    max_tasks_per_wave: int = Field(default=6, ge=1, le=6)
    max_total_tasks: int = Field(default=12, ge=1, le=18)
    deadline_seconds: int = Field(default=600, ge=1, le=3600)
    #: The reviewed variant production must run; candidates under evaluation skip the check.
    expected_variant: str | None = None


@dataclass(frozen=True)
class RuntimeProfile:
    variant: str
    limits: InvestigationLimits | AnalysisLimits
    mode: str = "incident"
    contract: str = "read-only-specialists-v1"

    def metadata(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "version": 1,
            "variant": self.variant,
            "limits": asdict(self.limits),
            "contract": self.contract,
        }


def resolve_profile(
    *, limits: InvestigationLimits | None = None, verify_variant_pin: bool = True
) -> RuntimeProfile:
    settings = RuntimeSettings()
    active_limits = limits or InvestigationLimits(
        **settings.model_dump(exclude={"expected_variant"})
    )
    # Explicit limits (fixtures/replays) are validated by the same policy.
    RuntimeSettings(**asdict(active_limits))
    encoded = json.dumps(current_values(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    variant = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if (
        verify_variant_pin
        and settings.expected_variant is not None
        and settings.expected_variant != variant
    ):
        raise ValueError("installed harness variant does not match FIN_HARNESS_EXPECTED_VARIANT")
    return RuntimeProfile(variant=variant, limits=active_limits)


def analysis_surface_values() -> dict[str, str]:
    """The analysis prompts and strategy files: the code a resumed run must match."""
    from fin_graph.graphs.analysis import prompts as analysis_prompts

    strategy = Path(__file__).resolve().parents[1] / "graphs" / "analysis" / "strategy"
    values = {
        name: str(value)
        for name, value in vars(analysis_prompts).items()
        if name.endswith("_PROMPT") and isinstance(value, str)
    }
    for path in sorted(strategy.iterdir()):
        if path.suffix in {".py", ".md"}:
            values[f"strategy/{path.name}"] = path.read_text(encoding="utf-8")
    return values


def resolve_analysis_profile(
    *, limits: AnalysisLimits | None = None, verify_variant_pin: bool = True
) -> RuntimeProfile:
    """The analysis budgets from ``FIN_ANALYSIS_*`` (or explicit ones) and the code fingerprint."""
    settings = AnalysisRuntimeSettings()
    active = limits or AnalysisLimits(**settings.model_dump(exclude={"expected_variant"}))
    AnalysisRuntimeSettings(**asdict(active))
    encoded = json.dumps(analysis_surface_values(), sort_keys=True, ensure_ascii=False)
    variant = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if (
        verify_variant_pin
        and settings.expected_variant is not None
        and settings.expected_variant != variant
    ):
        raise ValueError("installed analysis variant does not match FIN_ANALYSIS_EXPECTED_VARIANT")
    return RuntimeProfile(
        variant=variant, limits=active, mode="analysis", contract="read-only-analysts-v1"
    )
