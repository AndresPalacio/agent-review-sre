"""Explicitly registered actions with validated input and server-side preparation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from sre_graph.graphs.remediation.graph import ActionCatalog, ActionLedger, ActionSpec
from sre_graph.graphs.remediation.vendor_actions import vendor_action_specs
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.kubernetes.client import KubernetesClient


class ScaleDeploymentArgs(BaseModel):
    namespace: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    deployment: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    replicas: int = Field(ge=0, le=1000)


class RestartDeploymentArgs(BaseModel):
    namespace: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    deployment: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    restart_token: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")


class UndoDeploymentArgs(BaseModel):
    namespace: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    deployment: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    target_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=2)


class DeletePodArgs(BaseModel):
    namespace: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    pod: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    uid: str = Field(min_length=1, max_length=128)


_VERIFY_INTERVAL_SECONDS = 2.0
_VERIFY_ATTEMPTS = 10


async def _verify_with_poll(check: Callable[[], Awaitable[bool]]) -> bool:
    for attempt in range(_VERIFY_ATTEMPTS):
        if await check():
            return True
        if attempt < _VERIFY_ATTEMPTS - 1:
            await asyncio.sleep(_VERIFY_INTERVAL_SECONDS)
    return False


async def _prepare_scale(args: ScaleDeploymentArgs, context: RunContext) -> dict[str, Any]:
    client = context.client("kubernetes", KubernetesClient)
    previous = await client.deployment_replicas(args.namespace, args.deployment)
    preview = await client.scale_deployment(
        args.namespace, args.deployment, args.replicas, dry_run=True
    )
    return {**preview, "previous_replicas": previous}


async def _execute_scale(
    args: ScaleDeploymentArgs, key: str, context: RunContext
) -> dict[str, Any]:
    result = await context.client("kubernetes", KubernetesClient).scale_deployment(
        args.namespace, args.deployment, args.replicas
    )
    return {**result, "action_id": key}


async def _verify_scale(args: ScaleDeploymentArgs, context: RunContext) -> bool:
    client = context.client("kubernetes", KubernetesClient)
    return await _verify_with_poll(
        lambda: client.deployment_available(args.namespace, args.deployment, args.replicas)
    )


def _rollback_scale(
    args: ScaleDeploymentArgs, prepared: dict[str, Any], result: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    del result
    return (
        "k8s_scale_deployment",
        {
            "namespace": args.namespace,
            "deployment": args.deployment,
            "replicas": prepared["previous_replicas"],
        },
    )


async def _prepare_restart(args: RestartDeploymentArgs, context: RunContext) -> dict[str, Any]:
    return await context.client("kubernetes", KubernetesClient).rollout_restart(
        args.namespace, args.deployment, args.restart_token, dry_run=True
    )


async def _execute_restart(
    args: RestartDeploymentArgs, key: str, context: RunContext
) -> dict[str, Any]:
    result = await context.client("kubernetes", KubernetesClient).rollout_restart(
        args.namespace, args.deployment, args.restart_token
    )
    return {**result, "action_id": key}


async def _verify_restart(args: RestartDeploymentArgs, context: RunContext) -> bool:
    client = context.client("kubernetes", KubernetesClient)
    return await _verify_with_poll(
        lambda: client.deployment_has_restart_token(
            args.namespace, args.deployment, args.restart_token
        )
    )


async def _prepare_undo(args: UndoDeploymentArgs, context: RunContext) -> dict[str, Any]:
    return await context.client("kubernetes", KubernetesClient).rollout_undo(
        args.namespace,
        args.deployment,
        args.target_revision,
        args.expected_revision,
        dry_run=True,
    )


async def _execute_undo(args: UndoDeploymentArgs, key: str, context: RunContext) -> dict[str, Any]:
    result = await context.client("kubernetes", KubernetesClient).rollout_undo(
        args.namespace, args.deployment, args.target_revision, args.expected_revision
    )
    return {**result, "action_id": key}


async def _verify_undo(args: UndoDeploymentArgs, context: RunContext) -> bool:
    client = context.client("kubernetes", KubernetesClient)
    return await _verify_with_poll(
        lambda: client.deployment_matches_revision(
            args.namespace, args.deployment, args.target_revision
        )
    )


async def _prepare_delete_pod(args: DeletePodArgs, context: RunContext) -> dict[str, Any]:
    return await context.client("kubernetes", KubernetesClient).delete_pod(
        args.namespace, args.pod, args.uid, dry_run=True
    )


async def _execute_delete_pod(args: DeletePodArgs, key: str, context: RunContext) -> dict[str, Any]:
    result = await context.client("kubernetes", KubernetesClient).delete_pod(
        args.namespace, args.pod, args.uid
    )
    return {**result, "action_id": key}


async def _verify_delete_pod(args: DeletePodArgs, context: RunContext) -> bool:
    return await context.client("kubernetes", KubernetesClient).pod_uid_absent(
        args.namespace, args.pod, args.uid
    )


def default_action_catalog(*, ledger: ActionLedger | None = None) -> ActionCatalog:
    """Allowlist of actions implemented with bounded and idempotent vendor calls."""
    return ActionCatalog(
        [
            ActionSpec(
                "k8s_scale_deployment",
                ScaleDeploymentArgs,
                _prepare_scale,
                _execute_scale,
                _verify_scale,
                rollback="Restore the prior replica count after a separate approval.",
                rollback_action=_rollback_scale,
            ),
            ActionSpec(
                "k8s_rollout_restart",
                RestartDeploymentArgs,
                _prepare_restart,
                _execute_restart,
                _verify_restart,
                rollback="A restart cannot be undone; consider rollout undo with approval.",
            ),
            ActionSpec(
                "k8s_rollout_undo",
                UndoDeploymentArgs,
                _prepare_undo,
                _execute_undo,
                _verify_undo,
                rollback="Restore the prior template through a separately approved rollout undo.",
            ),
            ActionSpec(
                "k8s_delete_pod",
                DeletePodArgs,
                _prepare_delete_pod,
                _execute_delete_pod,
                _verify_delete_pod,
                rollback="Pod deletion is irreversible; the controller may create a replacement.",
            ),
            *vendor_action_specs(),
        ],
        ledger=ledger,
    )
