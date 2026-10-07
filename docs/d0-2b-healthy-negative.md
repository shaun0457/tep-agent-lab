# D0.2B — Healthy negative contract + fixture

Status: implemented. Owning spec: [`specs/benchmark-case-v0.md`](specs/benchmark-case-v0.md)
(section "HiddenSetup v1"). Builds on [`d0-2a-incident-family.md`](d0-2a-incident-family.md).

D0.2B adds a `NO_ABNORMAL_CAUSE` case to the benchmark family. It needed a deliberate
setup-contract change, `benchmark-setup/v1`. The frozen `benchmark-setup/v0` incident
fixtures are untouched.

**One healthy fixture next to three incident fixtures does not make the benchmark
balanced or statistically meaningful.** This change adds no C0, no candidate
enumeration, no trajectory ranking, no `NO_ABNORMAL` classifier, and no
identifiability or difficulty claim. Those belong to D0.2C.

**`rca-dev-004` is a DEVELOPMENT candidate/plumbing fixture. The current family is NOT
valid for healthy-vs-incident comparative evaluation**, because goal wording itself is
correlated with healthy/incident status, and so are other Agent-visible nuisances (see
"D0.2C nuisances"). "LeakageAudit PASS" below means the deterministic structural audit:
no evaluator label, source ref or setup checksum appears on an Agent surface. It does not
mean the family is blind at the family-comparison level.

## What changed

| Concern | Change |
|---|---|
| Setup contract | `benchmark-setup/v1` is a tagged union: `DisturbanceSetupV1` (`kind = DISTURBANCE`) or `NoInterventionSetup` (`kind = NO_INTERVENTION`). `hidden_setup_from_record` dispatches on `schema_version`, then on `kind`. A v0 record still parses to the unchanged `HiddenSetup` and is never rewritten. |
| Setup policy | `benchmark-setup-policy/v0` for v0 setups (unchanged for `rca-dev-001..003`), `benchmark-setup-policy/v1` for v1 setups. The harness puts the matching version in `BenchmarkRefs` and in the attestation. |
| Trusted setup | `BenchmarkCaseSetup` runs one timeline. For `NO_INTERVENTION` that is advance `pre_observation_hours`, advance `observation_hours`, observe, attest. It never applies anything. |
| Outcome consistency | `BenchmarkHarness` rejects any case/truth pair unless `NO_ABNORMAL_CAUSE` truth goes with exactly a `NO_INTERVENTION` setup, and unless `healthy_outcome_enabled` is true exactly for that healthy case. |
| Leakage policy | `benchmark-leakage-policy/v0` for setup/v0 cases (unchanged identity and behavior for `rca-dev-001..003`), `benchmark-leakage-policy/v1` for setup/v1 cases. v1 adds, for a `NO_INTERVENTION` case, the hidden labels `NO_INTERVENTION`, `tep-agent-lab.benchmark-setup/v1` and `tep-agent-lab.benchmark-setup-policy/v1`. `leakage_policy_version(setup)` selects the policy; `BenchmarkRefs` and every emitted `LeakageAudit` carry the policy that actually audited the case. |
| Fixture | `rca-dev-004` case and ground-truth pair, appended to the explicit EVALUATOR registry. |
| Tests | `tests/test_benchmark_healthy.py`. In `tests/test_benchmark_family.py` only the exact-registry assertion now lists four fixtures and probes `rca-dev-005` as the unknown identity. |

Unchanged: `benchmark-case/v0`, `benchmark-ground-truth/v0`, the Agent projection and
its policy, `CaseSetupAttestation` and P0, the tool and orchestration policies, leakage
policy v0 and the scorer (`benchmark-scorer/v0`).

### Healthy is structurally explicit

```text
hidden_setup
  schema_version         tep-agent-lab.benchmark-setup/v1
  kind                   NO_INTERVENTION
  pre_observation_hours  0.1
  observation_hours      0.5
```

These representations are rejected. None of them can stand for "healthy":

- `IDV(n) = 0`, in v0 or in v1 `DISTURBANCE`;
- `disturbance_id = null`, an empty `{}` or a `null` intervention;
- a `NO_INTERVENTION` setup that carries any `intervention` or `disturbance_id` field;
- an unknown `kind` (`HEALTHY`, `NO_FAULT`, lowercase spellings) or a missing `kind`;
- a `kind` field on a v0 record;
- `NO_ABNORMAL_CAUSE` truth paired with any disturbance setup, or incident truth paired
  with `NO_INTERVENTION`.

## Healthy semantics

The truth `NO_ABNORMAL_CAUSE` means **no evaluator-injected abnormal cause**. It does
not mean "all signals constant". The TEP plant runs closed-loop, and normal process and
control variation continues. A test checks that `XMEAS(9)` is not constant over the
`rca-dev-004` window; at authoring time all 37 samples were distinct.

The label comes from the fixture: the setup deliberately applies no abnormal
intervention. Setup never inspects hidden simulator state to produce or confirm a
health label. The only check of hidden state (`active_disturbances` is empty) is in an
EVALUATOR test, after setup.

## AGENT-visible properties

```text
benchmark_version   tep-rca-benchmark/v0
case_id             rca-dev-004
case_version        1
incident_id         incident-rca-dev-004
goal                Investigate the observed reactor thermal behavior and identify the
                    most plausible supported causal mechanism using available evidence.
                    Do not assume that an abnormal cause exists.
trigger_signal_ids  XMEAS(9)
initial_time_hours  0.6
allowed_tools       get_capability_summary, get_history
budget              model 4, tool 2, subagents 0/0, steps 16, simulation quotas 0
```

Agent projection checksum: `8e7bc3f5228a2f395f4a0be847ca0485e590488ae12ccadd5aa42dfa02e931a3`.

The projection has the same schema as the incident cases. The setup schema, setup kind
and setup policy are not part of it.

## EVALUATOR ONLY

```text
scenario_family_id  reactor-thermal-v0
partition           DEVELOPMENT        task_family  RCA     orchestration  O3
world               seed 14, python backend, closed_loop, record_interval 60 s
hidden_setup        setup/v1 NO_INTERVENTION: advance 0.1 h, then advance 0.5 h
setup policy        tep-agent-lab.benchmark-setup-policy/v1
scoring             benchmark-scorer/v0, all five claim fields, healthy_outcome_enabled true
```

Seed 14 is not used by any incident fixture (11, 12, 13). The 0.1 h + 0.5 h timeline
matches the `rca-dev-002` and `rca-dev-003` windows.

Ground truth (`causal_claim`):

| entity_id | mechanism | variable_or_actuator_id | fault_family | direction_or_mode |
|---|---|---|---|---|
| null | `NO_ABNORMAL_CAUSE` | null | null | null |

Frozen canonical-JSON sha256 values (both sources `visibility = EVALUATOR`):

| Source id | Checksum |
|---|---|
| `tep-agent-lab.benchmark-case.rca-dev-004.v1` | `2b30594c0e7f286b7dbb07b1dc25638c80b59d7af202e9c1f8ceba6d513427af` |
| `tep-agent-lab.benchmark-ground-truth.rca-dev-004.v1` | `f8c146ee10fea247a9e924ab51a06aacf3943632527de4d044b9c328122813cd` |

`BenchmarkRefs` (and so the internal manifest) records `setup_policy_version =
benchmark-setup-policy/v1` and `leakage_policy_version = benchmark-leakage-policy/v1`.
The setup attestation of a prepared run records `setup_policy_version =
benchmark-setup-policy/v1`, `operation_count = 2` (two advances, nothing applied) and
`final_simulation_time_hours = 0.6`. The run also records the case/truth source
checksums, the exact WorldSpec checksum, the hidden setup checksum and the sanitized
final observation checksum. P0 checks all of these before READY, as it does for incident
setups.

## Scoring (frozen `benchmark-scorer/v0`)

| Submission | `healthy_no_abnormal_correct` |
|---|---|
| `NO_ABNORMAL_CAUSE`, all cause fields null | `AVAILABLE true` (and `top1_causal_claim_exact` true) |
| any incident claim | `AVAILABLE false` |
| no claim (including every saved D0 scripted run) | `NOT_AVAILABLE` |

Incident cases keep `healthy_outcome_enabled = false`, so the metric stays
`NOT_APPLICABLE` there. There is no aggregate score.

## Proven by tests

`tests/test_benchmark_healthy.py` covers:

- historical v0 fixture bytes (file sha256 of the committed LF bytes) and their
  case/truth/projection checksums are unchanged;
- v0 still loads as `HiddenSetup`, is not rewritten, and keeps setup policy v0;
- v1 rejects unknown tags, missing tags and unknown fields;
- v1 `DISTURBANCE` validates and runs the same trajectory as the equivalent v0 setup
  (same final observation checksum) under setup policy v1;
- v1 `NO_INTERVENTION` validates; non-positive or invalid hours are rejected;
- no disabled or fake disturbance is accepted, and truth/setup kinds must agree;
- the healthy identity, frozen checksums and exact `NO_ABNORMAL_CAUSE` truth;
- the healthy sources are EVALUATOR-only and have opaque names;
- there is no healthy-answer leakage (`healthy`, `no_fault`, `no_abnormal`,
  `NO_ABNORMAL_CAUSE`, `NO_INTERVENTION`, setup/v1 and policy/v1 strings, source refs,
  setup checksum) in the Agent projection, the RunRequest, the persisted model
  ContextProjections or any collected Agent surface;
- the attestation is mandatory; a missing, `None`, v0-policy or mismatched attestation
  keeps the run `CREATED`;
- setup applies no disturbance, and the result equals a plain same-seed advance;
- same-seed setup gives identical attestations, observations and history checksums;
- one `create -> prepare -> start` run through P0 and the Coordinator ends
  `COMPLETED` / `DONE`, and the structural LeakageAudit passes under policy v1; the
  healthy surfaces also pass each incident harness's v0 audit;
- incident fixtures report leakage policy v0 and the healthy fixture v1, in
  `BenchmarkRefs`, the internal manifest and every emitted audit; the Agent manifest
  exposes no benchmark policy field;
- policy v1 detects `NO_INTERVENTION`, setup/v1 schema and setup-policy/v1
  contamination, while the incident v0 labels and audits are unchanged;
- the scorer gives the healthy outcome semantics in the table above;
- `rca-dev-004` and the v1 setup types are not referenced from any Agent or application
  module.

## D0.2C nuisances (recorded, not solved here)

These Agent-visible properties are correlated with case identity or class. D0.2C will
create comparable experimental variants or new case versions as needed.

| Case | Class | `initial_time_hours` | World seed | Goal wording |
|---|---|---|---|---|
| `rca-dev-001` | incident | 0.3 | 11 | "observed abnormal ... Do not assume a cause label." |
| `rca-dev-002` | incident | 0.6 | 12 | same as 001 |
| `rca-dev-003` | incident | 0.6 | 13 | same as 001 |
| `rca-dev-004` | healthy | 0.6 | 14 | "observed ... Do not assume that an abnormal cause exists." |

- **Goal wording:** separates healthy from incident by text alone.
- **`initial_time_hours`:** `rca-dev-001` alone sits at 0.3 h.
- **World seed:** unique per case. P0's AGENT-scoped manifest currently includes the
  `world` section and so the seed, which makes each case identifiable from it.
- **Healthy/incident class imbalance:** one healthy case against three incidents.

## Known limitations (for D0.2C, not fixed here)

- **Goal wording asymmetry.** The healthy goal is neutral ("Investigate the observed
  reactor thermal behavior ... Do not assume that an abnormal cause exists."). The frozen
  incident goals say "observed abnormal reactor thermal behavior ... Do not assume a
  cause label." Rewriting the incident goals would change historical fixture bytes, so
  they were left alone. As a result, the goal text alone separates the healthy case from
  the incident cases. D0.2C must treat goal wording as a nuisance variable, for example
  through new case versions with one shared neutral goal, before it makes any healthy vs
  incident claim.
- **Shared "incident" framing.** Every case, including the healthy one, uses an
  `incident-<case_id>` id and the `task_goal` suffix "Incident observed at simulation
  time ... h." This wording is the same for all cases, so it is not a tell. It does
  frame every case as an incident.
- **Setup policy version is kind-correlated (EVALUATOR only).** Only `rca-dev-004` uses
  setup policy v1, so `setup_policy_version` in the internal manifest separates it from
  the incident cases. That field is EVALUATOR-only: P0's Agent manifest projection omits
  the whole `benchmark` section, and the tests check that the healthy run's Agent
  surfaces contain no setup/v1 or policy/v1 string. Leakage policy v1 tokenizes them
  for the healthy case.
- **Outcome consistency is a harness check.** `BenchmarkCase` and
  `EvaluatorGroundTruth` are separate sources, so the healthy-truth-iff-`NO_INTERVENTION`
  rule is enforced in `BenchmarkHarness`, which every load path goes through.
- **Single healthy case.** There is one healthy fixture against three incidents, with one
  seed and one timeline. That is not balanced and supports no false-positive rate.
- **Scorer incident-field defects are unchanged.** Exact-null
  `variable_or_actuator_id` matching and the unpublished free-string `fault_family`
  still need a scorer-version redesign (D0.2C planning). They do not affect the healthy
  case, because a correct healthy claim leaves every cause field null.

## Next

- D0.2C — C0 and the identifiability pilot, with goal wording, initial time, world seed
  and healthy/incident class balance treated as nuisance variables.
