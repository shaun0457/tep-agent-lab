# Comparative Benchmark Pilot v0 (D0.2C)

Status: accepted

Owner repo: `tep-agent-lab`

Milestone: D0.2C0 — design freeze only. No code, fixture, LLM run or C0 result is part of D0.2C0.

## Purpose and ownership

This document freezes the experimental design that must exist before D0.2C makes any
comparative, identifiability, C0 or difficulty claim.

It owns:

- comparable pilot fixture design;
- nuisance control;
- the evaluator candidate family;
- scorer-v1 design requirements;
- the deterministic C0 contract;
- identifiability/separability outputs;
- conditions for retaining or excluding cases;
- the boundary between pilot evidence and later difficulty claims.

Relationship to other specs:

| Spec | Still owns |
|---|---|
| [`benchmark-case-v0.md`](benchmark-case-v0.md) | per-case schema, projection, setup attestation, leakage audit, scorer-v0 |
| [`benchmark-design-v0.md`](benchmark-design-v0.md) | general scenario-family, C0 and difficulty policy |
| [`evaluation-v0.md`](evaluation-v0.md) | the canonical capability/orchestration matrix (C0 is its row) |
| [`rca-v0.md`](rca-v0.md) | the RCA task and `CausalClaim` semantics |

This spec refines them for the D0.2C pilot. It does not replace them. Where an
implementation step needs a schema change in an owning spec (for example a versioned
scoring sub-record), the implementing PR updates that owning spec explicitly.

## Why the current fixtures cannot be compared

The DEVELOPMENT fixtures `rca-dev-001..004` are infrastructure/candidate fixtures. They
are **not** a valid comparative benchmark family, because Agent-visible nuisances are
correlated with class (recorded in `docs/d0-2b-healthy-negative.md`, "D0.2C nuisances"):

| Nuisance | Correlation |
|---|---|
| goal wording | "observed abnormal ..." on incidents, neutral wording on the healthy case |
| `initial_time_hours` | `rca-dev-001` alone is at 0.3 h |
| world seed | unique per case (11, 12, 13, 14), visible in the AGENT manifest |
| class balance | one healthy case against three incidents |
| setup policy version | only the healthy case uses setup policy v1 (EVALUATOR-only) |

### Historical fixtures are immutable

```text
rca-dev-001..004
```

remain immutable. D0.2C must not:

- change their goal;
- change their seed;
- change their timing;
- change their truth;
- change their checksums;
- relabel them as comparable experimental cases.

They stay registered under `tep-rca-benchmark/v0` with scorer-v0 and keep their current
tests. D0.2C creates **new pilot identities and versions**. No result computed on
`rca-dev-001..004` is reported as comparative pilot evidence.

## Pilot benchmark identity

```text
benchmark_version   tep-rca-benchmark/v1-pilot
case_version        1
scenario_family_id  reactor-thermal-comparative-v1
partition           DEVELOPMENT (all pilot cases)
task_family         RCA
case schema         tep-agent-lab.benchmark-case/v0 (unchanged)
```

`tep-rca-benchmark/v0` has already been used and is never reused for comparative
semantics.

The identity satisfies the existing contract checks: it carries no hidden-truth
vocabulary (`disturbance`, `evaluator`, `fault`, `candidate_cause`, IDV spellings), and
the scenario family id is not a substring of the benchmark version or any case id.

All pilot cases are DEVELOPMENT. The pilot creates no RESEARCH or HIDDEN_EVAL case. A
later benchmark version that supports difficulty claims needs held-out partitions; it is
out of scope here.

Implementation note for D0.2C1: `benchmark._packaged` currently hard-codes
`tep-rca-benchmark/v0` and `BenchmarkHarness` accepts only scorer-v0. D0.2C1 must make the
benchmark version and scorer version explicit per registered fixture without changing any
historical registry entry or checksum.

## Candidate family

Frozen evaluator candidate classes:

```text
NO_ABNORMAL_CAUSE
IDV(4)   evaluator-only implementation
IDV(11)  evaluator-only implementation
IDV(14)  evaluator-only implementation
```

Rules:

- this list is EVALUATOR-only. The Agent never receives it, in any projection, tool
  description, tool result, artifact, error or context source;
- the evaluator may use it for fixture generation, C0 and the identifiability pilot;
- the IDV implementations are pinned `tep-sim` disturbances with value `1`; nothing else
  about them (magnitude, schedule, interaction) is varied in the pilot;
- the healthy class is the setup/v1 `NO_INTERVENTION` kind. It is never encoded as a
  disabled disturbance.

Evaluator ground truth per class, following the pinned `tep-sim` EVALUATOR overlay exactly
as in D0.2A (no lab-inferred actuator):

| Class | entity_id | mechanism | variable_or_actuator_id | fault_family | direction_or_mode |
|---|---|---|---|---|---|
| healthy | null | `NO_ABNORMAL_CAUSE` | null | null | null |
| `IDV(4)` | `reactor_cooling_water_in` | `TEMPERATURE_DISTURBANCE` | null | `INLET_TEMPERATURE_STEP` | `STEP` |
| `IDV(11)` | `reactor_cooling_water_in` | `TEMPERATURE_DISTURBANCE` | null | `INLET_TEMPERATURE_RANDOM_VARIATION` | `RANDOM` |
| `IDV(14)` | `reactor_cooling_water_in` | `VALVE_STICKING` | null | `REACTOR_COOLING_VALVE_STICKING` | `STICKING` |

D0.2C1 re-verifies each incident truth against the pinned overlay. It does not copy the
binding registry.

## Paired experimental matrix

The pilot is built on paired seeds. Every seed is instantiated under every class.

```text
seed 21: healthy, IDV(4), IDV(11), IDV(14)
seed 22: healthy, IDV(4), IDV(11), IDV(14)
seed 23: healthy, IDV(4), IDV(11), IDV(14)
```

```text
4 classes × 3 seeds = 12 pilot cases
```

The pilot seed set is `{21, 22, 23}`. It does not overlap the historical seeds 11–14.

### Design count is not sample size

`12` is the **pilot design count**. It is not a **statistically sufficient benchmark
size**.

- three seeds per class support descriptive within-class variation, nothing more;
- no confidence interval, significance test or superiority claim is computed from the
  pilot as if it were a final benchmark;
- the benchmark size needed for statistical inference is set after the pilot, from the
  measured within-class and between-class variation. It is not fixed here.

## Shared Agent projection

All twelve pilot cases share one Agent projection apart from opaque identity.

### Neutral goal

Frozen goal text, identical bytes in every pilot case:

```text
Investigate the observed reactor thermal behavior and identify the most plausible
supported causal mechanism using available evidence. Do not assume that an abnormal
cause exists.
```

(In the fixture it is a single line with single spaces.) No pilot goal uses
`observed abnormal`, `healthy`, `fault`, `no fault` or any other class-dependent wording.

### Shared fields

Every pilot case has exactly the same:

| Field | Frozen pilot value |
|---|---|
| `trigger_signal_ids` | `XMEAS(9)` |
| `initial_time_hours` | `0.6` |
| `tool_policy` | `tep-agent-lab.benchmark-tool-policy/v0`: `get_capability_summary`, `get_history` |
| `budget` | model 4, tool 2, subagents 0/0, steps 16, all simulation quotas 0 |
| `orchestration_policy` | `O3`, `tep-agent-lab.benchmark-orchestration-policy/v0` |
| `subagent_policy` | disabled, 0/0 |
| `projection_policy_version` | `tep-agent-lab.benchmark-agent-projection-policy/v0` |
| projection schema | `tep-agent-lab.benchmark-agent-projection/v0` |
| world (except seed) | python backend, `closed_loop`, record interval 60 s |

The tool policy and budget are deliberately the existing D0 values. D0.2C measures the
benchmark and C0, not Agent capability; changing Agent capability is a later study on the
`evaluation-v0.md` axes.

### Shared framing (disclosed)

`benchmark.task_goal` appends "Trigger signals: XMEAS(9). Incident observed at simulation
time 0.6 h." and P0 uses an
`incident-<case_id>` investigation id. This framing is the same for every class, so it is
not a class proxy. It does frame every case, including healthy ones, as an incident, which
can bias any Agent toward an abnormal answer. The pilot keeps it unchanged and discloses
it. Changing it is a new `projection_policy_version`, never a silent edit.

### Projection invariants

`AgentCaseProjection` (schema `benchmark-agent-projection/v0`) has no `world` field. The
world seed reaches the Agent through the AGENT-scoped P0 manifest, whose `world` section
carries `WorldSpec.record()`. A check over `AgentCaseProjection` alone would therefore
miss the seed. The invariants are defined over an explicit Agent-visible case view:

```text
AgentVisibleCaseView
  projection  = AgentCaseProjection.record()
  world       = the Agent-visible WorldSpec record (seed, backend, control_mode,
                record_interval), exactly as the AGENT manifest exposes it
```

All comparisons use canonical JSON. No field other than the ones named below is removed
to make a check pass. D0.2C1 tests all four.

**A. Within-seed class invariant.** For each seed in `{21, 22, 23}`, across the four
EVALUATOR classes of that seed:

```text
AgentVisibleCaseView minus {projection.case_id, projection.incident_id}
```

is byte-identical. (For seed 21: the healthy, `IDV(4)`, `IDV(11)` and `IDV(14)` views are
identical once opaque identity is removed. Same for seeds 22 and 23.)

**B. Cross-seed normalized invariant.** Across all twelve cases:

```text
AgentVisibleCaseView minus {projection.case_id, projection.incident_id, world.seed}
```

is byte-identical.

**C. Seed-balance invariant.** Each EVALUATOR class exposes exactly the Agent-visible seed
multiset:

```text
{21, 22, 23}
```

**Projection-only check.** Because `AgentCaseProjection` carries no seed, across all twelve
cases:

```text
AgentCaseProjection minus {case_id, incident_id}
```

is byte-identical too. This is a consequence of A and B for the projection part, and it is
tested on its own so that a future projection field cannot carry the seed or the class
unnoticed.

## Shared timeline

One common pilot timeline:

```text
pre-event / warmup                     0.1 h
observation after the intervention point 0.5 h
total Agent-visible initial time        0.6 h
```

| Class | Setup (all `tep-agent-lab.benchmark-setup/v1`) |
|---|---|
| healthy | `NO_INTERVENTION`: `pre_observation_hours 0.1`, `observation_hours 0.5` |
| incident | `DISTURBANCE`: `pre_incident_hours 0.1`, `IDV(n) = 1`, `post_incident_hours 0.5` |

All incident candidates apply their intervention at the same hidden event time (0.1 h).
The healthy class uses the same timeline and applies nothing.

Every pilot case uses setup/v1, including incidents (the first registered use of setup/v1
`DISTURBANCE`). Setup schema and setup policy version are therefore identical across
pilot classes, which removes the D0.2B setup-policy correlation. Only the setup `kind`
differs, and it is EVALUATOR-only.

### Viability before freezing bytes

D0.2C1 runs an EVALUATOR-only viability check for every class × seed before it freezes
pilot fixture bytes. The check is the D0.2A viability check (not C0): setup is supported
and completes, repeated setup is deterministic, no shutdown occurs in the window, and each
incident trajectory differs from its same-seed healthy trajectory in the Agent-visible
window.

`IDV(11)` and `IDV(14)` were checked under exactly this 0.1 h / 0.5 h window in D0.2A (with
other seeds). `IDV(4)` has only been checked under 0.1 h / 0.2 h. Its viability under the
common window is an open check, not an assumption.

If any candidate cannot produce supported, differing dynamics under the common horizon:

```text
SPEC_CONFLICT
```

or an explicit revision of this spec (new timeline for **all** classes). No class ever
gets its own Agent-visible horizon.

## Seed nuisance control

The world seed may stay Agent-visible in the P0 AGENT manifest. That is acceptable only
because every pilot seed appears in every class:

```text
seed must not identify class
```

No seed is assigned to a single cause. Each class has the seed multiset `{21, 22, 23}`.

## Case identity

Pilot case ids are opaque. A case id must not encode:

- an IDV number;
- healthy / no abnormal cause;
- fault family or mechanism;
- the seed;
- the class or any cause.

Frozen scheme:

```text
case_id      rca-pc-NN     NN = 01..12
incident_id  incident-rca-pc-NN
files        rca-pc-NN.case.json, rca-pc-NN.ground-truth.json
source ids   tep-agent-lab.benchmark-case.rca-pc-NN.v1 (and ground-truth)
```

`NN` is assigned by a uniformly random permutation of the twelve (class, seed) cells,
drawn once when D0.2C1 authors the fixtures. It is not derived from class, seed or any
hash of them, so it cannot be recomputed from the design. The permutation and its draw are
recorded EVALUATOR-side. The id-to-class mapping exists only in EVALUATOR fixtures and
truth.

A single random draw can still produce a visible pattern, so the drawn permutation is
checked and redrawn if it fails. It is rejected when:

- any class or any seed occupies three consecutive `NN` values;
- the twelve ids sorted by `NN` are ordered by class, or by seed, in either direction.

Every rejected draw and the accepted one are recorded. D0.2C1 tests the accepted
permutation against these rules.

Registry order and fixture file order carry no class meaning that reaches the Agent; both
are EVALUATOR-only.

## Scorer v1

Scorer-v1 semantics are frozen here. D0.2C0 does not implement it, and scorer-v0 is not
modified. Pilot fixtures name scorer-v1; historical fixtures keep scorer-v0.

```text
scorer_version   tep-agent-lab.benchmark-scorer/v1
scoring schema   tep-agent-lab.benchmark-scoring/v1
score schema     tep-agent-lab.benchmark-score/v1
```

Scorer-v1 is implemented in D0.2C1, because the pilot fixture bytes include their scoring
configuration and the harness must validate it. The scoring sub-record is dispatched on
its `schema_version`, the same way the setup sub-record is (`benchmark-case-v0.md`
"HiddenSetup v1").

### Scorer-v0 defects that scorer-v1 fixes

1. Truth `variable_or_actuator_id = null` is matched by exact equality. That rewards
   omission and penalizes a possibly useful specific answer.
2. `fault_family` is evaluator-owned free text with no published Agent vocabulary, so a
   blind exact match is unreachable. Requiring it makes top-1 correctness unreachable.

### Field roles

Each claim field has one frozen role per case, recorded in the scoring configuration at
fixture-authoring time (never inferred at scoring time):

```text
PRIMARY         part of primary top-1 correctness
AUXILIARY       scored and reported, never part of primary correctness
NOT_APPLICABLE  not scored (evaluator truth is null)
```

Rules, applied in this order:

```text
mechanism:
  always PRIMARY (its vocabulary is the frozen public output vocabulary, see below)

any other field whose evaluator truth is null:
  NOT_APPLICABLE (any submitted value is neither rewarded nor penalized)

entity_id, variable_or_actuator_id (truth non-null):
  PRIMARY if identifier-reachable (below), else AUXILIARY

fault_family (truth non-null):
  AUXILIARY, unless a public frozen Agent output vocabulary for it exists

direction_or_mode (truth non-null):
  AUXILIARY, unless an explicit Agent-visible vocabulary is frozen
```

So on a healthy case every field except `mechanism` is NOT_APPLICABLE.

Two kinds of reachability decide the roles. They are different and must not be mixed:

- **Output-vocabulary reachability** (`mechanism`, and `fault_family` /
  `direction_or_mode` only if a vocabulary is ever frozen for them). The value comes from
  a closed, public, versioned output vocabulary that the Agent's structured result schema
  offers as a whole. The `CausalMechanism` vocabulary in `rca-v0.md` and
  `benchmark-case-v0.md` is that kind of vocabulary: all ten values are offered together,
  so offering it reveals no case answer.
- **Identifier reachability** (`entity_id`, `variable_or_actuator_id`). The exact truth id
  can appear on a model-facing surface (ContextProjection, tool spec, or tool result
  reachable with the case's allowed tools). D0.2C1 decides it by a test on the pilot
  surfaces and freezes the role in the scoring configuration.

Auxiliary fields never make primary correctness unreachable.

**Known conflict, resolved in D0.2C1.** The current structural leakage audit treats the
truth mechanism value as a hidden label (`_truth_labels`), and its vocabulary screen
matches `disturbance`. An Agent output schema that lists the full mechanism vocabulary
would therefore fail leakage policy v0/v1 on every incident case. D0.2C1 resolves this
with leakage policy v2 before it freezes pilot fixture bytes (see Leakage, "Mechanism
vocabulary and leakage policy v2"). D0 still has no structured RcaResult boundary and
D0.2C runs no Agent, so in D0.2C mechanism-level primary correctness is exercised only by
scoring saved submissions.

The pilot tool policy is `get_capability_summary` + `get_history`. It has no topology
tool. Unless D0.2C1 shows that `reactor_cooling_water_in` is identifier-reachable under
that policy, `entity_id` is AUXILIARY for the pilot, and primary incident correctness reduces to the
mechanism.

### Primary correctness

```text
healthy case:
  primary_correct = (submitted mechanism == NO_ABNORMAL_CAUSE)

incident case:
  primary_correct = mechanism match
                    AND entity match when entity_id is PRIMARY
                    AND variable_or_actuator match when that field is PRIMARY
```

A submitted `NO_ABNORMAL_CAUSE` claim must carry no cause fields (unchanged `CausalClaim`
rule).

**Consequence for this candidate family (disclosed, not a defect to tune away).** Under
these rules `IDV(4)` and `IDV(11)` have the same primary target
(`TEMPERATURE_DISTURBANCE` on `reactor_cooling_water_in`). Their difference is
`direction_or_mode` / `fault_family`, which are AUXILIARY. So the pilot has four
EVALUATOR classes but three primary Agent-scoring equivalence classes:

```text
{healthy}  {IDV(4), IDV(11)}  {IDV(14)}
```

This is acceptable and is not engineered away (no topology tool is added and no evaluator
identifier is exposed to make `entity_id` scorable).

### Target spaces

Two target spaces exist and are never confused:

```text
EVALUATOR target space (C0 candidate space)     4 classes
  NO_ABNORMAL_CAUSE, IDV(4), IDV(11), IDV(14)

primary-equivalence target space (scorer-v1)    3 classes with the current truth
                                                table (recomputed from frozen roles)
  {NO_ABNORMAL_CAUSE}
  {IDV(4), IDV(11)}   -> TEMPERATURE_DISTURBANCE equivalence class
  {IDV(14)}           -> VALVE_STICKING equivalence class
```

The collapse is deterministic and derived from the frozen scorer-v1 primary semantics,
never hand-written: two EVALUATOR classes fall into the same equivalence class exactly
when their PRIMARY truth fields (mechanism, plus every identifier field whose role is
PRIMARY) are equal. If D0.2C1 proves `entity_id` reachable, the collapse is recomputed from
the frozen roles; with the current truth table it is unchanged, because `IDV(4)` and
`IDV(11)` share the same entity.

### Comparison rule (hard rule)

> A C0 result and an Agent result may only be compared numerically when both are
> evaluated on the same target space.

Therefore:

- C0 4-class accuracy is never placed directly against Agent 3-class primary accuracy as
  though they measured the same task;
- a later capability table that compares C0 with an Agent condition uses the C0 collapsed
  primary-equivalence metric against the Agent primary-equivalence metric;
- the C0 EVALUATOR 4-class result is reported next to it, separately, as a finer
  identifiability diagnostic.

Every reported number names its target space.

### Scorer-v1 metric vector

Scorer-v1 keeps the scorer-v0 rules: deterministic re-scoring, no aggregate or weighted
score, every metric typed `AVAILABLE` / `NOT_APPLICABLE` / `NOT_AVAILABLE`. The vector is:

```text
primary_correct
causal_mechanism_match
entity_match
variable_or_actuator_match
fault_family_match            (auxiliary)
direction_or_mode_match       (auxiliary)
abnormal_presence_correct
evidence_ref_status_counts
hidden_ref_violation_count
unsupported_narrative_claim_count
model_calls
tool_calls
rollout_count
simulated_horizon_seconds
terminal_task_status
```

- each field metric reports its frozen role next to its value;
- `abnormal_presence_correct` is `(submitted mechanism == NO_ABNORMAL_CAUSE) ==
  (truth mechanism == NO_ABNORMAL_CAUSE)`. It is enabled on **every** pilot case, healthy
  and incident, so false positives and false negatives are both measurable. Scoring
  schema v1 therefore has no `healthy_outcome_enabled` flag; for scoring/v1 cases the
  harness keeps only the rule that `NO_ABNORMAL_CAUSE` truth goes with exactly a
  `NO_INTERVENTION` setup (the scoring/v0 flag rule is unchanged for v0 cases);
- scorer-v0 `top1_causal_claim_exact` (all fields exact) is not part of scorer-v1. A report
  that needs it uses scorer-v0 on scorer-v0 cases.

## Deterministic C0 contract

C0 is the `evaluation-v0.md` C0 row. It is EVALUATOR-only, is never registered as an Agent
tool, and never runs inside an Agent run.

Frozen logical pipeline:

```text
observed pilot trajectory
        ↓
evaluator candidate set
        ↓
for every candidate:
  same seed
  same timing
  same world config
  only candidate cause differs
        ↓
isolated deterministic rollout
        ↓
frozen evaluator feature / trajectory comparison
        ↓
best candidate
        ↓
NO_ABNORMAL_CAUSE or incident class
```

### Inputs

C0 may consume:

- the observed trajectory: the sanitized Agent-visible observation history of the
  prepared reference world over the 0.6 h window (the same telemetry P0 projects; never
  hidden simulator state such as `active_disturbances`);
- the world config (seed, backend, control mode, record interval);
- family-level design constants that are identical for every pilot case: the candidate
  set and the common timeline (event at 0.1 h, 0.5 h after it);
- evaluator candidate definitions through the candidate compiler (allowed because C0 is an
  evaluator baseline).

C0 must not consume:

- case id or incident id;
- the ground-truth label or any per-case hidden setup;
- goal wording;
- benchmark source path or source id;
- setup policy version;
- leakage policy version.

D0.2C2 tests the exclusions, for example by checking that C0's output is invariant when
those inputs are changed or removed.

### Rollouts

- every candidate rollout runs in an isolated world (or isolated SIMULATE branch) built
  from the same world config, seed and timeline; it never mutates a reference world;
- the healthy candidate is the same-seed no-intervention rollout;
- rollouts are deterministic: same inputs give byte-identical candidate trajectories.

Cost accounting. Under the paired design, the four cases that share a seed need the same
four (seed, candidate) rollouts, so the pilot has only twelve unique rollouts. D0.2C2 may
cache them. Cost is reported both ways and is never mixed:

- **per-case nominal cost**: what C0 costs on that case alone (four rollouts of the common
  0.6 h horizon), whether or not a cached rollout was reused. This is the comparable
  baseline cost for later Agent conditions;
- **pilot total**: unique rollouts actually simulated, plus cache hits.

### Exact-replay property (disclosed)

The simulator is deterministic and C0 reuses the observed seed. The true candidate's
rollout therefore reproduces the observed trajectory exactly. Same-seed C0 is kept as
designed, and it is an:

```text
exact-world-model candidate-identifiability upper bound
```

It primarily demonstrates whether candidate trajectories differ under the observed
seed/window. It does **not** demonstrate seed robustness, generalization, noise
robustness or deployment diagnostic accuracy. Nuisance-robustness claims come only from
the frozen cross-seed analyses or from a separately versioned future baseline. D0.2C3
must report how many decisions were exact (zero-distance) matches and must not present
same-seed C0 accuracy as nuisance-robust identifiability. Cross-seed separability is
measured by the required cross-seed identifiability outputs below, which never use
same-seed pairs. A seed-mismatched C0 variant, if wanted, is a separate versioned baseline
and never replaces this one.

### Feature contract (frozen by D0.2C2, not here)

D0.2C2 must freeze, under an explicit metric/version identity, before any accuracy is
reported:

- exact variable set;
- exact sample/window alignment;
- normalization;
- missing-data behavior;
- distance/feature calculation;
- tie-breaking;
- the healthy decision rule;
- numerical tolerances;
- simulator cost accounting (rollout count, simulated horizon, wall time);
- the C0 metric/version identity.

No free-text or LLM judge is part of C0.

C0 must not silently call an Agent-visible comparison tool and call the result an
independent baseline. If C0 and any Agent tool share lower-level functions, the sharing is
disclosed in the C0 version record and in every report (`benchmark-design-v0.md`,
"Deterministic scorer versus Agent-visible tools"). The pilot tool policy exposes no
comparison tool today.

### Healthy decision

`NO_ABNORMAL_CAUSE` is never "whatever wins when the minimum distance happens to be the
healthy rollout" without an explicit rule. D0.2C2 freezes a versioned deterministic rule
that states:

- how `NO_ABNORMAL_CAUSE` is selected;
- how ties and near-ties between healthy and incident candidates are resolved;
- every threshold or tolerance it uses, and the data each was derived from.

The pilot has no HIDDEN_EVAL partition, so a "not tuned on HIDDEN_EVAL" guard alone would
be empty. The frozen rule is:

- any data-derived threshold, tolerance or design choice for C0 (healthy rule included)
  comes from a **calibration seed set** that is disjoint from every benchmark seed
  (`11..14` historical, `21..23` pilot). D0.2C2 declares the calibration seeds before it
  generates calibration rollouts. Calibration rollouts are EVALUATOR-only and are not
  benchmark cases;
- C0 is never tuned on the twelve pilot cases. The rule and features are frozen and
  versioned before C0 is run on any pilot case for reporting;
- if a choice was nonetheless informed by pilot-case results, every affected number is
  labeled **in-sample** in the report and is not presented as C0 accuracy;
- no threshold may be invented from, tuned on, or checked against HIDDEN_EVAL performance
  in any later benchmark version either.

## Identifiability pilot outputs

D0.2C3 must report at least the following. This spec freezes the outputs, not threshold
values.

- within-class trajectory/feature variation across seeds;
- between-class distances;
- the nearest competing class for each case;
- a pairwise separability matrix over the four EVALUATOR classes;
- C0 EVALUATOR 4-class confusion matrix;
- C0 EVALUATOR 4-class accuracy, with the exact-match count;
- C0 collapsed primary-equivalence confusion matrix;
- C0 collapsed primary-equivalence accuracy;
- Agent primary-equivalence metrics: not part of D0.2C3; reported later, when an Agent
  milestone runs, under the comparison rule above;
- healthy false-positive and false-negative behavior;
- per-case and total rollout cost;
- ties and near-ties;
- excluded or confounded cases, with the reason.

**Cross-seed requirement.** Within-class variation, between-class distances, the nearest
competing class and the separability matrix are computed from **cross-seed** pairs only
(the two trajectories come from different seeds). Same-seed pairs are trivially separable
under a deterministic simulator and may be shown only as a separate, labeled table.
Nuisance-robust separability is claimed only from the cross-seed numbers.

**Class-balance requirement.** At the healthy-vs-abnormal level the pilot has 3 healthy
and 9 incident cases, so "always abnormal" already gets 9/12. Binary results
(`abnormal_presence_correct`, C0's healthy decision) are reported per class and as
balanced accuracy, never only as one pooled rate. Healthy false-positive behavior rests on
three cases and is descriptive.

Every number names the benchmark, scorer, C0 and feature versions it was computed with.

No EASY / MEDIUM / HARD label is assigned from intuition. The pilot introduces **no
difficulty thresholds**. Difficulty labels need empirical evidence after the pilot, under
`benchmark-design-v0.md` "Difficulty tiers".

## Retain / exclude rule

The pilot permits the result:

```text
this candidate is not identifiable under this evidence window
```

That is a valid scientific finding, not a failure to be engineered away.

- the benchmark is not tuned until every case becomes separable;
- a confounded or non-identifiable case is recorded and then excluded or relabeled
  explicitly, with the reason and the evidence that triggered it;
- exclusion never deletes data: excluded cases stay in the run history and the report;
- the only pre-freeze change to cases allowed is the explicit viability revision above
  (for all classes, recorded).

What freezes when:

| Artifact | Frozen at | A later change requires |
|---|---|---|
| pilot cases, candidate set, timeline, truth, scorer-v1 | D0.2C1 fixture freeze | a new benchmark version |
| C0 features, healthy rule, tolerances | D0.2C2, before any pilot-case C0 run is reported | a new C0 version and a new benchmark version (`benchmark-design-v0.md` "Benchmark evolution") |

D0.2C2 design iteration on calibration seeds happens before the first C0 freeze and needs
no version. After a C0 version has produced reported numbers, any change to it creates a
new C0 version and a new benchmark version, as `benchmark-design-v0.md` requires for C0
implementation changes. The earlier numbers stay attached to the earlier versions, and
the revised C0 is reported next to them, never in place of them.

## Leakage

D0.2C distinguishes two kinds of leakage.

**Structural leakage** (an evaluator label, source ref, setup checksum or hidden
vocabulary on an Agent surface) stays with `LeakageAudit`. Every pilot case is audited
under leakage policy v2 (below). In addition, each pilot case's Agent surfaces are
audited against the union of all four classes' labels and all twelve cases' hidden sources,
as D0.2A did across its family.

### Mechanism vocabulary and leakage policy v2

D0.2C1 adds `tep-agent-lab.benchmark-leakage-policy/v2` before it freezes pilot fixture
bytes. Leakage policy is owned by `benchmark-case-v0.md`; the D0.2C1 PR updates that spec
explicitly. v2 is defined in full here, not as a delta:

- **Scope.** v2 audits setup/v1 cases whose scoring schema is
  `tep-agent-lab.benchmark-scoring/v1`, that is, the pilot cases. Every other case keeps
  the policy its setup schema maps to. Leakage policy v0/v1 behavior and every historical
  audit result are unchanged.
- **Inherited checks.** Every leakage policy v1 check applies, including the
  `NO_INTERVENTION` hidden labels (setup kind, setup schema, setup policy version) and the
  cross-case union of all four classes' labels and all twelve cases' hidden sources. The
  implementation must not key those labels on `policy == v1` alone; D0.2C1 tests that a v2
  audit still finds each of them.
- **Whole-vocabulary check.** Outside the exempt node below, every value of the frozen
  `CausalMechanism` vocabulary on an Agent surface is a finding (all ten values, not only
  the pilot truth labels), and the hidden-vocabulary screen (for example `disturbance`)
  applies.
- **Exempt node.** Exactly one node is exempt: the `enum` of the mechanism field in a
  versioned, case-independent Agent output schema. Its value list is byte-identical, in
  the order the frozen vocabulary version lists the values, in every case and on every
  audit surface that shows it. The node carries no per-value description, default,
  example, ordering hint or other annotation. The exemption covers only the matching of
  those enum elements; everything else in the schema, including its title and field
  descriptions, is audited in full. The cross-case audit uses the same rule.
- **Tests.** D0.2C1 tests both directions. The exact exempt node passes. Each of these
  fails: a single value, a proper subset, a reordered list, an annotated list
  (description, default or example), the same list outside the exempt node, and an extra
  hidden ref next to the node.

**Semantic nuisance proxy** (class inferable from a property that is not itself a hidden
label) is a separate comparative check. Class must not be inferable from:

- goal wording;
- initial time;
- world seed;
- case id;
- tool policy;
- budget;
- orchestration metadata exposed to the Agent;
- source names;
- projection shape.

Deterministic acceptance rule for D0.2C1: for every field in the list above, the multiset
of Agent-visible values within each class is identical across the four classes (for
example, seed is `{21, 22, 23}` for every class and every other field is constant). The
case-id rule is the random permutation above. This is a pass/fail design check; it is not
a statistical test.

## TEP coupling boundary

C0, the candidate family, the scorer and the pilot fixtures are TEP-specific and live in
`tep-agent-lab`. None of the following moves into `industrial-agent-runtime`:

```text
IDV
XMEAS
XMV
TEP candidate cause
TEP simulator semantics
TEP benchmark scorer
```

```text
TEP benchmark -> generic runtime
generic runtime !-> TEP benchmark
```

## Planned implementation split

```text
D0.2C1  Comparable pilot fixture generation + validation
D0.2C2  Deterministic C0 implementation
D0.2C3  Identifiability/separability pilot report
```

**D0.2C1** — viability check; twelve pilot case/truth pairs under
`tep-rca-benchmark/v1-pilot`; per-fixture benchmark/scorer versions in the registry;
scorer-v1 and scoring schema v1; leakage policy v2; field-role reachability test; projection invariants
A–C plus the projection-only check; nuisance-proxy check; structural and cross-case
leakage audits; historical fixtures and their tests unchanged.

**D0.2C2** — EVALUATOR-only C0: candidate compiler, isolated deterministic rollouts,
declared calibration seeds, frozen feature contract, healthy decision rule, cost
accounting (nominal and unique), input-exclusion tests, shared-function disclosure.

**D0.2C3** — the identifiability report with every output listed above, in both target
spaces, including exclusions and the exact-replay disclosure. No difficulty labels.

No step runs a real LLM. Agent capability runs on the pilot belong to a later milestone.

## Acceptance for D0.2C as a whole

D0.2C is complete when:

1. twelve paired pilot cases exist under a new benchmark version, and `rca-dev-001..004`
   are byte-identical to `83a96c19675eed03831f7106834904b667959fbd`;
2. projection invariants A–C, the projection-only check and the nuisance-proxy check pass;
3. structural leakage audits pass for every pilot case under leakage policy v2, including
   the cross-case audit, and historical audits under v0/v1 are unchanged;
4. scorer-v1 scores and re-scores saved synthetic submissions byte-identically, and
   scorer-v0 output for historical cases is unchanged;
5. C0 runs on every pilot case under a frozen, versioned feature contract and healthy
   rule, with cost recorded;
6. the identifiability report contains every required output, including exclusions;
7. no difficulty threshold or label has been introduced.
