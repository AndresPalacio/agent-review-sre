# Verified DeepAgents solver implementation

Goal: extend the independent bm-workflow package so agents execute solutions and only report verified success after host-owned checks of the resulting environment.

## Global constraints

- Preserve existing adapters and optional dependency isolation; no imports from fin_graph.
- Native DeepAgents/LangChain handles planning, tools, delegation and repair. No second agent engine.
- Native after_model verification rejects a candidate, clears structured_response, adds feedback and jumps to model. At most two repairs. Missing capabilities/information are blocked; absence of proof is inconclusive.
- Host defines immutable acceptance criteria and a sync/async verifier that reads actual results. Schema-valid output and model assertions are not verification.
- Execution COMPLETE is distinct from resolution verified/failed/blocked/inconclusive.
- Explicit model and host tools; StateBackend default; no implicit shell or network. Host owns compiled graph/client lifespan.
- Shared run defaults: 24 agent turns, 48 tool calls, 4 summary calls, 2 simultaneous specialists, 120 seconds, 2 repairs. Parent and children share quotas, not global across runs. Count summary calls separately; never imply agent middleware covers them.
- Optional dependency deepagents>=0.7.32,<0.8. Python 3.12 and 3.14, Windows and Linux.
- No publishing, migration of fin_graph, remote agent server, durable resume or HITL in this increment.

## Task 1: Invocation and manifest contracts

Own agent_types.py, adapters.py, public __init__.py, tests/test_agent_invocation.py.
Add frozen AgentInvocation(config: Mapping[str, Any], context: Any) and AgentManifest(revisions: dict[str,str]) with JSON-only version references.
Add optional invocation_factory(state, RunContext | None) -> AgentInvocation (sync or async) and manifest to agent_step/deep_agent_step.
Snapshot config container only; preserve client/context/cancellation identity. Payload continues existing isolation. Pass config/context as kwargs when supplied; keep one-argument callable behavior unchanged by default. Factories run once per attempted execution, after dry-run interception.
Manifest goes into step metadata/digest; never hash/repr live providers or secrets. Reusing compiled graphs is explicit through agent_step; keep legacy deep_agent_step lazy per-invocation behavior.
Tests: context identity with noncopyable lock/client, config propagation, sync/async providers and factories, concurrency isolation, cancellation, dry-run no factory calls, manifest changes digest, backwards compatibility.

## Task 2: Verified native solver

Own src/bm_workflow/agents/ and tests/test_solver*.py plus isolated scripted test utility.
Public module exports build_solver_agent, solver_step, ProblemSpec, VerificationCheck, VerificationReport, SolverResult, SolverLimits and any necessary runtime context type.
ProblemSpec: id, goal, inputs (JSON mapping), constraints, acceptance_criteria (nonempty ID-to-description mapping). Verifier signature verify(problem, candidate, context) -> VerificationReport, sync/async. The context exposes workflow cancellation/deadline and host invocation context without deepcopy.
VerificationReport: verdict verified|failed|blocked|inconclusive, individual checks (criterion_id, passed, detail, evidence_refs), snapshot_digest, missing_requirements, reason. Only verified reports covering all required criteria with passed checks and no missing requirements can produce verified SolverResult.
SolverResult[SolutionT]: candidate (optional), verification, attempts, usage; derived verified property. Limits defaults above. Precise additional public signatures recorded in implementer report and sent to root/example author early.
Builder returns a native reusable CompiledStateGraph with explicit model, tools, solution schema, verifier, optional native specialists/skills/backend. Use native TodoList, filesystem, skills, summarization and response format. Explicit bounded general-purpose specialist suppresses uncontrolled default. No recursive delegation. A verifier specialist is read-only; host checks decide success. Use native after_model for bounded repairs and final checks. Verifier failures/timeouts never approve.
Keep run state isolated and quotas atomic under concurrent subagents. Native model middleware limits do not cover summary calls: wrap native summarizer with its own shared reservation. Guard task concurrency and tool/model calls at execution. Deadline/cancellation apply inside verifier too. No automatic retry of effectful tools; document EffectCoordinator reuse.
solver_step is an external StepNode, output is SolverResult[solution_schema]. Support problem mapper and invocation_factory/manifest from Task1. Dry-run bypasses construction/invocation/verifier. If graph pause encountered, reject as unsupported instead of claiming success. Build once in host lifespan; no implicit global cache.
Events: safe per-run observer with model/tool/verification/usage metadata and no prompt/tool-body copies by default. Version manifest included in step digest.
Tests first using actual DeepAgents and local scripted models: successful action+check, rejection then repair, exhaustion, forged evidence, mutation after an earlier check, invalid/missing criteria, verifier exception/timeout, missing data/tools, child quotas, summary quota, parallel isolation, cancellation, legacy dry-run behavior. No fin_graph imports.

## Task 3: Examples and evaluation

Own examples/solver* and tests/test_solver_evaluation.py; coordinate public signatures with Task2.
Provide offline runnable example and paired benchmark with isolated local tools for configuration repair, data transformation, and artifact changes. Native DeepAgents + scripted model exercises contracts without network. Optional real provider model via explicit CLI flag/import factory, never silently incur model calls.
Compare LangChain create_agent baseline to solver with same model factory/tools/criteria/budgets/cases. Separate scripted contract success from reasoning quality. Include direct solution, tool failure, initial wrong diagnosis, forged evidence, depleted budget, missing data/tools. Fresh environment per case/arm; independent final grader and holdout cases, no exact trajectory grading.
Report verified resolution rate, false successes, blocked cases, recovery, duplicate effects, model/tool/summary usage, duration and available token usage per solved case. Zero false successes/over-budget calls in deterministic contracts. No unmeasured claims of superiority.

## Task 4: Integration, documentation, verification

Root owns pyproject/lock/CI, package README, this plan and ledger, integration fixes as coordinated.
Upgrade package minor version to 0.2.0; lock optional dependency; CI tests base and agents extra while preserving no-extra wheel import smoke.
Document minimal solver example, host verifier trust, lifecycle, limits semantics and stopping outcomes. Run complete package suite, ruff, format check, mypy, examples/benchmark, wheel smoke on available Python runtimes, and root suite if package integration affects it. Independently review new code before completion.
