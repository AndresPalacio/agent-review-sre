from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from kubernetes.client.exceptions import ApiException
from pydantic import ValidationError

from fin_graph.graphs.remediation import actions as action_module
from fin_graph.graphs.remediation.actions import default_action_catalog
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.kubernetes.client import KubernetesClient


class FakeApps:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.desired_replicas = 1

    def patch_namespaced_deployment_scale(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if "dry_run" not in kwargs:
            self.desired_replicas = kwargs["body"]["spec"]["replicas"]
        return SimpleNamespace(spec=SimpleNamespace(replicas=kwargs["body"]["spec"]["replicas"]))

    def read_namespaced_deployment(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            metadata=SimpleNamespace(generation=1),
            spec=SimpleNamespace(replicas=self.desired_replicas),
            status=SimpleNamespace(
                observed_generation=1,
                replicas=self.desired_replicas,
                updated_replicas=self.desired_replicas,
                available_replicas=self.desired_replicas,
            ),
        )


async def test_scale_is_dry_run_before_execution_and_verifies_availability() -> None:
    client = KubernetesClient.__new__(KubernetesClient)
    apps = FakeApps()
    client._apps = apps
    client._timeout = 5.0
    context = RunContext(clients={"kubernetes": client})
    catalog = default_action_catalog()
    args = {"namespace": "shop", "deployment": "checkout", "replicas": 3}

    preview = await catalog.prepare("k8s_scale_deployment", args, context)
    result = await catalog.execute("k8s_scale_deployment", args, "action-1", context)
    verified = await catalog.verify("k8s_scale_deployment", args, context)

    assert preview["replicas"] == 3
    assert preview["previous_replicas"] == 1
    assert result["replicas"] == 3
    assert verified
    assert apps.calls[0]["dry_run"] == "All"
    assert "dry_run" not in apps.calls[1]
    assert apps.calls[0]["body"] == apps.calls[1]["body"]


def test_scale_rollback_restores_prior_replicas() -> None:
    spec = default_action_catalog().get("k8s_scale_deployment")
    args = spec.args_schema.model_validate(
        {"namespace": "shop", "deployment": "checkout", "replicas": 3}
    )
    assert spec.rollback_action is not None
    name, rollback_args = spec.rollback_action(args, {"previous_replicas": 1}, {"replicas": 3})
    assert name == "k8s_scale_deployment"
    assert rollback_args == {"namespace": "shop", "deployment": "checkout", "replicas": 1}


async def test_scale_down_is_not_verified_from_old_available_replicas() -> None:
    client = KubernetesClient.__new__(KubernetesClient)
    apps = FakeApps()
    apps.desired_replicas = 3
    client._apps = apps
    client._timeout = 5.0
    assert not await client.deployment_available("shop", "checkout", 0)


def test_default_catalog_explicitly_registers_vendor_mutations() -> None:
    names = set(default_action_catalog().actions)
    assert {
        "k8s_scale_deployment",
        "k8s_rollout_restart",
        "k8s_rollout_undo",
        "k8s_delete_pod",
        "alertmanager_create_silence",
        "alertmanager_expire_silence",
        "grafana_create_annotation",
        "datadog_mute_monitor",
        "sentry_update_issue",
        "github_create_issue",
        "pagerduty_update_incident",
    } <= names


class FakeMutatingApps:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.revision = "12"
        self.resource_version = "rv-12"
        self.conflict_on_patch = False
        self.generation = 12
        self.observed_generation = 12
        self.template = SimpleNamespace(
            metadata=SimpleNamespace(annotations={}),
            spec=SimpleNamespace(containers=[SimpleNamespace(image="image:new")]),
        )

    def read_namespaced_deployment(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            metadata=SimpleNamespace(
                uid="deployment-uid",
                generation=self.generation,
                resource_version=self.resource_version,
                annotations={
                    "deployment.kubernetes.io/revision": self.revision,
                },
            ),
            spec=SimpleNamespace(replicas=2, template=self.template),
            status=SimpleNamespace(
                observed_generation=self.observed_generation,
                available_replicas=2,
                updated_replicas=2,
            ),
        )

    def list_namespaced_replica_set(self, **kwargs: Any) -> Any:
        prior = SimpleNamespace(
            metadata=SimpleNamespace(
                annotations={"deployment.kubernetes.io/revision": "11"},
                resource_version="rs-rv-11",
                owner_references=[
                    SimpleNamespace(kind="Deployment", name="checkout", uid="deployment-uid")
                ],
            ),
            spec=SimpleNamespace(
                template=SimpleNamespace(
                    metadata=SimpleNamespace(
                        annotations={},
                        labels={"app": "checkout", "pod-template-hash": "hash"},
                        uid="pod-template-uid",
                        resource_version="pod-template-rv",
                    ),
                    spec=SimpleNamespace(containers=[SimpleNamespace(image="image:old")]),
                )
            ),
        )
        return SimpleNamespace(items=[prior])

    def patch_namespaced_deployment(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        body = kwargs["body"]
        resource_version = (
            body[0]["value"] if isinstance(body, list) else body["metadata"]["resourceVersion"]
        )
        if self.conflict_on_patch:
            self.resource_version = "rv-13"
        if resource_version != self.resource_version:
            raise ApiException(status=409, reason="Conflict")
        if "dry_run" not in kwargs:
            body_template = body[1]["value"] if isinstance(body, list) else body["spec"]["template"]
            if "spec" in body_template:
                self.template = body_template
            else:
                self.template.metadata.annotations.update(body_template["metadata"]["annotations"])
        return self.read_namespaced_deployment()


class FakeMutatingCore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.uid = "pod-uid"

    def read_namespaced_pod(self, **kwargs: Any) -> Any:
        return SimpleNamespace(metadata=SimpleNamespace(uid=self.uid))

    def delete_namespaced_pod(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if "dry_run" not in kwargs:
            self.uid = "replacement-uid"
        return SimpleNamespace(status="Success")

    def list_namespaced_pod(self, **kwargs: Any) -> Any:
        return SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(uid=self.uid))])


def _mutation_context() -> tuple[RunContext, FakeMutatingApps, FakeMutatingCore]:
    client = KubernetesClient.__new__(KubernetesClient)
    apps = FakeMutatingApps()
    core = FakeMutatingCore()
    client._apps = apps
    client._core = core
    client._timeout = 5.0
    return RunContext(clients={"kubernetes": client}), apps, core


@pytest.mark.parametrize(
    "name,args",
    [
        (
            "k8s_rollout_restart",
            {
                "namespace": "shop",
                "deployment": "checkout",
                "restart_token": "action-1",
            },
        ),
        (
            "k8s_rollout_undo",
            {
                "namespace": "shop",
                "deployment": "checkout",
                "target_revision": 11,
                "expected_revision": 12,
            },
        ),
        ("k8s_delete_pod", {"namespace": "shop", "pod": "checkout-123", "uid": "pod-uid"}),
    ],
)
async def test_kubernetes_mutations_prepare_then_execute_with_dry_run(
    name: str, args: dict[str, Any]
) -> None:
    context, apps, core = _mutation_context()
    catalog = default_action_catalog()
    preview = await catalog.prepare(name, args, context)
    result = await catalog.execute(name, args, "action-1", context)
    calls = core.calls if name == "k8s_delete_pod" else apps.calls
    assert preview["namespace"] == "shop"
    assert result["action_id"] == "action-1"
    assert calls[0]["dry_run"] == "All"
    assert "dry_run" not in calls[1]
    assert calls[0]["body"] == calls[1]["body"]
    if name == "k8s_rollout_restart":
        assert calls[0]["body"]["metadata"]["resourceVersion"] == "rv-12"
    if name == "k8s_rollout_undo":
        assert calls[0]["body"][0] == {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": "rv-12",
        }
        assert calls[0]["body"][1]["op"] == "replace"
        assert calls[0]["body"][1]["path"] == "/spec/template"
        template_meta = calls[0]["body"][1]["value"]["metadata"]
        assert template_meta == {"labels": {"app": "checkout"}, "annotations": {}}
    assert await catalog.verify(name, args, context)


@pytest.mark.parametrize(
    "name,args",
    [
        ("k8s_rollout_restart", {"namespace": "shop", "deployment": "checkout"}),
        (
            "k8s_rollout_undo",
            {"namespace": "shop", "deployment": "checkout", "target_revision": 11},
        ),
        ("k8s_delete_pod", {"namespace": "shop", "pod": "checkout-123"}),
    ],
)
async def test_kubernetes_mutations_require_stable_preconditions(
    name: str, args: dict[str, Any]
) -> None:
    context, apps, core = _mutation_context()
    with pytest.raises(ValidationError):
        await default_action_catalog().prepare(name, args, context)
    assert not apps.calls and not core.calls


async def test_undo_rejects_changed_current_revision_before_patch() -> None:
    context, apps, _ = _mutation_context()
    apps.revision = "13"
    args = {
        "namespace": "shop",
        "deployment": "checkout",
        "target_revision": 11,
        "expected_revision": 12,
    }
    with pytest.raises(ValueError, match="revision"):
        await default_action_catalog().prepare("k8s_rollout_undo", args, context)
    assert not apps.calls


async def test_delete_rejects_replaced_pod_before_delete() -> None:
    context, _, core = _mutation_context()
    core.uid = "different-uid"
    args = {"namespace": "shop", "pod": "checkout-123", "uid": "pod-uid"}
    with pytest.raises(ValueError, match="UID"):
        await default_action_catalog().prepare("k8s_delete_pod", args, context)
    assert not core.calls


async def test_undo_rechecks_revision_after_approval() -> None:
    context, apps, _ = _mutation_context()
    args = {
        "namespace": "shop",
        "deployment": "checkout",
        "target_revision": 11,
        "expected_revision": 12,
    }
    catalog = default_action_catalog()
    await catalog.prepare("k8s_rollout_undo", args, context)
    apps.revision = "13"
    with pytest.raises(ValueError, match="revision"):
        await catalog.execute("k8s_rollout_undo", args, "action-2", context)
    assert len(apps.calls) == 1


async def test_delete_rechecks_uid_after_approval() -> None:
    context, _, core = _mutation_context()
    args = {"namespace": "shop", "pod": "checkout-123", "uid": "pod-uid"}
    catalog = default_action_catalog()
    await catalog.prepare("k8s_delete_pod", args, context)
    core.uid = "replacement-uid"
    with pytest.raises(ValueError, match="UID"):
        await catalog.execute("k8s_delete_pod", args, "action-3", context)
    assert len(core.calls) == 1


async def test_undo_rejects_unowned_revision() -> None:
    context, apps, _ = _mutation_context()
    apps.list_namespaced_replica_set = lambda **kwargs: SimpleNamespace(items=[])
    args = {
        "namespace": "shop",
        "deployment": "checkout",
        "target_revision": 11,
        "expected_revision": 12,
    }
    with pytest.raises(ValueError, match="owned ReplicaSet"):
        await default_action_catalog().prepare("k8s_rollout_undo", args, context)
    assert not apps.calls


@pytest.mark.parametrize(
    "name,args",
    [
        (
            "k8s_rollout_restart",
            {
                "namespace": "shop",
                "deployment": "checkout",
                "restart_token": "action-1",
            },
        ),
        (
            "k8s_rollout_undo",
            {
                "namespace": "shop",
                "deployment": "checkout",
                "target_revision": 11,
                "expected_revision": 12,
            },
        ),
    ],
)
async def test_rollout_verification_waits_for_new_pods(
    name: str, args: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(action_module, "_VERIFY_INTERVAL_SECONDS", 0.0, raising=False)
    context, apps, _ = _mutation_context()
    catalog = default_action_catalog()
    await catalog.prepare(name, args, context)
    await catalog.execute(name, args, "action-4", context)
    original_read = apps.read_namespaced_deployment

    def not_updated(**kwargs: Any) -> Any:
        item = original_read(**kwargs)
        item.status.updated_replicas = 0
        return item

    apps.read_namespaced_deployment = not_updated
    assert not await catalog.verify(name, args, context)


async def test_rollout_verification_polls_until_new_pods_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(action_module, "_VERIFY_INTERVAL_SECONDS", 0.0, raising=False)
    context, apps, _ = _mutation_context()
    args = {"namespace": "shop", "deployment": "checkout", "restart_token": "action-1"}
    catalog = default_action_catalog()
    await catalog.prepare("k8s_rollout_restart", args, context)
    await catalog.execute("k8s_rollout_restart", args, "action-1", context)
    original_read = apps.read_namespaced_deployment
    reads = 0

    def eventually_updated(**kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        item = original_read(**kwargs)
        if reads < 3:
            item.status.updated_replicas = 0
        return item

    apps.read_namespaced_deployment = eventually_updated
    assert await catalog.verify("k8s_rollout_restart", args, context)
    assert reads == 3


@pytest.mark.parametrize(
    "name,args",
    [
        (
            "k8s_rollout_restart",
            {
                "namespace": "shop",
                "deployment": "checkout",
                "restart_token": "action-1",
            },
        ),
        (
            "k8s_rollout_undo",
            {
                "namespace": "shop",
                "deployment": "checkout",
                "target_revision": 11,
                "expected_revision": 12,
            },
        ),
    ],
)
async def test_rollout_patch_conflict_cannot_overwrite_concurrent_change(
    name: str, args: dict[str, Any]
) -> None:
    context, apps, _ = _mutation_context()
    apps.conflict_on_patch = True
    with pytest.raises(IntegrationError, match="409"):
        await default_action_catalog().prepare(name, args, context)
    assert apps.resource_version == "rv-13"


@pytest.mark.parametrize("name", ["k8s_rollout_restart", "k8s_rollout_undo"])
@pytest.mark.parametrize("observed_generation", [12, None])
async def test_rollout_verification_rejects_unobserved_generation(
    name: str, observed_generation: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(action_module, "_VERIFY_INTERVAL_SECONDS", 0.0)
    context, apps, _ = _mutation_context()
    args: dict[str, Any] = {"namespace": "shop", "deployment": "checkout"}
    if name == "k8s_rollout_restart":
        args["restart_token"] = "generation-check"
    else:
        args.update(target_revision=11, expected_revision=12)
    catalog = default_action_catalog()
    await catalog.execute(name, args, "generation-check", context)
    apps.generation = 13
    apps.observed_generation = observed_generation

    assert not await catalog.verify(name, args, context)

    apps.observed_generation = 13
    assert await catalog.verify(name, args, context)
