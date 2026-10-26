"""A recorded "payments" namespace served through the Kubernetes SDK's real HTTP seam.

``FakeKubeRest`` replaces only ``ApiClient.rest_client``: the SDK still builds
the URLs, validates parameters and deserializes the JSON, exactly as it does
against a live cluster. The checkout Deployment has one healthy pod and one
pod in CrashLoopBackOff after an OOM kill; node-2 is NotReady under memory
pressure; the checkout autoscaler is pinned at its maximum.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from kubernetes import client as k8s
from kubernetes.client import rest

from fin_graph.integrations.kubernetes.client import KubernetesClient

CRASHING_POD = "checkout-7d9f8b6c5-x2x9z"
HEALTHY_POD = "checkout-7d9f8b6c5-abcde"
LITERAL_DB_PASSWORD = "hunter2-literal-db-password"
LOG_TEXT = '{"level":"error","msg":"allocating 64MiB: out of memory"}\nfatal: OOM\n'
NAMESPACE = "/api/v1/namespaces/payments"
APPS = "/apis/apps/v1/namespaces/payments"


@dataclass
class FakeHttpResponse:
    status: int
    reason: str
    data: bytes
    headers: dict[str, str] = field(default_factory=dict)


class FakeKubeRest:
    """Answers each request path with a recorded body and remembers what was asked."""

    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self.routes = dict(routes)
        self.requests: list[tuple[str, dict[str, list[str]]]] = []
        self.failure: Exception | None = None

    def fail(self, path: str, status: int, reason: str, message: str) -> None:
        """Answer ``path`` with an API server error Status."""
        self.routes[path] = (status, status_body(status, reason, message))

    def query(self, path: str) -> dict[str, list[str]]:
        """Query parameters of the most recent request to ``path``."""
        return next(query for requested, query in reversed(self.requests) if requested == path)

    def request(
        self,
        method: str,
        url: str,
        headers: Any = None,
        body: Any = None,
        post_params: Any = None,
        _request_timeout: Any = None,
    ) -> rest.RESTResponse:
        parts = urlsplit(url)
        self.requests.append((parts.path, parse_qs(parts.query)))
        if self.failure is not None:
            raise self.failure
        status, payload = self.routes.get(
            parts.path, (404, status_body(404, "NotFound", f"{parts.path} not found"))
        )
        if isinstance(payload, str):
            data, content_type = payload.encode(), "text/plain"
        else:
            data, content_type = json.dumps(payload).encode(), "application/json"
        reason = "OK" if status < 400 else "Error"
        return rest.RESTResponse(
            FakeHttpResponse(status, reason, data, {"content-type": content_type})
        )

    def close(self) -> None:
        """The SDK calls this when its ApiClient closes."""


def status_body(code: int, reason: str, message: str) -> dict[str, Any]:
    return {
        "kind": "Status",
        "apiVersion": "v1",
        "status": "Failure",
        "message": message,
        "reason": reason,
        "code": code,
    }


def _listing(kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": kind, "apiVersion": "v1", "metadata": {}, "items": items}


def _template(app: str, image: str) -> dict[str, Any]:
    return {
        "metadata": {"labels": {"app": app}},
        "spec": {"containers": [{"name": "app", "image": image}]},
    }


def _pod(
    name: str,
    *,
    ready: bool,
    restarts: int,
    state: dict[str, Any],
    last_state: dict[str, Any] | None,
) -> dict[str, Any]:
    container_status: dict[str, Any] = {
        "name": "app",
        "image": "registry.local/checkout:1.4.2",
        "imageID": "sha256:0f1e",
        "ready": ready,
        "restartCount": restarts,
        "state": state,
    }
    if last_state is not None:
        container_status["lastState"] = last_state
    applied = {
        "spec": {"containers": [{"env": [{"name": "DB_PASSWORD", "value": LITERAL_DB_PASSWORD}]}]}
    }
    return {
        "metadata": {
            "name": name,
            "namespace": "payments",
            "uid": f"{name}-uid",
            "labels": {"app": "checkout"},
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": json.dumps(applied),
                "team": "payments",
            },
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "name": "checkout-7d9f8b6c5",
                    "uid": "rs-12",
                }
            ],
        },
        "spec": {
            "nodeName": "node-1",
            "containers": [
                {
                    "name": "app",
                    "image": "registry.local/checkout:1.4.2",
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"memory": "256Mi"},
                    },
                    "env": [
                        {"name": "DB_PASSWORD", "value": LITERAL_DB_PASSWORD},
                        {
                            "name": "API_KEY",
                            "valueFrom": {"secretKeyRef": {"name": "checkout", "key": "api-key"}},
                        },
                    ],
                }
            ],
        },
        "status": {
            "phase": "Running",
            "qosClass": "Burstable",
            "startTime": "2026-10-07T09:00:00Z",
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [container_status],
        },
    }


CRASHING = _pod(
    CRASHING_POD,
    ready=False,
    restarts=7,
    state={
        "waiting": {
            "reason": "CrashLoopBackOff",
            "message": "back-off 5m0s restarting failed container",
        }
    },
    last_state={
        "terminated": {"reason": "OOMKilled", "exitCode": 137, "finishedAt": "2026-10-07T10:05:00Z"}
    },
)
HEALTHY = _pod(
    HEALTHY_POD,
    ready=True,
    restarts=0,
    state={"running": {"startedAt": "2026-10-07T09:00:05Z"}},
    last_state=None,
)


def _event(name: str, *, kind: str, reason: str, message: str, last: str) -> dict[str, Any]:
    return {
        "metadata": {"name": name, "namespace": "payments"},
        "involvedObject": {"kind": "Pod", "name": CRASHING_POD, "namespace": "payments"},
        "type": kind,
        "reason": reason,
        "message": message,
        "count": 3,
        "firstTimestamp": "2026-10-07T09:30:00Z",
        "lastTimestamp": last,
    }


DEPLOYMENT = {
    "metadata": {
        "name": "checkout",
        "namespace": "payments",
        "uid": "checkout-uid",
        "resourceVersion": "rv-12",
        "annotations": {"deployment.kubernetes.io/revision": "12"},
    },
    "spec": {
        "replicas": 2,
        "selector": {"matchLabels": {"app": "checkout"}},
        "template": _template("checkout", "registry.local/checkout:1.4.2"),
    },
    "status": {
        "replicas": 2,
        "readyReplicas": 1,
        "updatedReplicas": 2,
        "availableReplicas": 1,
        "conditions": [
            {
                "type": "Available",
                "status": "False",
                "reason": "MinimumReplicasUnavailable",
                "message": "Deployment does not have minimum availability.",
            }
        ],
    },
}
STATEFULSET = {
    "metadata": {"name": "redis", "namespace": "payments"},
    "spec": {
        "replicas": 3,
        "serviceName": "redis",
        "selector": {"matchLabels": {"app": "redis"}},
        "template": _template("redis", "redis:7.4"),
    },
    "status": {"replicas": 3, "readyReplicas": 3, "updatedReplicas": 3, "availableReplicas": 3},
}
DAEMONSET = {
    "metadata": {"name": "node-exporter", "namespace": "payments"},
    "spec": {
        "selector": {"matchLabels": {"app": "node-exporter"}},
        "template": _template("node-exporter", "prom/node-exporter:1.8"),
    },
    "status": {
        "currentNumberScheduled": 3,
        "desiredNumberScheduled": 3,
        "numberMisscheduled": 0,
        "numberReady": 3,
        "numberAvailable": 3,
        "updatedNumberScheduled": 3,
    },
}


def _replica_set(
    name: str,
    *,
    owner: str,
    revision: int,
    image: str,
    created: str,
    replicas: int,
    change_cause: str | None = None,
) -> dict[str, Any]:
    annotations = {"deployment.kubernetes.io/revision": str(revision)}
    if change_cause is not None:
        annotations["kubernetes.io/change-cause"] = change_cause
    return {
        "metadata": {
            "name": name,
            "namespace": "payments",
            "creationTimestamp": created,
            "annotations": annotations,
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": owner,
                    "uid": f"{owner}-uid",
                }
            ],
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": "checkout"}},
            "template": _template("checkout", image),
        },
        "status": {"replicas": replicas, "readyReplicas": replicas},
    }


def _node(
    name: str, *, ready: bool, memory_pressure: bool, taints: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "metadata": {"name": name},
        "spec": {"taints": taints},
        "status": {
            "conditions": [
                {"type": "MemoryPressure", "status": "True" if memory_pressure else "False"},
                {"type": "DiskPressure", "status": "False"},
                {"type": "Ready", "status": "True" if ready else "False"},
            ],
            "allocatable": {
                "cpu": "3920m",
                "memory": "15Gi",
                "pods": "110",
                "ephemeral-storage": "95Gi",
            },
            "nodeInfo": {
                "architecture": "amd64",
                "bootID": "boot",
                "containerRuntimeVersion": "containerd://1.7.22",
                "kernelVersion": "6.1.0",
                "kubeProxyVersion": "v1.31.2",
                "kubeletVersion": "v1.31.2",
                "machineID": "machine",
                "operatingSystem": "linux",
                "osImage": "Ubuntu 24.04",
                "systemUUID": "uuid",
            },
        },
    }


HPA = {
    "metadata": {"name": "checkout", "namespace": "payments"},
    "spec": {
        "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "checkout"},
        "minReplicas": 2,
        "maxReplicas": 10,
        "metrics": [
            {
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "target": {"type": "Utilization", "averageUtilization": 80},
                },
            }
        ],
    },
    "status": {
        "currentReplicas": 10,
        "desiredReplicas": 10,
        "currentMetrics": [
            {
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "current": {"averageUtilization": 92, "averageValue": "740m"},
                },
            }
        ],
        "conditions": [
            {
                "type": "ScalingLimited",
                "status": "True",
                "reason": "TooManyReplicas",
                "message": "the desired replica count is more than the maximum replica count",
            }
        ],
    },
}
VERSION = {
    "major": "1",
    "minor": "31",
    "gitVersion": "v1.31.2",
    "gitCommit": "5864a46",
    "gitTreeState": "clean",
    "buildDate": "2026-01-01T00:00:00Z",
    "goVersion": "go1.22.8",
    "compiler": "gc",
    "platform": "linux/amd64",
}


def recorded_routes() -> dict[str, tuple[int, Any]]:
    """Every path the client calls in the payments namespace, with its recorded answer."""
    return {
        "/version/": (200, VERSION),
        f"{NAMESPACE}/pods": (200, _listing("PodList", [HEALTHY, CRASHING])),
        f"{NAMESPACE}/pods/{CRASHING_POD}": (200, CRASHING),
        f"{NAMESPACE}/pods/{CRASHING_POD}/log": (200, LOG_TEXT),
        f"{NAMESPACE}/events": (
            200,
            _listing(
                "EventList",
                [
                    _event(
                        "e1",
                        kind="Normal",
                        reason="Pulled",
                        message="Container image already present",
                        last="2026-10-07T09:40:00Z",
                    ),
                    _event(
                        "e2",
                        kind="Warning",
                        reason="BackOff",
                        message="Back-off restarting failed container app",
                        last="2026-10-07T10:06:00Z",
                    ),
                ],
            ),
        ),
        f"{APPS}/deployments": (200, _listing("DeploymentList", [DEPLOYMENT])),
        f"{APPS}/deployments/checkout": (200, DEPLOYMENT),
        f"{APPS}/statefulsets": (200, _listing("StatefulSetList", [STATEFULSET])),
        f"{APPS}/daemonsets": (200, _listing("DaemonSetList", [DAEMONSET])),
        f"{APPS}/replicasets": (
            200,
            _listing(
                "ReplicaSetList",
                [
                    _replica_set(
                        "checkout-5c6d7e8f9",
                        owner="checkout",
                        revision=11,
                        image="registry.local/checkout:1.4.1",
                        created="2026-10-01T08:00:00Z",
                        replicas=0,
                    ),
                    _replica_set(
                        "checkout-7d9f8b6c5",
                        owner="checkout",
                        revision=12,
                        image="registry.local/checkout:1.4.2",
                        created="2026-10-07T08:55:00Z",
                        replicas=2,
                        change_cause="deploy 1.4.2",
                    ),
                    _replica_set(
                        "checkout-canary-6b5a4",
                        owner="checkout-canary",
                        revision=3,
                        image="registry.local/checkout:2.0.0-rc1",
                        created="2026-10-06T12:00:00Z",
                        replicas=1,
                    ),
                ],
            ),
        ),
        "/api/v1/nodes": (
            200,
            _listing(
                "NodeList",
                [
                    _node("node-1", ready=True, memory_pressure=False, taints=[]),
                    _node(
                        "node-2",
                        ready=False,
                        memory_pressure=True,
                        taints=[
                            {"key": "node.kubernetes.io/memory-pressure", "effect": "NoSchedule"}
                        ],
                    ),
                ],
            ),
        ),
        "/apis/autoscaling/v2/namespaces/payments/horizontalpodautoscalers": (
            200,
            _listing("HorizontalPodAutoscalerList", [HPA]),
        ),
        "/apis/metrics.k8s.io/v1beta1/namespaces/payments/pods": (
            200,
            {
                "kind": "PodMetricsList",
                "items": [
                    {
                        "metadata": {"name": CRASHING_POD, "namespace": "payments"},
                        "containers": [
                            {"name": "app", "usage": {"cpu": "250m", "memory": "255Mi"}}
                        ],
                    }
                ],
            },
        ),
    }


KUBECONFIG = """\
apiVersion: v1
kind: Config
clusters:
- name: test
  cluster:
    server: https://k8s.test:6443
    insecure-skip-tls-verify: true
users:
- name: tester
  user:
    token: test-token
contexts:
- name: test
  context:
    cluster: test
    user: tester
current-context: test
"""


@pytest.fixture
def kube_rest() -> FakeKubeRest:
    return FakeKubeRest(recorded_routes())


@pytest.fixture
def kube_client(kube_rest: FakeKubeRest) -> KubernetesClient:
    api_client = k8s.ApiClient(k8s.Configuration(host="https://k8s.test:6443"))
    api_client.rest_client = kube_rest
    return KubernetesClient(api_client, request_timeout=5.0, verify_namespace="payments")


@pytest.fixture
def kubeconfig_file(tmp_path: Path) -> Path:
    path = tmp_path / "kubeconfig.yaml"
    path.write_text(KUBECONFIG, encoding="utf-8")
    return path
