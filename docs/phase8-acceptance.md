# Phase 8 acceptance without credentials

For later real-provider validation, see the [sandbox guide](phase8-sandbox.md).

Run from the repository using the locked Python environment:

```powershell
uv run python scripts/phase8_acceptance.py load
uv run python scripts/phase8_acceptance.py load --burst 64 --pending 16 --concurrent 4
uv run python scripts/phase8_acceptance.py preflight
```

Every command emits a JSON object and exits 0 only when its criteria pass. An exception emits its type, never exception content that could expose credentials. Invalid load arguments fail; supported bounds are `1 <= concurrent <= pending < burst <= 512`.

`load` builds the actual FastAPI app and investigation LangGraph with deterministic model-unavailable fallbacks, an empty injected integration registry, an in-memory graph checkpointer and temporary SQLite run ledger. HTTPX ASGITransport performs requests inside the process; no listening server, container, provider, outbound message or external service is needed. The graph is held after its first state to create reproducible concurrent pressure, then released to produce real deterministic reports. This gate is test instrumentation, not a production latency simulation.

The JSON measures accepted/rejected requests, execution concurrency, burst latency, total elapsed time and durable SSE event count. Passing requires exact finite admission and overload with Retry-After, no rejected run rows, bounded graph execution, one execution per accepted burst thread, completed reports, SSE cursor/replay correctness, subscriber cleanup, interrupted-run recovery, restored capacity, oversized-body rejection and clean shutdown. Cancellation/resume intentionally executes the interrupted thread twice, while the no-duplicate criterion covers the original burst. No mutating integration is configured, so this command does not establish exactly-once provider writes.

SSE verification consumes completed durable events and reconnects with Last-Event-ID. ASGITransport buffers complete responses; these checks do not claim live network streaming throughput, slow-reader behavior or cross-replica LISTEN/NOTIFY correctness. The separate real Postgres suite covers distributed coordination:

```powershell
$env:FIN_GRAPH_TEST_DATABASE_URL='<local Postgres URL>'
$env:FIN_GRAPH_REQUIRE_POSTGRES='1'
uv run pytest tests/server/test_distributed_runtime.py tests/server/test_postgres.py
```

`preflight` only reports presence of selected environment variables and docker/kubectl/kind/helm on PATH. It never emits values, reads `.env` or credential files, connects to a service, validates a token or checks a cluster/daemon. Its required readiness list includes a supported OpenAI/Anthropic credential, DATABASE_URL, BOOTSTRAP_ADMIN_KEY and these four tools. Without credentials it explicitly reports missing items and exits nonzero; offline load remains runnable. Alternative LLM providers and non-kind deployment workflows require their own checks.

Observed local run on 2026-10-08, Python 3.12.10 / Windows: default burst 32, pending limit 8, execution limit 2; 8 accepted, 24 overloaded, peak execution 2; burst 0.2093 seconds, total 1.7664 seconds, 10 durable SSE events; all 17 criteria passed. Timing varies with machine/load. This is reproducible local correctness evidence, not provider quality, production capacity, sandbox remediation or deployment certification.
