# AGENTS.md

Rules for coding agents working on `tep-agent-lab`.

## Product, goal and repository boundary

The product is the **domain-neutral industrial context layer**: Context Compiler -> Canonical Context -> Context Server / ContextSnapshot (`docs/decisions/ADR-005-context-layer-product-positioning.md`). The Agent / Investigation Runtime is its first and strictest consumer, not the product. The UI, evaluation harnesses and, later, external systems are other consumers.

- Domain semantics stay in domain packs. TEP is the process pack: TEP identifiers and ProcessGraph stay below the generic context contracts, and generic contracts never import TEP.
- Current goal: the L1 Portfolio MVP (`docs/product-maturity-and-mvp.md`), sequenced by ADR-004. Live plant mode is MVP-required. Nothing beyond the MVP is scheduled.
- Context-layer invariants: LLM output is never canonical; consumers read exact pinned revisions and T/K snapshots, never "latest"; the context layer never creates a second investigation state model.

This repository hosts the context-layer contracts (ADR-003, ADR-004, ADR-005), the P0 Playground application plane, the observatory/desktop clients (ADR-002) and the investigation consumer: TEP-specific Agent tools, domain state/projection, lab policy, experiments, Engineering Records and evaluation. It does not own TEP physics or generic runtime internals.

## Canonical specs

Before implementation, read the relevant owning spec:

- `docs/specs/investigation-state-v0.md`
- `docs/specs/knowledge-rule-registry-v0.md`
- `docs/specs/hypothesis-experiment-v0.md`
- `docs/specs/tool-surface-v0.md`
- `docs/specs/tool-bridge-v0.md`
- `docs/specs/engineering-records-v0.md`
- `docs/specs/benchmark-design-v0.md`
- `docs/specs/evaluation-v0.md`
- `docs/specs/benchmark-comparative-pilot-v0.md`
- `docs/specs/rca-v0.md`
- `docs/specs/hazop-v0.md`
- `docs/specs/recovery-v0.md`
- `docs/specs/autoresearch-v0.md`
- `docs/specs/playground-backend-v0.md`
- `docs/open-questions.md`
- `docs/decisions/ADR-001-simulate-before-reference-mutation.md`

`evaluation-v0.md` is the sole canonical capability/orchestration comparison matrix.

Also canonical:

- `docs/specs/benchmark-case-v0.md` (frozen D0 case contract)
- `docs/specs/plant-telemetry-contract-v0.md` (frozen P1.0 telemetry/context semantics)
- `docs/specs/context-compiler-v0.md` (P1.2 Context Compiler)
- `docs/decisions/ADR-002-ui-application-boundary.md`
- `docs/decisions/ADR-003-industrial-environment-boundary.md`
- `docs/decisions/ADR-004-p1-milestone-rebaseline.md` (P1 sequencing; milestone numbers never own semantics)
- `docs/decisions/ADR-005-context-layer-product-positioning.md` (product = domain-neutral industrial context layer; domain semantics stay in domain packs; the Agent is its first consumer; D1 measures context value; D2 parked)

Product direction guardrails (non-normative): `docs/industrial-context-platform-north-star.md`, `docs/product-maturity-and-mvp.md`. Current state, open PRs and next gate: `docs/program-status.md`.

If implementation discovers an architecture/spec contradiction, emit `SPEC_CONFLICT`; do not silently invent a local state/tool/runtime path.

## Dependency rules

- pin/import `tep-sim`; do not copy its physics, variable registry, snapshot semantics, or capability truth;
- pin/import `industrial-agent-runtime`; do not fork its ModelTurn/StateDelta/ToolSpec/gates/TaskStateStore/WorkBatch/subtask semantics locally;
- lab implements `TaskStateStore` for RcaState but runtime never imports RcaState;
- optional knowledge services remain behind typed read-only evidence adapters until an explicit later capability study;
- evaluator-only truth/candidate sets never become registered Agent tools.

## Hard rules

These rules bind the investigation consumer and the benchmarks. They are accepted semantics and ADR-005 does not change them.

1. Preserve hidden truth/candidate-answer isolation in blind experiments.
2. Every successful agent-visible ToolResult is deterministically registered as an immutable `ObservationRecord` during result ingestion.
3. `ObservationRecord != Evidence`: evidence exists only through an explicit `HypothesisEvidenceLink`/typed evidence-link StateDelta.
4. Model-proposed hypothesis/evidence/open-question/experiment-interpretation/working-explanation changes enter state only through runtime `ModelStateUpdateProposal` and lab `TaskStateStore.apply_batch`.
5. Model-proposed state changes are bound to the exact `ContextProjection.base_revision` seen by that turn; stale/illegal batches apply nothing.
6. Model state updates cannot directly change generic runtime budget/policy/status/authority or reference-world state.
7. Deterministic result-ingestion deltas from parallel WorkBatch items bind the then-current RcaState revision in stable work-item order; do not reuse the old model projection revision for every result.
8. Exact model-visible ContextProjection is persisted for every model turn.
9. Blind RCA does not expose canonical node-to-IDV answer bindings or full candidate list by default.
10. Counterfactual experiments use isolated branches; SIMULATE never mutates reference state.
11. MUTATE is a separate high-authority path and is disabled in blind RCA/AutoResearch by default.
12. Compound Tool Bridge operations that run simulator trials are SIMULATE and must reserve/report nested rollout/horizon/trial budget.
13. Tool Bridge is not an authorization layer; executable calls pass runtime gates + lab `validate_request`.
14. Domain Rule metadata is `origin × validation × authority`; K0–K4 is shorthand only.
15. LLM/paper/simulation evidence cannot self-promote execution authority.
16. Typed Prediction/ExperimentResult data is preferred over free-text judging for experiment discrimination.
17. Required first RCA baseline C0 is strong deterministic enumerate/simulate/match, not a strawman.
18. Tool exposure stays fixed across orchestration ablations unless exposure itself is the independent variable.
19. Subagents appear on the orchestration axis, not the capability axis.
20. Failed/negative runs/experiments remain in the append-only run history.
21. Engineering Records are archival in v0 and are not automatically retrieved into later benchmark context.
22. Unsupported physics are reported as unsupported, never fabricated as simulator evidence.
23. No arbitrary Agent Python/shell/import as the normal analysis path.

## RcaState update path

### Model-proposed internal state

```text
ModelTurn.state_update
 -> lab validates allowlisted RCA operation/ref/visibility
 -> atomic TaskStateStore.apply_batch
 -> revision increments once
 -> same-turn executable action proceeds only on success
```

Initial model-proposable operations are owned by `investigation-state-v0.md` and include hypothesis/evidence/open-question/experiment/working-explanation updates.

### Deterministic result ingestion

```text
successful ToolResult
 -> REGISTER_OBSERVATION
 -> artifact refs as applicable

successful ExperimentResult
 -> REGISTER_OBSERVATION
 -> REGISTER_COMPLETED_EXPERIMENT

successful SubtaskResult
 -> REGISTER_SUBTASK_RESULT
```

Observation registration is automatic. The model must explicitly link observations as evidence later.

## Research track (benchmark and evaluation)

Product-track (P1) sequencing is owned by `docs/decisions/ADR-004-p1-milestone-rebaseline.md` and product positioning by `docs/decisions/ADR-005-context-layer-product-positioning.md`; current state is in `docs/program-status.md`.

ADR-005 Decision 3 redirects this track: finish D0.2C (C0 + identifiability); D1 is a context-value study on the existing evaluation-v0 ladder (C0 floor, C2 -> C3 topology, C5 -> C6 engineering context; OQ-16); D2 orchestration ablation (O0-O5) is parked. The block below is the original v0 research order, kept as dependency history:

```text
RcaState / run-log / ContextProjection
 -> Rule/policy metadata
 -> Hypothesis + Prediction + Experiment
 -> blind TEP Tool Surface
 -> minimal Tool Bridge
 -> benchmark identifiability + C0
 -> RCA capability ladder
 -> orchestration O0-O5
 -> later HAZOP / Recovery
 -> later AutoProcessResearch
 -> optional knowledge/memory studies
```

Do not implement HAZOP/Recovery/AutoResearch infrastructure just because their proposal specs exist; first RCA contracts/tracing/evaluation must stabilize.

## Subagents

Inherit runtime policy:

- depth 1 default;
- cumulative max 3 children per task default;
- child MUTATE disabled;
- child cannot create nested SUBTASK at depth limit;
- child receives scoped ContextProjection/tools;
- parent receives `SubtaskResult`, not complete child transcript.

The parent/lab decides which child observation refs become EvidenceLinks through explicit state updates.

## Tool Bridge v0 scope

Initial bridge implementation should remain small:

- response features / trajectory comparison;
- cross-correlation / lag;
- optional upstream TEP detector baseline if benchmark requires it.

SALib/Optuna/PCA/PLS/Granger/MCP are later, concrete-need additions.

## Benchmark/reporting

Every first-family report should include:

- C0 performance/cost;
- task quality;
- evidence/Prediction validity;
- observation/query waste;
- model/state-update/tool/rollout/subtask resources;
- gate/authority violations;
- benchmark/tool/scorer versions;
- final InvestigationReport ref.

## Session protocol

Start every planning, review or implementation session with `docs/program-status.md`: current main, open PRs, next gate and the step-by-step protocol. Record only merged facts there.

Keep this file concise. Detailed workflow semantics belong in `docs/specs`; unresolved empirical questions belong in `docs/open-questions.md`.
