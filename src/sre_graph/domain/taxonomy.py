"""Root-cause categories: the one taxonomy the diagnosis, the report and the evals share.

A category is deliberately narrow, so it points at the failing subsystem
("storage IOPS throttling", "OOMKilled") rather than a bucket that matches a
dozen failure modes. The generic group exists for when evidence cannot
support a narrower answer. Ported from OpenSRE's taxonomy; the names match
its evaluation scenarios exactly.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum


class CategoryGroup(StrEnum):
    """Groups that order the taxonomy in prompts and reports."""

    DATABASE = "database"
    KUBERNETES = "kubernetes_workload"
    NETWORK = "network_and_dns"
    CLOUD_STORAGE = "cloud_storage"
    DEPENDENCY = "external_dependency"
    CODE_AND_CONFIG = "code_and_configuration"
    DATA_PIPELINE = "data_and_pipeline"
    WORKLOAD = "workload_and_traffic"
    INFRASTRUCTURE = "infrastructure"
    GENERIC = "generic_fallback"


@dataclass(frozen=True)
class RootCauseCategory:
    """One category: a stable name, its group, and a one-line meaning."""

    name: str
    group: CategoryGroup
    description: str


HEALTHY = "healthy"
UNKNOWN = "unknown"

_D = CategoryGroup.DATABASE
_K = CategoryGroup.KUBERNETES
_N = CategoryGroup.NETWORK
_S = CategoryGroup.CLOUD_STORAGE
_X = CategoryGroup.DEPENDENCY
_C = CategoryGroup.CODE_AND_CONFIG
_P = CategoryGroup.DATA_PIPELINE
_W = CategoryGroup.WORKLOAD
_I = CategoryGroup.INFRASTRUCTURE
_G = CategoryGroup.GENERIC

CATEGORIES: tuple[RootCauseCategory, ...] = (
    RootCauseCategory(
        "connection_exhaustion", _D, "max_connections ceiling reached; new sessions are rejected."
    ),
    RootCauseCategory(
        "connection_pool_leak", _D, "Application connection pool acquires faster than it releases."
    ),
    RootCauseCategory(
        "idle_in_transaction_session_leak",
        _D,
        "Sessions stuck idle-in-transaction hold locks and slots.",
    ),
    RootCauseCategory(
        "max_connections_misconfigured",
        _D,
        "Server-side max_connections value is too low for current load shape.",
    ),
    RootCauseCategory(
        "cpu_saturation_bad_query",
        _D,
        "One hot SQL pattern (missing index, full scan) saturates CPU.",
    ),
    RootCauseCategory(
        "cpu_saturation_workload_burst",
        _D,
        "Aggregate workload increase saturates CPU without one dominant query.",
    ),
    RootCauseCategory(
        "query_plan_regression",
        _D,
        "Planner choice changed (stats, parameter, version) and degraded.",
    ),
    RootCauseCategory(
        "stale_statistics", _D, "Outdated planner stats produce inefficient plans on changed data."
    ),
    RootCauseCategory(
        "missing_index", _D, "Required selective index absent; sequential scans dominate IO/CPU."
    ),
    RootCauseCategory(
        "lock_contention", _D, "Row/table locks queue; queries wait without saturating compute."
    ),
    RootCauseCategory(
        "deadlock_storm", _D, "Repeated deadlock cycles drive transaction failure rate."
    ),
    RootCauseCategory(
        "storage_exhaustion", _D, "Disk full; writes block, FreeStorageSpace approaches zero."
    ),
    RootCauseCategory(
        "storage_iops_throttling",
        _D,
        "EBS/storage IOPS or throughput limit reached, queueing reads/writes.",
    ),
    RootCauseCategory(
        "storage_burst_balance_depleted",
        _D,
        "gp2-style burst balance exhausted; baseline IOPS too low for load.",
    ),
    RootCauseCategory(
        "checkpoint_io_storm",
        _D,
        "Bursty checkpoint flushes saturate write I/O (LWLock:BufferMapping).",
    ),
    RootCauseCategory(
        "vacuum_freeze_storm", _D, "Autovacuum FREEZE on a large table dominates load and locks."
    ),
    RootCauseCategory(
        "autovacuum_blocked", _D, "Autovacuum cannot keep up due to long transactions or settings."
    ),
    RootCauseCategory(
        "transaction_id_wraparound_pressure",
        _D,
        "MaximumUsedTransactionIDs nearing wraparound; reads/writes degrade.",
    ),
    RootCauseCategory(
        "table_bloat", _D, "Heavy update/delete bloat degrades scans and inflates IO."
    ),
    RootCauseCategory(
        "replication_lag_wal_volume", _D, "Primary generates WAL faster than replica can replay."
    ),
    RootCauseCategory(
        "replication_lag_long_query_on_replica",
        _D,
        "Long-running query on replica blocks WAL apply.",
    ),
    RootCauseCategory(
        "replication_lag_replica_undersized",
        _D,
        "Replica instance class too small to keep up with steady write rate.",
    ),
    RootCauseCategory(
        "wal_archiving_failure", _D, "WAL archive backlog grows; threatens primary availability."
    ),
    RootCauseCategory(
        "failover_event", _D, "Multi-AZ or replica failover; outage explained by topology change."
    ),
    RootCauseCategory(
        "dual_resource_exhaustion",
        _D,
        "Two independent DB resources saturate simultaneously (compositional).",
    ),
    RootCauseCategory(
        "pod_oomkilled", _K, "Container exceeded memory limit and was killed by the kubelet."
    ),
    RootCauseCategory(
        "pod_cpu_throttled", _K, "Container hit CPU limit and is throttled; latency rises."
    ),
    RootCauseCategory(
        "pod_evicted_node_pressure", _K, "Pod evicted due to node memory/disk/PID pressure."
    ),
    RootCauseCategory(
        "pod_crashloop_backoff",
        _K,
        "Container exits non-zero on start; kubelet backs off restarts.",
    ),
    RootCauseCategory(
        "pod_imagepull_backoff", _K, "Image pull fails (auth, missing tag, registry outage)."
    ),
    RootCauseCategory(
        "pod_pending_insufficient_resources",
        _K,
        "Scheduler cannot place pod due to CPU/mem/quota constraints.",
    ),
    RootCauseCategory(
        "pod_pending_unschedulable",
        _K,
        "Scheduler cannot place pod due to taints/affinity/topology.",
    ),
    RootCauseCategory(
        "liveness_probe_misconfigured", _K, "Liveness probe kills otherwise-healthy containers."
    ),
    RootCauseCategory(
        "readiness_probe_misconfigured",
        _K,
        "Readiness probe failure removes pods from Service endpoints.",
    ),
    RootCauseCategory(
        "node_not_ready", _K, "Node went NotReady (kubelet, container runtime, network)."
    ),
    RootCauseCategory(
        "service_endpoint_mismatch",
        _K,
        "Service selector / target port doesn't match pod labels/ports.",
    ),
    RootCauseCategory(
        "deployment_rollout_stuck", _K, "Rollout blocked before any new pod becomes ready."
    ),
    RootCauseCategory(
        "hpa_misconfiguration", _K, "HPA cannot scale (missing metrics, wrong target)."
    ),
    RootCauseCategory(
        "pdb_blocking_drain", _K, "PodDisruptionBudget prevents node drain or scale-in."
    ),
    RootCauseCategory(
        "ingress_misconfiguration", _K, "Ingress/route rules don't match desired traffic path."
    ),
    RootCauseCategory(
        "dns_resolution_failure", _N, "Service or upstream DNS resolution fails or times out."
    ),
    RootCauseCategory(
        "tls_certificate_expired", _N, "Certificate expiry or rotation issue breaks TLS handshakes."
    ),
    RootCauseCategory(
        "mtls_handshake_failure", _N, "Mutual TLS misconfiguration prevents handshake completion."
    ),
    RootCauseCategory(
        "security_group_misconfiguration", _N, "VPC/SG/NACL rule blocks required traffic."
    ),
    RootCauseCategory(
        "load_balancer_unhealthy_targets",
        _N,
        "ALB/NLB/ELB target group health check failures drive traffic loss.",
    ),
    RootCauseCategory(
        "nat_gateway_throttling", _N, "NAT gateway port allocation or throughput limit reached."
    ),
    RootCauseCategory(
        "network_partition", _N, "Connectivity loss between subnets, VPCs, or regions."
    ),
    RootCauseCategory(
        "s3_object_missing", _S, "Expected S3 object missing (timing, prefix, or upstream skipped)."
    ),
    RootCauseCategory(
        "s3_access_denied", _S, "S3 permissions/IAM policy block expected reads or writes."
    ),
    RootCauseCategory(
        "s3_throttling", _S, "S3 prefix throttling (503 SlowDown) limits throughput."
    ),
    RootCauseCategory("ebs_volume_full", _S, "Attached EBS volume reached capacity; writes fail."),
    RootCauseCategory(
        "upstream_service_outage", _X, "External service is unavailable / SLO breached."
    ),
    RootCauseCategory(
        "upstream_schema_change", _X, "Upstream API contract changed and broke consumers."
    ),
    RootCauseCategory(
        "upstream_rate_limit", _X, "Upstream throttling (HTTP 429) is the proximate cause."
    ),
    RootCauseCategory(
        "upstream_authentication_failure",
        _X,
        "Token / cred rotation / expiry breaks downstream auth.",
    ),
    RootCauseCategory(
        "third_party_breaking_change",
        _X,
        "Third-party SDK or service made a backward-incompatible change.",
    ),
    RootCauseCategory(
        "bad_deploy", _C, "A specific deploy introduced the failure; rollback would restore."
    ),
    RootCauseCategory(
        "feature_flag_misconfiguration",
        _C,
        "Flag enables a broken path in the wrong env or cohort.",
    ),
    RootCauseCategory(
        "env_var_missing", _C, "Required env var not set; service fails on startup or first use."
    ),
    RootCauseCategory(
        "env_var_misconfiguration",
        _C,
        "Env var value points at wrong endpoint / region / credential.",
    ),
    RootCauseCategory(
        "secret_missing", _C, "Required secret not mounted/available to the workload."
    ),
    RootCauseCategory("secret_rotation_failure", _C, "Rotation produced stale or invalid secrets."),
    RootCauseCategory(
        "code_defect_null_handling",
        _C,
        "Unhandled null/None path breaks under specific input shape.",
    ),
    RootCauseCategory(
        "code_defect_concurrency_bug",
        _C,
        "Race / lost-update / deadlock surfaces under concurrent load.",
    ),
    RootCauseCategory(
        "code_defect_resource_leak", _C, "FD / connection / memory leak grows until exhaustion."
    ),
    RootCauseCategory(
        "code_defect_serialization", _C, "Encoder/decoder bug corrupts payloads or fails parse."
    ),
    RootCauseCategory(
        "data_schema_drift", _P, "Upstream schema changed; downstream consumers reject new shape."
    ),
    RootCauseCategory(
        "data_missing_required_field",
        _P,
        "Records missing a field declared as required by the consumer.",
    ),
    RootCauseCategory(
        "data_corrupted_record", _P, "Malformed bytes/values cause parse errors mid-pipeline."
    ),
    RootCauseCategory(
        "data_late_arrival", _P, "Expected partition / batch never arrived in time window."
    ),
    RootCauseCategory(
        "data_volume_anomaly", _P, "Surprise volume change (zero or huge) trips downstream limits."
    ),
    RootCauseCategory(
        "data_partition_skew", _P, "One partition holds disproportionate volume; hot-spots workers."
    ),
    RootCauseCategory("job_timeout", _P, "Job exceeded scheduler / runtime timeout."),
    RootCauseCategory(
        "job_dependency_failure", _P, "Upstream job failed; downstream cannot proceed."
    ),
    RootCauseCategory(
        "lambda_concurrent_executions_exceeded",
        _P,
        "Lambda hit account/function concurrency limit; invocations throttled.",
    ),
    RootCauseCategory("lambda_init_timeout", _P, "Cold start / init phase exceeded timeout."),
    RootCauseCategory(
        "application_tier_load_spike",
        _W,
        "Application tier traffic surge propagates pressure downstream.",
    ),
    RootCauseCategory(
        "traffic_burst_unprotected",
        _W,
        "Real traffic spike exceeds rate limits / autoscale headroom.",
    ),
    RootCauseCategory("abusive_traffic", _W, "Bot or scraper traffic dominates capacity."),
    RootCauseCategory(
        "ddos_event", _W, "Distributed denial-of-service traffic pattern is the proximate cause."
    ),
    RootCauseCategory(
        "cascading_failure", _W, "Backpressure between services cascades into broader degradation."
    ),
    RootCauseCategory(
        "az_outage", _I, "Single availability zone degraded; symptoms scoped to that AZ."
    ),
    RootCauseCategory("region_outage", _I, "Region-wide cloud provider event."),
    RootCauseCategory(
        "cloud_provider_event", _I, "Confirmed provider service event published on the status page."
    ),
    RootCauseCategory(
        "iam_policy_misconfiguration", _I, "IAM/role/policy denies an action the workload requires."
    ),
    RootCauseCategory(
        "service_quota_exceeded", _I, "AWS/cloud account-level quota or limit reached."
    ),
    RootCauseCategory(
        "configuration_error", _G, "Generic configuration root cause; prefer a narrower category."
    ),
    RootCauseCategory(
        "code_defect", _G, "Generic code defect; prefer a narrower code_defect_* category."
    ),
    RootCauseCategory(
        "data_quality", _G, "Generic data quality root cause; prefer a narrower data_* category."
    ),
    RootCauseCategory(
        "resource_exhaustion",
        _G,
        "Generic resource exhaustion fallback; prefer a narrower exhaustion category.",
    ),
    RootCauseCategory(
        "dependency_failure",
        _G,
        "Generic dependency failure; prefer a narrower upstream_* category.",
    ),
    RootCauseCategory(
        "infrastructure",
        _G,
        "Generic infrastructure fallback; prefer a narrower infrastructure category.",
    ),
    RootCauseCategory(
        "cpu_saturation",
        _G,
        ("Generic CPU saturation fallback; prefer cpu_saturation_bad_query / workload_burst."),
    ),
    RootCauseCategory(
        "replication_lag",
        _G,
        (
            "Generic replication lag fallback; prefer replication_lag_wal_volume / "
            "long_query_on_replica."
        ),
    ),
    RootCauseCategory(
        "healthy", _G, "All metrics within normal bounds; alert is informational or stale."
    ),
    RootCauseCategory("unknown", _G, "Insufficient evidence to commit to a category."),
)

CATEGORY_NAMES: frozenset[str] = frozenset(category.name for category in CATEGORIES)
_BY_NAME: dict[str, RootCauseCategory] = {category.name: category for category in CATEGORIES}

#: Adjacent labels older prompts or models emit, mapped onto a real category.
_ALIASES: dict[str, str] = {
    "code_bug": "code_defect_null_handling",
    "config_error": "configuration_error",
    "configuration": "configuration_error",
    "connection_pool_exhaustion": "connection_exhaustion",
    "cpu_throttling": "pod_cpu_throttled",
    "database": "connection_exhaustion",
    "database_connection_failure": "connection_exhaustion",
    "dns_failure": "dns_resolution_failure",
    "memory_pressure": "pod_oomkilled",
    "mysql_connection_pool_exhaustion": "connection_pool_leak",
    "network_delay": "network_partition",
    "network_latency_issue": "network_partition",
    "oom_killed": "pod_oomkilled",
    "oomkilled": "pod_oomkilled",
    "performance": "application_tier_load_spike",
    "pod_cpu_overload": "pod_cpu_throttled",
    "pod_oom_killed": "pod_oomkilled",
    "redis_connection_pool_exhaustion": "connection_pool_leak",
}
_SEPARATORS = re.compile(r"[\s\-/]+")

#: Keywords that strongly signal a group; two hits are needed to call a mismatch.
_GROUP_SIGNALS: dict[CategoryGroup, tuple[str, ...]] = {
    CategoryGroup.DATABASE: (
        "postgres", "postgresql", "mysql", "mariadb", "redis", "connection pool",
        "max_connections", "replication lag", "slow query", "sql database",
    ),
    CategoryGroup.KUBERNETES: (
        "oomkilled", "oom killed", "crashloop", "crash loop", "kubelet", "liveness probe",
        "readiness probe", "imagepull", "evicted",
    ),
    CategoryGroup.NETWORK: (
        "dns resolution", "dns failure", "tls certificate", "security group", "nat gateway",
        "network partition",
    ),
}  # fmt: skip
_MIN_SIGNAL_HITS = 2


def category(name: str) -> RootCauseCategory | None:
    """The category called ``name``, if it exists."""
    return _BY_NAME.get(name)


def normalize_category(raw: str, allowed: Collection[str] | None = None) -> str:
    """Map a model's category label onto an allowed category, or ``unknown``.

    Fails closed: a label outside the taxonomy is never kept. Case, spaces,
    dashes and known aliases are tolerated.
    """
    names = CATEGORY_NAMES if allowed is None else frozenset(allowed) & CATEGORY_NAMES
    cleaned = raw.strip()
    token = _SEPARATORS.sub("_", cleaned.lower()).strip("_")
    for candidate in (cleaned, token, _ALIASES.get(token, "")):
        if candidate in names:
            return candidate
    return UNKNOWN


def render_taxonomy(names: Collection[str] | None = None) -> str:
    """The taxonomy as a prompt section: groups in order, ``- name: description`` lines."""
    wanted = CATEGORY_NAMES if names is None else frozenset(names)
    lines: list[str] = []
    for group in CategoryGroup:
        members = [entry for entry in CATEGORIES if entry.group is group and entry.name in wanted]
        if members:
            lines.append(f"### {group.value}")
            lines.extend(f"- {entry.name}: {entry.description}" for entry in members)
    return "\n".join(lines)


def category_text_mismatch(root_cause: str, category_name: str) -> str | None:
    """Why the root-cause text seems to belong to another group, or None when it agrees.

    Only groups with strong keyword signals are compared, and the category's
    own words appearing in the text count as agreement.
    """
    entry = _BY_NAME.get(category_name.strip())
    if entry is None or entry.name in (HEALTHY, UNKNOWN) or entry.group not in _GROUP_SIGNALS:
        return None
    text = root_cause.lower()
    scores = {
        group: sum(keyword in text for keyword in keywords)
        for group, keywords in _GROUP_SIGNALS.items()
    }
    best = max(scores, key=lambda group: scores[group])
    if scores[best] < _MIN_SIGNAL_HITS or best is entry.group:
        return None
    if any(token in text for token in entry.name.split("_") if len(token) >= 3):
        return None
    return (
        f"the root cause reads as {best.value} ({scores[best]} keyword hits) "
        f"but {entry.name!r} is {entry.group.value}"
    )
