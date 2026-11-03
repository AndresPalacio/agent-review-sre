# AI Hedge Fund Replacement Release Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the obsolete SRE product, close every parity contract, and ship a clean, documented, reproducible AI Hedge Fund replacement.

**Architecture:** Use the differential manifest as the release checklist, delete legacy code only after replacement tests own its remaining contracts, and verify source, wheel, web, Docker, SQLite, and Postgres profiles from clean environments.

**Tech Stack:** uv, pytest, Ruff, mypy, import-linter, npm/Vitest/Playwright, Hatch wheel, Docker/Compose, Postgres, SQLite.

**Spec:** `docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md`

**Prerequisites:** Plans 01 through 05 complete.

## Global Constraints

- Delete no SRE module until `rg`, import-linter, and replacement tests prove it has no supported consumer.
- Preserve financial gateways, scheduler, delivery, harness, workflow package, approvals, paper ledger, and `/v1` APIs.
- The source reference and its database remain untouched and are not required by any final test or runtime.
- A release claim requires fresh full-suite, web, wheel, Docker, and requirement-manifest evidence.
- No deployment, publication, push, or real external request is authorized by this plan.

## Review Focus

- Removing SRE integrations must not remove generic HTTP, delivery, security, or storage utilities used by finance; Task 1 tests import ownership.
- Old environment names must either map deliberately or fail clearly, never be silently ignored; Task 2 tests the documented matrix.
- A clean machine with no extras must still import the base wheel, while the app image contains all advertised capabilities; Task 3 tests both.
- SQLite and Postgres must report different distributed capabilities accurately without changing public finance results; Task 4 tests parity.
- Every manifest item marked complete must point to an executable test, not a prose assertion; Task 5 enforces it.

---

### Task 1: Remove the SRE domain and migrate remaining generic ownership

**Files:**
- Delete after dependency proof: `src/fin_graph/domain/alerts/`
- Delete after dependency proof: `src/fin_graph/graphs/investigation/`
- Delete after dependency proof: `src/fin_graph/domain/diagnosis.py`
- Delete after dependency proof: `src/fin_graph/domain/investigation.py`
- Delete after dependency proof: `src/fin_graph/domain/remediation.py`
- Delete after dependency proof: `src/fin_graph/domain/remediation_templates.py`
- Delete after dependency proof: `src/fin_graph/evals/data/eks/`
- Delete after dependency proof: `src/fin_graph/evals/data/rds_postgres/`
- Delete after dependency proof: `src/fin_graph/integrations/alertmanager/`, `aws/`, `datadog/`, `github/`, `grafana/`, `kubernetes/`, `loki/`, `newrelic/`, `prometheus/`, `sentry/`, `tempo/`, and `wave2/`.
- Delete after dependency proof: `src/fin_graph/evals/fixtures.py`, `recording.py`, `runner.py`, `scenarios.py`, `scoring.py`, `trace.py`, and `trajectory.py`; move reusable comparison primitives from `evals/comparison.py` before deleting it only if its ownership report is SRE-only.
- Modify: `src/fin_graph/cli/main.py`
- Modify: `src/fin_graph/integrations/catalog.py`
- Modify: `pyproject.toml`
- Create: `tests/test_no_sre_product.py`
- Create: `docs/compatibility/sre-removal-inventory.md`

**Interfaces:**
- Produces an ownership inventory classifying each candidate as delete, retain generic, or move; `fin-graph` no longer exposes `investigate` or SRE eval suites.

- [ ] **Step 1: Write failing no-SRE and retained-finance tests**

Assert forbidden packages/commands/datasets/environment prefixes are absent; generic utilities remain importable; financial API/chat/gateways/scheduler/harness/workflow demos still import; package dependency list drops SRE-only SDKs.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_no_sre_product.py -q`

Expected: FAIL listing the existing SRE surfaces.

- [ ] **Step 3: Generate ownership report, move shared helpers, then delete proven SRE code/tests**

Use `rg` and import-linter output before each deletion. Update or delete historical SRE tests in the same change; do not weaken financial tests.

- [ ] **Step 4: Run import, CLI, graph, gateway, scheduler, and harness suites**

Run: `uv run lint-imports`

Run: `uv run pytest tests/test_no_sre_product.py tests/cli tests/graphs tests/gateway tests/scheduler tests/harness packages/workflow/tests -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add -A src tests pyproject.toml uv.lock docs/compatibility/sre-removal-inventory.md && git commit -m "refactor: remove obsolete sre product"`

### Task 2: Finalize configuration, documentation, and notices

**Files:**
- Rewrite: `README.md`
- Modify: `.env.example`
- Modify: `langgraph.json`
- Modify: `NOTICE`
- Modify: `LICENSE` only if attribution text requires a separate notice reference, not license replacement.
- Create: `docs/compatibility/ai-hedge-fund.md`
- Create: `docs/compatibility/configuration-matrix.md`
- Create: `docs/compatibility/api-manifest.md`
- Create: `docs/operations/local-app.md`
- Create: `docs/operations/remote-deployment.md`
- Modify: `tests/test_env_example.py`
- Modify: `tests/test_langgraph_json.py`
- Create: `tests/compat/test_documented_commands.py`

**Interfaces:**
- Documents one-command local use, secured remote use, legacy/native endpoints, CLI/imports, extras, models, providers, data sources, paper-only safety, limitations, and clean-state behavior.

- [ ] **Step 1: Write failing documentation/config executable tests**

Parse every documented command and environment variable; assert no SRE names, no real-trading claim, complete legacy-to-modern mapping, valid LangGraph exports, required notices, and command `--help` success.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/compat/test_documented_commands.py tests/test_env_example.py tests/test_langgraph_json.py tests/test_license.py -q`

Expected: FAIL on stale README/configuration.

- [ ] **Step 3: Rewrite documentation and configuration from the final product**

Do not carry phase-progress claims into the quickstart. Clearly separate deterministic parity from live-LLM textual variability and optional live-service smokes.

- [ ] **Step 4: Run documentation/config tests**

Run: `uv run pytest tests/compat/test_documented_commands.py tests/test_env_example.py tests/test_langgraph_json.py tests/test_license.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add README.md .env.example langgraph.json NOTICE docs tests/compat/test_documented_commands.py tests/test_env_example.py tests/test_langgraph_json.py && git commit -m "docs: describe ai hedge fund replacement"`

### Task 3: Verify reproducible package and container profiles

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.dockerignore`
- Modify: `.github/workflows/ci.yml` or the repository's actual CI workflow files.
- Create: `tests/packaging/test_clean_install.py`
- Create: `tests/packaging/test_container_contract.py`

**Interfaces:**
- Produces a base wheel, complete `app` installation profile, compiled static UI in wheel/image, and container health/readiness with no dependency on the reference tree.

- [ ] **Step 1: Write failing clean-install and container-contract tests**

Assert base install/import, app install/import of all advertised models, legacy shims, static assets, CLI help, no source-path leakage, no reference files/private state, non-root image user, loopback/local and Postgres Compose profiles, and health checks.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/packaging -q`

Expected: FAIL until packaging and images are final.

- [ ] **Step 3: Finalize build metadata, multistage UI build, image, Compose, and CI gates**

Pin only where parity/reproducibility requires it. Keep the base wheel small and make the application image install `app` extras.

- [ ] **Step 4: Build and test artifacts**

Run: `uv lock --check`

Run: `uv build --wheel`

Run: `uv run pytest tests/packaging -q`

Run: `docker build -t fin-graph:compat .`

Expected: all exit 0.

- [ ] **Step 5: Commit**

Run: `git add pyproject.toml uv.lock Dockerfile docker-compose.yml .dockerignore .github tests/packaging && git commit -m "build: package complete replacement profiles"`

### Task 4: Run backend and deployment parity across SQLite and Postgres

**Files:**
- Create: `tests/acceptance/test_runtime_parity.py`
- Create: `tests/acceptance/test_restart_and_concurrency.py`
- Create: `scripts/acceptance_ai_hedge_fund.py`
- Create: `docs/compatibility/runtime-capabilities.md`

**Interfaces:**
- The acceptance script accepts `--backend sqlite|postgres`, `--output`, and optional external-service flags; it emits machine-readable results and a Markdown report without secrets.

- [ ] **Step 1: Write failing cross-backend acceptance tests**

Run identical fixture analyses, flows, v1/v2 backtests, jobs, cancellations, key metadata, paper orders, restart and concurrent CAS on both backends; assert equal public financial results and accurate capability/readiness differences.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run --extra app pytest tests/acceptance/test_runtime_parity.py -q`

Expected: FAIL until the acceptance runner/capability report exists.

- [ ] **Step 3: Implement the acceptance runner and capability document generation**

Keep external smokes opt-in and redact credentials before report serialization.

- [ ] **Step 4: Run SQLite and configured Postgres acceptance**

Run: `uv run --extra app python scripts/acceptance_ai_hedge_fund.py --backend sqlite --output .acceptance/sqlite`

Run when Postgres is configured: `uv run --extra app python scripts/acceptance_ai_hedge_fund.py --backend postgres --output .acceptance/postgres`

Expected: zero failed mandatory checks and matching financial digests.

- [ ] **Step 5: Commit**

Run: `git add tests/acceptance scripts/acceptance_ai_hedge_fund.py docs/compatibility/runtime-capabilities.md && git commit -m "test: verify runtime parity across storage backends"`

### Task 5: Close the contract manifest and full release gate

**Files:**
- Modify: `tests/compat/fixtures/manifest.json`
- Create: `docs/compatibility/parity-report.md`
- Create: `scripts/check_compatibility_manifest.py`
- Create: `tests/compat/test_manifest_closure.py`
- Modify: `docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md` only to append final verified evidence, without changing approved requirements.

**Interfaces:**
- Every manifest contract records `status`, owning test node id, reference digest, candidate digest/rule, and evidence command. The checker fails for missing tests, pending/unknown status, duplicate ownership, stale snapshot, or unapproved exclusion.

- [ ] **Step 1: Write the failing manifest-closure checker test**

Add tests that a pending item, missing pytest node, stale reference digest, unapproved exclusion, or prose-only evidence fails; a fully owned manifest passes.

- [ ] **Step 2: Run checker tests and confirm RED**

Run: `uv run pytest tests/compat/test_manifest_closure.py -q`

Expected: FAIL because the closure checker/report is absent or items remain pending.

- [ ] **Step 3: Implement the checker and link every contract to evidence**

Do not mark a contract complete merely because a neighboring route/test passes. Record any safe intentional deviation with its regression test and design rationale.

- [ ] **Step 4: Run the fresh full release gate**

Run: `uv run ruff check .`

Run: `uv run ruff format --check .`

Run: `uv run mypy`

Run: `uv run lint-imports`

Run: `uv lock --check`

Run: `uv run --extra app pytest`

Run: `uv run --project packages/workflow pytest packages/workflow/tests`

Run: `cd web; npm run typecheck; npm test; npm run build; npm run test:e2e`

Run: `uv build --wheel`

Run: `uv run python scripts/check_compatibility_manifest.py`

Run: `git diff --check`

Expected: every command exits 0; no manifest item is pending; reports contain no secret.

- [ ] **Step 5: Commit final evidence**

Run: `git add tests/compat/fixtures/manifest.json docs/compatibility/parity-report.md scripts/check_compatibility_manifest.py docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md && git commit -m "test: close ai hedge fund parity manifest"`

## Final Acceptance

The replacement is complete only when Task 5's commands have been run fresh after the final change and the parity report shows no missing reference behavior. Live-provider smokes are reported separately and cannot turn an offline contract failure into success.
