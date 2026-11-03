"""Validated, content-addressed, atomically published quantitative artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fin_graph.quant.signals.base import JsonValue

ARTIFACT_SCHEMA_VERSION = 1


class ArtifactError(RuntimeError):
    """Base error for persisted quantitative models."""


class ArtifactPublishError(ArtifactError):
    """An artifact could not be completely validated and published."""


class ArtifactCorruptionError(ArtifactError):
    """Published bytes no longer match their manifest."""


class ArtifactCompatibilityError(ArtifactError):
    """A valid artifact does not belong to the requested model/input."""


class TrainingRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str = Field(min_length=1)
    start_date: date
    end_date: date
    seed: int = 42
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ordered_dates(self) -> TrainingRequest:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class ModelArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    artifact_id: str
    algorithm_version: str
    model_name: str
    ticker: str
    start_date: date
    end_date: date
    trained_through: date
    seed: int
    parameters: dict[str, JsonValue]
    feature_names: tuple[str, ...]
    dependency_versions: dict[str, str]
    payload_sha256: str
    payload_path: str
    manifest_path: str


class ArtifactStore:
    """Joblib store where a manifest appears only after payload validation."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def publish(
        self,
        *,
        model_name: str,
        request: TrainingRequest,
        trained_through: date,
        algorithm_version: str,
        feature_names: tuple[str, ...],
        dependency_versions: dict[str, str],
        parameters: dict[str, JsonValue],
        payload: Any,
        validator: Callable[[Any], None] | None = None,
    ) -> ModelArtifact:
        import joblib  # type: ignore[import-untyped]

        staging = self.root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        temporary_payload = staging / f"{token}.joblib.tmp"
        temporary_manifest = staging / f"{token}.json.tmp"
        final_payload: Path | None = None
        final_manifest: Path | None = None
        published_payload = False
        published_manifest = False
        try:
            joblib.dump(payload, temporary_payload)
            loaded = joblib.load(temporary_payload)
            if validator is not None:
                validator(loaded)
            payload_hash = hashlib.sha256(temporary_payload.read_bytes()).hexdigest()
            identity = json.dumps(
                {
                    "model_name": model_name,
                    "ticker": request.ticker,
                    "start_date": request.start_date.isoformat(),
                    "end_date": request.end_date.isoformat(),
                    "trained_through": trained_through.isoformat(),
                    "seed": request.seed,
                    "parameters": parameters,
                    "features": feature_names,
                    "payload": payload_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            artifact_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
            directory = self.root / model_name / request.ticker
            directory.mkdir(parents=True, exist_ok=True)
            final_payload = directory / f"{artifact_id}.joblib"
            final_manifest = directory / f"{artifact_id}.json"
            artifact = ModelArtifact(
                artifact_id=artifact_id,
                algorithm_version=algorithm_version,
                model_name=model_name,
                ticker=request.ticker,
                start_date=request.start_date,
                end_date=request.end_date,
                trained_through=trained_through,
                seed=request.seed,
                parameters=parameters,
                feature_names=feature_names,
                dependency_versions=dependency_versions,
                payload_sha256=payload_hash,
                payload_path=str(final_payload.resolve()),
                manifest_path=str(final_manifest.resolve()),
            )
            temporary_manifest.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
            ModelArtifact.model_validate_json(temporary_manifest.read_text(encoding="utf-8"))
            os.replace(temporary_payload, final_payload)
            published_payload = True
            os.replace(temporary_manifest, final_manifest)
            published_manifest = True
            return artifact
        except ArtifactError:
            raise
        except Exception as exc:
            raise ArtifactPublishError(f"could not publish {model_name} artifact") from exc
        finally:
            temporary_payload.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
            if published_payload and not published_manifest and final_payload is not None:
                final_payload.unlink(missing_ok=True)
            if final_manifest is not None and not published_manifest:
                final_manifest.unlink(missing_ok=True)

    @staticmethod
    def load(artifact: ModelArtifact) -> Any:
        import joblib

        manifest = Path(artifact.manifest_path)
        payload = Path(artifact.payload_path)
        try:
            stored = ModelArtifact.model_validate_json(manifest.read_text(encoding="utf-8"))
            if stored != artifact:
                raise ArtifactCorruptionError("artifact manifest does not match supplied metadata")
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            if digest != artifact.payload_sha256:
                raise ArtifactCorruptionError("artifact payload hash mismatch")
            return joblib.load(payload)
        except ArtifactCorruptionError:
            raise
        except Exception as exc:
            raise ArtifactCorruptionError(
                f"could not load artifact {artifact.artifact_id}"
            ) from exc
