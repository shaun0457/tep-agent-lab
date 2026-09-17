# TEP Agent Lab Specifications

These v0 specs define TEP-specific integration, investigation, research, and evaluation on top of `tep-sim` and `industrial-agent-runtime`.

Phase 0 Design Freeze is complete for the first RCA/runtime-lab boundary. See `../../../../design-freeze-record.md` and `../../../../implementation-plan.md` for release status.

## State / knowledge / experiment contracts

- [`investigation-state-v0.md`](investigation-state-v0.md) — canonical RcaState/TaskStateStore implementation, ModelStateUpdateProposal/StateDelta operations, automatic ObservationRecord ingestion, revisions, projection, and stopping readiness.
- [`knowledge-rule-registry-v0.md`](knowledge-rule-registry-v0.md) — `origin × validation × authority` rule metadata, provenance, enforcement classes, and later promotion direction.
- [`hypothesis-experiment-v0.md`](hypothesis-experiment-v0.md) — first-class hypotheses, typed Predictions, evidence links, experiment proposals/run specs/results, and explicit interpretation-to-StateDelta mapping.
- [`engineering-records-v0.md`](engineering-records-v0.md) — InvestigationReport / DecisionRecord / ExperimentRecord archive contracts.

## Tools

- [`tool-surface-v0.md`](tool-surface-v0.md) — agent-visible TEP environment tools and authority classes.
- [`tool-bridge-v0.md`](tool-bridge-v0.md) — allowlisted adapters to mature analysis/optimization/graph tools with nested budget accounting.

## Research workflows

- [`rca-v0.md`](rca-v0.md) — blind root-cause investigation contract.
- [`hazop-v0.md`](hazop-v0.md) — later simulation-backed HAZOP contract.
- [`recovery-v0.md`](recovery-v0.md) — later counterfactual recovery-planning contract.
- [`autoresearch-v0.md`](autoresearch-v0.md) — later frozen-evaluator autonomous engineering experiment loop.

## Benchmark / evaluation

- [`benchmark-design-v0.md`](benchmark-design-v0.md) — scenario-family design, identifiability pilot, difficulty, partitioning, leakage controls, and strong C0 baseline.
- [`evaluation-v0.md`](evaluation-v0.md) — environment/runtime/task/scientific-behavior metrics plus canonical capability and orchestration ablations.

The lab owns TEP/domain-policy adapters and evaluation logic. It does not reimplement TEP physics or generic runtime mechanics.
