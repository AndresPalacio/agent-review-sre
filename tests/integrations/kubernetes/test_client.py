from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import urllib3
from kubernetes import client as k8s
from kubernetes.config.config_exception import ConfigException

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.kubernetes.client import KubernetesClient
from fin_graph.integrations.kubernetes.settings import KubernetesSettings

CRASHING_POD = "checkout-7d9f8b6c5-x2x9z"
HEALTHY_POD = "checkout-7d9f8b6c5-abcde"
PODS = "/api/v1/namespaces/payments/pods"


def test_a_kubeconfig_is_loaded_into_a_private_configuration(kubeconfig_file: Path) -> None:
    kube = KubernetesClient.from_settings(
        KubernetesSettings(_env_file=None, enabled=True, kubeconfig=str(kubeconfig_file))
    )

    assert kube.host == "https://k8s.test:6443"
    assert k8s.Configuration.get_default_copy().host != "https://k8s.test:6443"


def test_an_unknown_context_is_a_configuration_error(kubeconfig_file: Path) -> None:
    settings = KubernetesSettings(
        _env_file=None, enabled=True, kubeconfig=str(kubeconfig_file), context="nope"
    )

    with pytest.raises(ConfigException):
        KubernetesClient.from_settings(settings)


async def test_pods_come_back_unhealthy_first_with_container_states(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    pods = await kube_client.list_pods("payments", "app=checkout")

    assert [pod["name"] for pod in pods] == [CRASHING_POD, HEALTHY_POD]
    crashing = pods[0]
    assert crashing["ready"] is False
    assert crashing["restarts"] == 7
    assert crashing["containers"][0]["state"] == {
        "waiting": "CrashLoopBackOff",
        "message": "back-off 5m0s restarting failed container",
    }
    assert crashing["containers"][0]["last_state"] == {
        "terminated": "OOMKilled",
        "exit_code": 137,
        "finished": "2026-10-07T10:05:00+00:00",
    }
    assert kube_rest.query(PODS) == {"labelSelector": ["app=checkout"], "limit": ["200"]}


async def test_pod_detail_names_env_vars_but_never_their_values(
    kube_client: KubernetesClient,
) -> None:
    detail = await kube_client.describe_pod("payments", CRASHING_POD)

    container = detail["containers"][0]
    assert container["env"] == ["DB_PASSWORD", "API_KEY"]
    assert container["limits"] == {"memory": "256Mi"}
    assert detail["annotations"] == {"team": "payments"}
    assert detail["owners"] == ["ReplicaSet/checkout-7d9f8b6c5"]
    assert "hunter2" not in json.dumps(detail)


async def test_logs_pass_every_option_and_keep_json_lines_verbatim(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    text = await kube_client.pod_logs(
        "payments", CRASHING_POD, container="app", previous=True, tail_lines=50, since_seconds=600
    )

    assert text.startswith('{"level":"error","msg":"allocating 64MiB: out of memory"}\n')
    assert kube_rest.query(f"{PODS}/{CRASHING_POD}/log") == {
        "container": ["app"],
        "previous": ["true"],
        "sinceSeconds": ["600"],
        "tailLines": ["50"],
    }


async def test_log_requests_are_capped_at_1000_lines(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    await kube_client.pod_logs("payments", CRASHING_POD, tail_lines=50_000)

    assert kube_rest.query(f"{PODS}/{CRASHING_POD}/log")["tailLines"] == ["1000"]


async def test_events_come_newest_first(kube_client: KubernetesClient, kube_rest: Any) -> None:
    events = await kube_client.list_events(
        "payments", involved_object=CRASHING_POD, warnings_only=True
    )

    assert [event["reason"] for event in events] == ["BackOff", "Pulled"]
    assert events[0] == {
        "type": "Warning",
        "reason": "BackOff",
        "object": f"Pod/{CRASHING_POD}",
        "message": "Back-off restarting failed container app",
        "count": 3,
        "first_seen": "2026-10-07T09:30:00+00:00",
        "last_seen": "2026-10-07T10:06:00+00:00",
    }
    assert kube_rest.query("/api/v1/namespaces/payments/events")["fieldSelector"] == [
        f"involvedObject.name={CRASHING_POD},type=Warning"
    ]


async def test_workloads_missing_replicas_come_first(kube_client: KubernetesClient) -> None:
    workloads = await kube_client.list_workloads("payments")

    assert [(item["kind"], item["name"]) for item in workloads] == [
        ("Deployment", "checkout"),
        ("DaemonSet", "node-exporter"),
        ("StatefulSet", "redis"),
    ]
    assert workloads[0]["desired"] == 2
    assert workloads[0]["ready"] == 1
    assert workloads[0]["conditions"][0]["reason"] == "MinimumReplicasUnavailable"


async def test_rollout_history_lists_this_deployments_revisions_newest_first(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    revisions = await kube_client.rollout_history("payments", "checkout")

    assert [(item["revision"], item["images"]) for item in revisions] == [
        (12, ["registry.local/checkout:1.4.2"]),
        (11, ["registry.local/checkout:1.4.1"]),
    ]
    assert revisions[0]["change_cause"] == "deploy 1.4.2"
    assert kube_rest.query("/apis/apps/v1/namespaces/payments/replicasets")["labelSelector"] == [
        "app=checkout"
    ]


async def test_describe_pod_exposes_uid_needed_for_safe_delete(
    kube_client: KubernetesClient,
) -> None:
    pod = await kube_client.describe_pod("payments", "checkout-7d9f8b6c5-x2x9z")
    assert pod["uid"] == "checkout-7d9f8b6c5-x2x9z-uid"


async def test_delete_pod_sdk_sends_uid_precondition_in_server_dry_run(
    kube_client: KubernetesClient, kube_rest: Any, monkeypatch: Any
) -> None:
    requests: list[tuple[str, Any]] = []
    original_request = kube_rest.request

    def record_request(method: str, url: str, **kwargs: Any) -> Any:
        requests.append((method, kwargs.get("body")))
        return original_request(method, url, **kwargs)

    monkeypatch.setattr(kube_rest, "request", record_request)
    result = await kube_client.delete_pod(
        "payments",
        "checkout-7d9f8b6c5-x2x9z",
        "checkout-7d9f8b6c5-x2x9z-uid",
        dry_run=True,
    )
    assert result["uid"] == "checkout-7d9f8b6c5-x2x9z-uid"
    assert kube_rest.query("/api/v1/namespaces/payments/pods/checkout-7d9f8b6c5-x2x9z")[
        "dryRun"
    ] == ["All"]
    assert any(
        method == "DELETE" and "checkout-7d9f8b6c5-x2x9z-uid" in str(body)
        for method, body in requests
    )


async def test_restart_sdk_sends_conditional_server_dry_run_patch(
    kube_client: KubernetesClient, kube_rest: Any, monkeypatch: Any
) -> None:
    patches: list[dict[str, Any]] = []
    content_types: list[str] = []
    original_request = kube_rest.request

    def record_request(method: str, url: str, **kwargs: Any) -> Any:
        if method == "PATCH":
            patches.append(kwargs["body"])
            content_types.append(kwargs["headers"]["Content-Type"])
        return original_request(method, url, **kwargs)

    monkeypatch.setattr(kube_rest, "request", record_request)
    await kube_client.rollout_restart("payments", "checkout", "action-1", dry_run=True)
    assert kube_rest.query("/apis/apps/v1/namespaces/payments/deployments/checkout")["dryRun"] == [
        "All"
    ]
    assert content_types == ["application/strategic-merge-patch+json"]
    assert patches == [
        {
            "metadata": {"resourceVersion": "rv-12"},
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "fin-graph.io/restart-token": "action-1",
                        }
                    }
                }
            },
        }
    ]


async def test_undo_sdk_restores_owned_template_with_exact_json_patch(
    kube_client: KubernetesClient, kube_rest: Any, monkeypatch: Any
) -> None:
    patches: list[list[dict[str, Any]]] = []
    content_types: list[str] = []
    original_request = kube_rest.request

    def record_request(method: str, url: str, **kwargs: Any) -> Any:
        if method == "PATCH":
            patches.append(kwargs["body"])
            content_types.append(kwargs["headers"]["Content-Type"])
        return original_request(method, url, **kwargs)

    monkeypatch.setattr(kube_rest, "request", record_request)
    await kube_client.rollout_undo("payments", "checkout", 11, 12, dry_run=True)
    assert kube_rest.query("/apis/apps/v1/namespaces/payments/deployments/checkout")["dryRun"] == [
        "All"
    ]
    assert content_types == ["application/json-patch+json"]
    assert patches[0][0] == {"op": "test", "path": "/metadata/resourceVersion", "value": "rv-12"}
    assert patches[0][1]["op"] == "replace"
    assert patches[0][1]["path"] == "/spec/template"
    assert patches[0][1]["value"]["spec"]["containers"][0]["image"] == (
        "registry.local/checkout:1.4.1"
    )


async def test_nodes_in_trouble_come_first(kube_client: KubernetesClient) -> None:
    nodes = await kube_client.list_nodes()

    assert [node["name"] for node in nodes] == ["node-2", "node-1"]
    assert nodes[0]["ready"] is False
    assert nodes[0]["pressure"] == ["MemoryPressure"]
    assert nodes[0]["taints"] == ["node.kubernetes.io/memory-pressure:NoSchedule"]
    assert nodes[1]["allocatable"] == {"cpu": "3920m", "memory": "15Gi", "pods": "110"}


async def test_an_autoscaler_pinned_at_its_maximum_says_so(kube_client: KubernetesClient) -> None:
    [autoscaler] = await kube_client.list_hpas("payments")

    assert autoscaler["target"] == "Deployment/checkout"
    assert autoscaler["at_max"] is True
    assert autoscaler["metrics"] == ["cpu 92%"]
    assert autoscaler["targets"] == ["cpu target 80%"]
    assert autoscaler["conditions"][0]["reason"] == "TooManyReplicas"


async def test_pod_usage_comes_from_metrics_server(kube_client: KubernetesClient) -> None:
    usage = await kube_client.pod_usage("payments")

    assert usage == [{"pod": CRASHING_POD, "container": "app", "cpu": "250m", "memory": "255Mi"}]


async def test_a_missing_metrics_server_is_explained(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    del kube_rest.routes["/apis/metrics.k8s.io/v1beta1/namespaces/payments/pods"]

    with pytest.raises(IntegrationError, match="metrics-server is not installed"):
        await kube_client.pod_usage("payments")


async def test_a_forbidden_call_carries_the_api_servers_explanation(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    kube_rest.fail(
        PODS, 403, "Forbidden", 'pods is forbidden: User "dev" cannot list resource "pods"'
    )

    with pytest.raises(IntegrationError) as caught:
        await kube_client.list_pods("payments")

    assert str(caught.value) == (
        "Kubernetes API error 403 while listing pods in payments: "
        'pods is forbidden: User "dev" cannot list resource "pods"'
    )


async def test_a_missing_pod_is_reported_by_name(kube_client: KubernetesClient) -> None:
    with pytest.raises(IntegrationError, match="404 while reading pod payments/ghost"):
        await kube_client.describe_pod("payments", "ghost")


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionRefusedError("Connection refused"),
        urllib3.exceptions.ProtocolError("Connection aborted."),
    ],
)
async def test_an_unreachable_cluster_is_an_integration_error(
    kube_client: KubernetesClient, kube_rest: Any, failure: Exception
) -> None:
    kube_rest.failure = failure

    with pytest.raises(
        IntegrationError,
        match=r"cannot reach the Kubernetes API at https://k8s\.test:6443 while listing pods",
    ):
        await kube_client.list_pods("payments")


@pytest.mark.parametrize(
    "failure",
    [
        ConfigException("exec plugin failed: run aws sso login"),
        RuntimeError("unexpected SDK state"),
    ],
)
async def test_any_other_client_failure_becomes_an_integration_error(
    kube_client: KubernetesClient, kube_rest: Any, failure: Exception
) -> None:
    kube_rest.failure = failure

    with pytest.raises(IntegrationError, match=re.escape(str(failure))):
        await kube_client.list_pods("payments")


async def test_a_listing_the_api_server_cut_short_is_marked_partial(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    status, listing = kube_rest.routes[PODS]
    metadata = {"continue": "page-2", "remainingItemCount": 300}
    kube_rest.routes[PODS] = (status, {**listing, "metadata": metadata})

    pods = await kube_client.list_pods("payments")

    assert len(pods) == 2
    assert "300 more items" in getattr(pods, "note", "")
