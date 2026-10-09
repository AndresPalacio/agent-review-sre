# Native second-wave integrations

The native registry includes 13 additional providers and 29 finite read tools. All stay disabled until configured. These clients do not depend on OpenSRE or remote MCP servers. There are currently no provider credentials in this deployment: tests use offline HTTP/subprocess fixtures; real authentication, account permissions and deployment-specific schemas still require verification when credentials become available.

The shared implementation is `src/sre_graph/integrations/wave2/`. `wave2_integrations()` supplies registry adapters and `WAVE2_SETTINGS` maps IDs to Pydantic settings classes. The CLI setup catalog consumes these classes directly: environment names are each class's uppercase `env_prefix` plus the uppercase field name. `SecretStr` identifies credentials. Settings enforce cross-field rules and auth choices, including when loaded outside the wizard.

Every HTTP integration requires `<PREFIX>ENABLED=true` and `<PREFIX>URL`. Credentials are loaded from the environment/dotenv, never accepted as agent tool arguments. URL configuration permits HTTP(S) without userinfo, query or fragment; use HTTPS for hosted providers. Specify the actual deployment/region base URL without an API path (GitLab's base URL precedes `/api/v4`, Argo CD's precedes `/api/v1`). No guessed provider MCP endpoints are shipped.

| ID / prefix | Credentials and additional settings | Tools / provider surface |
| --- | --- | --- |
| `opsgenie` / `OPSGENIE_` | `TOKEN`: API key; region-specific URL | `alerts`, `alert`, `alert_logs`; v2 alert API, GenieKey authentication |
| `incident_io` / `INCIDENT_IO_` | `TOKEN`: API key | `incidents`, `incident`, `updates`; v2 incidents/incident_updates, bearer authentication |
| `jira` / `JIRA_` | `EMAIL` and `TOKEN`: Jira Cloud API token | `issues`, `issue`; Cloud REST v3 enhanced `/search/jql`, basic authentication |
| `honeycomb` / `HONEYCOMB_` | `TOKEN`: configuration/query key; `DATASET` | `traces`: service or trace ID groups, query/result creation followed by finite polling |
| `elasticsearch` / `ELASTICSEARCH_` | `INDEX`; choose `TOKEN` (API key) or `USERNAME` + `PASSWORD` | `logs`, `health`; fixed `_search` / `_cluster/health` |
| `opensearch` / `OPENSEARCH_` | `INDEX`; choose `USERNAME` + `PASSWORD` or an installation-provided compatible `TOKEN` | `logs`, `health`; search-compatible endpoints; AWS SigV4 is not implemented |
| `splunk` / `SPLUNK_` | `TOKEN`: Splunk authentication token; `INDEX`; `API_VERSION=v2` (or explicitly `v1`) | `logs`: search export; fixed literal search and `head` |
| `victoria_logs` / `VICTORIA_LOGS_` | optional bearer `TOKEN`; `ACCOUNT_ID=0`, `PROJECT_ID=0` | `logs`: fixed literal LogsQL search via form POST `/select/logsql/query` |
| `coralogix` / `CORALOGIX_` | `TOKEN`: DataPrime key; region-specific URL | `logs`: fixed `source logs`, literal message filter and limit |
| `azure` / `AZURE_` | `TOKEN`: access token; `WORKSPACE_ID`: GUID; `TABLE=AppTraces` | `logs`: fixed workspace/table KQL with literal filter; no arbitrary KQL |
| `argocd` / `ARGOCD_` | `TOKEN`: existing bearer token; optional `PROJECT`, `NAMESPACE` | `applications`, `application`, `diff` (managed live/target state); no sync/refresh |
| `helm` / `HELM_` | local `EXECUTABLE=helm`, `NAMESPACE=default`; optional `CONTEXT`, `KUBECONFIG` | `releases`, `status`, `history`, `values`, `manifest`; allowlisted argv subprocess |
| `gitlab` / `GITLAB_` | `TOKEN`: personal/project token with read permissions; `PROJECT`: numeric ID or group/project | `commits`, `merge_requests`, `pipelines`, `file`; REST v4 |

Actual tool names include the integration prefix, such as `splunk_logs`, `incident_io_updates` and `helm_status`.

## Bounds and output

`REQUEST_TIMEOUT_SECONDS` defaults to 15 and is limited to 60. This is a total deadline for each tool operation, including Honeycomb polling, not merely a socket inactivity timeout. Responses are capped at 2 MiB; redirects are not followed. Logs/trace windows must increase, have positive Unix-second timestamps, and span at most seven days. Page/row limits default to 50 with a hard cap of 100. Helm history defaults to 20. All output contains `source`, `items`, `truncated`, and `next_cursor`; exact-cap results conservatively report possible truncation even without a cursor.

Opsgenie alerts use numeric offsets; incident.io uses returned `after`; Jira uses `nextPageToken`; GitLab uses bounded page numbers (up to 1000). No automatic unbounded pagination runs. Other tools reject unsupported cursors. Argo CD does not offer server-side application pagination; the complete HTTP body must fit the byte cap and returned items are capped. GitLab files must be UTF-8/base64 and at most 100,000 decoded characters. The specialist sees compact capped text; the full bounded, sanitized artifact remains attached to its tool result.

For log tools, `query` means **literal message text**, including in Splunk, Azure, VictoriaLogs and Coralogix. Pipes, commands and query syntax are quoted as text. Elasticsearch/OpenSearch build `match_phrase` plus a mandatory timestamp range; agents cannot submit scripts or arbitrary Query DSL. Azure always targets the configured workspace/table and constrains both KQL timestamps and API timespan. Raw SPL is deliberately unavailable because valid SPL can write data. Jira JQL is accepted only at the issue search endpoint, which has no issue mutation surface.

Honeycomb needs `service` or `trace_id` and returns aggregated trace groups, not complete trace span retrieval. Its query/result POST operations create analytical server resources, are tagged read-only for investigation routing, and are **not tagged idempotent**. Creation is never retried. `POLL_ATTEMPTS=5` (1–10) and `POLL_INTERVAL_SECONDS=0.5` (0–5) bound polling within the total operation deadline. Verification uses `/1/auth` without creating queries. Azure uses an existing access token; automatic refresh, managed identity and client credentials are not implemented.

Helm passes arguments through `create_subprocess_exec` without a shell. It runs only list/status/history/get-values/get-manifest in the configured namespace/context; subprocess stdin is disabled, stdout/stderr are capped, and timeout/cancellation terminates the process. Manifest YAML aliases are rejected. Kubernetes Secret data/stringData and last-applied annotations, known credential keys, named secret env values, inline Argo values, recognizable token formats, and the client's credential strings are scrubbed before artifacts leave the client. This cannot infer that an arbitrary innocuously named ConfigMap/value contains a secret; use read-scoped accounts and keep secrets in Secret resources.

Verification performs a bounded read: alert list, incident identity, Jira self, Honeycomb auth, search cluster health, one-minute log query, Argo application list, Helm release list, or GitLab commits. A successful identity check does not assert permission for every project/dataset/tool. Errors identify status/type without provider response bodies or credential values. Partial search shards, malformed stream envelopes, incomplete Honeycomb jobs and Azure error responses fail rather than producing empty-success evidence.

## API references and compatibility

- [Opsgenie Alert API](https://docs.opsgenie.com/do/docs/alert-api).
- [incident.io API introduction](https://docs.incident.io/api-reference/introduction) and [official endpoint index](https://docs.incident.io/llms.txt): incidents-v2 and incident-updates-v2.
- [Jira Cloud issue search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/) and [issue detail](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/). [Atlassian's migration note](https://confluence.atlassian.com/jirakb/run-jql-search-query-using-jira-cloud-rest-api-1289424308.html) reports legacy `/rest/api/3/search` removed; this connector uses `/rest/api/3/search/jql`.
- [Honeycomb query specification creation](https://docs.honeycomb.io/api/queries/create-a-query) and [query authorization/result lifecycle](https://api-docs.honeycomb.io/api/query-data/authorization).
- [Elasticsearch search](https://www.elastic.co/guide/en/elasticsearch/reference/current/search-search.html) and [OpenSearch search](https://docs.opensearch.org/latest/api-reference/search-apis/search/).
- [Splunk search export endpoint versions](https://help.splunk.com/en/splunk-enterprise/rest-api-reference/9.4/search-endpoints/search-endpoint-descriptions). Select the version supported by the installation explicitly.
- [VictoriaLogs HTTP querying](https://docs.victoriametrics.com/victorialogs/querying/).
- [Coralogix DataPrime HTTP queries](https://coralogix.com/docs/dataprime/API/direct-archive-query-http/).
- [Azure Monitor Logs request format](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/request-format).
- [Argo CD API/authentication](https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/); managed-resource response compatibility must match the deployment's Swagger schema.
- [Helm read commands](https://helm.sh/docs/helm/).
- GitLab [commits](https://docs.gitlab.com/api/commits/), [merge requests](https://docs.gitlab.com/api/merge_requests/), [pipelines](https://docs.gitlab.com/api/pipelines/) and [repository files](https://docs.gitlab.com/api/repository_files/).

These are the finite named read surfaces in the phase 8 spec. Provider mutations, incident notes/comments, Helm/Argo rollout operations, additional auth modes, and unnamed OpenSRE vendors are outside this read connector surface. MCP is an independent configured extension path, not a replacement for claiming concrete native support.

Run deterministic coverage with `uv run pytest tests/integrations/test_wave2.py tests/integrations/test_wave2_operations.py tests/integrations/test_wave2_helm.py -q`. Provider connectivity remains pending until real credentials are configured and verified.
