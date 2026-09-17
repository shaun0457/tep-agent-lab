# RCA Experiment v0

Status: proposal  
Version: v0  
Owner repo: `tep-agent-lab`

## Goal

Evaluate whether an Agent can investigate a hidden TEP incident by selecting relevant observations, traversing process structure, forming competing hypotheses, designing discriminating experiments, and producing an evidence-backed causal claim.

The target is investigation quality, not memorized fault-name recall.

## Scenario fixture

Each case separates evaluator truth from the Agent-visible projection:

```text
case_id
case_version
baseline_config
hidden_disturbance_schedule
reference_run_seed
incident_start_time
agent_visible_trigger
allowed_tool_policy
experiment_budget
scoring_config
ground_truth_causal_claim
```

Ground truth is evaluator-only in blind mode.

## Agent-visible start state

Example:

```text
incident_id
simulation_time
trigger/top abnormal signals
current safety summary
minimal local process seed
recent action summary
available tools/budgets
```

The Agent does not automatically receive all variables, canonical IDV labels, or a candidate-cause answer list.

## Structured causal output

Do not score an unconstrained free-text `selected_root_cause`.

### `CausalClaim`

```text
CausalClaim
  entity_ref?
  mechanism
  variable_or_actuator_ref?
  fault_family?
  direction_or_mode?
  description?
  confidence_or_rank
  supporting_evidence_link_refs[]
```

Initial mechanism vocabulary should be stable/versioned and broad enough to avoid exposing exact hidden case IDs, for example:

```text
FLOW_DISTURBANCE
TEMPERATURE_DISTURBANCE
COMPOSITION_DISTURBANCE
VALVE_STICKING
CONTROL_ACTION_OR_LOOP
REACTION_OR_PROCESS_DYNAMICS
MEASUREMENT_OR_SENSOR
MULTIPLE_OR_INTERACTING_CAUSES
OTHER_SUPPORTED_MECHANISM
NO_ABNORMAL_CAUSE
```

A benchmark may extend the vocabulary only by versioning the fixture/scorer.

Ground truth is represented using the same structured fields so matching is deterministic over canonical entity/mechanism/variable/family fields rather than LLM free-text judging.

The complete set of ground-truth candidates for a case/family is evaluator-owned and is not exposed as a multiple-choice list to the Agent.

## Required RCA result

```text
RcaResult
  incident_id
  ranked_hypothesis_refs[]
  selected_causal_claim: CausalClaim
  uncertainty
  remaining_questions[]
  evidence_link_refs[]
  experiment_refs[]
  investigation_report_ref
```

A prose explanation may accompany this structure for readability but is not the canonical scoring object.

## Investigation behavior

A run may include:

```text
observe
 -> query relevant topology/history
 -> generate competing hypotheses/predictions
 -> select discriminating analysis/experiment
 -> isolated fork/rollout where useful
 -> compare deterministic features/results to predictions
 -> update hypothesis ranking/evidence links
 -> finish within budget
```

Runtime does not hard-code that exact reasoning sequence for ReAct/Hybrid conditions.

## Counterfactual semantics

An RCA counterfactual:

- names the hypothesis/predictions it tests;
- uses a supported isolated branch;
- declares expected typed outcomes where practical;
- records exact run spec/seed/horizon/tool/scorer versions;
- never mutates the reference state.

Unsupported hypotheses may remain plausible/unresolved but cannot be described as simulation-confirmed.

## First benchmark family

Start with reactor/cooling-water-related behavior because the simulator/process relationships are well understood enough to construct a controlled family.

However:

- exact hidden disturbance/timing/magnitude is chosen only after identifiability pilot;
- blind Agent tools do not expose canonical node-to-IDV answer bindings by default;
- at least one harder case should involve a cause not trivially enumerated from the trigger node's local disturbance binding;
- include a healthy/benign case with `NO_ABNORMAL_CAUSE`.

## Budget

The task uses generic runtime budgets plus named extra dimensions, e.g.:

```text
max_model_calls
max_tool_calls
max_subagents
max_steps
extra_dimensions:
  simulation_rollouts
  simulated_horizon_seconds
  optimizer_trials?
```

Lab/scenario policies determine the numeric limits. Compound bridge tools must reserve the same dimensions before execution.

## Deterministic C0 baseline — mandatory

Every first-family RCA report must include a strong no-Agent baseline.

C0 may use evaluator-known candidate causes for the family and perform:

```text
candidate enumeration
 -> instantiate supported candidate scenarios
 -> isolated rollouts
 -> deterministic trajectory/feature comparison
 -> choose best match or NO_ABNORMAL_CAUSE
```

C0 is intentionally allowed evaluator candidate knowledge because it represents a strong search/matching baseline, not an Agent-visible tool.

Interpretation:

- if C0 solves a case reliably/cheaply, that case is not evidence that an Agent is needed;
- such a case cannot be classified MEDIUM/HARD merely because an LLM found it difficult;
- Agent value must come from harder investigation/generalization/efficiency conditions, not a weakened baseline.

## Scoring

Minimum:

- deterministic structured causal-claim match/top-k;
- evidence-ref validity and relevance;
- typed prediction/result consistency;
- irrelevant/unused observation rate;
- counterfactual rollout count/horizon;
- model/tool/subtask resource usage;
- unsupported/hallucinated environment claims;
- false-positive diagnosis on healthy cases;
- confidence calibration only when the reported confidence has an explicitly defined probability semantics and enough repeated samples.

If confidence is only a relative rank/score, do not report probability calibration.

## Ablation source of truth

Do not maintain a third independent baseline/ablation list here.

`evaluation-v0.md` is the sole canonical capability/orchestration matrix. RCA runs select conditions from that matrix while holding fixture/tool exposure/scorer constant as required.

## Evidence rule

A query/tool result creates an ObservationRecord.

A final claim is considered evidence-backed only when it cites explicit HypothesisEvidenceLink/observation refs that exist and are visible in the trace/run log.

## Engineering record

Every completed RCA run produces an `InvestigationReport` defined in `engineering-records-v0.md`, tied to the final RcaState revision and exact evidence/experiment/trace refs.

This report is archival in v0 and is not automatically retrieved into future benchmark contexts.

## Invariants

- Hidden scenario truth is evaluator-only.
- Agent does not receive a complete candidate-cause answer list.
- C0 remains strong and is not weakened to make Agent results look better.
- Observation and evidence are distinct.
- Simulator/tool results cannot be invented in model text.
- Diagnosis never mutates reference state.
- Healthy/no-abnormal conclusion is a first-class valid result.

## Acceptance criteria

1. Reproduce one hidden incident fixture deterministically.
2. Prove Agent-visible projection/tool set contains no injected fault ID/candidate answer list.
3. Represent evaluator ground truth and Agent result using deterministic structured CausalClaim fields.
4. Complete one single-Agent run under fixed budgets.
5. Execute at least one typed discriminating counterfactual where the selected condition requires it.
6. Run mandatory C0 enumerate/simulate/match baseline on the same case.
7. Build one healthy fixture and score `NO_ABNORMAL_CAUSE` correctly.
8. Generate/verify InvestigationReport with exact state/evidence/experiment/trace refs.
