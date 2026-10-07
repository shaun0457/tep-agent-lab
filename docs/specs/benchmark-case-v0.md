# Benchmark Case Contract v0

Status: **FROZEN FOR D0 IMPLEMENTATION**  
Owner repo: `tep-agent-lab`  
Schema family: `tep-agent-lab.benchmark-*/v0`

## Purpose

D0 freezes the contract for one reproducible blind benchmark case before adding real-LLM capability.

D0 is **benchmark infrastructure, not an Agent capability milestone**.

The contract must make these questions answerable without model text or implementation convention:

- what exact case/version was run;
- what truth was hidden;
- what the Agent was allowed to see;
- what trusted setup was applied;
- which scoring rules/versions apply;
- whether an Agent-visible projection leaked evaluator truth;
- whether a saved result can be re-scored deterministically.

C0 candidate enumeration, identifiability pilots, empirical difficulty labels, and broad benchmark-family expansion remain later benchmark-pilot work.

## Ownership

```text
Benchmark fixture / evaluator truth
        |
        | trusted evaluator-only
        v
BenchmarkHarness
        |
        +--> AgentCaseProjection --> RunRequest / Agent-visible task
        |
        +--> trusted case setup --> CaseSetupAttestation
        |
        v
P0 RunManager
        |
        +--> immutable RunManifest
        +--> Agent-scoped projections
        +--> EVALUATOR-scoped scoring/audit
```

P0 remains the run/provenance/lifecycle owner.

D0 must not introduce:

- a second RunManifest;
- a second ProcessGraph;
- a second TaskStateStore;
- a second simulator/world truth store;
- an Agent-visible benchmark truth registry.

## Versioned identities

The following identities are immutable strings in v0:

```text
benchmark_version
case_id
case_version
scenario_family_id
scorer_version
projection_policy_version
setup_policy_version
leakage_policy_version
```

A previously used `benchmark_version + case_id + case_version` is immutable.

Changing hidden setup, Agent-visible projection semantics, scoring vocabulary, scorer behavior, or leakage policy requires a new version.

`case_id` must be semantic/opaque and must not encode a canonical IDV/fault answer.

## Partition

```text
DEVELOPMENT
RESEARCH
HIDDEN_EVAL
```

`HIDDEN_EVAL` fixture/truth content is evaluator-only and may not enter:

- Agent ContextProjection;
- Agent-visible canonical context inventory;
- engineering-record retrieval;
- tool descriptions/results;
- artifact names/metadata;
- UI Agent scope.

## BenchmarkCase

Canonical evaluator-owned fixture:

```text
BenchmarkCase
  schema_version = "tep-agent-lab.benchmark-case/v0"
  benchmark_version
  case_id
  case_version
  scenario_family_id
  partition
  task_family = "RCA"

  world
    seed
    backend
    control_mode
    record_interval

  agent_projection
    incident_id
    goal
    trigger_signal_ids[]
    initial_time_hours
    projection_policy_version

  tool_policy
    policy_version
    allowed_tools[]

  orchestration_policy
    condition
    policy_version

  subagent_policy
    enabled
    max_count
    max_depth

  budget

  hidden_setup
  scoring
```

The full `BenchmarkCase` source is EVALUATOR-only.

The Agent never resolves the full fixture source.

### v0 world rule

`world` must map deterministically to existing P0 `WorldSpec`.

No benchmark-specific simulator configuration bypasses `WorldSpec`.

## HiddenSetup

For D0 v0, hidden setup is intentionally narrow and TEP-specific.

```text
HiddenSetup
  schema_version = "tep-agent-lab.benchmark-setup/v0"
  pre_incident_hours
  intervention
    kind = "DISTURBANCE"
    disturbance_id
    value
  post_incident_hours
```

Rules:

- exactly one hidden disturbance intervention in D0 v0;
- finite nonnegative pre/post durations;
- intervention must be supported by the pinned `tep-sim`;
- hidden setup is evaluator-only;
- the intervention id/value must not be copied into Agent-visible records.

Multi-fault/interacting schedules require a later benchmark schema version.

This restriction is for benchmark-freeze clarity, not a claim that the simulator cannot support more complex scenarios.

## EvaluatorGroundTruth

Ground truth is a separate EVALUATOR-only canonical source.

```text
EvaluatorGroundTruth
  schema_version = "tep-agent-lab.benchmark-ground-truth/v0"
  benchmark_version
  case_id
  case_version
  causal_claim
```

`causal_claim` uses the same deterministic field vocabulary as RCA scoring:

```text
CausalClaim
  entity_id?
  mechanism
  variable_or_actuator_id?
  fault_family?
  direction_or_mode?
```

Frozen v0 mechanism vocabulary:

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

Free-text description is not canonical scoring truth.

## AgentCaseProjection

A trusted deterministic projection function creates the Agent-visible case projection from `BenchmarkCase`.

```text
AgentCaseProjection
  schema_version = "tep-agent-lab.benchmark-agent-projection/v0"
  benchmark_version
  case_id
  case_version
  incident_id
  goal
  trigger_signal_ids[]
  initial_time_hours
  allowed_tools[]
  budget
  projection_policy_version
```

The projection must be a pure deterministic function of explicitly Agent-visible fixture fields.

It must not read `EvaluatorGroundTruth`.

The projection must not contain:

- hidden disturbance id/value;
- evaluator candidate-cause set;
- hidden setup checksum;
- evaluator source ids/paths;
- scorer-relevant hidden labels;
- hidden case filenames whose names reveal truth.

A checksum of the exact Agent projection is recorded for reproducibility.

## Benchmark source registration

A benchmark run registers at minimum two EVALUATOR-only canonical sources:

```text
benchmark_case_source_id
evaluator_ground_truth_source_id
```

Both are exact-revision/checksum `ContextSourceRef` values.

They are materializable only through `ProjectionScope.EVALUATOR`.

AGENT inventory must not reveal their ids, paths, descriptions, or count.

## P0 BenchmarkRefs binding

D0 extends the existing P0 `BenchmarkRefs` contract rather than creating a parallel benchmark manifest.

For a benchmark run it carries immutable trusted metadata equivalent to:

```text
BenchmarkRefs
  benchmark_version
  case_id
  case_version
  partition
  benchmark_case_source_id
  evaluator_ground_truth_source_id
  agent_projection_checksum
  scorer_version
  setup_policy_version
  leakage_policy_version
```

Non-benchmark developer/demo runs may continue to use an empty/default `BenchmarkRefs`.

The internal `RunManifest.benchmark` records benchmark identity/refs/versions and setup attestation.

`RunManifest.agent_projection()` continues to exclude the internal benchmark section entirely.

## Trusted setup and attestation

The existing P0 boolean:

```text
case_setup_applied: true
```

is insufficient for a frozen benchmark.

A benchmark case setup must return a typed evaluator-only attestation.

```text
CaseSetupAttestation
  schema_version = "tep-agent-lab.case-setup-attestation/v0"
  benchmark_version
  case_id
  case_version
  setup_policy_version
  case_source_checksum
  ground_truth_source_checksum
  world_config_checksum
  hidden_setup_checksum
  operation_count
  final_simulation_time_hours
  final_agent_observation_checksum
```

Rules:

- generated by trusted harness code after successful setup;
- checksums use deterministic canonical JSON where applicable;
- `final_agent_observation_checksum` hashes the sanitized Agent-visible observation, not hidden simulator metadata;
- the attestation is recorded only in the internal/EVALUATOR manifest;
- setup failure aborts P0 preparation atomically;
- benchmark READY is forbidden without a valid attestation;
- non-benchmark E0 developer demo may retain the existing boolean-only path.

D0 must not persist the raw hidden setup as an Agent-visible artifact.

## ScoringConfig

D0 freezes deterministic scoring semantics but **does not freeze an arbitrary aggregate scalar score**.

```text
ScoringConfig
  schema_version = "tep-agent-lab.benchmark-scoring/v0"
  scorer_version
  causal_claim_match_fields[]
  healthy_outcome_enabled
  evidence_ref_policy_version
  authority_policy_version
```

Initial deterministic metric vector includes, where data exists:

```text
top1_causal_claim_exact
causal_mechanism_match
entity_match
variable_or_actuator_match
fault_family_match
direction_or_mode_match
healthy_no_abnormal_correct
evidence_ref_status_counts
hidden_ref_violation_count
unsupported_narrative_claim_count
model_calls
tool_calls
rollout_count
simulated_horizon_seconds
terminal_task_status
```

No weighted aggregate score or EASY/MEDIUM/HARD threshold is part of D0.

Those require benchmark pilot/C0/identifiability evidence.

## Evidence reference statuses

Frozen v0 vocabulary:

```text
VALID_RELEVANT_REF
VALID_BUT_IRRELEVANT_REF
UNUSED_OBSERVATION
MISSING_REF
HIDDEN_REF_VIOLATION
UNSUPPORTED_NARRATIVE_CLAIM
```

A scorer may only classify relevance from evaluator-owned frozen case/scoring configuration or deterministic structural checks.

It may not ask an LLM to invent relevance labels when deterministic information is available.

## Deterministic re-scoring

D0 scorer inputs are saved-run records plus evaluator-only benchmark sources.

Re-scoring must not require:

- live model calls;
- mutable model state;
- simulator rerun when the metric is defined from persisted records;
- UI state.

Same saved inputs + same scorer version must produce byte-equivalent canonical metric output.

Metrics that genuinely require future C0/simulator recomputation are outside D0.

## LeakageAudit

Before benchmark freeze/run acceptance, produce a typed deterministic audit result.

```text
LeakageAudit
  schema_version = "tep-agent-lab.benchmark-leakage-audit/v0"
  leakage_policy_version
  benchmark_version
  case_id
  case_version
  agent_projection_checksum
  findings[]
  passed
```

Audit surfaces include:

- AgentCaseProjection;
- RunManifest Agent projection;
- AGENT ContextInventoryView;
- ContextProjection/model-turn inputs where available;
- tool names/descriptions/result payloads;
- ProcessGraph/binding projection;
- artifact ids/names/metadata;
- run events/engineering records visible to Agent scope.

A finding records a deterministic location/category and must not copy hidden payload content into Agent-visible output.

For D0, any hidden-truth or candidate-answer leakage is a hard failure.

## BenchmarkHarness

The D0 harness is trusted application/evaluator code.

Conceptual flow:

```text
load + validate BenchmarkCase
load + validate EvaluatorGroundTruth
verify matching benchmark/case/version
project AgentCaseProjection
construct existing RunRequest
construct BenchmarkRefs
prepare P0 with trusted case_setup
receive CaseSetupAttestation
READY only after attestation + manifest publication
start blind run
audit Agent-visible surfaces
score saved result deterministically
```

The harness must not call Agent tools on behalf of the Agent.

It may apply hidden setup only through the trusted `case_setup` path before execution.

## First D0 fixture

D0 implementation requires exactly one deterministic DEVELOPMENT RCA case sufficient to prove the contract.

It is not yet evidence for benchmark difficulty or Agent capability.

Requirements:

- reactor/cooling-water-related;
- opaque semantic case id;
- one hidden supported disturbance;
- deterministic seed/timing/value;
- valid structured ground truth;
- Agent projection without canonical IDV answer text;
- fixed tool/budget policy;
- leakage audit passes.

A healthy negative case and multi-variant family belong immediately after contract implementation but are not required to prove the first D0 plumbing PR.

## SPEC_CONFLICT rule

If D0 implementation requires bypassing P0 or leaking hidden setup into Agent scope:

```text
SPEC_CONFLICT
 -> stop local invention
 -> identify owning contract
 -> update benchmark/P0 spec
 -> continue only after review
```

In particular, do not bypass P0 by:

- directly constructing a second manifest;
- attaching hidden truth to Agent task metadata;
- reading simulator private state from the scorer;
- using filenames/case ids that encode fault labels.

## D0 acceptance

D0 benchmark contract implementation is complete when one DEVELOPMENT case proves:

1. typed fixture + evaluator ground truth validate and share exact identities;
2. deterministic Agent projection is checksumed and contains no hidden setup/truth;
3. P0 internal manifest records exact benchmark identity/refs/versions;
4. P0 Agent manifest projection exposes none of the benchmark/internal refs;
5. trusted setup produces a typed valid `CaseSetupAttestation`;
6. failed/missing attestation prevents benchmark READY atomically;
7. one blind fake-provider run can execute through normal Coordinator/runtime authority;
8. deterministic leakage audit passes;
9. deterministic scorer can score/re-score a saved synthetic/fake result identically;
10. no C0, difficulty, real LLM, UI, HTTP, or new simulator capability is required.

After D0 contract implementation, the next milestones may add:

- healthy negative fixture;
- additional variants;
- C0/identifiability pilot;
- real LLM blind D1 run.
