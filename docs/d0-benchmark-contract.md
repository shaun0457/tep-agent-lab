# D0.1 — Benchmark contract implementation

Status: implemented. Owning spec: [`specs/benchmark-case-v0.md`](specs/benchmark-case-v0.md).

D0.1 proves one reproducible blind DEVELOPMENT benchmark case end-to-end through the
normal P0 lifecycle. It is benchmark infrastructure, not an Agent-capability result:
there is no C0, no difficulty label, no healthy/variant pack and no real LLM.

## Implemented path

```text
EVALUATOR fixture (BenchmarkCase)   EVALUATOR ground truth (EvaluatorGroundTruth)
              \                         /
               BenchmarkHarness  (load -> frozen-checksum verify -> strict parse -> identity match)
                 |        |         |            |
                 |        |         |            +--> EVALUATOR-only ContextSourceRefs (x2)
                 |        |         +--> BenchmarkRefs (fully bound)
                 |        +--> trusted case_setup -> CaseSetupAttestation
                 +--> project_case -> AgentCaseProjection (+ checksum) -> RunRequest
                                   |
                     RunManager.create -> prepare (attestation checked) -> RunManifest
                                   |
                     RunManager.start -> Coordinator -> B2 gates -> lab consumer
                                   -> Executor -> B3 verification  (FakeProvider)
                                   |
                     LeakageAudit over AGENT views + captured model inputs
                                   |
                     deterministic metric-vector scorer (saved input, re-scored)
```

Code owners:

| Concern | Location |
|---|---|
| Benchmark contracts, projection, harness, audit, scorer | `src/tep_agent_lab/benchmark.py` |
| `BenchmarkPartition`, `BenchmarkRefs`, `CaseSetupAttestation`, attested prepare | `src/tep_agent_lab/playground.py` (P0-owned) |
| Case fixture / ground-truth fixture | `src/tep_agent_lab/fixtures/benchmarks/` (package data) |
| Tests | `tests/test_benchmark.py` |

There is no second manifest, scheduler, TaskStateStore, world or execution path.
The harness only builds inputs for `RunManager` and reads public P0 query views.

## First fixture identity (Agent-visible)

```text
benchmark_version   tep-rca-benchmark/v0
case_id             rca-dev-001
case_version        1
partition           DEVELOPMENT
task_family         RCA
incident_id         incident-rca-dev-001
trigger_signal_ids  XMEAS(9)
initial_time_hours  0.3
allowed_tools       get_capability_summary, get_history
budget              model 4, tool 2, subagents 0/0, steps 16, simulation quotas 0
world               seed 11, python backend, closed_loop, record_interval 60 s
```

Agent projection checksum: `5db21a1c22e9ea626ce5b993e460cefc557e33916759da0265c840b9cfa7b3b5`.

The Agent-visible goal is the frozen fixture goal plus the trigger signals and
incident time (`benchmark.task_goal`). The fixture goal says "Do not assume a cause
label": the wording "fault label" would trip P0's AGENT leakage screen, which treats
`fault` as hidden-truth vocabulary.

## EVALUATOR ONLY

Nothing in this section may appear in an Agent projection, `RunRequest`, task
metadata, ContextProjection, artifact, tool description, or Agent-visible error.

```text
scenario_family_id   reactor-thermal-v0
hidden_setup         advance 0.1 h -> DisturbanceIntervention IDV(4) = 1 -> advance 0.2 h
ground truth         entity_id          reactor_cooling_water_in
                     mechanism          TEMPERATURE_DISTURBANCE
                     variable_or_actuator_id  null
                     fault_family       INLET_TEMPERATURE_STEP
                     direction_or_mode  STEP
```

The claim agrees with the pinned tep-sim evaluator binding (`IDV(4)` → semantic
entity `reactor_cooling.inlet_temperature_step_disturbance`, attached to
`reactor_cooling_water_in`, quantity `temperature`); a test checks this through the
EVALUATOR ProcessGraph view instead of copying the binding registry into the lab.

Canonical sources (both `visibility = EVALUATOR`, repository
`shaun0457/tep-agent-lab`, exact lab revision):

| Source id | Kind | Frozen canonical-JSON sha256 |
|---|---|---|
| `tep-agent-lab.benchmark-case.rca-dev-001.v1` | `BENCHMARK_CASE` | `94cfb522e7d1d17c8bf1a5f5e43913c1c3d80297d552c014ada20ffc9d8ed47c` |
| `tep-agent-lab.benchmark-ground-truth.rca-dev-001.v1` | `EVALUATOR_GROUND_TRUTH` | `2feb22f1657624cbd739aeada44033758eceaa2555c540de1adaf381a91155f7` |

The checksums are frozen in `benchmark.D0_FIXTURE` and tests. They are never
recomputed from the bytes being attested. The harness verifies fixture bytes before
parsing, and P0's `CanonicalContextRegistry` re-attests them when they are
materialized through `PackageSourceMaterializer("tep_agent_lab")`.

## P0 binding

`BenchmarkRefs` is either completely unbound (developer/demo runs, including E0) or
completely bound with benchmark identity, partition, both EVALUATOR source ids, the
Agent projection checksum, and the scorer, setup-policy and leakage-policy versions.
A half-populated binding is rejected.

For a bound benchmark, `RunManager.prepare` requires a `case_setup` that returns a
typed `CaseSetupAttestation`. P0 checks:

- the identity and `setup_policy_version` against `BenchmarkRefs`;
- the case and ground-truth checksums against the source refs P0 itself attested;
- the world-config checksum against `WorldSpec.record()`;
- the final simulation time and the sanitized `ReferenceWorld.observe()` checksum,
  both recomputed by P0.

The `hidden_setup_checksum` is opaque to P0. Any failure (no setup, `None`, untyped
record, wrong identity or policy, bad checksum, exception) raises `PrepareError`:
the run stays `CREATED`, no manifest is published, and the partial session is
removed. Non-benchmark setups may still return `None` and keep the boolean
`case_setup_applied` record.

The internal manifest `benchmark` block records the full binding plus the
attestation. `RunManifest.agent_projection()` still excludes the whole block.

Trust boundary:

- P0 does not own the projection schema, so it checks only the shape of
  `agent_projection_checksum`. `BenchmarkHarness.binding_findings(manifest)` is the
  EVALUATOR check that a prepared run hosts exactly the projected case: checksum,
  investigation id, goal, budget, allowed tools and world. `create_and_prepare` runs
  it, and tests cover each mismatch.
- P0 cannot see the hidden setup either. An evaluator test rebuilds the incident
  world independently and checks that the attested observation equals it and differs
  from the un-intervened world.

The PyInstaller desktop sidecar does not bundle the benchmark fixtures. D0 runs are
source or wheel workflows only.

## Blind run

`scripted_blind_provider()` takes no harness, case or truth input. It reads the
trigger signals and window from the Agent-visible goal in each ContextProjection and
issues `get_capability_summary`, then `get_history(XMEAS(9), 0.3 h)`, then finish
(`require_conclusion=False`). It claims no diagnosis. `ModelInputRecorder` wraps the
provider and captures exactly what each turn received (projection, tool specs, output
schema, limits) for the evaluator-side audit.

## LeakageAudit

`audit_agent_surfaces` walks every key and string of each Agent surface. It flags:

- disturbance-id spellings and hidden-truth vocabulary;
- exact evaluator labels: the hidden id, scenario family, truth mechanism and fault
  family;
- the hidden setup checksum;
- hidden source ids, paths, file names and checksums;
- structural `visibility: EVALUATOR` refs or hidden source kinds.

Findings record `surface`, a JSON location (hidden keys are addressed by position)
and a category; they never copy the payload. Any finding sets `passed = false`. The
audit is EVALUATOR output.

D0.1 audits the AgentCaseProjection, the AGENT manifest, context inventory, run
summary, ProcessGraph, investigation, events, budget, artifacts and artifact
contents, telemetry, branch tree, and every captured model input.

## Deterministic scorer

`score_submission(submission, truth, config)` is a pure function of saved records.
It does not call a model, the simulator, the UI, or a clock. It returns the frozen
15-metric vector with no aggregate or weighted score. Each metric is typed
`AVAILABLE` (with a value), `NOT_APPLICABLE` (excluded by the scoring config) or
`NOT_AVAILABLE` (no such data in the saved input).

Evidence statuses are classified only structurally:

- `HIDDEN_REF_VIOLATION`, `MISSING_REF` and `UNUSED_OBSERVATION` come from ref
  visibility and availability;
- `UNSUPPORTED_NARRATIVE_CLAIM` is a causal claim with no valid cited ref;
- scorer v0 has no frozen relevance configuration, so `VALID_RELEVANT_REF` and
  `VALID_BUT_IRRELEVANT_REF` are `null`, and valid refs are counted separately.

Two kinds of submission exist:

- a `SYNTHETIC` submission is an evaluator-side scorer fixture and is never injected
  into a run;
- a `RUN_OUTCOME` submission comes from a finished run through public trusted reads.
  D0 has no structured RcaResult boundary yet, so its claim is recorded as absent,
  not inferred from text.

Re-scoring a saved submission JSON with the same truth and scorer version produces
byte-identical canonical output.

## Validation

```text
python scripts/check.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim
```

## Next (not D0.1)

These remain open:

- healthy `NO_ABNORMAL_CAUSE` fixture;
- additional variants;
- C0 enumerate/simulate/match;
- the identifiability pilot and difficulty labels;
- a structured RcaResult submission boundary;
- the real-LLM D1 blind run.
