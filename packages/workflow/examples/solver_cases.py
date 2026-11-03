"""Public benchmark problems and isolated input fixtures; never model answers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from bm_workflow.agents import ProblemSpec, SolverLimits


@dataclass(frozen=True)
class Case:
    id: str
    family: Literal["configuration", "transformation", "artifact", "investigation"]
    scenario: str = "direct"
    split: Literal["contract", "holdout"] = "contract"
    variant: int = 0

    @property
    def source(self) -> str:
        return {
            "configuration": "config/service.json",
            "transformation": "data/orders.csv",
            "artifact": "docs/release.txt",
            "investigation": "incident/manifest.json",
        }[self.family]

    @property
    def destination(self) -> str:
        return {
            "transformation": "artifacts/totals.json",
            "investigation": "artifacts/pages.json",
        }.get(self.family, self.source)

    @property
    def initial_source(self) -> str:
        if self.family == "configuration":
            return json.dumps({"port": 3000, "retries": 0, "log_level": "INFO"})
        if self.family == "transformation":
            if self.variant:
                return (
                    "status,region,quantity,unit_price\n"
                    "paid,west,4,7\nvoid,west,9,100\npaid,east,2,3\n"
                )
            return (
                "region,quantity,unit_price,status\neast,2,10,paid\nwest,3,4,paid\neast,1,9,void\n"
            )
        if self.family == "investigation":
            return self.initial_documents[self.source]
        suffix = "Owner: QA\n" if self.variant else "Owner: Operations\n"
        return "Project Atlas\nRELEASE_PENDING\n" + suffix

    @property
    def initial_documents(self) -> dict[str, str]:
        if self.family != "investigation":
            return {self.source: self.initial_source}
        # The holdout changes lookup paths, ordering, identifiers, owners, threshold
        # and eligible services. The model sees these only through local tools.
        if self.variant:
            manifest = {
                "policy": "policies/night.json",
                "observations": "snapshots/eu.json",
                "inventory": "registry/production.json",
            }
            services = [
                {"id": "search", "owner": "discovery", "active": True},
                {"id": "legacy", "owner": "archive", "active": False},
                {"id": "billing", "owner": "finance", "active": True},
                {"id": "auth", "owner": "identity", "active": True},
            ]
            rates = {"billing": 5, "legacy": 99, "auth": 11, "search": 7}
            threshold = 5
        else:
            manifest = {
                "inventory": "inventory/services.json",
                "observations": "telemetry/current.json",
                "policy": "runbooks/escalation.json",
            }
            services = [
                {"id": "checkout", "owner": "payments", "active": True},
                {"id": "catalog", "owner": "catalog-team", "active": True},
                {"id": "retired", "owner": "legacy", "active": False},
            ]
            rates = {"checkout": 12, "catalog": 2, "retired": 99}
            threshold = 10
        return {
            self.source: json.dumps(manifest),
            manifest["inventory"]: json.dumps({"services": services}),
            manifest["observations"]: json.dumps({"error_rates": rates}),
            manifest["policy"]: json.dumps({"page_above": threshold}),
        }

    @property
    def missing_document(self) -> str:
        if self.family == "investigation":
            return json.loads(self.initial_source)["observations"]
        return self.source

    def problem(self) -> ProblemSpec:
        goals = {
            "configuration": "Set service port to 8443 and retries to 3; preserve log_level.",
            "transformation": (
                "Read orders CSV and write JSON mapping each region to the sum of quantity * "
                "unit_price for paid rows only. Preserve the source CSV."
            ),
            "artifact": "Replace RELEASE_PENDING with Version 2.0; preserve all other text.",
            "investigation": (
                "Read the incident manifest, then discover and read its inventory, observations "
                "and policy documents. Write a JSON object with a pages list, sorted by service "
                "ID. Include {service: ID, owner: OWNER} for each active inventory service whose "
                "observed error_rate is strictly greater than policy page_above. Use error_rates "
                "from observations; do not guess absent observations. Preserve every input."
            ),
        }
        criteria = {
            "configuration": {
                "configuration": "The JSON config has integer port 8443 and retries 3.",
                "preservation": "The existing log_level remains INFO.",
            },
            "transformation": {
                "transformation": "Output totals match the paid source records by region.",
                "preservation": "The source CSV is unchanged.",
            },
            "artifact": {
                "artifact": "The release marker is replaced exactly once by Version 2.0.",
                "preservation": "The project title and owner lines remain unchanged.",
            },
            "investigation": {
                "investigation": (
                    "The sorted pages list contains exactly eligible services "
                    "and their inventory owners."
                ),
                "preservation": (
                    "All manifest, inventory, observation and policy documents remain unchanged."
                ),
            },
        }
        return ProblemSpec(
            id=self.id,
            goal=goals[self.family],
            inputs={"source": self.source, "destination": self.destination},
            constraints=("Use only supplied local tools; no network or shell.",),
            acceptance_criteria=criteria[self.family],
        )

    def limits(self) -> SolverLimits:
        return (
            SolverLimits(max_model_calls=1)
            if self.scenario == "depleted_budget"
            else SolverLimits()
        )


def cases() -> tuple[Case, ...]:
    """Public contract fixtures plus separately labelled changed-data holdouts."""
    return (
        Case("config-direct", "configuration"),
        Case("transform-direct", "transformation"),
        Case("artifact-direct", "artifact"),
        Case("investigate-direct", "investigation"),
        Case("transform-tool-failure", "transformation", "tool_failure"),
        Case("config-wrong-diagnosis", "configuration", "wrong_diagnosis"),
        Case("config-forged-evidence", "configuration", "forged_evidence"),
        Case("config-depleted-budget", "configuration", "depleted_budget"),
        Case("transform-missing-data", "transformation", "missing_data"),
        Case("investigate-missing-data", "investigation", "missing_data"),
        Case("artifact-missing-tool", "artifact", "missing_tool"),
        Case("transform-holdout", "transformation", split="holdout", variant=1),
        Case("artifact-holdout", "artifact", split="holdout", variant=1),
        Case("investigate-holdout", "investigation", split="holdout", variant=1),
    )
