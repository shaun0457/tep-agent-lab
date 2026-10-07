# Roadmap — TEP Agent Lab

Phase 0 Design Freeze is complete. The lab may now implement the first RCA information/tool/evaluation slice in dependency order.

Canonical specs live in `docs/specs/`.



## Observatory / UI decoupling track

This track is intentionally separate from the scientific RCA capability ladder. It turns the current developer/research observatory into a replaceable client architecture without rewriting the industrial backend.

Architecture decision: `docs/decisions/ADR-002-ui-application-boundary.md`.

### E0 / E0.1 — Static observatory and process overlay — complete

- self-contained reproducible HTML report;
- P&ID-style presentation geometry;
- canonical ProcessGraph binding;
- P0 telemetry/artifact projection;
- file:// developer/research inspection.

The static report remains supported.

### E0.2A — Application View Service — complete

Implement a transport-neutral Python application read boundary on top of public P0/application queries.

Initial read models:

- Run;
- Entity;
- Signal;
- bounded SignalHistory.

Requirements:

- no direct simulator/private-session access;
- no duplicate ProcessGraph/signal registry;
- no raw artifact paths;
- bounded result sizes;
- Agent visibility preserved;
- static E0/E0.1 remains green.

If arbitrary signal history is impossible through the current owning contracts, stop with `SPEC_CONFLICT` and change the owning P0 contract explicitly rather than bypassing it.

### E0.2B — Thin local transport — complete

After E0.2A contracts are stable, expose them through a narrow replaceable local transport.

The versioned JSON dispatcher wraps an already-constructed `ApplicationViewService`
and exposes only its four reads. No HTTP, Tauri, standalone process, or run
attachment is introduced. E0.2C owns process lifecycle and IPC framing.

Contract and error codes: `docs/e0-2b-application-transport.md`.

### E0.2C — Tauri desktop client — complete

#### E0.2C1 — Python desktop backend process — complete

- shared deterministic E0 developer-demo bootstrap with the static report;
- one persistent Python-owned P0 session and ApplicationTransport;
- bounded NDJSON stdin/stdout, protocol-only stdout and diagnostic stderr;
- existing four AGENT-visible application reads, no new domain/protocol methods.

Contract: `docs/e0-2c-desktop-backend.md`.

#### E0.2C2A — Tauri/TypeScript/Rust live shell — complete

- React/TypeScript/Vite development shell with four demonstration reads;
- one narrow Tauri v2 command and Rust-owned persistent Python process;
- serialized, correlated, bounded NDJSON and deterministic shutdown;
- real Python bridge and headless Tauri command tests; desktop CI validation.

Contract and development setup: `docs/e0-2c-tauri-shell.md`.

#### E0.2C2B — Packaged Python sidecar — complete

- PyInstaller backend with embedded exact build provenance and canonical pins;
- Rust-owned release externalBin launch, shared bounded process bridge;
- Windows x86_64 frozen four-read smoke and unsigned NSIS CI artifact;
- source/development Python workflow remains available.

Contract: `docs/e0-2c-packaged-sidecar.md`.
Architecture = platform-neutral; C2B validation matrix = Windows x86_64 only.
macOS/Linux packaging is deferred.

**E0.2 COMPLETE = MVP UI is no longer an architectural dependency.**
Static E0 HTML and Tauri Desktop are replaceable clients of Application/P0.
E1 remains the richer Industrial Observatory UI and is not complete.

Target UI stack:

- Tauri v2;
- TypeScript;
- Vite;
- React for component/state management.

Tauri Rust is initially limited to:

- desktop lifecycle;
- permissions/OS integration;
- Python service lifecycle;
- narrow IPC/process supervision;
- packaging.

Do not port P0, ProcessGraph, Agent runtime, simulator integration, or Context Layer into Rust.

### E1 — Interactive Industrial Observatory

Build richer interaction on top of the application boundary:

- process/P&ID view;
- on-demand signal explorer;
- bounded historical telemetry;
- run and Agent event views;
- branch/counterfactual inspection.

The UI must request information on demand rather than preload a giant industrial-world payload.

### Later

Context Layer views can join the same application boundary for SOP/manual/incident/asset context.

Go is not part of this track. A distributed Go control plane is a future independent decision if multi-tenant deployment/fleet orchestration creates a concrete need.


## D0 — Benchmark contract freeze — current

D0 is benchmark infrastructure, not a new Agent capability.

Owning spec: `docs/specs/benchmark-case-v0.md`.

### D0.0 — Contract freeze — complete after this documentation PR

Freeze:

- typed `BenchmarkCase` identity/version/partition;
- separate EVALUATOR-only ground truth;
- deterministic `AgentCaseProjection`;
- trusted `CaseSetupAttestation`;
- P0 benchmark binding/manifest fields;
- deterministic metric-vector scoring and re-scoring;
- hard-fail leakage audit.

D0 deliberately does **not** add C0, empirical difficulty labels, a real LLM,
multi-fault fixtures, UI features or new simulator capability.

### D0.1 — Benchmark contract implementation — complete

Implement one deterministic DEVELOPMENT RCA case proving the frozen contract through
the normal P0 lifecycle and fake-provider runtime path.

Implementation note: `docs/d0-benchmark-contract.md`.

Exit:

```text
typed fixture + truth
 -> deterministic Agent projection
 -> P0 prepare + setup attestation
 -> blind fake-provider run
 -> leakage audit
 -> deterministic saved-result re-score
```

After D0.1, add the healthy/variant/C0/identifiability pilot before making benchmark
difficulty or Agent-capability claims.

### D0.2A — Incident benchmark family expansion — complete

Three deterministic DEVELOPMENT incident fixtures (`rca-dev-001..003`) in the
`reactor-thermal-v0` family, with an explicit EVALUATOR-only fixture registry. These
are candidate fixtures only. No C0, identifiability or difficulty claim has been made.

Implementation note: `docs/d0-2a-incident-family.md`.

### D0.2B — Healthy negative contract + fixture — complete

`benchmark-setup/v1` adds an explicit `DISTURBANCE | NO_INTERVENTION` setup union while
the frozen `benchmark-setup/v0` incident fixtures stay byte-identical. One healthy
DEVELOPMENT fixture (`rca-dev-004`) has `NO_ABNORMAL_CAUSE` ground truth and an attested
no-intervention setup. This does not make the benchmark balanced.

Implementation note: `docs/d0-2b-healthy-negative.md`.

### D0.2C — C0 + identifiability pilot — next

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
