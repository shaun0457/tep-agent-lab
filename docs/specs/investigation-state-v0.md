# Investigation State v0

Status: accepted  
Owner repo: `tep-agent-lab`  
Implements: `industrial-agent-runtime` `TaskStateStore`

## Goal

Define the first domain state used by the TEP lab without making the generic runtime understand TEP/RCA fields, and define exactly how model-proposed reasoning state and deterministic tool results enter that state.

v0 implements **RCA state first**. HAZOP, Recovery, and AutoResearch may reuse a small generic shell later but receive their own domain-state schemas when implemented.

## Generic/runtime boundary

`industrial-agent-runtime` owns:

```text
TaskStatus
StateDelta envelope
ModelStateUpdateProposal
ModelTurn
ContextProjection
TaskStateStore protocol
```

`tep-agent-lab` owns RCA-specific fields, legal operation names, deterministic result-ingestion mapping, and domain/ref validation.

The runtime Coordinator calls the interface; it never imports `RcaState`.

## `RcaState`

```text
RcaState
  investigation_id
  case_id?
  goal
  incident_ref
  current_time_ref?

  hypothesis_refs[]
  observation_refs[]
  evidence_link_refs[]
  open_questions[]

  planned_experiment_refs[]
  completed_experiment_refs[]
  delegated_task_refs[]

  current_best_explanation?
  uncertainty_summary?
  safety_state_ref?

  artifact_refs[]
  conclusion_ref?

  generic_status: TaskStatus
  revision
```

Budget authority remains in generic runtime `Budget`/budget accounting. The lab may expose a compact budget summary in the projection but does not maintain a competing authoritative budget counter.

## Generic status

Use runtime status only:

```text
RUNNING
WAITING
READY
DONE
FAILED
EXHAUSTED
CANCELLED
```

RCA-specific investigation phase, if useful for reporting, is metadata rather than a second authoritative status machine.

The Main Agent cannot set `generic_status` directly through a model-proposed
`StateDelta`. Finish/status transitions are runtime-controlled from validated
`FinishProposal`, hard-stop, failure, cancellation, or exhaustion logic through
the generic `TaskStateStore.transition_status(status, expected_revision)`
protocol. The runtime never names a lab-owned status operation.

## Observation versus evidence

The state explicitly separates what the system observed from what the Agent uses as evidence.

### `ObservationRecord`

Every **successful agent-visible ToolResult/simulator/analysis result** is automatically registered during deterministic result ingestion as an immutable observation/result reference:

```text
ObservationRecord
  observation_id
  producer_request_ref
  tool_or_service_ref
  summary
  artifact_refs[]
  information_refs[]
  provenance
  visibility
  created_at
```

Registration is automatic, not relevance-filtered by the model. This is required so evaluation can distinguish:

- queries made;
- observations produced;
- observations actually linked as evidence;
- unused/irrelevant work.

Failed/denied tool calls remain trace events/results but do not create successful `ObservationRecord`s.

An observation is not automatically supporting evidence.

### `HypothesisEvidenceLink`

A model/application may propose an explicit relation from an existing visible observation to a hypothesis/claim:

```text
hypothesis_ref
observation_ref
relation: SUPPORT | CONTRADICT | CONTEXT | NEUTRAL
strength?
reason_summary
producer
```

The deterministic state validator checks ref existence/visibility/provenance. It does not decide open-ended scientific truth.

This separation enables distinct metrics for irrelevant queries, unused observations, and evidence quality.

## RCA StateDelta operations

The consumer owns an allowlisted set of operations. Initial v0 operations are:

### Model-proposable operations

```text
ADD_HYPOTHESIS
UPDATE_HYPOTHESIS
ADD_EVIDENCE_LINK
ADD_OPEN_QUESTION
UPDATE_OPEN_QUESTION
PLAN_EXPERIMENT
ADD_EXPERIMENT_INTERPRETATION
ADD_DELEGATED_TASK_REF
UPDATE_WORKING_EXPLANATION
SET_CONCLUSION_REF
```

Each model-proposed operation is carried inside runtime `ModelStateUpdateProposal` and is bound to the exact `ContextProjection.base_revision` seen by that turn.

### Deterministic ingestion/runtime operations

```text
REGISTER_OBSERVATION
REGISTER_COMPLETED_EXPERIMENT
REGISTER_SUBTASK_RESULT
REGISTER_ARTIFACT_REF
```

These operations are produced by deterministic adapters/runtime logic, not free model prose.

The consumer MAY add narrowly specified operations later, but coding agents must not invent unregistered state mutation routes.

## Hypothesis state

`hypothesis_refs` point to typed `Hypothesis` objects defined in `hypothesis-experiment-v0.md`.

`ADD_HYPOTHESIS` materializes/records the validated typed Hypothesis object and appends its ref. State may project compact active summaries but does not duplicate full hypothesis history.

## Open questions

```text
OpenQuestion
  question_id
  question
  why_it_matters
  related_hypotheses[]
  resolvable_by: TOOL | EXPERIMENT | SUBTASK | EXTERNAL_KNOWLEDGE | HUMAN
  priority
  status
```

Questions are useful working state, but evaluation must not reward the Agent for creating trivial questions merely to close them.

## Experiment state

Planned and completed experiments are referenced separately.

`PLAN_EXPERIMENT` is model-proposed internal task state. Completion/results are registered only after deterministic execution/result ingestion.

Exact duplicate detection is a lab pre-execution policy over canonical experiment keys; it is not a generic runtime Coordinator responsibility.

## Delegation state

Each `delegated_task_ref` points to runtime subtask trace/result metadata. Full subagent transcripts are not copied into RCA state.

Subtask completion is deterministically registered; any parent interpretation of the subtask's observations/claims becomes an explicit later model state update/evidence link.

## Working explanation

```text
WorkingExplanationUpdate
  leading_hypothesis_ref?
  current_rank_or_score_summary?
  key_evidence_link_refs[]
  key_counterevidence_link_refs[]
  remaining_uncertainties[]

WorkingExplanation
  leading_hypothesis_ref?
  current_rank_or_score_summary?
  key_evidence_link_refs[]
  key_counterevidence_link_refs[]
  remaining_uncertainties[]
  last_updated_revision
```

It is working state, not final truth.

`UPDATE_WORKING_EXPLANATION` carries the shared typed
`WorkingExplanationUpdate`. It may be proposed by the model but must reference
only currently visible/valid objects. The proposal does not supply a revision;
after atomic validation succeeds, the consumer materializes
`WorkingExplanation.last_updated_revision` as the resulting state revision.

## `TaskStateStore` implementation

The lab implements:

```text
revision() -> Revision
status() -> TaskStatus
project(policy) -> ContextProjection
apply_batch(deltas[], expected_revision) -> NewRevision
transition_status(status, expected_revision) -> NewRevision
```

A single-delta convenience `apply()` MAY wrap `apply_batch([delta], ...)`, but the batch contract is canonical.

### `apply_batch`

Lab validates the whole batch before mutation:

- target path/object/operation is allowlisted and legal for the producer class;
- refs exist and visibility/ownership are allowed;
- append-only records are not silently rewritten;
- transition does not expose evaluator-only truth;
- expected revision matches current revision;
- domain invariants for RCA state remain valid;
- model-proposed operations do not change runtime budgets/policies/generic authority/status directly.

A batch is atomic: all legal deltas apply and increment `revision` once, or none apply.

Every accepted/rejected update is retained as an append-only trace/state event.

### `transition_status`

Terminal lifecycle transitions are a generic runtime-to-store protocol call,
separate from model/result `StateDelta` batches. `status` is restricted to
`DONE | FAILED | EXHAUSTED | CANCELLED`; any current nonterminal state may move
to one of them. The consumer checks the exact expected revision, persists an
accepted/rejected event, and increments the state revision once on a new
terminal transition. Repeating the same terminal status at the exact current
revision is an idempotent no-op; overwriting it with a different terminal status
is rejected. `WAITING` and `READY` are not set through this terminal method. A
model cannot call or encode this transition through `SET_GENERIC_STATUS`.

## Model-proposed update semantics

A `ModelStateUpdateProposal` uses:

```text
base_revision = ContextProjection.base_revision
```

All contained deltas are interpreted against that same projection/state revision. The runtime/lab validates and atomically applies the batch before dispatching any tool/work action from the same ModelTurn.

If the update is stale or invalid, the same turn's action is not dispatched.

Examples:

```text
ADD_HYPOTHESIS(H3)
ADD_EVIDENCE_LINK(H3 <- observation O17)
PLAN_EXPERIMENT(E8)
UPDATE_WORKING_EXPLANATION(...)
```

These are internal reasoning-state proposals, not tool calls, and therefore do not consume `max_tool_calls`. Runtime accounts one `max_steps` unit for the proposal batch.

## Deterministic result-ingestion semantics

After post-execution result verification, the lab maps successful result objects to deterministic StateDelta batches.

### Successful ToolResult

At minimum:

```text
ToolResult
 -> REGISTER_OBSERVATION(ObservationRecord)
 -> optional REGISTER_ARTIFACT_REF(...)
```

All successful agent-visible ToolResults are registered; the model does not decide whether an observation is worth recording.

### Successful ExperimentResult

At minimum:

```text
ExperimentResult
 -> REGISTER_OBSERVATION(result summary/features)
 -> REGISTER_COMPLETED_EXPERIMENT(experiment/result refs)
 -> artifact refs
```

### Successful SubtaskResult

At minimum:

```text
SubtaskResult
 -> REGISTER_SUBTASK_RESULT(ref/status)
 -> referenced child observations/artifacts remain addressable
```

## Parallel WorkBatch revision semantics

Parallel work may originate from the same projection revision, but deterministic result-ingestion deltas do not reuse that old model revision.

For one completed scheduling wave:

1. post-execution verification completes for each returned work item;
2. successful result-ingestion batches are ordered deterministically by the WorkBatch ordering policy (default stable `work_id` order);
3. immediately before each batch is applied, the Coordinator supplies the then-current RcaState revision as `expected_revision`;
4. each accepted ingestion batch increments revision;
5. the resulting order/revisions are trace-visible.

Therefore three parallel results from revision 10 may deterministically apply as 10->11, 11->12, 12->13 rather than the second/third being rejected as stale.

This rebinding applies only to deterministic result/runtime ingestion. Model-proposed deltas always remain bound to the projection revision the model actually saw.

## Model-facing projection

The lab owns a deterministic/reference-aware projection function:

```text
project_rca_state(state, policy) -> ContextProjection
```

The projection may include:

- goal/incident summary;
- active hypotheses;
- selected recent/high-value observations/evidence links;
- unresolved high-priority questions;
- planned/completed experiment summaries;
- delegated-work summary;
- remaining runtime budget summary;
- relevant process/safety refs.

The generic runtime validates projection metadata/visibility/token limits but does not choose what chemical/process evidence is relevant.

Every model turn persists the exact immutable ContextProjection ref.

## v0 stopping semantics

There is no formal information-gain estimator in v0.

Stopping is:

```text
Main Agent FinishProposal
 -> deterministic structural verification
 -> READY/DONE or structured deficiency
```

Minimum deterministic readiness checks may include:

- output schema can be populated;
- no failed mandatory work remains active;
- no critical unresolved verifier error exists;
- selected cause/conclusion references valid visible evidence/observations;
- required experiment/result refs exist when the benchmark requires them;
- uncertainty/remaining questions are represented when conclusion is non-certain.

Whether the evidence is substantively sufficient is part of the Agent capability being evaluated. Formal expected-information-gain stopping is OPEN_RESEARCH and requires an explicit belief/probability model before implementation.

## Ground-truth isolation

Evaluator truth is stored outside agent-visible RcaState. The lab projection function and runtime visibility checks both fail closed on `EVALUATOR` refs.

## Persistence

v0 requires durable per-run state reconstruction from append-only state/result events plus artifacts.

Cross-incident retrieval/learned memory is out of scope for the first RCA benchmark.

## Invariants

- Conversation transcript is not canonical state.
- Runtime never imports RcaState.
- Generic runtime budget counters remain authoritative.
- Every successful agent-visible ToolResult is registered as an ObservationRecord.
- Observation is not automatically evidence.
- Evidence links are explicit model/application state updates.
- Model state updates are projection-revision-bound and atomic.
- Deterministic result ingestion binds the current revision at apply time.
- Hidden truth is not agent-visible state.
- Subagent transcripts are not merged into parent state.
- Final report is linked to the exact state revision and evidence/experiment refs used.

## Acceptance tests

1. Initialize blind RCA state without hidden truth leakage.
2. Apply a valid multi-delta ModelStateUpdateProposal through TaskStateStore without runtime importing RcaState.
3. Reject a stale model state-update batch and verify its same-turn tool request does not execute.
4. Verify atomicity: one invalid delta rejects the whole model state-update batch.
5. Execute a successful ToolResult and automatically register exactly one ObservationRecord without an evidence link.
6. Add a HypothesisEvidenceLink later and verify observation/evidence metrics remain distinguishable.
7. Plan an experiment by model delta, execute it, and register completion by deterministic ingestion.
8. Complete a subtask and retain only SubtaskResult/ref metadata.
9. Apply two or more parallel WorkBatch result-ingestion batches in deterministic order without stale-revision conflict.
10. Materialize an exact bounded ContextProjection with no EVALUATOR refs.
11. Reconstruct final RcaState from saved events deterministically.
12. Reject final readiness when required evidence/artifact refs are missing.
