# AI Hedge Fund Web Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the remaining SRE console with a tested financial flow workbench that reproduces the AI Hedge Fund frontend and keeps Fin Graph's native approvals and paper-ledger views.

**Architecture:** Rebuild the reference interaction model in the existing React/Vite application, using a typed API/SSE client and React contexts for flow/tab/layout state. Compatibility views use the exact legacy endpoints; native features live in separate tabs over `/v1` and never alter saved legacy flow JSON.

**Tech Stack:** React 19, TypeScript 5.8, Vite 6, Tailwind 4, XYFlow, Radix UI, Vitest/Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md`

**Prerequisite:** Plan 04.

## Global Constraints

- No component may hard-code `http://localhost:8000`; all requests use the configured relative/base URL client.
- The original frontend must also pass its E2E flow against the new backend without source changes.
- Provider/API keys remain memory-only in browser requests and never enter URLs, localStorage, logs, snapshots, or error text.
- Saved flow JSON and node ids/types remain compatible with the frozen reference.
- Every async view handles loading, empty, error, cancellation, reconnect, and stale-response ordering.

## Review Focus

- A slow response from a previously selected flow must not overwrite the current flow; Task 3 tests it.
- SSE reconnect/duplicate events must not duplicate output or terminal state; Task 4 tests it.
- Malformed persisted node positions/data must show a recoverable problem instead of crashing the canvas; Task 2 tests it.
- Keyboard shortcuts and destructive actions must not fire while a dialog/input owns focus; Task 3 tests it.
- Secrets must be absent from storage, URL, DOM after close, console, and screenshots; Task 5 tests it.

---

### Task 1: Establish the typed workbench shell and API client

**Files:**
- Modify: `web/package.json`
- Modify: `web/src/App.tsx`
- Modify: `web/src/main.tsx`
- Replace: `web/src/style.css`
- Create: `web/src/lib/api-client.ts`
- Create: `web/src/lib/sse.ts`
- Create: `web/src/types/compat.ts`
- Create: `web/src/contexts/LayoutContext.tsx`
- Create: `web/src/components/layout/WorkbenchLayout.tsx`
- Create: `web/src/components/layout/TopBar.tsx`
- Create: `web/src/components/layout/Panel.tsx`
- Create: `web/src/lib/api-client.test.ts`
- Modify: `web/src/App.test.tsx`

**Interfaces:**
- Produces `ApiClient` methods for every Plan 04 endpoint, `stream(request, handlers, signal)` with typed legacy events, and `WorkbenchLayout` slots for top/left/canvas/right/bottom.

- [ ] **Step 1: Write failing client and shell tests**

Assert relative URL resolution, JSON/error parsing, abort propagation, SSE CRLF/multiline/partial chunks, one terminal callback, duplicate id suppression, responsive panel landmarks, theme persistence without secrets, and top-level error boundary.

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd web; npm test -- --run src/lib/api-client.test.ts src/App.test.tsx`

Expected: FAIL on missing client/layout.

- [ ] **Step 3: Add audited UI dependencies and implement the shell/client**

Add XYFlow and only the Radix/resizable dependencies actually used. Keep server state out of layout context.

- [ ] **Step 4: Run typecheck and focused tests**

Run: `cd web; npm run typecheck; npm test -- --run src/lib/api-client.test.ts src/App.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add web/package.json web/package-lock.json web/src && git commit -m "feat(web): add financial workbench shell"`

### Task 2: Implement the flow canvas and component catalog

**Files:**
- Create: `web/src/types/flow.ts`
- Create: `web/src/data/analysts.ts`
- Create: `web/src/data/node-catalog.ts`
- Create: `web/src/contexts/NodeContext.tsx`
- Create: `web/src/components/flow/FlowCanvas.tsx`
- Create: `web/src/components/flow/FlowControls.tsx`
- Create: `web/src/components/flow/nodes/NodeShell.tsx`
- Create: `web/src/components/flow/nodes/PortfolioStartNode.tsx`
- Create: `web/src/components/flow/nodes/AnalystNode.tsx`
- Create: `web/src/components/flow/nodes/PortfolioManagerNode.tsx`
- Create: `web/src/components/flow/nodes/OutputNode.tsx`
- Create: `web/src/components/catalog/ComponentCatalog.tsx`
- Create: `web/src/components/flow/FlowCanvas.test.tsx`

**Interfaces:**
- Produces exact legacy `FlowNode`/`FlowEdge` JSON, node renderers for all recorded node types, and catalog entries for nineteen analysts plus start/portfolio/output components.

- [ ] **Step 1: Write failing canvas, serialization, and malformed-data tests**

Assert add/connect/delete/select/drag, valid connection rules, exact JSON serialization, analyst metadata/order, copy/paste ids, fit view, unknown node fallback, non-finite/missing positions, and keyboard deletion blocked while typing.

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd web; npm test -- --run src/components/flow/FlowCanvas.test.tsx`

Expected: FAIL on missing canvas/catalog.

- [ ] **Step 3: Implement XYFlow canvas and typed node registry**

Keep model/API-key configuration in node data compatible with the reference schema. Reject invalid graph edits client-side while leaving server validation authoritative.

- [ ] **Step 4: Run typecheck and canvas tests**

Run: `cd web; npm run typecheck; npm test -- --run src/components/flow/FlowCanvas.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add web/src/types web/src/data web/src/contexts/NodeContext.tsx web/src/components/flow web/src/components/catalog && git commit -m "feat(web): add compatible flow canvas"`

### Task 3: Add flow library, tabs, history, and editing

**Files:**
- Create: `web/src/contexts/FlowContext.tsx`
- Create: `web/src/contexts/TabsContext.tsx`
- Create: `web/src/components/flows/FlowSidebar.tsx`
- Create: `web/src/components/flows/FlowList.tsx`
- Create: `web/src/components/flows/FlowDialog.tsx`
- Create: `web/src/components/tabs/TabBar.tsx`
- Create: `web/src/hooks/useFlowHistory.ts`
- Create: `web/src/hooks/useKeyboardShortcuts.ts`
- Create: `web/src/components/flows/FlowLibrary.test.tsx`

**Interfaces:**
- Produces create/open/save/rename/duplicate/delete flow behavior, dirty-state tabs, undo/redo history, and conflict/error prompts over `ApiClient`.

- [ ] **Step 1: Write failing CRUD, stale-response, and focus-safety tests**

Assert list/create/rename/duplicate/delete exact requests, dirty close confirmation, save revision refresh, 404/conflict recovery, out-of-order load response isolation, undo/redo boundaries, and shortcuts disabled in inputs/dialogs.

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd web; npm test -- --run src/components/flows/FlowLibrary.test.tsx`

Expected: FAIL on missing contexts/components.

- [ ] **Step 3: Implement state contexts and flow operations**

Use `AbortController` per active load and revision ids from the server; never merge a stale server record into a dirty tab.

- [ ] **Step 4: Run flow UI tests**

Run: `cd web; npm run typecheck; npm test -- --run src/components/flows src/components/flow`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add web/src/contexts web/src/components/flows web/src/components/tabs web/src/hooks && git commit -m "feat(web): add flow library and editing history"`

### Task 4: Add run streaming and result inspection

**Files:**
- Create: `web/src/contexts/RunContext.tsx`
- Create: `web/src/components/output/BottomPanel.tsx`
- Create: `web/src/components/output/OutputTab.tsx`
- Create: `web/src/components/output/ReasoningTab.tsx`
- Create: `web/src/components/output/ProblemsTab.tsx`
- Create: `web/src/components/output/TerminalTab.tsx`
- Create: `web/src/components/output/InvestmentReportDialog.tsx`
- Create: `web/src/components/output/JsonOutputDialog.tsx`
- Create: `web/src/components/output/RunOutput.test.tsx`

**Interfaces:**
- Produces run/start/cancel state, analyst/node status projection, legacy SSE accumulation, reasoning/report/JSON rendering, and persisted run/cycle history navigation.

- [ ] **Step 1: Write failing stream, reconnect, error, and rendering tests**

Assert request payload, progress by node, exact terminal result, duplicate/replayed event ids, reconnect, partial chunk, cancellation, child error, global error, stale stream isolation, report fields, safe JSON rendering, and absence of raw secret/tool payloads.

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd web; npm test -- --run src/components/output/RunOutput.test.tsx`

Expected: FAIL on missing run/output UI.

- [ ] **Step 3: Implement streaming state and inspectors**

Index events by stable id and derive UI status; do not mutate saved node data with transient results.

- [ ] **Step 4: Run output and client tests**

Run: `cd web; npm run typecheck; npm test -- --run src/components/output src/lib/api-client.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add web/src/contexts/RunContext.tsx web/src/components/output && git commit -m "feat(web): stream and inspect flow runs"`

### Task 5: Add backtests, models, Ollama, keys, and native paper views

**Files:**
- Create: `web/src/components/backtests/BacktestForm.tsx`
- Create: `web/src/components/backtests/BacktestOutput.tsx`
- Create: `web/src/components/settings/SettingsDialog.tsx`
- Create: `web/src/components/settings/CloudModels.tsx`
- Create: `web/src/components/settings/OllamaSettings.tsx`
- Create: `web/src/components/settings/ProviderKeys.tsx`
- Create: `web/src/components/native/PortfolioView.tsx`
- Create: `web/src/components/native/OrdersView.tsx`
- Create: `web/src/components/native/ApprovalView.tsx`
- Create: `web/src/components/native/AnalysisExplainView.tsx`
- Create: `web/src/components/settings/SettingsDialog.test.tsx`
- Create: `web/src/components/backtests/BacktestOutput.test.tsx`
- Create: `web/src/components/native/NativeViews.test.tsx`

**Interfaces:**
- Produces v1 and algorithm backtest forms/results/events/cancel, exact model/provider/Ollama/key settings, and separate native `/v1` portfolio/order/approval/explanation views.

- [ ] **Step 1: Write failing backtest, settings, and secret-hygiene tests**

Assert input validation, run/progress/cancel/result, metrics/trades/signals, model catalog, Ollama lifecycle/progress/error, provider-key CRUD, secret disappearance after close, no secret in URL/localStorage/console/DOM snapshot, and native order approval role/error states.

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd web; npm test -- --run src/components/backtests src/components/settings src/components/native`

Expected: FAIL on missing components.

- [ ] **Step 3: Implement the views over typed clients**

Keep compatibility and native auth sessions separate. Never prefill a saved provider secret; show only metadata/status.

- [ ] **Step 4: Run the full component suite**

Run: `cd web; npm run typecheck; npm test`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add web/src/components/backtests web/src/components/settings web/src/components/native && git commit -m "feat(web): add backtests settings and paper trading views"`

### Task 6: Prove browser compatibility against both frontends

**Files:**
- Create: `web/e2e/compat-workbench.spec.ts`
- Create: `web/e2e/native-paper.spec.ts`
- Modify: `web/playwright.config.ts`
- Create: `tests/web/reference_frontend_server.py`
- Create: `tests/web/test_reference_frontend_contract.py`
- Modify: `src/fin_graph/server/app.py`

**Interfaces:**
- E2E starts the local compatibility backend with fixtures, serves the new UI, and separately serves the frozen reference UI build configured only by its expected backend address.

- [ ] **Step 1: Write failing Playwright journeys**

New UI: create/save/duplicate a flow, connect analysts, run, inspect report, backtest, cancel a job, configure fake Ollama, view portfolio, approve/reject a paper order. Reference UI: load agents/models, create/open flow, run and backtest against the new backend. Assert zero console/page errors and no secret in screenshots/traces.

- [ ] **Step 2: Run E2E and confirm RED**

Run: `cd web; npm run build; npm run test:e2e`

Expected: FAIL until all routes/components/static serving are connected.

- [ ] **Step 3: Wire static build and fixture runtime**

Serve `web/dist` through the existing static artifact path; keep `/v1`, compatibility routes, `/docs`, and health outside the SPA catch-all.

- [ ] **Step 4: Run all web gates**

Run: `cd web; npm run typecheck; npm test; npm run build; npm run test:e2e`

Run: `uv run pytest tests/web tests/server/test_web_static.py -q`

Expected: PASS with both frontend journeys.

- [ ] **Step 5: Commit**

Run: `git add web tests/web src/fin_graph/server/app.py && git commit -m "test: prove web replacement compatibility"`

## Phase Gate

Run: `cd web; npm run typecheck; npm test; npm run build; npm run test:e2e`

Run: `uv run pytest tests/web tests/compat/http -q`

Expected: all pass; the original frontend and the new workbench both complete the recorded end-to-end flow against Fin Graph.
