# Phase 8 completion implementation plan

> For agentic workers: use subagent-driven-development with disjoint ownership;
> root resolves shared interfaces and performs final verification.

**Goal:** Close the concrete remaining migration deliverables with reproducible evidence.
**Architecture:** Durable Postgres coordination, bounded read integrations, native CLI tooling, offline acceptance.
**Tech Stack:** Existing Python3.12/LangGraph/FastAPI/Postgres/React stack; no OpenSRE runtime dependency.
**Spec:** `docs/superpowers/specs/2026-10-08-fase-8-cierre-design.md` and base migration design.

## Global constraints

- Preserve phase6/7 baseline commit acd7ecf, user .idea and sibling opensre.
- No provider mutations/messages/deployments; external validation only with explicit sandbox configuration.
- No secrets in logs/results, no arbitrary agent HTTP paths/shell/SPL; enforce finite response/deadline limits.
- Write regression first for each behavior, verify targeted tests, report commands and results.
- Agents do not commit or delegate; root commits reviewed work and integrates locally after gates.

## Review focus

- Lost notify/listener reconnect must replay committed events, never leak payload or hang shutdown.
- Scheduler failover and concurrent resume must not duplicate operations.
- Overload/cancel/disconnect must release capacity; body limit must apply before parsing.
- Credentials/query syntax/path components/CLI argument injection must stay behind typed boundaries.
- Recorded evidence must preserve actual tool arguments/results, refuse unknown calls, and never invent ground truth.

## Task 1 — distributed runtime

Owner architecture agent: server/app.py, config.py, manager.py, storage.py, new server/events.py, scheduler/service.py and corresponding tests.

- [x] Commit-scoped `pg_notify('sre_graph_events', thread_id)` in Storage.add_event with SQL parameter binding; SQLite unchanged. Listener uses one dedicated psycopg connection, coalescing local subscriptions, reconnect, start/stop lifecycle. SSE subscribe-before-read; durable replay, timeout fallback, no per-client listener connections.
- [x] Scheduler election supervisor retries standby every configurable few seconds, validates lease before dispatch, stops old leadership on loss, safely recreates scheduler where needed. Public stop stops election too. Real Postgres two-instance takeover test.
- [x] Serialize RunManager.resume check/create via distributed lock. Add finite pending limit default128 and typed overload (HTTP429+Retry-After) before acceptance; cleanup on scheduling failure and lock lifecycle. Tests concurrent resumes, burst overflow, capacity recovery, cancellation.
- [x] Keep existing API contracts and phase7 UI compatibility. Root supplies `install_http_guards(app, max_requests=128, max_streams=64, max_body_bytes=1048576)` in server/http_guards.py; call after app construction. Root supplies `await with_configured_mcp(registry)` from integrations/mcp/runtime.py; call for production registry before compiling graphs, not injected fixtures.
- [x] Add config tunables with safe bounds for election/stream/admission; app owns wiring. Report `.superpowers/fase-8/runtime-report.md`.

## Task 2 — named wave2 integrations

Owner integrations agent: integrations/wave2/**, integrations/catalog.py, tests/integrations/test_wave2*.py, docs/integrations-wave2.md. Do not edit registry.py/http.py/.env.example/CLI.

- [x] Implement and register all13 IDs and finite operations from spec/matrix in `.superpowers/fase-8/integrations-audit.md`, using shared internal clients where sensible. Fixed endpoints, encoded path IDs, bounds, next cursor/truncation explicit. NDJSON/form transport capped; Honeycomb polling bounded without retrying creation; Helm allowlisted argv only and Secret sanitation. Safe query construction required for Splunk and logs windows.
- [x] Expose `wave2_integrations()` used by catalog, and a documented metadata mapping for setup field env names/settings classes. Coordinate with tooling agent; settings environment prefixes per uppercase ID.
- [x] Read-only MockTransport/subprocess fixture tests per vendor, malformed/error/oversize/deadline/path/shell injection cases, registry metadata/domain filtering. Use official primary docs, record URLs and limitations without claiming unsupported auth modes.
- [x] Report `.superpowers/fase-8/wave2-report.md`.

## Task 3 — setup, recording/replay, eval regression

Owner tooling agent: cli/main.py, new cli/integrations.py, cli/eval_tools.py, integrations/setup.py, evals/recording.py, evals/comparison.py, tests/cli/test_setup.py, tests/evals/test_recording.py, tests/evals/test_comparison.py; other new targeted test files if needed. Do not modify server or integration catalog.

- [x] Add integrations list/verify/setup including existing+wave2 fields. Validation before atomic dotenv update, keep unrelated comments/values, hidden secret prompt, explicit env-file, safe file input/env references noninteractive, dry-run prints no secrets. Existing env values must not mask proposed invalid values. Invalid verification leaves prior file unchanged. MCP config option should be supported through root runtime helper; do not inventremote declarations.
- [x] Record existing API investigation with alert/evidence/args/results/window/provenance into versioned newdirectory, no overwrite/traversal, redact before writing, require independently supplied Answer. Generic strict replay keyed canonicaltool+args, no fallback to live providers; executable replay harness through tools/graph and results compatible comparator. CLI `eval record`, `eval replay`.
- [x] `eval compare` baseline/current validates matching scenarios/model/repeats/eligibility; refuses scripted-quality comparison, missing/failed runs and measurable regression exit nonzero. Tests validpair/regression/incomparable/nansecrets.
- [x] CLI local chat builds registry using root `await with_configured_mcp(default_registry())`; root sync make_runtime handles investigate. Report `.superpowers/fase-8/tooling-report.md`.

## Task 4 — root safety, MCP, load, release

- [x] Root MCP runtime wrapper: integrates loaded tools with registry, correct tool metadata/content_and_artifact and EvidenceCollector, sanitized bounded outputs/deadlines, failures isolated, explicit configured path `MCP_CONFIG`; actual discovered external/mutating tools excluded from RCA/chat by existing read filter. Add registry extension API, sync local runtime/async Studio adaptation as needed; native CLI/server injected registry interface above.
- [x] Pure ASGI body/admission/response security guards (no unsafe buffering of unbounded streams); bounded API and SSE admission,413/429, cancellation cleanup, no-store API responses and UI security headers; test adversarial bodies/concurrency/abort/static compatibility. Hook by Task1.
- [x] Standalone offline burst/load command/script and measured report with real app/graphs, enforce acceptance thresholds (no duplicate writes, deterministic overload, no leaked capacity); Postgres multi-replica tests Task1.
- [x] Reproducible real-provider preflight and sandbox demo/acceptance guide, honest configured/missing report without printing credentials. Provider tests only when configured/authorized. No sending to others implicitly.
- [x] Complete root .env.example/README/CI/docs with actual new capabilities, full36 scripted eval, full backend/frontend gates, installed wheel/Docker smoke, independent review and fixes. Commit and fast-forward local main only after checks; no remote configured so no push.

## Status

- [x] Task1 distributed runtime
- [x] Task2 named integrations
- [x] Task3 setup/record/replay/comparison
- [x] Task4 safety/MCP/load/release

Final evidence and external-validation limits: `docs/progress/2026-10-08-fase-8.md`. The user confirmed no credentials are available; provider acceptance was documented, not fabricated.
