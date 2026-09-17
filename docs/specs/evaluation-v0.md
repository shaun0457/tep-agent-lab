# Evaluation v0

Status: proposal  
Owner repo: `tep-agent-lab`

## Goal

Provide the sole canonical benchmark/evaluation matrix for the TEP Agent Lab and measure both task outcome and investigation process without conflating capability, orchestration, tool exposure, or hidden scoring information.

Other RCA/charter/roadmap documents reference this spec rather than maintaining competing ablation lists.

## Experiment fixture

Each case is versioned with evaluator-only and Agent-visible sections:

```text
case_id
case_version
benchmark_version
partition: DEVELOPMENT | RESEARCH | HIDDEN_EVAL
environment_config
reference_run_setup
hidden_ground_truth
agent_visible_projection_policy
tool_policy
orchestration_policy
subagent_policy
budget
scoring_config
```

The evaluator owns the hidden section. A deterministic consumer projection function creates the Agent-visible task/context.

## Run identity

Every run MUST record:

```text
run_id
case_id/version
benchmark_version
tep-sim revision
upstream simulator revision
industrial-agent-runtime revision
tep-agent-lab revision
model/provider/model version
prompt/template version
sampling/model settings
orchestration condition
capability condition
tool exposure policy/version
rule/policy/tool-bridge versions
environment/model run seeds where supported
start/end timestamps
```

## Trace completeness

A run is invalid for quantitative comparison if required records are missing for:

- model turns and exact ContextProjection refs;
- tool calls/resource draws;
- WorkBatch/subtask requests/results;
- pre-execution GateDecisions;
- post-execution verification;
- state revisions;
- experiment/run-spec/result refs;
- rollout/artifact refs;
- final evidence links;
- final InvestigationReport.

## E0 — Environment validity

Measure/check:

- deterministic environment same-seed reproducibility;
- snapshot/fork isolation;
- fixture/hidden-truth correctness;
- artifact/run completeness;
- supported/unsupported capability classification;
- canonical variable/binding consistency.

## E1 — Runtime / authority correctness

- invalid schema/unknown operation blocked;
- allowlist/authority enforcement;
- standard/extra-dimensional budget enforcement;
- compound tool reservation/accounting;
- child depth/count enforcement;
- WorkBatch dependency/failure semantics;
- reference mutation denial for SIMULATE;
- stale-state/revision protection;
- consumer `validate_request` behavior;
- post-execution ref/provenance/result verification;
- correct termination reason.

## E2 — Task quality

### RCA

- structured CausalClaim top-1/top-k/family/mechanism match;
- healthy/no-abnormal correctness;
- evidence validity/relevance;
- typed prediction consistency;
- unsupported/hallucinated environment claims;
- confidence calibration only where confidence has explicit probability semantics.

### HAZOP

- supported/unsupported/partial classification;
- evidence-backed consequence quality;
- unsupported physics honesty.

### Recovery

- recovery success;
- safety/shutdown outcomes;
- recovery/settling time;
- production/performance proxy;
- intervention magnitude/complexity.

### AutoProcessResearch

- best visible/research candidate score;
- hidden-eval generalization;
- robustness/constraint compliance;
- experiment efficiency.

## E3 — Investigation / scientific behavior

Evaluate whether the Agent performs useful investigation rather than consuming budget or gaming metrics.

Candidate metrics:

```text
relevant observation query rate
irrelevant/unused observation rate
plausible competing hypotheses considered
prediction coverage
experiment discriminativeness
plausible hypotheses weakened/eliminated per experiment
redundant/duplicate experiment rate
unsupported experiment request rate
useful evidence links per model/tool/rollout cost
rank/belief change after informative evidence
critical open-question resolution
stop efficiency
```

### Anti-gaming denominator rule

Do not reward an Agent for inventing many implausible hypotheses/questions and then eliminating/closing them.

Metrics such as:

```text
hypotheses eliminated per experiment
open-question resolution
```

must use evaluator/fixture-defined plausible competitor/question sets where possible, or explicitly report raw Agent-generated counts separately.

### Typed experiment discrimination

When `Prediction` objects are available, discrimination should be computed from the frozen prediction feature/range space and deterministic ExperimentResult features.

A benchmark may measure:

```text
predicted separation between plausible hypotheses
observed result-to-prediction distances
cost-normalized separation
```

Do not use a free-text LLM judge where deterministic prediction/feature comparison suffices.

### Information-value terminology

A deterministic separation/rank-change proxy may be reported as an **information-value proxy**.

Do not call it Shannon information gain unless the benchmark defines a probability model/update rule supporting that interpretation.

## E4 — Efficiency

- model calls/tokens/context size;
- tool calls by side-effect class;
- WorkBatch item count/parallelism;
- subagent count/depth;
- number/total horizon of simulator rollouts;
- optimizer trials/sensitivity samples;
- model/tool/simulation latency;
- provider cost when available;
- artifact/materialization volume.

Report nested compound-tool usage explicitly rather than hiding it under one top-level call.

## E5 — Safety / authority behavior

- direct reference mutation attempts;
- denied authority escalation attempts;
- policy/gate violations;
- SIMULATE reference-state mutation attempts;
- stale validation-token attempts;
- hidden-ground-truth access attempts;
- unsupported physics claimed as fact;
- recovery safety/shutdown results where applicable.

# Canonical comparison axes

## Capability axis

This tests what information/tool capability is available while holding orchestration/tool-exposure rules otherwise fixed.

```text
C0  deterministic no-Agent enumerate/simulate/match baseline
C1  static compact LLM context only
C2  C1 + read telemetry/history tools
C3  C2 + non-answer-leaking process topology tools
C4  C3 + first Tool Bridge analysis tools
C5  C4 + counterfactual simulation tools
C6  C5 + external/validated rule/knowledge evidence (later)
C7  C5/C6 + numerical research/optimization tools where task-appropriate (later)
```

Subagents are **not** a capability-axis condition; they are orchestration.

AutoProcessResearch is a separate research task family, not simply another capability row.

### C0 definition

C0 is a strong evaluator-side baseline:

```text
evaluator-known family candidate causes
 -> instantiate supported variants
 -> isolated rollouts
 -> frozen deterministic trajectory/feature matching
 -> best candidate or NO_ABNORMAL_CAUSE
```

Report C0 accuracy, simulation cost, and latency. Never weaken C0 to make Agent conditions appear useful.

## Orchestration axis

Compare orchestration while holding the same Agent-visible capability/tool policy fixed.

```text
O0  one-shot LLM over the configured static visible projection; no iterative tools
O1  ReAct: Main Agent selects one next tool/action per turn; no fixed investigation stages
O2  fixed workflow: observe -> hypothesize -> experiment/analyze -> conclude;
    fixed stage order, one bounded model decision/output per stage, no strategic replan
O3  Hybrid reference loop: deterministic authority/state shell + local ReAct; no WorkBatch/subagents
O4  O3 + dependency-aware WorkBatch planning for TOOL work only; no SUBTASK items
O5  O4 + bounded ephemeral SUBTASK items/subagents
```

A future full mutable Dynamic-DAG replanning engine, if implemented, receives a new orchestration condition/version after its semantics are specified. It is not assumed by O4.

## Tool exposure control

For O1–O5 comparisons, the same task-level Agent-visible tool set MUST be used unless tool exposure is the independent variable.

Do not silently expose different tools based on hidden orchestration stages and then attribute the result to orchestration architecture.

Tool-exposure strategy may be studied separately as its own experiment.

## WorkBatch/subagent metrics

When enabled:

- batch/work proposals;
- rejected work requests/reasons;
- dependency graph size/parallelism;
- failed/skipped dependency work;
- subtask reason/context size;
- child observation refs used/ignored by parent;
- incremental quality versus resource overhead;
- whether a simpler parent-only path sufficed.

Do not maximize work/subagent count as an objective.

## AutoProcessResearch evaluation

AutoProcessResearch is evaluated as its own task/campaign family after recovery/optimization infrastructure exists.

Report:

- baseline score;
- trials/Agent turns to first/best improvement;
- accept/reject/neutral/failure counts;
- duplicate/repeated idea rate;
- deterministic optimizer trials versus model research turns;
- total simulation/model/tool budget;
- constraint/safety violations attempted;
- visible/research score versus hidden-eval score;
- robustness across scenario/seed distribution;
- accepted strategy complexity.

It is not included as O6 because changing the research task itself would confound a pure orchestration comparison.

## Randomization / stochasticity

Environment scenario generation is deterministic for a frozen fixture/seed.

LLM provider behavior may be stochastic/non-reproducible. Architecture comparisons should:

- pair fixtures/environment seeds across conditions;
- freeze model/provider/settings per study;
- repeat stochastic conditions when pilot variance indicates material uncertainty;
- report all failures;
- use paired comparisons/confidence intervals once sample size supports them.

Early smoke tests may be descriptive but may not support broad superiority claims.

## Ground-truth leakage audit

Automated/manual audit inspects:

- fixture Agent-visible projection;
- ContextProjection artifacts;
- registered tools/descriptions/results;
- ProcessGraph/binding visibility;
- Rule metadata;
- artifact filenames/metadata;
- engineering-record retrieval configuration;

for injected cause labels/evaluator candidate sets/hidden fixture IDs.

## Evidence/reference scoring

A final evidence-backed claim references valid `HypothesisEvidenceLink`/ObservationRecords.

Scorer distinguishes:

```text
VALID_RELEVANT_REF
VALID_BUT_IRRELEVANT_REF
UNUSED_OBSERVATION
MISSING_REF
HIDDEN_REF_VIOLATION
UNSUPPORTED_NARRATIVE_CLAIM
```

Case scoring configuration defines evaluator-known relevant/plausible evidence categories where needed; it is not inferred only from Agent assertions.

## Scorer versus Agent-visible analysis tools

If the scorer and an Agent-visible analysis bridge use the same underlying metric/library, this is disclosed/versioned.

Prefer independent/complementary scoring where practical. At minimum do not conceal that the Agent has access to the same similarity function used by C0/evaluator.

## Semantic stopping evaluation

v0 stopping mechanism:

```text
Agent proposes finish
 -> deterministic structural readiness verification
 -> DONE or return missing requirements
```

Measure:

- premature conclusion;
- extra low-value actions after correct stable conclusion;
- unresolved critical benchmark-defined questions at finish;
- cost/value of final N actions.

Formal information-gain stopping is OPEN_RESEARCH until an explicit belief/probability update model exists.

## Benchmark partitions / overfitting

Use:

```text
DEVELOPMENT
RESEARCH
HIDDEN_EVAL
```

HIDDEN_EVAL cases/variants are not repeatedly exposed after every AutoResearch iteration. A campaign defines when hidden evaluation is consumed and preserves a fresh/unseen evaluation set or version for reported final checks.

Historical Engineering Records are not retrieved into HIDDEN_EVAL context unless memory retrieval is the explicit capability being studied.

## Reproducibility bundle

A report can reconstruct:

- exact benchmark fixture/version;
- environment/runtime/lab revisions;
- provider/model/config/prompt version;
- exact ContextProjection refs per model turn;
- tool/rule/policy versions;
- TaskState revisions/deltas;
- WorkBatch/Subtask/tool/rollout records;
- ExperimentRunSpecs/results;
- final evidence links/InvestigationReport;
- scorer versions/metrics.

## Initial suite

Start small:

1. healthy/reference case;
2. 2–3 RCA variants with identifiability/C0 measurements;
3. expand scenario/difficulty coverage only after RCA tracing/scoring is reliable;
4. HAZOP/recovery/AutoResearch remain later task families and must not delay first RCA evaluation.

## Invariants

- Evaluator truth is hidden from the Agent.
- C0 is a mandatory strong baseline for first RCA family.
- Capability and orchestration axes change one intended factor at a time.
- Tool exposure is held fixed across orchestration comparisons.
- Subagents appear only on orchestration axis.
- AutoProcessResearch is a separate task family.
- Model output never defines its own numeric score.
- Scientific-behavior formulas/plausible sets are frozen per benchmark version before comparison.
- Failure runs remain in reports/datasets.

## Acceptance criteria

1. Run one frozen RCA case with C0 and at least two Agent capability conditions.
2. Run at least O1 and O3 with identical visible tool/capability policy.
3. Verify exact ContextProjection refs exist for every model turn.
4. Pass hidden-truth/candidate-leakage audit.
5. Score typed CausalClaim, Prediction results, evidence refs, efficiency, and authority behavior deterministically where defined.
6. Re-score a saved run identically under the same scorer versions.
7. Produce a human-readable report separating correctness, investigation behavior, and resource/safety behavior.
