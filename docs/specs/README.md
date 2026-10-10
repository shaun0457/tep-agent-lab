# TEP Agent Lab Specifications

These v0 specs define TEP-specific integration, investigation, research, application hosting, and evaluation on top of `tep-sim` and `industrial-agent-runtime`.

Phase 0 Design Freeze is complete for the first RCA/runtime-lab boundary. Program Re-baseline v1 adds an Application / Playground Plane and canonical-context contract without reopening those accepted runtime/world/investigation boundaries. Industrial Context Platform sequencing is owned by [ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md); current state is in [`program-status.md`](../program-status.md).

## State / knowledge / experiment contracts

- [`investigation-state-v0.md`](investigation-state-v0.md) — canonical RcaState/TaskStateStore implementation, ModelStateUpdateProposal/StateDelta operations, automatic ObservationRecord ingestion, revisions, projection, and stopping readiness.
- [`knowledge-rule-registry-v0.md`](knowledge-rule-registry-v0.md) — `origin × validation × authority` rule metadata, provenance, enforcement classes, and later promotion direction.
- [`hypothesis-experiment-v0.md`](hypothesis-experiment-v0.md) — first-class hypotheses, typed Predictions, evidence links, experiment proposals/run specs/results, and explicit interpretation-to-StateDelta mapping.
- [`engineering-records-v0.md`](engineering-records-v0.md) — InvestigationReport / DecisionRecord / ExperimentRecord archive contracts.

## Industrial Context Platform

- [`plant-telemetry-contract-v0.md`](plant-telemetry-contract-v0.md) — frozen P1.0 plant context, engineering context, telemetry (clocks, SignalSample, append store, T/K snapshots, bounded reads), ContextSnapshot and evidence-provenance semantics. Sequencing per ADR-004.

## Application / Playground

- [`playground-backend-v0.md`](playground-backend-v0.md) — minimal local-first RunManager/RunManifest lifecycle, Git/repository-backed `CanonicalContextRegistry`, visibility-aware context-source resolution, read/event projections, branch-tree views, and typed artifact access. Application/UI APIs remain separate from Agent tool authority.

The Playground backend is initially an integration layer in this repo, not a fourth core repository and not another canonical TaskStateStore/world store. Existing runtime/lab/world components remain authoritative; Playground views are derived projections.

## Tools

- [`tool-surface-v0.md`](tool-surface-v0.md) — agent-visible TEP environment tools and authority classes.
- [`tool-bridge-v0.md`](tool-bridge-v0.md) — allowlisted adapters to mature analysis/optimization/graph tools with nested budget accounting.

## Research workflows

- [`rca-v0.md`](rca-v0.md) — blind root-cause investigation contract.
- [`hazop-v0.md`](hazop-v0.md) — later simulation-backed HAZOP contract.
- [`recovery-v0.md`](recovery-v0.md) — later counterfactual recovery-planning contract.
- [`autoresearch-v0.md`](autoresearch-v0.md) — later frozen-evaluator autonomous engineering experiment loop.

## Benchmark / evaluation

- [`benchmark-case-v0.md`](benchmark-case-v0.md) — frozen D0 per-case identity, evaluator truth, deterministic Agent projection, trusted setup attestation, deterministic scoring vector, re-scoring and leakage-audit contract.
- [`benchmark-design-v0.md`](benchmark-design-v0.md) — scenario-family design, identifiability pilot, difficulty, partitioning, leakage controls, and strong C0 baseline.
- [`evaluation-v0.md`](evaluation-v0.md) — environment/runtime/task/scientific-behavior metrics plus canonical capability and orchestration ablations.

## Canonical context principle

Versioned engineering/research truth may be materialized locally from repository-controlled sources at exact revisions, but local availability does not imply Agent visibility. `ContextProjection` remains bounded and visibility-aware.

Run-specific hypotheses, observations, evidence links, branches, traces, budgets and temporary telemetry remain run state and are not automatically promoted into Git-backed canonical knowledge.

The lab owns TEP/domain-policy adapters, initial Playground integration, and evaluation logic. It does not reimplement TEP physics or generic runtime mechanics.
