# AGENTS.md

Rules for coding agents working on `tep-agent-lab`. The product is the industrial context layer; the Agent investigation is its first consumer.

## Session protocol

Start every planning, review or implementation session with `docs/program-status.md`: current main, open PRs, next gate and the step-by-step protocol it owns. Record only merged facts there.

## Product and boundary

The product is the **domain-neutral industrial context layer**: Context Compiler -> Canonical Context -> Context Server / ContextSnapshot (`docs/decisions/ADR-005-context-layer-product-positioning.md` Decision 1). The Agent / Investigation Runtime is its first and strictest consumer, not the product. The UI, evaluation harnesses and, later, external systems are other consumers.

- Three layers (ADR-005 Decision 2): the domain-neutral context core (the product), domain packs, and workflows on top. Domain semantics stay in domain packs; TEP is the process pack.
- Current goal: the L1 Portfolio MVP (`docs/product-maturity-and-mvp.md`), sequenced by ADR-004, including live plant mode and the RCA investigation wedge. No product-track work beyond the MVP is scheduled. The research track (D0.2C, D1 context-value study) is scheduled independently and does not block P1 (ADR-004 Decision 7).

This repository hosts the context-layer contracts (ADR-003, ADR-004, ADR-005), the P0 Playground application plane, the observatory/desktop clients (ADR-002) and the investigation consumer: TEP-specific Agent tools, domain state/projection, lab policy, experiments, Engineering Records and evaluation. It does not own TEP physics or generic runtime internals.

## SPEC_CONFLICT

If implementation discovers an architecture/spec contradiction, emit `SPEC_CONFLICT`; do not silently invent a local state/tool/runtime path. Owning specs may list their own triggers, for example `docs/specs/context-compiler-v0.md` §SPEC_CONFLICT.

## Canonical specs

Before implementation, read the owning spec. Where a summary in this file differs from its source, the source wins.

Context core contracts:

- `docs/specs/plant-telemetry-contract-v0.md` (frozen P1.0 telemetry/context semantics)
- `docs/specs/context-compiler-v0.md` (P1.2 Context Compiler)
- `docs/specs/knowledge-rule-registry-v0.md` (RuleRegistry, reused by the compiler)
- `docs/decisions/ADR-003-industrial-environment-boundary.md`
- `docs/decisions/ADR-004-p1-milestone-rebaseline.md` (P1 sequencing; milestone numbers never own semantics)
- `docs/decisions/ADR-005-context-layer-product-positioning.md` (product = domain-neutral industrial context layer; domain semantics stay in domain packs; the Agent is its first consumer; D1 measures context value; D2 parked)

Process pack (TEP): `docs/specs/tool-surface-v0.md` (TEP Agent tools). ProcessGraph itself is owned by `tep-sim`.

Application plane:

- `docs/specs/playground-backend-v0.md`
- `docs/decisions/ADR-002-ui-application-boundary.md`

Investigation consumer:

- `docs/specs/investigation-state-v0.md`
- `docs/specs/hypothesis-experiment-v0.md`
- `docs/specs/engineering-records-v0.md`
- `docs/specs/tool-bridge-v0.md`
- `docs/specs/rca-v0.md`
- `docs/decisions/ADR-001-simulate-before-reference-mutation.md`

Research and evaluation:

- `docs/specs/evaluation-v0.md` (the sole canonical capability/orchestration comparison matrix)
- `docs/specs/benchmark-case-v0.md` (frozen D0 case contract)
- `docs/specs/benchmark-design-v0.md`
- `docs/specs/benchmark-comparative-pilot-v0.md`

Later task specs (not yet implemented; see Research track): `docs/specs/hazop-v0.md`, `docs/specs/recovery-v0.md`, `docs/specs/autoresearch-v0.md`.

Implementation notes (informative): `docs/p1-*.md`.

Other references: `docs/open-questions.md`. Product direction guardrails (non-normative): `docs/industrial-context-platform-north-star.md`, `docs/product-maturity-and-mvp.md`.

## Dependency rules

- pin/import `tep-sim`; do not copy its physics, variable registry, snapshot semantics, or capability truth;
- pin/import `industrial-agent-runtime`; do not fork its ModelTurn/StateDelta/ToolSpec/gates/TaskStateStore/WorkBatch/subtask semantics locally;
- lab implements `TaskStateStore` for RcaState but runtime never imports RcaState;
- optional knowledge services remain behind typed read-only evidence adapters until an explicit later capability study;
- evaluator-only truth/candidate sets never become registered Agent tools;
- ProcessGraph stays `tep-sim`-owned and human-verified; plant-semantics changes go through the tep-sim review path, and the lab projects ProcessGraph without renaming or re-keying its identities (ADR-004 Decisions 4 and 5).

## Context-layer rules

Each rule summarizes an accepted source and adds no semantics; the cited section wins. The hard rules below also apply.

- **CL-1** Candidate and Canonical Context are separate trust domains: model (LLM/VLM) output is never canonical, confidence is not proof, and publication needs an explicit versioned validation path. Source: `context-compiler-v0.md` §Hard trust invariant, §Confidence semantics; ADR-003 §API, adapters, and repository boundaries.
- **CL-2** Review and publication are Application API operations, never Agent tools; a ReviewRecord is decided by a human or a `policy_id@version` deterministic policy, never by a model or Agent. Source: `context-compiler-v0.md` §Human review.
- **CL-3** Compiler input is an exact trusted ContextSourceRef, never an arbitrary local path; a compiler run is not an Agent run (no Coordinator, no tools) and records every model call's exact inputs, outputs and provider/model/method version. Source: `context-compiler-v0.md` §Exact source input, §Model/provider boundary.
- **CL-4** Publication creates an immutable EngineeringContextRevision; there is no in-place "current context", no mutable latest registry or resolver, and a READY CanonicalContextRegistry is never mutated. Source: `context-compiler-v0.md` §Publication; ADR-004 Decision 4.
- **CL-5** Derived output takes the most restrictive visibility of its inputs; evaluator-only inputs never create Agent-visible knowledge; engineering context enters a blind benchmark run only as C6, under a versioned leakage-audit extension. Source: `context-compiler-v0.md` §Visibility and derivation taint, §Benchmark use; ADR-004 Decision 6.
- **CL-6** Simulation, event, ingest and wall-clock time stay separate; simulation time is never UTC without a declared mapping; sample identity is `(source_id, signal_id, sequence)`, never a timestamp. Source: `plant-telemetry-contract-v0.md` §Four time concepts and clock representation, §Identity, deduplication, and acceptance ordering.
- **CL-7** Every read pins an exact TelemetryReadSnapshot (event horizon T, ingest cutoff K) and exact context revisions, never "latest"; a consumer at (T, K) never receives a sample invisible under that cutoff. Source: `plant-telemetry-contract-v0.md` §Append store and read snapshots, §Hard temporal leakage invariant; ADR-004 Decision 6.
- **CL-8** Reads are bounded (finite interval, positive max_points, policy limits, no implicit source switching); the Agent never consumes a raw stream, only typed bounded operations under lab policy and runtime gates. Source: `plant-telemetry-contract-v0.md` §Bounded deterministic reads, §Application, Agent, transport, and TEP boundaries.
- **CL-9** Application API != Agent Tool Surface: reads, UI and transport grant no SIMULATE/MUTATE authority, there is no shortcut such as `ui_run_rollout()`, and trusted harness setup actions are not Agent tools. Source: `playground-backend-v0.md` §Authority boundary; ADR-002 §Decision; `plant-telemetry-contract-v0.md` §Scope and ownership.
- **CL-10** ContextSnapshot and InvestigationContextView reference, never copy, ProcessGraph, RuleRegistry, CanonicalContextRegistry, TimeSeriesStore or RcaState; there is no combined static-context revision and no second investigation state model. Source: `plant-telemetry-contract-v0.md` §Scope and ownership, §ContextSnapshot: exact available industrial context; ADR-004 Decision 4; ADR-005 Decision 1.
- **CL-11** Telemetry- or knowledge-derived observations used as evidence keep the exact refs that produced them; missing refs report unavailable and are never reconstructed from newer data. Source: `plant-telemetry-contract-v0.md` §Evidence provenance bridge (hard invariant).
- **CL-12** Generic contracts never import TEP or other domain vocabulary; ProcessGraph is the process-pack instance, not the generic model; no `IndustrialEnvironmentAdapter` or generic pack interface exists before a second domain. Source: `plant-telemetry-contract-v0.md` §Application, Agent, transport, and TEP boundaries; ADR-003 §API, adapters, and repository boundaries; ADR-005 Decision 2.
- **CL-13** P1.1C changes application reads only; Agent tools keep their behavior until P1.5; Agent-facing changes ship as new tool, projection or benchmark versions; frozen D0 fixtures stay byte-identical; the dirty-stream replay harness precedes any non-simulated source. Source: ADR-004 Decision 6.
- **CL-14** Live plant mode keeps the READY-frozen source inventory and RunManifest; streamed samples are run data, not new canonical sources; pacing never changes physics, values, identities or order; any incident injection is harness authority and EVALUATOR-only. Source: `playground-backend-v0.md` §Run lifecycle, §CanonicalContextRegistry; `plant-telemetry-contract-v0.md` §Delivery and dirty streams; ADR-005 §Live plant mode is MVP-required.
- **CL-15** Transport and storage are replaceable adapters, not domain architecture; no REST API is required and no database or broker is selected. Source: ADR-003 §API, adapters, and repository boundaries; `plant-telemetry-contract-v0.md` §Application, Agent, transport, and TEP boundaries.
- **CL-16** Milestone numbers never own semantics; cite the owning spec section, not a milestone. Source: ADR-004 Decision 1.

## Hard rules (repo-wide)

These rules bind the whole repository, including the context layer (Context Compiler LLM extraction, the live-plant harness, telemetry), not only the investigation consumer and the benchmarks. They are accepted semantics and ADR-005 does not change them.

1. Preserve hidden truth/candidate-answer isolation in blind experiments.
2. Every successful agent-visible ToolResult is deterministically registered as an immutable `ObservationRecord` during result ingestion.
3. `ObservationRecord != Evidence`: evidence exists only through an explicit `HypothesisEvidenceLink`/typed evidence-link StateDelta.
4. Model-proposed hypothesis/evidence/open-question/experiment-interpretation/working-explanation changes enter state only through runtime `ModelStateUpdateProposal` and lab `TaskStateStore.apply_batch`.
5. Model-proposed state changes are bound to the exact `ContextProjection.base_revision` seen by that turn; stale/illegal batches apply nothing.
6. Model state updates cannot directly change generic runtime budget/policy/status/authority or reference-world state.
7. Deterministic result-ingestion deltas from parallel WorkBatch items bind the then-current RcaState revision in stable work-item order; do not reuse the old model projection revision for every result.
8. Exact model-visible ContextProjection is persisted for every model turn.
9. Blind RCA does not expose canonical node-to-IDV answer bindings or full candidate list by default. (TEP benchmark)
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

## Investigation consumer

- RcaState update path: `docs/specs/investigation-state-v0.md` §Model-proposed update semantics, §Deterministic result-ingestion semantics.
- Subagent policy is inherited from `industrial-agent-runtime`; subagents are an orchestration condition (`docs/specs/evaluation-v0.md` §Orchestration axis), and D2/B4 are parked (ADR-005 Decision 3).
- Tool Bridge scope: `docs/specs/tool-bridge-v0.md` and OQ-6 in `docs/open-questions.md`.
- Benchmark reporting: `docs/specs/evaluation-v0.md` §E2–§E5 and §Reproducibility bundle; D1 reporting: ADR-005 Decision 3.

## Research track

ADR-005 Decision 3 owns this track: finish D0.2C (C0 + identifiability); D1 is a context-value study on the evaluation-v0 ladder (open points: OQ-16); D2 orchestration ablation (O0-O5) is parked. The original v0 research order is history: the Phase 0–12 sections of `docs/roadmap.md`.

Do not implement HAZOP/Recovery/AutoResearch infrastructure just because their proposal specs exist; first RCA contracts/tracing/evaluation must stabilize.

Keep this file concise. Detailed workflow semantics belong in `docs/specs`; unresolved empirical questions belong in `docs/open-questions.md`.
