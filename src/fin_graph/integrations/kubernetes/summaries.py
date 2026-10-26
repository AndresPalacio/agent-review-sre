"""Turn Kubernetes API objects into small dicts a model can read: facts only, no secrets.

Environment variable values never leave this module, only their names, and
the ``kubectl apply`` annotation (a full copy of the manifest, env values
included) is dropped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from kubernetes.client import (
    CoreV1Event,
    V1Container,
    V1ContainerState,
    V1ContainerStatus,
    V1DaemonSet,
    V1Deployment,
    V1Node,
    V1ObjectMeta,
    V1Pod,
    V1PodSpec,
    V1ReplicaSet,
    V1StatefulSet,
    V2HorizontalPodAutoscaler,
    V2MetricSpec,
    V2MetricStatus,
    V2MetricTarget,
    V2MetricValueStatus,
)

LAST_APPLIED_ANNOTATION = "kubectl.kubernetes.io/last-applied-configuration"
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
CHANGE_CAUSE_ANNOTATION = "kubernetes.io/change-cause"
PRESSURE_CONDITIONS = ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable")
_NEVER = datetime.min.replace(tzinfo=UTC)


def compact(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None or empty; zero and False are facts and stay."""
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def iso(moment: datetime | None) -> str | None:
    """ISO-8601 text for a timestamp, None when there is none."""
    return moment.isoformat() if moment else None


def pod_summary(pod: V1Pod) -> dict[str, Any]:
    """Phase, readiness, restarts and per-container state of one pod."""
    status = pod.status
    container_statuses = (status.container_statuses or []) if status else []
    return compact(
        {
            "name": _name(pod.metadata),
            "phase": status.phase if status else None,
            "ready": _condition_true(status.conditions if status else None, "Ready"),
            "restarts": sum(item.restart_count for item in container_statuses),
            "node": pod.spec.node_name if pod.spec else None,
            "started": iso(status.start_time) if status else None,
            "containers": [container_status(item) for item in container_statuses],
        }
    )


def pod_is_healthy(summary: Mapping[str, Any]) -> bool:
    """Succeeded, or running and ready without restarts."""
    if summary.get("phase") == "Succeeded":
        return True
    return (
        summary.get("phase") == "Running"
        and summary.get("ready") is True
        and summary.get("restarts", 0) == 0
    )


def container_status(status: V1ContainerStatus) -> dict[str, Any]:
    """Readiness, restarts, current state and the state before the last restart."""
    return compact(
        {
            "name": status.name,
            "ready": status.ready,
            "restarts": status.restart_count,
            "state": container_state(status.state),
            "last_state": container_state(status.last_state),
        }
    )


def container_state(state: V1ContainerState | None) -> dict[str, Any] | None:
    """Waiting (with reason), terminated (with reason and exit code) or running."""
    if state is None:
        return None
    if state.waiting is not None:
        return compact(
            {"waiting": state.waiting.reason or "Waiting", "message": state.waiting.message}
        )
    if state.terminated is not None:
        ended = state.terminated
        return compact(
            {
                "terminated": ended.reason or "Terminated",
                "exit_code": ended.exit_code,
                "finished": iso(ended.finished_at),
                "message": ended.message,
            }
        )
    if state.running is not None:
        return compact({"running_since": iso(state.running.started_at)})
    return None


def pod_detail(pod: V1Pod) -> dict[str, Any]:
    """Everything useful about one pod: conditions, owners, containers with limits and env names."""
    meta, spec, status = pod.metadata, pod.spec, pod.status
    statuses = {
        item.name: item
        for item in [
            *((status.init_container_statuses or []) if status else []),
            *((status.container_statuses or []) if status else []),
        ]
    }
    annotations = (meta.annotations or {}) if meta else {}
    return compact(
        {
            "name": _name(meta),
            "namespace": meta.namespace if meta else None,
            "phase": status.phase if status else None,
            "reason": status.reason if status else None,
            "message": status.message if status else None,
            "node": spec.node_name if spec else None,
            "qos_class": status.qos_class if status else None,
            "started": iso(status.start_time) if status else None,
            "labels": dict((meta.labels or {}) if meta else {}),
            "annotations": {k: v for k, v in annotations.items() if k != LAST_APPLIED_ANNOTATION},
            "owners": [
                f"{ref.kind}/{ref.name}" for ref in ((meta.owner_references or []) if meta else [])
            ],
            "conditions": conditions(status.conditions if status else None),
            "init_containers": [
                container_detail(item, statuses.get(item.name))
                for item in ((spec.init_containers or []) if spec else [])
            ],
            "containers": [
                container_detail(item, statuses.get(item.name))
                for item in (spec.containers if spec else [])
            ],
        }
    )


def container_detail(container: V1Container, status: V1ContainerStatus | None) -> dict[str, Any]:
    """Image, resources, env var names (never values) and the live status of one container."""
    resources = container.resources
    return compact(
        {
            **(container_status(status) if status is not None else {"name": container.name}),
            "image": container.image,
            "requests": dict(resources.requests or {}) if resources else None,
            "limits": dict(resources.limits or {}) if resources else None,
            "env": [item.name for item in (container.env or [])],
        }
    )


def conditions(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Type, status, reason and message of each condition (pods, workloads, nodes, HPAs)."""
    return [
        compact(
            {
                "type": item.type,
                "status": item.status,
                "reason": item.reason,
                "message": item.message,
            }
        )
        for item in (items or [])
    ]


def event_last_seen(event: CoreV1Event) -> datetime:
    """When the event last happened, from whichever field the API server filled in."""
    series = event.series
    return (
        event.last_timestamp
        or (series.last_observed_time if series else None)
        or event.event_time
        or event.metadata.creation_timestamp
        or _NEVER
    )


def event_summary(event: CoreV1Event) -> dict[str, Any]:
    """What happened to which object, how often, and when."""
    involved = event.involved_object
    series = event.series
    last_seen = event_last_seen(event)
    return compact(
        {
            "type": event.type,
            "reason": event.reason,
            "object": f"{involved.kind}/{involved.name}",
            "message": event.message,
            "count": event.count or (series.count if series else None),
            "first_seen": iso(event.first_timestamp),
            "last_seen": iso(last_seen) if last_seen is not _NEVER else None,
        }
    )


def deployment_summary(deployment: V1Deployment) -> dict[str, Any]:
    """Desired versus ready replicas, images and conditions of a Deployment."""
    spec, status = deployment.spec, deployment.status
    desired = spec.replicas if spec and spec.replicas is not None else 1
    return _workload(
        "Deployment",
        deployment.metadata,
        desired=desired,
        ready=(status.ready_replicas or 0) if status else 0,
        updated=(status.updated_replicas or 0) if status else 0,
        available=(status.available_replicas or 0) if status else 0,
        pod_spec=spec.template.spec if spec else None,
        status_conditions=status.conditions if status else None,
    )


def statefulset_summary(statefulset: V1StatefulSet) -> dict[str, Any]:
    """Desired versus ready replicas, images and conditions of a StatefulSet."""
    spec, status = statefulset.spec, statefulset.status
    desired = spec.replicas if spec and spec.replicas is not None else 1
    return _workload(
        "StatefulSet",
        statefulset.metadata,
        desired=desired,
        ready=(status.ready_replicas or 0) if status else 0,
        updated=(status.updated_replicas or 0) if status else 0,
        available=(status.available_replicas or 0) if status else 0,
        pod_spec=spec.template.spec if spec else None,
        status_conditions=status.conditions if status else None,
    )


def daemonset_summary(daemonset: V1DaemonSet) -> dict[str, Any]:
    """Scheduled versus ready pods, images and conditions of a DaemonSet."""
    spec, status = daemonset.spec, daemonset.status
    return _workload(
        "DaemonSet",
        daemonset.metadata,
        desired=status.desired_number_scheduled if status else 0,
        ready=status.number_ready if status else 0,
        updated=(status.updated_number_scheduled or 0) if status else 0,
        available=(status.number_available or 0) if status else 0,
        pod_spec=spec.template.spec if spec else None,
        status_conditions=status.conditions if status else None,
    )


def workload_is_healthy(summary: Mapping[str, Any]) -> bool:
    """Every desired replica is ready and available."""
    desired = summary.get("desired", 0)
    return bool(summary.get("ready", 0) >= desired and summary.get("available", 0) >= desired)


def selector_text(deployment: V1Deployment) -> str:
    """The Deployment's ``matchLabels`` as a label selector string ("" when there are none)."""
    selector = deployment.spec.selector if deployment.spec else None
    labels = (selector.match_labels or {}) if selector else {}
    return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))


def is_owned_by(replica_set: V1ReplicaSet, kind: str, name: str) -> bool:
    """Whether ``replica_set`` belongs to the workload ``kind/name``."""
    owners = (replica_set.metadata.owner_references or []) if replica_set.metadata else []
    return any(owner.kind == kind and owner.name == name for owner in owners)


def revision_summary(replica_set: V1ReplicaSet) -> dict[str, Any]:
    """One rollout revision: number, when it was created, its images and replicas."""
    meta, spec, status = replica_set.metadata, replica_set.spec, replica_set.status
    annotations = (meta.annotations or {}) if meta else {}
    revision = annotations.get(REVISION_ANNOTATION, "")
    return compact(
        {
            "revision": int(revision) if revision.isdigit() else None,
            "replica_set": _name(meta),
            "created": iso(meta.creation_timestamp) if meta else None,
            "images": _images(spec.template.spec if spec and spec.template else None),
            "desired": spec.replicas if spec else None,
            "ready": (status.ready_replicas or 0) if status else 0,
            "change_cause": annotations.get(CHANGE_CAUSE_ANNOTATION),
        }
    )


def node_summary(node: V1Node) -> dict[str, Any]:
    """Readiness, pressure, schedulability, taints and allocatable capacity of a node."""
    spec, status = node.spec, node.status
    node_conditions = (status.conditions or []) if status else []
    allocatable = (status.allocatable or {}) if status else {}
    return compact(
        {
            "name": _name(node.metadata),
            "ready": _condition_true(node_conditions, "Ready"),
            "pressure": [
                item.type
                for item in node_conditions
                if item.type in PRESSURE_CONDITIONS and item.status == "True"
            ],
            "unschedulable": bool(spec.unschedulable) if spec else False,
            "taints": [
                f"{taint.key}={taint.value}:{taint.effect}"
                if taint.value
                else f"{taint.key}:{taint.effect}"
                for taint in ((spec.taints or []) if spec else [])
            ],
            "kubelet": status.node_info.kubelet_version if status and status.node_info else None,
            "allocatable": {
                key: allocatable[key] for key in ("cpu", "memory", "pods") if key in allocatable
            },
        }
    )


def node_is_healthy(summary: Mapping[str, Any]) -> bool:
    """Ready, schedulable and under no pressure."""
    return (
        summary.get("ready") is True
        and not summary.get("pressure")
        and summary.get("unschedulable") is not True
    )


def hpa_summary(hpa: V2HorizontalPodAutoscaler) -> dict[str, Any]:
    """Replica bounds, current and target metrics, and whether the autoscaler is pinned at max."""
    spec, status = hpa.spec, hpa.status
    current = status.current_replicas if status else None
    return compact(
        {
            "name": _name(hpa.metadata),
            "target": f"{spec.scale_target_ref.kind}/{spec.scale_target_ref.name}"
            if spec
            else None,
            "min": (spec.min_replicas or 1) if spec else None,
            "max": spec.max_replicas if spec else None,
            "current": current,
            "desired": status.desired_replicas if status else None,
            "at_max": bool(spec and current is not None and current >= spec.max_replicas),
            "metrics": [
                _metric_status_text(item)
                for item in ((status.current_metrics or []) if status else [])
            ],
            "targets": [_metric_spec_text(item) for item in ((spec.metrics or []) if spec else [])],
            "conditions": conditions(status.conditions if status else None),
        }
    )


def pod_usage(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """CPU and memory per container from one ``metrics.k8s.io`` PodMetrics object."""
    pod = (item.get("metadata") or {}).get("name")
    return [
        compact(
            {
                "pod": pod,
                "container": container.get("name"),
                "cpu": (container.get("usage") or {}).get("cpu"),
                "memory": (container.get("usage") or {}).get("memory"),
            }
        )
        for container in item.get("containers") or []
    ]


def _workload(
    kind: str,
    meta: V1ObjectMeta | None,
    *,
    desired: int,
    ready: int,
    updated: int,
    available: int,
    pod_spec: V1PodSpec | None,
    status_conditions: Iterable[Any] | None,
) -> dict[str, Any]:
    return compact(
        {
            "kind": kind,
            "name": _name(meta),
            "desired": desired,
            "ready": ready,
            "updated": updated,
            "available": available,
            "images": _images(pod_spec),
            "conditions": conditions(status_conditions),
        }
    )


def _name(meta: V1ObjectMeta | None) -> str | None:
    return meta.name if meta else None


def _images(pod_spec: V1PodSpec | None) -> list[str]:
    return [item.image for item in (pod_spec.containers if pod_spec else []) if item.image]


def _condition_true(items: Iterable[Any] | None, condition_type: str) -> bool | None:
    for item in items or []:
        if item.type == condition_type:
            return bool(item.status == "True")
    return None


def _metric_spec_text(metric: V2MetricSpec) -> str:
    resource = metric.resource or metric.container_resource
    if resource is not None:
        return f"{resource.name} target {_target_text(resource.target)}"
    named = metric.pods or metric.object or metric.external
    if named is not None:
        return f"{named.metric.name} target {_target_text(named.target)}"
    return metric.type


def _metric_status_text(metric: V2MetricStatus) -> str:
    resource = metric.resource or metric.container_resource
    if resource is not None:
        return f"{resource.name} {_value_text(resource.current)}"
    named = metric.pods or metric.object or metric.external
    if named is not None:
        return f"{named.metric.name} {_value_text(named.current)}"
    return metric.type


def _target_text(target: V2MetricTarget) -> str:
    if target.average_utilization is not None:
        return f"{target.average_utilization}%"
    return str(target.average_value or target.value or "?")


def _value_text(current: V2MetricValueStatus) -> str:
    if current.average_utilization is not None:
        return f"{current.average_utilization}%"
    return str(current.average_value or current.value or "?")
