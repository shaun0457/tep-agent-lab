# Architecture — TEP Agent Lab

## Purpose

`tep-agent-lab` is the TEP-specific integration/research layer between the generic Agent Runtime and the trusted TEP world. It owns domain state/projection, tools/adapters, policy/rules, experiments, engineering records, benchmark fixtures, and evaluation.

## System boundary

```text
industrial-agent-runtime
  generic control plane
  ContextProjection
  Main Agent / ModelTurn
  ModelStateUpdateProposal + routed action
  pre-execution gates
  Executor
  post-execution verifier
  deterministic result ingestion
  WorkBatch / SubtaskResult
          |
          v
+------------------------------------------------+
|                 tep-agent-lab                  |
|                                                |
|  RcaState implements TaskStateStore            |
|  ObservationRecord / EvidenceLink              |
|  Hypothesis / Prediction / Experiment          |
|  Rule metadata: origin x validation x authority|
|  Tool Bridge + domain validators               |
|  Engineering Records                           |
|  benchmark / scoring / research workflows      |
+----------------------+-------------------------+
                       |
                       v
                    tep-sim
         ProcessGraph / VariableRegistry
         simulator truth / dynamics
         snapshot / fork / rollout / capability
```

`manufacturing-kg-agent` may later provide read-only evidence as an explicit capability condition.

## Runtime integration

The lab consumes runtime contracts rather than redefining them.

### State

Runtime knows:

```text
TaskStateStore
ContextProjection
StateDelta
ModelStateUpdateProposal
ModelTurn
TaskStatus
```

Lab implements these with `RcaState`, an allowlisted RCA StateDelta operation set, deterministic result-ingestion mapping, and a domain-aware `project_rca_state` function.

Runtime never imports RcaState fields.

### Model-proposed state path

```text
ContextProjection
 -> Main Agent / ModelTurn.state_update?
 -> lab validates RCA operation/ref/visibility
 -> atomic TaskStateStore.apply_batch
 -> same-turn executable action may continue only on success
```

Model-proposed updates are bound to the exact projection revision seen by the model and cannot change generic runtime budget/policy/status/authority or reference-world state.

### Executable request path

```text
ModelTurn.action
 -> runtime G0-G3
 -> lab validate_request
      + benchmark/experiment policy
      + tep-sim capability/bounds/control checks as needed
 -> Executor
 -> lab/tep-sim/Tool Bridge adapter
 -> post-execution verify_result
 -> deterministic result-ingestion StateDelta(s)
 -> lab TaskStateStore.apply_batch
```

Pre-execution request validation and post-execution result verification are different hooks.

## Deterministic result ingestion

Every successful agent-visible ToolResult is automatically registered as an immutable `ObservationRecord`.

```text
ToolResult
 -> REGISTER_OBSERVATION
 -> optional artifact refs
```

ExperimentResult additionally registers experiment completion/result refs. SubtaskResult registers delegated-task completion/ref metadata.

Observation registration is automatic; evidence creation is explicit.

For parallel WorkBatch results:

- verify each result;
- order successful ingestion batches deterministically by the runtime WorkBatch policy;
- bind the then-current RcaState revision immediately before each apply;
- apply each batch atomically;
- trace the exact order/revisions.

Thus parallel work started from one old projection does not create false stale conflicts during deterministic ingestion.

## Work planning

v0 uses runtime dependency-aware WorkBatch only:

```text
WorkItem(kind=TOOL | SUBTASK, depends_on=[...])
```

There are no lab-specific ANALYSIS/MERGE graph nodes.

- deterministic analysis is a TOOL;
- open-ended merge/integration is the next Main Agent turn;
- simulation is a TOOL with side-effect class SIMULATE;
- full mutable Dynamic DAG replanning is later research.

## Information Plane

The program Information Plane is a logical ownership/ref layer, not multiple mandatory databases.

### Environment-owned

From `tep-sim`:

- ProcessGraph / DEXPI semantics;
- VariableRegistry;
- simulator capabilities/constraints;
- snapshots/rollouts/safety artifacts.

### Lab-owned/domain views

- RcaState;
- ObservationRecords;
- HypothesisEvidenceLinks;
- Hypotheses/Predictions/Experiments;
- lab Rule/policy metadata;
- benchmark fixtures/scorers;
- Engineering Records.

### v0 persistence

One append-only run log plus artifact files may back all logical views:

```text
runs/<run_id>/manifest.json
events.jsonl
artifacts/
investigation-report.json
```

Do not create separate Evidence/Experiment/Trace databases without a demonstrated need.

## Observation / evidence semantics

```text
successful ToolResult / simulator result
 -> ObservationRecord

ObservationRecord
 + explicit ADD_EVIDENCE_LINK state update
 -> HypothesisEvidenceLink
```

Observation is not automatically evidence.

This supports separate measurement of:

- irrelevant queries;
- unused observations;
- valid/invalid evidence links;
- evidence quality.

## Hypothesis / experiment interpretation semantics

Hypotheses and planned experiments are explicit model-proposed state objects.

After deterministic ExperimentResult creation, the Main Agent may return an `ExperimentInterpretation`; the lab maps its contents to explicit StateDelta operations such as:

```text
ADD_EVIDENCE_LINK
UPDATE_HYPOTHESIS
ADD_OPEN_QUESTION
UPDATE_WORKING_EXPLANATION
ADD_EXPERIMENT_INTERPRETATION
```

No interpretation mutates state merely because it appeared in model prose.

## Rule / knowledge semantics

Canonical metadata:

```text
origin × validation × authority
```

K0–K4 is shorthand only.

- simulator/runtime constraints may carry HARD_GATE authority within their scope;
- reviewed lab policies are explicit `origin=POLICY` rules;
- simulation-validated relationships are usually ADVISORY/PLANNING, not automatic HARD_GATE;
- literature/Agent claims cannot self-promote authority.

## Tool layers

### Environment/read tools

```text
get_current_observation
get_history
get_variable_metadata
get_process_node / neighbors / upstream / downstream
get_related_measurements
get_related_actuators
get_safety_margins
snapshot / fork
check_scenario_capability
compile_process_deviation
run_rollout
```

Canonical `get_related_disturbances`/IDV answer enumeration is not in the default blind RCA allowlist.

### Tool Bridge

First RCA bridge set:

```text
response-feature extraction
trajectory comparison
cross-correlation / lag
optional upstream TEP detector baseline
```

Later concrete studies may add PCA/PLS, sensitivity, optimizer, or other libraries.

Bridge implementations are allowlisted/pinned, but runtime gates/budget/authority remain upstream of the bridge.

A bridge that internally runs simulator trials is SIMULATE and consumes explicit nested rollout/horizon/trial budget.

### Recovery/MUTATE tools

Later only:

```text
propose_intervention
validate_intervention
apply_validated_intervention
```

`apply_validated_intervention` is MUTATE and requires expected reference-state revision binding. It is disabled in blind RCA/AutoResearch by default.

## RCA workflow

```text
incident
 -> RcaState
 -> observations
 -> competing Hypotheses + Predictions
 -> explicit EvidenceLinks
 -> discriminating analysis/counterfactual ExperimentProposal
 -> frozen ExperimentRunSpec
 -> deterministic ExperimentResult / PredictionEvaluation
 -> deterministic result ingestion
 -> Main Agent ExperimentInterpretation
 -> explicit interpretation StateDelta batch
 -> structured CausalClaim
 -> InvestigationReport
```

The research target is investigation behavior relative to strong deterministic baselines, not fault-label recall.

## Mandatory C0 baseline

First-family RCA includes evaluator-side:

```text
enumerate candidate causes
 -> compile/rollout
 -> deterministic trajectory/feature match
 -> best candidate or NO_ABNORMAL_CAUSE
```

If C0 solves a case cheaply/reliably, that is evidence an Agent is unnecessary for that case.

## Later HAZOP / Recovery / AutoResearch

Existing proposal specs define later task-family direction but do not block first RCA implementation.

### HAZOP

Must preserve supported/unsupported capability honesty; not a claim of formal plant HAZOP completion.

### Recovery

Simulate candidate strategies first. SIMULATE never mutates reference state; any reference application is MUTATE.

### AutoProcessResearch

Separate later task family with frozen evaluator/search surface, deterministic scoring, explicit nested simulation budget, append-only experiment history, and hidden evaluation.

It is not another orchestration-ablation row.

## Engineering records

Every completed RCA produces a typed InvestigationReport tied to final state revision/evidence/experiment/trace refs.

DecisionRecord and ExperimentRecord preserve meaningful decisions/experiments.

LessonLearned/Runbook/manual promotion is deferred. v0 records are archive-only and are not automatically retrieved into later benchmark contexts.

## Evaluation architecture

```text
fixture/hidden truth --------------------> evaluator
       |
       v
Agent-visible ContextProjection/tool policy
       |
       v
runtime + lab + tep-sim
       |
       v
run log / state / observations / experiments / report
       |
       +---------------------------------> evaluator
```

`evaluation-v0.md` is the only canonical comparison matrix.

- Capability axis excludes subagents.
- Orchestration axis holds tool exposure fixed and includes WorkBatch/subagent conditions.
- AutoResearch is separate task-family evaluation.

## Key invariants

- evaluator truth/candidate set never enters blind Agent-visible refs/tools;
- runtime/lab state boundary is TaskStateStore, not imports;
- model-proposed task-state changes are explicit and revision-bound;
- successful ToolResult registration as Observation is automatic;
- Observation != Evidence;
- deterministic ExperimentResult != Agent interpretation;
- interpretation updates state only through explicit typed deltas;
- Tool Bridge != authorization layer;
- SIMULATE != MUTATE;
- compound simulator tools expose nested budget usage;
- conversation history is not canonical state;
- Engineering Records do not automatically become future context/rules;
- unsupported physics stays unsupported;
- failures/negative experiments remain traceable;
- rules/tools/scorers/context projections are versioned for reported runs.


## Observatory / application UI boundary

The interactive Observatory is a client of the application plane, not part of the canonical industrial state model.

See `docs/decisions/ADR-002-ui-application-boundary.md`.

Target direction:

```text
Tauri v2 desktop UI
TypeScript + Vite + React
          |
          v
thin transport adapter
          |
          v
ApplicationViewService (Python)
          |
          v
P0 RunQueries
 /        |         \
Process  Telemetry  Artifacts
Graph
```

Ownership is strict:

- frontend owns rendering, navigation, selection, and transient UI state;
- the Tauri Rust host owns native shell/process/permission integration only;
- `ApplicationViewService` owns bounded application read assembly;
- P0 owns run/projection truth;
- ProcessGraph owns semantic/topology truth;
- telemetry/artifact contracts own dynamic data.

The UI must not read `TEPEnvironment`, `ReferenceWorld`, fixture files, artifact filesystem paths, or private RunManager/session state directly.

E0/E0.1 self-contained HTML remains a supported reproducible research/report client. It is not the target data-access architecture for the future interactive product.

The current industrial backend remains Python. Rust is not a replacement backend merely because Tauri is selected, and Go is not introduced without a separate distributed control-plane need.

The decoupling sequence is:

```text
E0.2A  transport-neutral Python ApplicationViewService
E0.2B  thin replaceable local transport
E0.2C  Tauri v2 + TypeScript/Vite client
E1     interactive Industrial Observatory
```

Future Context Layer views may join the same application boundary; they must not be embedded as frontend-owned domain state.

## Industrial context and telemetry plane (P1)

Owning contracts: `docs/decisions/ADR-003-industrial-environment-boundary.md` and `docs/specs/plant-telemetry-contract-v0.md` (semantics); `docs/decisions/ADR-004-p1-milestone-rebaseline.md` (sequencing and the P1.2/P1.3 boundary). This section summarizes; it defines nothing new.

```text
Plant Context Plane        ProcessGraph (tep-sim) -> Canonical Plant Context revision
                           (PlantContextRevision, first implemented in P1.3)
Engineering Knowledge      DocumentRef / KnowledgeRef scoped to plant semantics;
                           structured claims stay in RuleRegistry / RuleRef;
                           P1.2 Context Compiler output is defined by
                           context-compiler-v0 (PR #24, not yet accepted)
Telemetry Plane            TelemetrySource -> TelemetryIngestor -> TimeSeriesStore
                           -> TimeSeriesReader, pinned by TelemetryReadSnapshot (T, K)
        |
        v
ContextSnapshot (derived, pinned refs) + RcaState revision
        -> InvestigationContextView -> Application reads / Agent ContextProjection
```

Implemented today:

- `telemetry.py` (P1.1A): stdlib-only generic core; T and K apply before any ordering, reduction or count;
- `tep_telemetry.py` (P1.1B): the only module that knows TEP identifiers on this path; canonical `signal_id` is the ProcessGraph semantic entity id;
- neither is used by a production path yet. Application and Agent telemetry still read `ReferenceWorld.history()`; P1.1C and P1.5 migrate them.

Invariants kept from the owning contracts:

- generic contracts never import TEP; TEP adapter -> generic contract only;
- `CanonicalContextRegistry` remains the run-scoped attested source inventory; published context revisions enter a run only through prepare-time registration;
- no second ProcessGraph, RuleRegistry, RcaState store or "latest context" resolver;
- `Observation != Evidence`; Application API != Agent Tool Surface.
