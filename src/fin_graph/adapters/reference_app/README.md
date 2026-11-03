# Reference application adapter

This package is the outer boundary that lets existing clients use Fin Graph as a
drop-in replacement for the reference application. It owns translations for the
reference Python API, HTTP routes, SSE events, schemas, and the result shape
expected by those clients.

The financial agents do not live here. The nineteen DeepAgents personas are in
`fin_graph.agents.analysis`; their deterministic orchestration is in
`fin_graph.workflows.analysis`; production assembly is in `fin_graph.runtime`.
This adapter depends on those core packages and injects the reference result
mapping when it assembles the persona supervisor. Core packages never import
this adapter.

Historical external identifiers are intentionally preserved only where clients
or stored data require them: route and payload spellings, environment aliases,
database table names, migration names, and response literals. Internal Python
packages and symbols use responsibility-based names such as `reference_app`,
`ReferenceAppProfile`, and `ReferenceStore`.

Related boundaries:

- `fin_graph.persistence.reference_app` stores reference-facing records.
- `fin_graph.conformance.reference_app` snapshots and audits the upstream app.
- `fin_graph.conformance.contracts` compares frozen public contracts.
