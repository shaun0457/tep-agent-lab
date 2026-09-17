# Roadmap — TEP Agent Lab

Phase 0 Design Freeze is complete. The lab may now implement the first RCA information/tool/evaluation slice in dependency order.

Canonical specs live in `docs/specs/`.

## Phase 0 — Reproducible lab / benchmark shell

Specs: `evaluation-v0.md`, `benchmark-design-v0.md`

Deliver:

- pinned `tep-sim` and `industrial-agent-runtime` revisions;
- versioned case/scenario-family schema;
- development/research/hidden-eval partitions;
- evaluator-only vs Agent-visible projection;
- run/artifact manifest;
- leakage audit;
- deterministic re-scoring;
- first identifiability pilot utilities.

Exit: a minimal/fake run can be projected, traced, re-scored, and leakage-tested reproducibly.

## Phase 1 — RcaState / Information Plane

Branch: `feat/investigation-state-v0`  
Specs: `investigation-state-v0.md`, `engineering-records-v0.md`

Deliver:

- RcaState implementing runtime TaskStateStore;
- allowlisted RCA StateDelta operations;
- atomic `apply_batch` revision/visibility validation;
- model-proposed state-update path;
- automatic ObservationRecord registration for successful agent-visible ToolResults;
- explicit EvidenceLink lifecycle;
- deterministic result-ingestion order for parallel WorkBatch results;
- append-only run log/artifacts;
- ContextProjection;
- InvestigationReport / DecisionRecord / ExperimentRecord.

Exit: one investigation is reconstructable without relying on chat transcript as canonical state, and model/tool-derived state changes have one unambiguous route.

## Phase 2 — Rule / policy metadata

Branch: `feat/rule-registry-v0`  
Spec: `knowledge-rule-registry-v0.md`

Deliver:

```text
origin × validation × authority
```

with a small representative rule/policy set, provenance/versioning, and hard-authority restrictions.

Do not implement the full promotion engine yet.

## Phase 3 — Hypothesis / Prediction / Experiment

Branch: `feat/hypothesis-experiment-v0`  
Spec: `hypothesis-experiment-v0.md`

Deliver:

- Hypothesis;
- typed Prediction;
- EvidenceLink state operations;
- ExperimentProposal;
- frozen ExperimentRunSpec;
- deterministic ExperimentResult / PredictionEvaluation;
- ExperimentInterpretation -> StateDelta mapping;
- canonical experiment duplicate key.

Exit: two competing hypotheses can be tested by a traceable discriminating experiment and updated only through explicit typed state changes.

## Phase 4 — TEP environment tools + minimal Tool Bridge

Branches:

```text
feat/tool-surface-v0
feat/tool-bridge-v0
```

Specs: `tool-surface-v0.md`, `tool-bridge-v0.md`

### Environment tools

- observations/history;
- ProcessGraph/topology without canonical answer leakage;
- snapshot/fork/rollout;
- capability/safety;
- no MUTATE in blind RCA.

### Initial bridge

Start minimal and benchmark-driven:

- response features / trajectory comparison;
- cross-correlation / lag;
- optional upstream TEP detector baseline.

Sensitivity/optimization dependencies are introduced only when later task families require them.

Exit: runtime can inspect/analyze/simulate TEP only through typed, versioned, leakage-audited tools; arbitrary Agent Python/shell/import is unavailable.

## Phase 5 — Benchmark identifiability + C0

Branch: `exp/rca-benchmark-pilot-v0`  
Specs: `benchmark-design-v0.md`, `evaluation-v0.md`, `rca-v0.md`

Deliver:

- scenario variants;
- strong deterministic enumerate/simulate/match C0;
- healthy/no-abnormal case;
- topology/candidate leakage audit;
- data-informed difficulty;
- at least one non-local/nontrivial case for medium/hard claims.

## Phase 6 — Blind RCA capability ladder

Branch: `exp/rca-reactor-v0`

Canonical capability progression:

```text
C1 static LLM
 -> C2 telemetry
 -> C3 topology
 -> C4 analysis bridge
 -> C5 counterfactual simulation
```

Subagents are not a capability row.

Exit: at least one blind incident is reproducibly investigated with evidence-backed hypotheses, typed predictions, and counterfactual results.

## Phase 7 — Orchestration architecture ablation

Branch: `exp/orchestration-ablation-v0`

Hold capability/tool exposure constant and compare:

```text
O0 one-shot
O1 ReAct
O2 fixed workflow
O3 Hybrid reference loop
O4 O3 + dependency-aware TOOL WorkBatch
O5 O4 + bounded SUBTASK work
```

Measure task quality, scientific behavior, state/tool/rollout/subtask overhead, and stopping efficiency.

## Phase 8 — Simulation-backed HAZOP

Branch: `exp/hazop-reactor-v0`  
Spec: `hazop-v0.md`

Start with reactor/cooling subsystem and a small supported/unsupported deviation pack.

## Phase 9 — Recovery planning

Branch: `exp/recovery-reactor-v0`  
Spec: `recovery-v0.md`

Rank forked strategies first. Enable reference application only after revision-bound MUTATE/gate tests and explicit benchmark policy permit it.

## Phase 10 — AutoProcessResearch

Branch: `exp/autoresearch-recovery-v0`  
Spec: `autoresearch-v0.md`

Prerequisites: stable recovery objective, scenario split, Tool Bridge optimizer path, Experiment Ledger/run history, and hidden evaluation.

## Phase 11 — Knowledge / organizational-memory research

Only after clean no-KG/no-memory baselines:

- manufacturing-kg-agent read-only evidence;
- literature/document rule candidates;
- validation/promotion workflow if needed;
- structured Engineering Record retrieval across incidents;
- Lesson Learned / Runbook proposal studies.

## Phase 12 — Benchmark freeze / expansion

Freeze representative versioned packs across healthy/negative, RCA difficulty, orchestration, HAZOP, recovery, AutoResearch, and optional knowledge/memory conditions.

## Not on the critical path

- P&ID OCR/model generation;
- 3D visualization;
- plant-wide formal HAZOP automation;
- learned cross-run Agent memory before explicit study;
- unrestricted recursive swarms;
- production deployment control authority;
- full Dynamic DAG/LangGraph/MCP infrastructure without measured need.
