# Hypothesis and Experiment Contracts v0

Status: accepted  
Owner repo: `tep-agent-lab`

## Goal

Represent hypotheses, predictions, experiments, deterministic results, and model interpretation as first-class typed objects so investigation/evaluation does not depend on free-form chat history.

All model-authored changes to investigation state are carried through runtime `ModelStateUpdateProposal` / `StateDelta`; they are not implicit effects of prose.

## `Hypothesis`

```text
Hypothesis
  hypothesis_id
  investigation_id
  claim
  hypothesis_type
  scope_refs[]
  status
  prior_weight?
  current_rank_or_score?
  supporting_evidence_link_refs[]
  contradicting_evidence_link_refs[]
  experiment_refs[]
  assumptions[]
  falsification_prediction_refs[]
  created_by
  created_at
  last_updated_revision
```

### Status

```text
PROPOSED
ACTIVE
SUPPORTED
WEAKENED
REJECTED
UNTESTABLE
UNRESOLVED
```

### Types

```text
ROOT_CAUSE
MECHANISM
PROCESS_RELATION
RECOVERY_EFFECT
HAZOP_CAUSE
RESEARCH_IDEA
```

Types are semantic metadata, not agent roles.

## Observation/evidence relationship

Every successful agent-visible ToolResult/simulator result is deterministically registered as an immutable `ObservationRecord` by the lab result-ingestion path defined in `investigation-state-v0.md`.

Observation registration is automatic; evidence creation is not.

Evidence for a hypothesis is an explicit `HypothesisEvidenceLink` from a visible observation to the hypothesis:

```text
HypothesisEvidenceLink
  hypothesis_ref
  observation_ref
  relation: SUPPORT | CONTRADICT | CONTEXT | NEUTRAL
  strength?
  reason_summary
  producer
```

The model proposes such a link through:

```text
StateDelta(operation=ADD_EVIDENCE_LINK, ...)
```

inside a runtime `ModelStateUpdateProposal` bound to the exact projection revision it saw.

Deterministic state validation checks ref existence/visibility/provenance and legal operation shape, not open-ended scientific correctness.

## `Prediction`

Experiments require machine-readable expected outcomes when practical.

```text
Prediction
  prediction_id
  hypothesis_ref
  variable_ref
  feature
  window_or_horizon
  expected_value_or_range
  tolerance?
  preprocessing_ref?
  metric_ref?
  conditions[]?
```

Initial feature vocabulary:

```text
DIRECTION
DELTA
PEAK
MINIMUM
LAG
ONSET_TIME
SETTLING_TIME
STEADY_STATE_RANGE
INTEGRATED_ERROR
CORRELATION
TRAJECTORY_DISTANCE
EVENT_OR_SHUTDOWN
```

Examples:

```text
H1 / XMEAS_9 / DIRECTION / next 30 min / INCREASE
H1 / XMEAS_21 / LAG / next 60 min / [2 min, 8 min]
H2 / reactor_temperature / PEAK / next 30 min / [124 C, 128 C]
```

When no supported deterministic feature can represent an expected outcome, the field may be explicitly `QUALITATIVE_UNSCORED`; such a prediction is not used for deterministic discrimination metrics.

## `ExperimentProposal`

```text
ExperimentProposal
  experiment_id
  investigation_id
  goal
  hypothesis_refs[]
  experiment_type
  rationale
  discriminating_question
  prediction_refs[]
  scenario_or_intervention
  required_input_refs[]
  requested_tools[]
  seed_policy
  horizon
  metrics[]
  budget_request
  safety_constraints[]
  status
```

### Experiment types

```text
COUNTERFACTUAL_ROLLOUT
PARAMETER_SWEEP
SENSITIVITY_ANALYSIS
SIGNAL_ANALYSIS
RULE_VALIDATION
RECOVERY_COMPARISON
AUTORESEARCH_TRIAL
```

A model-created `ExperimentProposal` becomes planned investigation state through:

```text
StateDelta(operation=PLAN_EXPERIMENT, value_or_ref=ExperimentProposal)
```

The planned state change is internal task state only. It does not execute the experiment.

## Discriminating-experiment principle

A useful RCA experiment should identify which competing predictions differ and what result would distinguish them.

Conceptually:

```text
H1 -> Prediction P1
H2 -> Prediction P2

Experiment E
  measures features supporting P1/P2
  -> deterministic result features
  -> compare result against P1/P2 tolerances
```

A discrimination metric should use typed predictions/feature distances and benchmark-defined plausible competitors rather than free-text LLM grading.

## Experiment compilation

A proposal is data until validated/compiled:

```text
planned ExperimentProposal
 -> runtime G0-G3 for requested executable tool/work
 -> lab validate_request
 -> capability/scenario/tool resolution
 -> frozen ExperimentRunSpec
 -> isolated execution
 -> deterministic ExperimentResult
 -> post-execution verify_result
 -> deterministic result ingestion
```

## `ExperimentRunSpec`

```text
ExperimentRunSpec
  run_spec_id
  experiment_id
  parent_state_content_checksum
  branch_or_snapshot_ref
  resolved_tool/config versions
  resolved_interventions
  seeds_or_seed_policy
  horizon
  metric/scorer versions
  resource_limits
  canonical_experiment_key
```

### Canonical experiment identity

Opaque snapshot IDs alone must not define duplication identity.

`canonical_experiment_key` is derived from normalized content such as:

```text
hash(
  parent_state_content_checksum,
  resolved_interventions/scenario,
  tool/config versions,
  horizon,
  seed policy,
  metric/scorer versions,
  relevant preprocessing
)
```

Lab pre-execution policy owns exact duplicate blocking because generic runtime does not understand experiment semantics.

## `ExperimentResult`

```text
ExperimentResult
  experiment_id
  run_spec_ref
  status
  metric_values
  prediction_evaluations[]
  observation_refs[]
  rollout/artifact_refs[]
  safety_summary_ref?
  failure_class?
  cost_usage
  started_at
  completed_at
```

`prediction_evaluations` are deterministic where feature extraction/tolerance checking is supported:

```text
prediction_ref
observed_feature
match_status: MATCH | CONTRADICT | INCONCLUSIVE | UNSCORED
metric_distance?
```

The model does not author numeric simulator/tool/scorer values that deterministic components can compute.

After verification, the lab result-ingestion path deterministically registers the result/observations and moves the experiment from planned to completed state using runtime-bound current revision semantics.

## Model interpretation

After execution, the Main Agent may propose:

```text
ExperimentInterpretation
  interpretation_id
  experiment_ref
  proposed_evidence_links[]
  hypothesis_updates[]
  conclusion_summary
  residual_uncertainty
  next_questions[]
```

Interpretation is never substituted for the deterministic `ExperimentResult`.

### Interpretation-to-state mapping

`ExperimentInterpretation` is not a hidden side effect. The lab maps its model-proposed contents to explicit RCA StateDelta operations inside the same `ModelStateUpdateProposal`, for example:

```text
proposed_evidence_links[]
  -> ADD_EVIDENCE_LINK

hypothesis_updates[]
  -> UPDATE_HYPOTHESIS

next_questions[]
  -> ADD_OPEN_QUESTION

conclusion_summary / residual_uncertainty
  -> UPDATE_WORKING_EXPLANATION

interpretation object itself
  -> ADD_EXPERIMENT_INTERPRETATION
```

All such deltas are bound to the `ContextProjection.base_revision` the model saw and are atomically validated/applied by the consumer TaskStateStore.

A rejected/stale interpretation update does not silently mutate hypothesis/evidence state.

## Duplicate / low-value experiment control

Before execution, the **lab pre-execution validator**, not generic Coordinator, may inspect prior run-log/Experiment Ledger views for:

- identical canonical experiment key;
- exhausted parameter region;
- equivalent question already tested;
- insufficient expected discrimination;
- budget disproportionate to configured value policy.

v0 MUST block exact canonical duplicates. Semantic redundancy detection may initially warn/annotate and is an evaluated feature.

## Parameter-search boundary

For numerical optimization, the Agent specifies:

```text
search variables
bounds/constraints
objective(s)
why the variables matter
```

A deterministic/seeded optimizer Tool Bridge selects numeric trials within its reserved budget. Repeated LLM guessing of floating-point values is not the default architecture.

## Provenance

Every experiment binds:

- motivating hypothesis/question/predictions;
- parent state/snapshot content checksum;
- resolved environment/tool/library/scorer versions;
- seeds;
- rule/policy versions used for validation;
- artifacts/results;
- proposer/interpreter identity;
- model state-update proposal/ref used to persist interpretation;
- actual resource usage.

## Invariants

- Hypotheses are working claims, not persistent rules.
- Every successful result may create observations automatically, but Observation is not automatically evidence.
- Evidence links and hypothesis interpretations are explicit model-proposed StateDelta operations.
- Prediction is typed when used for deterministic discrimination scoring.
- Proposed experiments never execute before deterministic validation/resource reservation.
- ExperimentResult is separate from model interpretation.
- Counterfactual experiments never mutate reference state.
- Failed/negative experiments remain in run history.

## Acceptance tests

1. Create two competing hypotheses through a model state-update batch and attach typed predictions.
2. Propose a discriminating ExperimentProposal and persist it with PLAN_EXPERIMENT without executing it.
3. Reject unsupported scenario before execution.
4. Freeze/execute a valid ExperimentRunSpec in an isolated branch.
5. Produce deterministic PredictionEvaluation values and automatically register the successful result observation(s).
6. Have the next ModelTurn return an ExperimentInterpretation whose proposed evidence links map to ADD_EVIDENCE_LINK StateDelta operations.
7. Reject a stale interpretation update without changing hypothesis/evidence state.
8. Reject an exact duplicate experiment using canonical experiment key even when a new snapshot ID was created from identical content.
9. Hand bounded numeric tuning to a SIMULATE optimizer bridge whose trial/rollout budget is pre-reserved.
