# AI Hedge Fund Parity Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the local AI Hedge Fund behavior and add the configuration, comparison, licensing, and storage foundations every compatibility slice will use.

**Architecture:** Treat the local reference tree as a read-only oracle and record deterministic contracts into licensed fixtures. Product code exposes typed compatibility contracts and storage ports; tests never import or require the reference tree after recording.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, SQLAlchemy asyncio, SQLite, SHA-256, uv/hatchling.

**Spec:** `docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md`

## Global Constraints

- Baseline `fin-graph` commit: `f4c701a58e484d259816904ae85dc717f2967966`; reference root: `C:\Users\Usuario\Desktop\ai-hedge-fund`.
- Never read or copy `.env`, `hedge_fund.db`, caches, virtual environments, bytecode, installed dependencies, or secrets from the reference.
- Python remains `>=3.12,<3.13`; Deep Agents remains `>=0.7.29,<0.8`; LangGraph remains `>=1.2.14,<1.3`.
- The reference is never a runtime dependency; generated fixtures record source path, content hash, and applicable license.
- No production code before its failing test; every task ends with its focused suite and a commit.

## Review Focus

- A symlink/junction escaping the reference root must be rejected by the snapshotter; Task 1 tests it.
- Conflicting modern and legacy environment variables must fail with both variable names but no values; Task 3 tests it.
- Golden comparison must not normalize a real business-field difference; Task 2 tests strict allowlisted normalization.
- SQLite must preserve immutable run revisions across restart and concurrent updates; Task 4 tests both.
- A resource without a recognized redistributable license must remain excluded from packaging; Task 5 tests it.

---

### Task 1: Freeze the reference snapshot safely

**Files:**
- Create: `src/fin_graph/compat/__init__.py`
- Create: `src/fin_graph/compat/reference.py`
- Create: `scripts/snapshot_ai_hedge_fund.py`
- Create: `tests/compat/test_reference_snapshot.py`
- Create: `tests/compat/reference-snapshot.json`

**Interfaces:**
- Consumes: a read-only `Path` supplied explicitly to the recording script.
- Produces: `ReferenceFile(path: str, sha256: str, size: int, license_id: str | None)`, `ReferenceSnapshot(schema_version: int, files: tuple[ReferenceFile, ...], root_digest: str)`, and `build_reference_snapshot(root: Path) -> ReferenceSnapshot`.

- [ ] **Step 1: Write the failing snapshot tests**

Add `test_snapshot_is_stable_and_excludes_private_state`, `test_snapshot_rejects_symlink_escape`, and `test_snapshot_digest_changes_with_source` asserting sorted relative paths, exclusion of `.env`/DB/cache patterns, root containment, and a stable aggregate digest.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `uv run pytest tests/compat/test_reference_snapshot.py -q`

Expected: FAIL because `fin_graph.compat.reference` does not exist.

- [ ] **Step 3: Implement the snapshot models and recorder**

Use resolved paths and `Path.relative_to(root.resolve())`; hash file bytes without printing contents. Make the script require `--root` and `--output` and refuse an output inside the reference tree.

- [ ] **Step 4: Generate and verify the frozen manifest**

Run: `uv run python scripts/snapshot_ai_hedge_fund.py --root C:\Users\Usuario\Desktop\ai-hedge-fund --output tests/compat/reference-snapshot.json`

Run: `uv run pytest tests/compat/test_reference_snapshot.py -q`

Expected: PASS; the manifest contains no excluded path and no secret value.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/compat scripts/snapshot_ai_hedge_fund.py tests/compat && git commit -m "test: freeze ai hedge fund reference snapshot"`

### Task 2: Build the differential contract oracle

**Files:**
- Create: `src/fin_graph/compat/contracts.py`
- Create: `scripts/record_ai_hedge_fund_contracts.py`
- Create: `tests/compat/oracle.py`
- Create: `tests/compat/test_contract_oracle.py`
- Create: `tests/compat/fixtures/manifest.json`

**Interfaces:**
- Consumes: `ComparisonRule(path: str, mode: Literal["exact", "float", "volatile"], abs_tol: float = 0, rel_tol: float = 0)` and JSON-compatible reference/candidate values.
- Produces: recursive `JsonValue`/`JsonObject` aliases, `normalize_contract(value: JsonValue, volatile_paths: frozenset[str]) -> JsonValue`, `compare_contract(reference: JsonValue, candidate: JsonValue, rules: Sequence[ComparisonRule]) -> list[ContractMismatch]`, and a recording CLI that writes fixtures only under `tests/compat/fixtures`.

- [ ] **Step 1: Write failing comparator and fixture-provenance tests**

Assert exact money/decision differences fail, only allowlisted ids/timestamps normalize, `1e-8` float drift passes the default deterministic rule, a missing field fails, and every fixture entry carries snapshot digest, source file, recorder version, and license.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `uv run pytest tests/compat/test_contract_oracle.py -q`

Expected: FAIL because comparator interfaces are missing.

- [ ] **Step 3: Implement strict JSON-path comparison and recording boundaries**

Do not add recursive wildcard normalization. Unknown modes and duplicate rules raise `ValueError`. The recorder must accept explicit fixture names and never discover `.env` or database content.

- [ ] **Step 4: Run focused and compatibility tests**

Run: `uv run pytest tests/compat -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/compat/contracts.py scripts/record_ai_hedge_fund_contracts.py tests/compat && git commit -m "test: add differential compatibility oracle"`

### Task 3: Resolve legacy configuration and provider names

**Files:**
- Create: `src/fin_graph/compat/settings.py`
- Modify: `src/fin_graph/config/settings.py`
- Modify: `src/fin_graph/llm/models.py`
- Create: `tests/compat/test_compat_settings.py`
- Modify: `tests/llm/test_models.py`

**Interfaces:**
- Consumes: modern `FIN_*`/`FIN_GRAPH_*` settings and the legacy provider variables documented in the reference `.env.example`.
- Produces: `resolve_alias(env: Mapping[str, str], modern: str, legacy: Sequence[str]) -> str | None`, `CompatibilityProfile(local: bool, legacy_api: bool, allow_insecure_remote: bool, trusted_proxies: tuple[str, ...])`, and provider/model aliases accepted by `parse_model_spec`.

- [ ] **Step 1: Write failing alias, conflict, and trust-boundary tests**

Cover legacy-only resolution, equal dual values, conflicting dual values without value leakage, unknown provider, untrusted forwarded headers, loopback default, and refusal of unauthenticated legacy API on `0.0.0.0`.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `uv run pytest tests/compat/test_compat_settings.py tests/llm/test_models.py -q`

Expected: FAIL on missing compatibility settings/aliases.

- [ ] **Step 3: Implement aliases with modern-name precedence and explicit conflicts**

Keep core `Settings` free of HTTP policy; `CompatibilityProfile` owns bind/proxy decisions. Add only providers/models present in the frozen reference catalog.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/compat/test_compat_settings.py tests/config tests/llm -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/compat/settings.py src/fin_graph/config/settings.py src/fin_graph/llm/models.py tests/compat/test_compat_settings.py tests/llm/test_models.py && git commit -m "feat: accept ai hedge fund configuration aliases"`

### Task 4: Add local compatibility storage ports

**Files:**
- Create: `src/fin_graph/compat/storage/__init__.py`
- Create: `src/fin_graph/compat/storage/models.py`
- Create: `src/fin_graph/compat/storage/protocol.py`
- Create: `src/fin_graph/compat/storage/crypto.py`
- Create: `src/fin_graph/compat/storage/sqlite.py`
- Create: `tests/compat/storage/test_sqlite.py`

**Interfaces:**
- Produces: immutable `FlowRecord`, `FlowRevision`, `FlowRunRecord`, `RunCycleRecord`, `ProviderKeyRecord`, `BacktestJobRecord`, and `ArtifactRecord`; `SecretCipher` protocol plus `FernetSecretCipher`; `CompatibilityStore` async protocol with CRUD/list methods and compare-and-swap transitions; `open_compat_sqlite(path: Path, cipher: SecretCipher) -> AsyncContextManager[CompatibilityStore]`.
- All records carry `created_at`, `updated_at`, stable public id, and revision/version. Provider secrets are encrypted by the injected cipher and never returned by list methods.

- [ ] **Step 1: Write failing persistence, restart, CAS, and encryption tests**

Assert clean initialization, flow revision immutability, duplicate-name errors, run/cycle ordering, one winner for concurrent job transition, restart persistence, and absence of plaintext provider keys in the SQLite file.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `uv run pytest tests/compat/storage/test_sqlite.py -q`

Expected: FAIL because the storage package is absent.

- [ ] **Step 3: Implement the protocol and SQLite backend**

Use SQLAlchemy asyncio with explicit transactions and unique/version constraints. Keep schema creation local to the backend; do not couple it to the current Postgres `Storage` class.

- [ ] **Step 4: Run storage and existing persistence tests**

Run: `uv run pytest tests/compat/storage tests/persistence -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/compat/storage tests/compat/storage && git commit -m "feat: add sqlite compatibility store"`

### Task 5: Gate dependencies and redistributable assets

**Files:**
- Create: `src/fin_graph/compat/licensing.py`
- Create: `scripts/audit_ai_hedge_fund_assets.py`
- Create: `tests/compat/test_licensing.py`
- Create: `docs/compatibility/third-party-assets.json`
- Modify: `pyproject.toml`
- Modify: `NOTICE`

**Interfaces:**
- Produces: `AssetLicense(path: str, origin: str, license_id: str, redistributable: bool, notice: str)` and `audit_assets(root: Path, snapshot: ReferenceSnapshot) -> tuple[AssetLicense, ...]`.
- Adds optional dependency groups `quant`, `ml`, and `app`; the base install remains free of NumPy/Pandas/TensorFlow. `quant` pins NumPy `>=2.1,<3`, Pandas `>=2.1,<3`, SciPy `>=1.11,<2`, scikit-learn `>=1.5,<2`, statsmodels `>=0.14.6,<0.15`, ta `>=0.11,<0.12`, vaderSentiment `>=3.3.2,<4`, joblib `>=1.4,<2`, and matplotlib `>=3.9,<4`; `ml` adds TensorFlow `>=2.21,<2.22`; `app` contains the union plus the existing `agents` dependencies.

- [ ] **Step 1: Write failing license and dependency-boundary tests**

Assert MIT/Apache assets are allowed with notice, unknown/missing licenses are excluded, nested resource paths stay covered, base wheel metadata omits heavy dependencies, and `app` includes `quant` plus `ml`.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `uv run pytest tests/compat/test_licensing.py tests/test_license.py -q`

Expected: FAIL on missing audit and extras.

- [ ] **Step 3: Implement the audit and package extras**

Generate the inventory from the frozen snapshot and explicit provenance documents. Never infer a permissive license from file extension or directory alone.

- [ ] **Step 4: Verify audit, lock, and base wheel metadata**

Run: `uv lock --check`

Run: `uv run pytest tests/compat tests/test_license.py -q`

Run: `uv build --wheel`

Expected: all commands exit 0; the base wheel imports without quant/ml packages installed.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/compat/licensing.py scripts/audit_ai_hedge_fund_assets.py tests/compat/test_licensing.py docs/compatibility/third-party-assets.json pyproject.toml uv.lock NOTICE && git commit -m "build: define compatibility extras and asset audit"`

## Phase Gate

Run: `uv run ruff check src/fin_graph/compat tests/compat scripts`

Run: `uv run mypy`

Run: `uv run pytest tests/compat tests/config tests/llm tests/persistence tests/test_license.py -q`

Expected: all pass, the reference snapshot is frozen, and the working tree contains no copied private state.
