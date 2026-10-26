"""Read-only access to one Kubernetes cluster, async over the blocking SDK."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import urllib3
from kubernetes import client as k8s
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from kubernetes.config.incluster_config import load_incluster_config
from kubernetes.config.kube_config import load_kube_config

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.kubernetes import summaries
from fin_graph.integrations.kubernetes.settings import KubernetesSettings
from fin_graph.integrations.payload import PartialList

MAX_LOG_LINES = 1000


class KubernetesClient:
    """One cluster's API. Every call runs the blocking SDK in a worker thread.

    Lists come back with the entries that most need attention first (unhealthy
    pods, workloads and nodes; newest events), because long lists are cut
    before they reach the model. A list the API server cut short (at
    ``K8S_MAX_ITEMS``) is a ``PartialList`` whose note tells the model. Every
    failure raises ``IntegrationError``, with the API server's own explanation
    when there is one.
    """

    def __init__(
        self,
        api_client: k8s.ApiClient,
        *,
        request_timeout: float = 30.0,
        max_items: int = 200,
        verify_namespace: str = "default",
    ) -> None:
        self._api_client = api_client
        self._core = k8s.CoreV1Api(api_client)
        self._apps = k8s.AppsV1Api(api_client)
        self._autoscaling = k8s.AutoscalingV2Api(api_client)
        self._custom = k8s.CustomObjectsApi(api_client)
        self._version = k8s.VersionApi(api_client)
        self._timeout = request_timeout
        self._max_items = max_items
        self.verify_namespace = verify_namespace

    @classmethod
    def from_settings(cls, settings: KubernetesSettings) -> KubernetesClient:
        """Load credentials into a private ``Configuration``, never the SDK's global default."""
        configuration = k8s.Configuration()
        kubeconfig = Path(settings.kubeconfig).expanduser() if settings.kubeconfig else None
        if settings.in_cluster:
            load_incluster_config(client_configuration=configuration)
        else:
            if kubeconfig is not None and not kubeconfig.is_file():
                raise FileNotFoundError(f"kubeconfig {kubeconfig} does not exist")
            load_kube_config(
                config_file=str(kubeconfig) if kubeconfig else None,
                context=settings.context,
                client_configuration=configuration,
                persist_config=False,
            )
        return cls(
            k8s.ApiClient(configuration),
            request_timeout=settings.request_timeout_seconds,
            max_items=settings.max_items,
            verify_namespace=settings.verify_namespace,
        )

    @property
    def host(self) -> str:
        """The API server URL this client talks to."""
        return str(self._api_client.configuration.host)

    async def version(self) -> str:
        """The API server's version, for example ``v1.31.2``."""
        info = await self._call("reading the server version", self._version.get_code)
        return str(info.git_version)

    async def scale_deployment(
        self, namespace: str, deployment: str, replicas: int, *, dry_run: bool = False
    ) -> dict[str, Any]:
        """Set a Deployment replica count; server-side dry-run is mandatory in preparation.

        Repeating the same scale is safe because it sets a desired value instead of
        incrementing one. The caller still supplies a stable action key for auditing.
        """
        if not namespace or not deployment or not 0 <= replicas <= 1000:
            raise ValueError("invalid deployment scale request")
        kwargs: dict[str, Any] = {
            "name": deployment,
            "namespace": namespace,
            "body": {"spec": {"replicas": replicas}},
        }
        if dry_run:
            kwargs["dry_run"] = "All"
        result = await self._call(
            f"scaling deployment {namespace}/{deployment}",
            self._apps.patch_namespaced_deployment_scale,
            **kwargs,
        )
        spec = result.spec
        if spec is None or spec.replicas is None:
            raise IntegrationError("Kubernetes scale response has no replica count")
        return {"namespace": namespace, "deployment": deployment, "replicas": spec.replicas}

    async def deployment_available(self, namespace: str, deployment: str, replicas: int) -> bool:
        """Confirm the controller observed the requested scale, including scale to zero."""
        item = await self._call(
            f"checking deployment {namespace}/{deployment}",
            self._apps.read_namespaced_deployment,
            name=deployment,
            namespace=namespace,
        )
        status = item.status
        metadata = item.metadata
        if status is None or metadata is None or item.spec is None:
            return False
        return (
            item.spec.replicas == replicas
            and (status.observed_generation or 0) >= (metadata.generation or 0)
            and (status.replicas or 0) == replicas
            and (status.updated_replicas or 0) == replicas
            and (status.available_replicas or 0) == replicas
        )

    async def deployment_replicas(self, namespace: str, deployment: str) -> int:
        """Read the desired count so a scale can propose an exact compensating action."""
        item = await self._call(
            f"reading deployment {namespace}/{deployment}",
            self._apps.read_namespaced_deployment,
            name=deployment,
            namespace=namespace,
        )
        replicas = getattr(getattr(item, "spec", None), "replicas", None)
        if not isinstance(replicas, int):
            raise IntegrationError("Kubernetes deployment has no desired replica count")
        return replicas

    async def rollout_restart(
        self, namespace: str, deployment: str, restart_token: str, *, dry_run: bool = False
    ) -> dict[str, Any]:
        """Change the pod template using a caller-stable token, after reading the target."""
        item = await self._call(
            f"reading deployment {namespace}/{deployment}",
            self._apps.read_namespaced_deployment,
            name=deployment,
            namespace=namespace,
        )
        if item.metadata is None or not item.metadata.resource_version:
            raise IntegrationError("Kubernetes deployment has no resource version")
        annotations = (
            getattr(getattr(item.spec.template, "metadata", None), "annotations", None) or {}
        )
        if annotations.get("fin-graph.io/restart-token") == restart_token:
            raise ValueError("restart token is already present on deployment")
        body = {
            "metadata": {"resourceVersion": item.metadata.resource_version},
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "fin-graph.io/restart-token": restart_token,
                        }
                    }
                }
            },
        }
        kwargs: dict[str, Any] = {
            "name": deployment,
            "namespace": namespace,
            "body": body,
            "_content_type": "application/strategic-merge-patch+json",
        }
        if dry_run:
            kwargs["dry_run"] = "All"
        await self._call(
            f"restarting deployment {namespace}/{deployment}",
            self._apps.patch_namespaced_deployment,
            **kwargs,
        )
        return {"namespace": namespace, "deployment": deployment, "restart_token": restart_token}

    async def deployment_has_restart_token(
        self, namespace: str, deployment: str, restart_token: str
    ) -> bool:
        item = await self._call(
            f"checking deployment {namespace}/{deployment}",
            self._apps.read_namespaced_deployment,
            name=deployment,
            namespace=namespace,
        )
        annotations = (
            getattr(getattr(item.spec.template, "metadata", None), "annotations", None) or {}
        )
        desired = item.spec.replicas or 0
        available = item.status.available_replicas if item.status else 0
        updated = item.status.updated_replicas if item.status else 0
        return (
            annotations.get("fin-graph.io/restart-token") == restart_token
            and item.metadata is not None
            and item.metadata.generation is not None
            and item.status is not None
            and item.status.observed_generation is not None
            and item.status.observed_generation >= item.metadata.generation
            and (available or 0) >= desired
            and (updated or 0) >= desired
        )

    async def rollout_undo(
        self,
        namespace: str,
        deployment: str,
        target_revision: int,
        expected_revision: int,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Restore an owned ReplicaSet template if the current revision is unchanged."""
        item = await self._call(
            f"reading deployment {namespace}/{deployment}",
            self._apps.read_namespaced_deployment,
            name=deployment,
            namespace=namespace,
        )
        metadata = item.metadata
        if metadata is None or not metadata.uid or not metadata.resource_version:
            raise IntegrationError("Kubernetes deployment has no UID or resource version")
        current = (metadata.annotations or {}).get("deployment.kubernetes.io/revision")
        if current != str(expected_revision):
            raise ValueError("deployment revision changed before rollout undo")
        if target_revision >= expected_revision:
            raise ValueError("target revision must precede the current revision")
        template = await self._revision_template(
            namespace, deployment, target_revision, metadata.uid
        )
        body = [
            {"op": "test", "path": "/metadata/resourceVersion", "value": metadata.resource_version},
            {"op": "replace", "path": "/spec/template", "value": template},
        ]
        kwargs: dict[str, Any] = {
            "name": deployment,
            "namespace": namespace,
            "body": body,
            "_content_type": "application/json-patch+json",
        }
        if dry_run:
            kwargs["dry_run"] = "All"
        await self._call(
            f"undoing deployment {namespace}/{deployment} to revision {target_revision}",
            self._apps.patch_namespaced_deployment,
            **kwargs,
        )
        return {
            "namespace": namespace,
            "deployment": deployment,
            "target_revision": target_revision,
            "expected_revision": expected_revision,
        }

    async def deployment_matches_revision(
        self, namespace: str, deployment: str, target_revision: int
    ) -> bool:
        item = await self._call(
            f"checking deployment {namespace}/{deployment}",
            self._apps.read_namespaced_deployment,
            name=deployment,
            namespace=namespace,
        )
        if item.metadata is None or not item.metadata.uid:
            raise IntegrationError("Kubernetes deployment has no UID")
        template = await self._revision_template(
            namespace, deployment, target_revision, item.metadata.uid
        )
        live = _template_dict(item.spec.template)
        desired = item.spec.replicas or 0
        available = item.status.available_replicas if item.status else 0
        updated = item.status.updated_replicas if item.status else 0
        return (
            live.get("spec") == template.get("spec")
            and item.metadata.generation is not None
            and item.status is not None
            and item.status.observed_generation is not None
            and item.status.observed_generation >= item.metadata.generation
            and (available or 0) >= desired
            and (updated or 0) >= desired
        )

    async def _revision_template(
        self, namespace: str, deployment: str, revision: int, deployment_uid: str
    ) -> dict[str, Any]:
        listing = await self._call(
            f"listing replica sets of {namespace}/{deployment}",
            self._apps.list_namespaced_replica_set,
            namespace=namespace,
        )
        for replica_set in listing.items:
            metadata = replica_set.metadata
            if metadata is None:
                continue
            owners = metadata.owner_references or []
            if (
                (metadata.annotations or {}).get("deployment.kubernetes.io/revision")
                == str(revision)
                and any(
                    owner.kind == "Deployment"
                    and owner.name == deployment
                    and owner.uid == deployment_uid
                    for owner in owners
                )
                and replica_set.spec is not None
                and replica_set.spec.template is not None
            ):
                template = _template_dict(replica_set.spec.template)
                template_metadata = template.get("metadata") or {}
                labels = dict(template_metadata.get("labels") or {})
                labels.pop("pod-template-hash", None)
                annotations = dict(template_metadata.get("annotations") or {})
                annotations.pop("deployment.kubernetes.io/revision", None)
                return {
                    "metadata": {"labels": labels, "annotations": annotations},
                    "spec": template["spec"],
                }
        raise ValueError("target revision is not an owned ReplicaSet with a pod template")

    async def delete_pod(
        self, namespace: str, pod: str, uid: str, *, dry_run: bool = False
    ) -> dict[str, Any]:
        """Delete exactly one pod UID, so a replacement cannot be deleted by name."""
        item = await self._call(
            f"reading pod {namespace}/{pod}",
            self._core.read_namespaced_pod,
            name=pod,
            namespace=namespace,
        )
        if item.metadata is None or item.metadata.uid != uid:
            raise ValueError("pod UID changed before deletion")
        body = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid}}
        kwargs: dict[str, Any] = {"name": pod, "namespace": namespace, "body": body}
        if dry_run:
            kwargs["dry_run"] = "All"
        await self._call(
            f"deleting pod {namespace}/{pod}", self._core.delete_namespaced_pod, **kwargs
        )
        return {"namespace": namespace, "pod": pod, "uid": uid}

    async def pod_uid_absent(self, namespace: str, pod: str, uid: str) -> bool:
        listing = await self._call(
            f"checking pod {namespace}/{pod}",
            self._core.list_namespaced_pod,
            namespace=namespace,
            field_selector=f"metadata.name={pod}",
            limit=1,
        )
        return all(item.metadata is None or item.metadata.uid != uid for item in listing.items)

    async def list_pods(
        self, namespace: str, label_selector: str | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Pod summaries, unhealthy pods first."""
        listing = await self._call(
            f"listing pods in {namespace}",
            self._core.list_namespaced_pod,
            namespace,
            label_selector=label_selector,
            limit=limit or self._max_items,
        )
        pods = [summaries.pod_summary(pod) for pod in listing.items]
        ordered = sorted(pods, key=lambda pod: (summaries.pod_is_healthy(pod), pod.get("name", "")))
        return _partial(ordered, "narrow with label_selector", listing.metadata)

    async def describe_pod(self, namespace: str, name: str) -> dict[str, Any]:
        """One pod in detail, with environment variable names but never their values."""
        pod = await self._call(
            f"reading pod {namespace}/{name}", self._core.read_namespaced_pod, name, namespace
        )
        return {**summaries.pod_detail(pod), "uid": pod.metadata.uid if pod.metadata else None}

    async def pod_logs(
        self,
        namespace: str,
        name: str,
        *,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 200,
        since_seconds: int | None = None,
    ) -> str:
        """The last ``tail_lines`` log lines (at most 1000) of a container."""
        # The raw body is decoded here so a log made of JSON lines is never parsed as JSON.
        response: Any = await self._call(
            f"reading logs of pod {namespace}/{name}",
            self._core.read_namespaced_pod_log,
            name,
            namespace,
            container=container,
            previous=previous,
            tail_lines=min(max(tail_lines, 1), MAX_LOG_LINES),
            since_seconds=since_seconds,
            _preload_content=False,
        )
        return bytes(response.data).decode("utf-8", errors="replace")

    async def list_events(
        self, namespace: str, *, involved_object: str | None = None, warnings_only: bool = False
    ) -> list[dict[str, Any]]:
        """Events, newest first, optionally for one object or only warnings."""
        selectors = []
        if involved_object:
            selectors.append(f"involvedObject.name={involved_object}")
        if warnings_only:
            selectors.append("type=Warning")
        listing = await self._call(
            f"listing events in {namespace}",
            self._core.list_namespaced_event,
            namespace,
            field_selector=",".join(selectors) or None,
            limit=self._max_items,
        )
        ordered = sorted(listing.items, key=summaries.event_last_seen, reverse=True)
        events = [summaries.event_summary(event) for event in ordered]
        return _partial(events, "narrow with involved_object or warnings_only", listing.metadata)

    async def list_workloads(
        self, namespace: str, label_selector: str | None = None
    ) -> list[dict[str, Any]]:
        """Deployments, StatefulSets and DaemonSets, the ones missing replicas first."""
        deployments, statefulsets, daemonsets = await asyncio.gather(
            self._call(
                f"listing deployments in {namespace}",
                self._apps.list_namespaced_deployment,
                namespace,
                label_selector=label_selector,
                limit=self._max_items,
            ),
            self._call(
                f"listing statefulsets in {namespace}",
                self._apps.list_namespaced_stateful_set,
                namespace,
                label_selector=label_selector,
                limit=self._max_items,
            ),
            self._call(
                f"listing daemonsets in {namespace}",
                self._apps.list_namespaced_daemon_set,
                namespace,
                label_selector=label_selector,
                limit=self._max_items,
            ),
        )
        workloads = [
            *(summaries.deployment_summary(item) for item in deployments.items),
            *(summaries.statefulset_summary(item) for item in statefulsets.items),
            *(summaries.daemonset_summary(item) for item in daemonsets.items),
        ]
        ordered = sorted(
            workloads,
            key=lambda item: (
                summaries.workload_is_healthy(item),
                item["kind"],
                item.get("name", ""),
            ),
        )
        return _partial(
            ordered,
            "narrow with label_selector",
            deployments.metadata,
            statefulsets.metadata,
            daemonsets.metadata,
        )

    async def rollout_history(self, namespace: str, deployment: str) -> list[dict[str, Any]]:
        """The Deployment's revisions (its ReplicaSets), newest first."""
        target = await self._call(
            f"reading deployment {namespace}/{deployment}",
            self._apps.read_namespaced_deployment,
            deployment,
            namespace,
        )
        listing = await self._call(
            f"listing replica sets of {namespace}/{deployment}",
            self._apps.list_namespaced_replica_set,
            namespace,
            label_selector=summaries.selector_text(target) or None,
            limit=self._max_items,
        )
        revisions = [
            summaries.revision_summary(item)
            for item in listing.items
            if summaries.is_owned_by(item, "Deployment", deployment)
        ]
        ordered = sorted(revisions, key=lambda item: item.get("revision", 0), reverse=True)
        return _partial(ordered, "older revisions may be missing", listing.metadata)

    async def list_nodes(self) -> list[dict[str, Any]]:
        """Node summaries, the not-ready or pressured ones first."""
        listing = await self._call("listing nodes", self._core.list_node, limit=self._max_items)
        nodes = [summaries.node_summary(node) for node in listing.items]
        ordered = sorted(
            nodes, key=lambda node: (summaries.node_is_healthy(node), node.get("name", ""))
        )
        return _partial(ordered, "only part of the cluster's nodes is listed", listing.metadata)

    async def list_hpas(self, namespace: str) -> list[dict[str, Any]]:
        """HorizontalPodAutoscalers with their bounds, metrics and conditions."""
        listing = await self._call(
            f"listing autoscalers in {namespace}",
            self._autoscaling.list_namespaced_horizontal_pod_autoscaler,
            namespace,
            limit=self._max_items,
        )
        autoscalers = [summaries.hpa_summary(item) for item in listing.items]
        return _partial(
            autoscalers, "only part of the namespace's autoscalers is listed", listing.metadata
        )

    async def pod_usage(
        self, namespace: str, label_selector: str | None = None
    ) -> list[dict[str, Any]]:
        """Current CPU and memory per container, from ``metrics.k8s.io``."""
        listing = await self._call(
            f"reading pod usage in {namespace}",
            self._custom.list_namespaced_custom_object,
            "metrics.k8s.io",
            "v1beta1",
            namespace,
            "pods",
            label_selector=label_selector,
            not_found=(
                f"pod usage is unavailable: metrics.k8s.io answered 404 (metrics-server is not "
                f"installed, or namespace {namespace} does not exist)"
            ),
        )
        body = listing if isinstance(listing, dict) else {}
        usage = [entry for item in body.get("items", []) for entry in summaries.pod_usage(item)]
        if (body.get("metadata") or {}).get("continue"):
            return PartialList(
                usage, note="…[partial: the API server has more items; narrow with label_selector]"
            )
        return usage

    async def _call[T](
        self,
        action: str,
        method: Callable[..., T],
        /,
        *args: Any,
        not_found: str | None = None,
        **kwargs: Any,
    ) -> T:
        def run() -> T:
            try:
                return method(*args, _request_timeout=self._timeout, **kwargs)
            except ApiException as exc:
                raise self._api_error(exc, action, not_found) from exc
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                raise IntegrationError(
                    f"cannot reach the Kubernetes API at {self.host} while {action}: {exc}"
                ) from exc
            except ConfigException as exc:
                # An exec credential plugin (EKS, GKE, AKS) failing to refresh its token.
                raise IntegrationError(
                    f"cannot authenticate to the Kubernetes API at {self.host} "
                    f"while {action}: {exc}"
                ) from exc
            except Exception as exc:
                # Anything else the SDK raises still reaches the model; the run goes on.
                raise IntegrationError(
                    f"Kubernetes client error while {action}: {type(exc).__name__}: {exc}"
                ) from exc

        return await asyncio.to_thread(run)

    def _api_error(self, exc: ApiException, action: str, not_found: str | None) -> IntegrationError:
        if not exc.status:
            return IntegrationError(
                f"cannot reach the Kubernetes API at {self.host} while {action}: {exc.reason}"
            )
        if exc.status == 404 and not_found:
            return IntegrationError(not_found)
        return IntegrationError(
            f"Kubernetes API error {exc.status} while {action}: {_status_message(exc)}"
        )


def _status_message(exc: ApiException) -> str:
    """The ``message`` of the API server's Status body, else the HTTP reason."""
    try:
        body = json.loads(exc.body or "")
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return str(exc.reason or "no details")


def _template_dict(template: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        k8s.ApiClient().sanitize_for_serialization(template),  # type: ignore[no-untyped-call]
    )


def _partial(
    items: list[dict[str, Any]], hint: str, *metas: k8s.V1ListMeta | None
) -> list[dict[str, Any]]:
    """``items``, marked partial when the API server holds more than it returned."""
    cut = [
        meta
        for meta in metas
        if meta is not None and (meta.var_continue or meta.remaining_item_count)
    ]
    if not cut:
        return items
    remaining = sum(meta.remaining_item_count or 0 for meta in cut)
    more = f"{remaining} more items" if remaining else "more items"
    return PartialList(items, note=f"…[partial: the API server has {more}; {hint}]")
