# Deep Agents Harness Reliability Design

## Intent

Make optimization fail fast when the configured model cannot operate the official Deep Agents
filesystem tools, while preserving the full `create_deep_agent` runtime and the private holdout
boundary. Also remove the two false negatives observed in the deep-value case: an overly timid
portfolio instruction and an English-only keyword reference for Spanish output.

## Goals

- Keep the optimizer on the official `deepagents.create_deep_agent` API.
- Prove native read/edit tool use before spending time on baseline evaluation.
- Detect tool-call JSON printed as prose and report a protocol error instead of `no_changes`.
- Give small local models a staged, one-action-at-a-time bootstrap with a smaller output budget.
- Reconsider an unexplained `hold` once when a verified, sufficiently confident thesis has a
  directional action available; remain fail-closed if the retry also holds or fails.
- Score Spanish and English equivalents for the same deep-value concept.
- Preserve train/holdout/scorecard isolation and all trading gates.

## Non-goals

- No automatic trade is created when the model chooses `hold` twice.
- No hidden holdout or scorecard details are exposed to the proposer.
- No unbounded evaluation or model concurrency.
- No automatic model download or change to the user's installed Ollama models.

## Approaches

### Selected: guarded Deep Agent plus capability preflight

Run a tiny scratch Deep Agent in a bounded child process. It must read and edit a known file via
native tools. Only then evaluate the baseline. The production proposer uses the same model,
factory, backend, system prompt family, and guarded tool protocol. This directly tests the
capability the optimization loop needs and saves the expensive 16-case baseline when it is absent.

### Rejected: prompt-only correction

The guarded first instruction caused the 3B model to issue a native read, but its following edit
arguments were incomplete. Prompting alone therefore cannot establish edit capability.

### Rejected: remove Deep Agents tools or subagents

Disabling the task tool or replacing the optimizer with deterministic file rewriting can reduce
tool confusion, but loses the Deep Agents behavior the application is intended to exercise.

## Design

The preflight runs before baseline evaluation only for the built-in proposer and only when at least
one optimization iteration is requested. It writes a diagnostic log under the run directory and
returns a blocked summary on capability failure. Scripted smoke runs and injected test proposers do
not invoke it.

The proposer prompt mandates one native tool call at a time and forbids rendering tool-call JSON.
After an agent run, unchanged surfaces plus serialized function-call syntax raise a dedicated
protocol error. Normal, intentional no-change proposals remain valid.

Portfolio selection retains all existing deterministic gates. If the first structured response is
`hold` while the thesis is verified and a non-hold action exists, the service retries once with the
first decision and the allowed actions in the payload. A second hold is accepted, preserving safe
behavior.

The deep-value reference accepts `margin of safety|margen de seguridad`; the narrative instruction
asks for that concept and Graham attribution only when the supplied category/evidence supports it.
If a small model omits the category phrase, the service restores `margen de seguridad` only when
the deterministic valuation score is bullish. Graham remains present through the score-backed
claim, so valid model prose is not otherwise rewritten.

## Verification

- Unit tests watch preflight ordering and protocol failures.
- An offline Deep Agent integration test proves native edit side effects.
- Service tests prove exactly one semantic retry and fail-closed second hold behavior.
- Eval tests load the real deep-value fixture and accept the Spanish wording.
- Full pytest, Ruff, harness validation, offline smoke, and a real local proposer preflight.
