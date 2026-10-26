"""Read-only Kubernetes tools for the kubernetes, logs, metrics and changes specialists."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.kubernetes.client import KubernetesClient
from fin_graph.integrations.kubernetes.settings import INTEGRATION_ID
from fin_graph.integrations.payload import text_payload, tool_payload
from fin_graph.integrations.tooling import integration_tool


def _meta(evidence_type: EvidenceType, *extra_domains: Domain) -> ToolMeta:
    return ToolMeta(
        integration=INTEGRATION_ID,
        domains=frozenset({Domain.KUBERNETES, *extra_domains}),
        evidence_type=evidence_type,
    )


def _client(runtime: ToolRuntime[RunContext]) -> KubernetesClient:
    return require_client(runtime, INTEGRATION_ID, KubernetesClient)


@integration_tool(_meta(EvidenceType.TOPOLOGY))
async def k8s_list_pods(
    namespace: str, runtime: ToolRuntime[RunContext], label_selector: str | None = None
) -> tuple[str, Any]:
    """List pods with phase, readiness, restarts and container states, unhealthy pods first.

    Args:
        namespace: Kubernetes namespace.
        label_selector: Optional label selector, for example "app=checkout".
    """
    pods = await _client(runtime).list_pods(namespace, label_selector)
    return tool_payload(pods), pods


@integration_tool(_meta(EvidenceType.TOPOLOGY))
async def k8s_describe_pod(
    namespace: str, pod: str, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Describe one pod: conditions, owners, and per container image, limits, state, last exit.

    Args:
        namespace: Kubernetes namespace.
        pod: Pod name.
    """
    detail = await _client(runtime).describe_pod(namespace, pod)
    return tool_payload(detail), detail


@integration_tool(_meta(EvidenceType.LOGS, Domain.LOGS))
async def k8s_pod_logs(
    namespace: str,
    pod: str,
    runtime: ToolRuntime[RunContext],
    container: str | None = None,
    previous: bool = False,
    tail_lines: int = 200,
    since_seconds: int | None = None,
) -> tuple[str, Any]:
    """Read the most recent log lines of a pod's container.

    Args:
        namespace: Kubernetes namespace.
        pod: Pod name.
        container: Container name; required when the pod has more than one container.
        previous: Read the logs of the previous, crashed instance of the container.
        tail_lines: How many of the last lines to return, at most 1000.
        since_seconds: Only lines from the last this-many seconds.
    """
    text = await _client(runtime).pod_logs(
        namespace,
        pod,
        container=container,
        previous=previous,
        tail_lines=tail_lines,
        since_seconds=since_seconds,
    )
    artifact = {
        "namespace": namespace,
        "pod": pod,
        "container": container,
        "previous": previous,
        "text": text,
    }
    return (text_payload(text) if text else "(no log lines)"), artifact


@integration_tool(_meta(EvidenceType.EVENTS))
async def k8s_list_events(
    namespace: str,
    runtime: ToolRuntime[RunContext],
    involved_object: str | None = None,
    warnings_only: bool = False,
) -> tuple[str, Any]:
    """List events (BackOff, OOMKilling, FailedScheduling, probe failures…), newest first.

    Args:
        namespace: Kubernetes namespace.
        involved_object: Only events about the object with this name, for example a pod.
        warnings_only: Only events of type Warning.
    """
    events = await _client(runtime).list_events(
        namespace, involved_object=involved_object, warnings_only=warnings_only
    )
    return tool_payload(events), events


@integration_tool(_meta(EvidenceType.TOPOLOGY))
async def k8s_list_workloads(
    namespace: str, runtime: ToolRuntime[RunContext], label_selector: str | None = None
) -> tuple[str, Any]:
    """List Deployments, StatefulSets and DaemonSets: desired vs ready replicas, images, conditions.

    Args:
        namespace: Kubernetes namespace.
        label_selector: Optional label selector, for example "app=checkout".
    """
    workloads = await _client(runtime).list_workloads(namespace, label_selector)
    return tool_payload(workloads), workloads


@integration_tool(_meta(EvidenceType.CHANGES, Domain.CHANGES))
async def k8s_rollout_history(
    namespace: str, deployment: str, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """List a Deployment's rollout revisions, newest first, with images and creation times.

    Args:
        namespace: Kubernetes namespace.
        deployment: Deployment name.
    """
    revisions = await _client(runtime).rollout_history(namespace, deployment)
    return tool_payload(revisions), revisions


@integration_tool(_meta(EvidenceType.TOPOLOGY))
async def k8s_list_nodes(runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List nodes with readiness, memory/disk/PID pressure, taints and allocatable capacity."""
    nodes = await _client(runtime).list_nodes()
    return tool_payload(nodes), nodes


@integration_tool(_meta(EvidenceType.METRICS))
async def k8s_list_hpas(namespace: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List HorizontalPodAutoscalers: replica bounds, current vs target metrics, conditions.

    Args:
        namespace: Kubernetes namespace.
    """
    autoscalers = await _client(runtime).list_hpas(namespace)
    return tool_payload(autoscalers), autoscalers


@integration_tool(_meta(EvidenceType.METRICS, Domain.METRICS))
async def k8s_pod_usage(
    namespace: str, runtime: ToolRuntime[RunContext], label_selector: str | None = None
) -> tuple[str, Any]:
    """Show current CPU and memory use per container (needs metrics-server).

    Args:
        namespace: Kubernetes namespace.
        label_selector: Optional label selector, for example "app=checkout".
    """
    usage = await _client(runtime).pod_usage(namespace, label_selector)
    return tool_payload(usage), usage


TOOLS: tuple[BaseTool, ...] = (
    k8s_list_pods,
    k8s_describe_pod,
    k8s_pod_logs,
    k8s_list_events,
    k8s_list_workloads,
    k8s_rollout_history,
    k8s_list_nodes,
    k8s_list_hpas,
    k8s_pod_usage,
)
