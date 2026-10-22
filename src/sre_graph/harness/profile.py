"""Production configuration, independent of optional optimization dependencies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sre_graph.graphs.investigation.graph import InvestigationLimits
from sre_graph.harness.surfaces import current_values


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SRE_HARNESS_",
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


@dataclass(frozen=True)
class RuntimeProfile:
    variant: str
    limits: InvestigationLimits

    def metadata(self) -> dict[str, Any]:
        return {
            "mode": "incident",
            "version": 1,
            "variant": self.variant,
            "limits": asdict(self.limits),
            "contract": "read-only-specialists-v1",
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
        raise ValueError("installed harness variant does not match SRE_HARNESS_EXPECTED_VARIANT")
    return RuntimeProfile(variant=variant, limits=active_limits)
