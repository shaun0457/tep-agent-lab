# D0.2A — Reactor thermal incident family

Status: implemented. Owning spec: [`specs/benchmark-case-v0.md`](specs/benchmark-case-v0.md).
Builds on [`d0-benchmark-contract.md`](d0-benchmark-contract.md) (D0.1).

D0.2A grows the frozen D0 infrastructure from one incident fixture to a small
DEVELOPMENT incident family. It adds incident variants only.

**These are candidate benchmark fixtures. No C0, identifiability, or difficulty claim
has been made.** There is no healthy fixture (D0.2B) and no C0 or identifiability pilot
(D0.2C).

## What changed

| Concern | Change |
|---|---|
| Fixture registry | `benchmark.BENCHMARK_FIXTURES`: an explicit immutable map `(case_id, case_version) -> PackagedFixture`. It is not discovered from a directory scan. `D0_FIXTURE` is kept as the `rca-dev-001` entry. |
| Evaluator helpers | `load_fixture(case_id, case_version, *, lab_revision)` returns a verified `BenchmarkHarness` and fails closed on an unknown identity. `iter_development_fixtures(lab_revision)` yields the DEVELOPMENT harnesses in registry order. |
| Fixtures | `rca-dev-002` and `rca-dev-003` case/ground-truth pairs, packaged next to `rca-dev-001`. |
| Tests | `tests/test_benchmark_family.py`. `tests/test_benchmark.py` is unchanged. |

Unchanged: the contracts, the `benchmark-setup/v0` exactly-one-disturbance rule,
projection, setup attestation, P0, the runtime path, tool policy, the leakage policy and
the scorer. `rca-dev-001` keeps its case, ground-truth and projection checksums.

The registry and helpers are EVALUATOR code. No Agent tool, application view, transport
or desktop module imports them. A test checks this two ways: a source scan of those
modules, and a fresh interpreter that imports them all and finds
`tep_agent_lab.benchmark` absent from `sys.modules`.

## AGENT-visible family properties

All three cases project the same task. They differ only in opaque identity and
incident time:

```text
benchmark_version   tep-rca-benchmark/v0
case_id             rca-dev-001 | rca-dev-002 | rca-dev-003
case_version        1
incident_id         incident-<case_id>
goal                Investigate the observed abnormal reactor thermal behavior and
                    identify the most plausible supported causal mechanism using
                    available evidence. Do not assume a cause label.
trigger_signal_ids  XMEAS(9)
initial_time_hours  0.3 (rca-dev-001) | 0.6 (rca-dev-002, rca-dev-003)
allowed_tools       get_capability_summary, get_history
budget              model 4, tool 2, subagents 0/0, steps 16, simulation quotas 0
```

Agent projection checksums:

| Case | Projection checksum |
|---|---|
| `rca-dev-001` | `a9565b8ba5e21b53e2c22176fa79da36d48536c6325a629656ff32168bea6d35` |
| `rca-dev-002` | `75ff97da2b509f4e7e5836289bdccbac91557ade48eea6e36caa5f73bcb6d6ef` |
| `rca-dev-003` | `3d6a4a02a9046ae71c323307b4846467203470bcfb8f01b3b5a407df44802042` |

The orchestration condition is `O3` for all three cases. It is EVALUATOR metadata and
not part of any projection. D0 runs a deterministic authority/state shell with local
one-action ReAct, no WorkBatch and no subagents.

## EVALUATOR ONLY

Nothing in this section may appear in an Agent projection, `RunRequest`, task
metadata, ContextProjection, artifact, tool description or Agent-visible error.

```text
scenario_family_id  reactor-thermal-v0
partition           DEVELOPMENT        task_family  RCA
world               python backend, closed_loop, record_interval 60 s
```

| Case | Seed | Hidden setup | Pinned evaluator binding |
|---|---|---|---|
| `rca-dev-001` | 11 | advance 0.1 h, then `IDV(4) = 1`, then advance 0.2 h | `reactor_cooling.inlet_temperature_step_disturbance`, attached to `reactor_cooling_water_in`, quantity `temperature` |
| `rca-dev-002` | 12 | advance 0.1 h, then `IDV(11) = 1`, then advance 0.5 h | `reactor_cooling.inlet_temperature_random_disturbance`, attached to `reactor_cooling_water_in`, quantity `temperature` |
| `rca-dev-003` | 13 | advance 0.1 h, then `IDV(14) = 1`, then advance 0.5 h | `reactor_cooling.valve_sticking_disturbance`, attached to `reactor_cooling_water_in`, quantity `valve_position` |

Ground truth (`causal_claim`):

| Case | entity_id | mechanism | variable_or_actuator_id | fault_family | direction_or_mode |
|---|---|---|---|---|---|
| `rca-dev-001` | `reactor_cooling_water_in` | `TEMPERATURE_DISTURBANCE` | null | `INLET_TEMPERATURE_STEP` | `STEP` |
| `rca-dev-002` | `reactor_cooling_water_in` | `TEMPERATURE_DISTURBANCE` | null | `INLET_TEMPERATURE_RANDOM_VARIATION` | `RANDOM` |
| `rca-dev-003` | `reactor_cooling_water_in` | `VALVE_STICKING` | null | `REACTOR_COOLING_VALVE_STICKING` | `STICKING` |

Frozen canonical-JSON sha256 values (all sources `visibility = EVALUATOR`):

| Source id | Checksum |
|---|---|
| `tep-agent-lab.benchmark-case.rca-dev-001.v1` | `e3305b5cd4ba1e2c59c625e5e067ceb067c90a9b9db3631d4d38bc80c088e176` |
| `tep-agent-lab.benchmark-ground-truth.rca-dev-001.v1` | `2feb22f1657624cbd739aeada44033758eceaa2555c540de1adaf381a91155f7` |
| `tep-agent-lab.benchmark-case.rca-dev-002.v1` | `ea571876db66c11e40dfb554849b60d5819edd74f8044eabcf7046ad9c76caf5` |
| `tep-agent-lab.benchmark-ground-truth.rca-dev-002.v1` | `7f6d3bb9621a72f56252db028633a60da7e09c4fa5f641b0846ee73c91a28ec0` |
| `tep-agent-lab.benchmark-case.rca-dev-003.v1` | `f2ed7acf8fdd568e0832535d47db74820a00bef6db4120cbe6c31b98ad502a7d` |
| `tep-agent-lab.benchmark-ground-truth.rca-dev-003.v1` | `09aa3063514ec22499494092d3fdc7eb35a87b46bf144daaf614ad6ebf8455a0` |

### Truth verification

Tests check every case's truth against the pinned tep-sim EVALUATOR ProcessGraph
overlay (tep-sim `ea0b7304090017b14ac13665595b6f5e9d195250`), read through P0's
EVALUATOR view of a prepared run. They do not compare it with a copied mapping. Each
case follows this chain:

```text
hidden disturbance id
 -> exactly one overlay binding (relation DISTURBS)
 -> semantic_entity_id
 -> attached_to == truth.entity_id (present in the AGENT topology)
 -> quantity -> mechanism (temperature -> TEMPERATURE_DISTURBANCE,
                           valve_position -> VALVE_STICKING)
```

`rca-dev-003` keeps `variable_or_actuator_id = null`. The HUMAN_VERIFIED ProcessGraph
has exactly one `ACTUATES` binding on `reactor_cooling_water_in`: `XMV(10)`, which is
`reactor_cooling.flow_actuator` with quantity `flow`. The pinned evaluator overlay
does not link the `IDV(14)` valve-sticking disturbance to any actuator. Recording
`XMV(10)` would therefore be a lab inference, not evaluator truth. A test enforces the
rule for every case: a non-null actuator id must be the unique verified actuator on the
truth entity. Today all three are null.

### Viability check (not C0)

Before freezing `rca-dev-002` and `rca-dev-003`, an EVALUATOR-only check compared each
incident against the same seed run over the same timeline without the intervention.
It checked only that:

- the setup is supported and completes;
- repeated setup is deterministic;
- the trajectory differs from the healthy run;
- the trigger signal and nearby reactor/cooling signals change;
- the observation window is long enough.

`IDV(11)` is a "random variation", but under the pinned simulator seed it is
deterministic.

The recommended first window, `pre 0.1 h / post 0.5 h`, was enough for both cases. It
was not lengthened. The figures below are authoring-time observations from that one-off
check (37 samples at 60 s; the first difference is at the first post-onset sample). Tests
do not pin these numbers. They enforce only the qualitative properties: setup is
deterministic, the pre-onset trigger history is identical, the post-onset trigger
history differs, and there is no shutdown.

| Case | max abs diff XMEAS(9) | XMEAS(21) | XMV(10) | shutdown |
|---|---|---|---|---|
| `rca-dev-002` | 0.031 | 0.029 | 2.33 | no |
| `rca-dev-003` | 0.30 | 1.50 | 8.72 | no |

These numbers only show that each incident differs from its own healthy run. They do
not compare one cause with another, rank causes, or support any separability,
accuracy or difficulty statement.

## Proven by tests

`tests/test_benchmark_family.py` covers:

- the registry has exactly these three fixtures and is immutable;
- `rca-dev-001` checksums are unchanged;
- identities match, and checksums are frozen and exact;
- fixture files reject unknown fields;
- context sources are EVALUATOR-only;
- the projections are structurally equivalent and blind;
- the projection is independent of the O3 condition;
- there is no cross-case label leakage, both in projections and in full run surfaces
  (each case's surfaces are audited against every other case's labels and sources);
- truth agrees with the pinned overlay, and actuator ids are verified or null;
- same-seed setup and history are deterministic;
- each incident differs from its same-seed healthy run;
- one `create -> prepare -> start` run per fixture through P0 and the Coordinator:
  valid attestation, `COMPLETED` / `DONE`, LeakageAudit PASS;
- the scorer scores a synthetic submission for each truth;
- the D0.1 synthetic re-score bytes are pinned;
- no Agent or application module can reach the registry.

## Known limitations (for D0.2C, not fixed here)

- **Incident time tracks the cause.** `rca-dev-001` is frozen at 0.3 h. The new cases
  use the preferred 0.1 h / 0.5 h window and so sit at 0.6 h. With one case per cause,
  the visible incident time alone separates `rca-dev-001` from the others. The
  identifiability pilot must treat time as a nuisance variable, for example by varying
  timing within each cause, before it makes any claim.
- **Scorer semantics of absent fields.** Truth `variable_or_actuator_id = null` is
  matched by exact equality. A submission that names an actuator therefore fails that
  field, as it already did for D0.1. `fault_family` is an unpublished free string, so a
  blind exact match is effectively unreachable. Both are scorer-vocabulary questions
  that need a new scorer version, not a fixture change.
- **The leakage policy skips `direction_or_mode`.** It does this because `STEP` is
  generic. `RANDOM` and `STICKING` are not, so the family tests check that these words
  are absent from every Agent surface. Adding them to the policy itself is a
  `leakage_policy_version` change.

## Next

- D0.2B — healthy negative contract and fixture. This needs a deliberate
  `benchmark-setup` contract change; D0.2A does not touch it.
- D0.2C — C0 and the identifiability pilot.
