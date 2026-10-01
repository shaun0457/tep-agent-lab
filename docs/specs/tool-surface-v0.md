# TEP Agent Tool Surface v0

Status: proposal  
Version: v0  
Owner repo: `tep-agent-lab`  
Depends on: `tep-sim`, `industrial-agent-runtime`

## Goal

Expose a narrow typed TEP tool surface without giving the Agent raw simulator internals, evaluator-only truth, arbitrary code execution, or reference-world mutation authority during diagnosis.

## Tool groups

### Read tools

```text
get_current_observation()
get_history(window, variables)
get_variable_metadata(ids)
get_process_node(node_id)
get_neighbors(node_id, direction?)
get_related_measurements(node_id)
get_related_actuators(node_id)
get_safety_margins()
```

Potentially answer-leaking relation tools such as canonical disturbance/fault bindings are **not in the default blind RCA allowlist**.

A benchmark may explicitly enable a semantic disturbance/cause relation tool as an ablation, but this must be recorded in `tool_policy` and treated as additional information/capability.

All read tools return compact structured results. Dense time-series data remains artifact-backed unless explicitly materialized within policy/budget.

### Analysis tools

Initial analysis is provided through `tool-bridge-v0.md`:

```text
compute_response_features(...)
analyze_cross_correlation(...)
compare_trajectories(...)
```

Tool Bridge results are observation/results and become evidence only through explicit evidence links.

### Simulation tools

```text
snapshot_environment()
fork_environment(snapshot_id)
check_scenario_capability(scenario)
compile_process_deviation(deviation)
run_rollout(branch_id, horizon, interventions?)
```

Rollout/trajectory comparison is not a C4 simulation tool: it belongs to the C5 Tool
Bridge trajectory-analysis contract (`tool-bridge-v0.md`).

Simulation tools operate only on isolated sandbox/branch state.

Semantic scenario interventions execute only on branches descending from a trusted
harness-designated counterfactual baseline/origin. Incident/reference-lineage
branches may be rolled forward but may not receive semantic candidate scenarios.

**SIMULATE never mutates the reference branch.** There is no recovery exception to this rule.

### Proposal/recovery tools

When recovery experiments are enabled:

```text
propose_intervention(proposal)
validate_intervention(proposal, reference_state_ref)
apply_validated_intervention(validation_token)
```

`apply_validated_intervention` is `MUTATE`, not SIMULATE.

It is disabled in blind RCA and AutoResearch by default. When enabled later, it requires the complete deterministic request-validation path and a validation token bound to the exact expected reference-state revision.

## Runtime authority classes

```text
READ       -> observation/topology/history queries
COMPUTE    -> non-simulator deterministic analysis
SIMULATE   -> isolated branch/sandbox execution
PROPOSE    -> candidate intervention/plan data only
MUTATE     -> validated reference-state application
```

A compound analysis/optimization tool that internally runs simulator trials is SIMULATE even if its top-level name sounds like analysis/optimization.

## Pre-execution domain validation

All tool calls first pass generic runtime G0–G3.

The lab exposes one logical deterministic consumer request validator:

```text
validate_request(request, task_policy, application_context, expected_revision)
```

The lab may internally compose:

```text
experiment/benchmark policy
+ tep-sim capability/bounds/control-mode checks
```

For enabled MUTATE paths it also checks:

- allowed actuator/action set;
- intervention magnitude/rate limits;
- cooldown/count limits;
- simulate-before-apply requirement;
- exact expected reference-state revision.

## Post-execution result verification

The lab exposes deterministic result invariants through the runtime post-execution verifier, such as:

- result/artifact refs exist;
- simulator branch identity/provenance is correct;
- reference branch remained unchanged for SIMULATE;
- actual rollout/horizon/trial usage is reported;
- hidden/evaluator-only fields did not enter Agent-visible results.

## Blind RCA information boundary

Initial Agent context should contain only configured visible information, such as:

- incident/task identity;
- current timestamp/state summary;
- trigger/top abnormal signals;
- minimal local topology seed;
- current safety summary;
- available tools/budgets.

It does not automatically receive:

- injected IDV/fault ID;
- evaluator candidate-cause catalog;
- canonical node-to-fault answer list;
- complete ProcessGraph;
- all telemetry history.

The Agent discovers additional engineering evidence through allowed queries/experiments.

## Artifact/result shape

Dense outputs return:

```text
summary
schema
shape
artifact_ref
selected deterministic features/preview
provenance
actual_budget_draw
```

not complete arrays in model context.

## Failure behavior

```text
INVALID_REQUEST
UNSUPPORTED_CAPABILITY
POLICY_DENIED
BUDGET_DENIED
SIMULATION_FAILED
ARTIFACT_ERROR
STALE_STATE
SUCCESS
```

`SUCCESS` is the runtime canonical result status (`RESULT_SUCCESS`); it replaces the
earlier `OK` wording. Only `SUCCESS` results are ingestible.

Unsupported requests are explicit evidence for replanning; the Agent cannot fabricate simulator outcomes.

## Ground-truth isolation

Evaluator-only APIs/fixtures are never registered into the Agent tool set.

Leakage audit covers:

- tool names/descriptions;
- result metadata;
- artifact filenames/IDs;
- ProcessGraph bindings;
- Rule Registry entries;
- ContextProjection refs.

## Invariants

- Blind RCA does not expose canonical hidden fault IDs/candidate answer bindings by default.
- SIMULATE never mutates reference state.
- MUTATE is a separate high-authority class/path.
- Every tool call passes runtime gates plus consumer request validation.
- Every simulator-running bridge/tool reports nested resource use.
- Hidden evaluator truth never appears through Agent-visible tools.

## Acceptance tests

1. Query reactor topology/measurements/actuators without exposing hidden injected cause ID.
2. Confirm `get_related_disturbances`/canonical fault-binding enumeration is absent from the default blind RCA allowlist.
3. Run an isolated fork/rollout and prove reference state is unchanged.
4. Attempt to use SIMULATE for reference mutation and verify deterministic denial.
5. Attempt direct MUTATE in blind RCA/AutoResearch and verify tool is unavailable/denied.
6. When recovery MUTATE is later enabled, validate/apply only a state-revision-bound frozen request.
7. Return dense rollout data via artifact refs with actual budget usage.
8. Hidden-fault lookup through registered Agent tools is impossible in blind mode.

## Accepted C4 adjudications

- **SC-1 semantic scenario discovery.** The default blind-RCA tool policy MUST NOT
  enumerate the supported semantic scenario catalog or the evaluator candidate
  family. Query by an Agent-proposed scenario/mechanism remains allowed. `AMBIGUOUS`
  results MUST NOT return candidate mappings. Scenario enumeration may be enabled
  later only as an explicit, recorded tool-policy/capability ablation. If exact
  scenario ids prove too brittle for model use, D0 may study a versioned
  non-enumerative alias/normalization contract; none exists in v0.
- **SC-2 result status.** `SUCCESS` (runtime canonical), see Failure behavior.
- **SC-3 rollout comparison.** Removed from the C4 simulation surface; C5 Tool
  Bridge trajectory analysis owns it.
- **SC-4 counterfactual origin.** Semantic scenarios execute only on branches
  descending from a trusted harness-designated counterfactual baseline/origin;
  incident/reference-lineage branches allow forward rollouts only. The trusted
  benchmark harness, not the Agent and not `active_disturbances`, is authoritative
  for designating the origin before incident injection. An `active_disturbances`
  check may remain as IDV-specific defense in depth but is not proof that arbitrary
  hidden incident state (XMV, constraint, operating-condition manipulation) is
  absent. D0 must separately define counterfactual-origin timing visibility;
  baseline/origin metadata must not encode hidden benchmark injection timing unless
  that timing is explicitly Agent-visible under the benchmark policy.
- **SC-5 dynamic request reservation.** Accepted runtime gap, not a lab workaround.
  v0 keeps the conservative maximum reservation for expression draws; the lab does
  not parse or evaluate budget expressions. A separate runtime B2.1 contract for
  request-bound reservations (preserving D-037, no expression DSL) is planned
  before D0.

## v0 implementation notes (C4)

Implemented in `src/tep_agent_lab/tool_surface.py` and `src/tep_agent_lab/tep_world.py`
against `tep-sim` `ae1c14d` and `industrial-agent-runtime` `2f243bd`. These notes
record implementation-defined details; they do not change the contract above.

- Default blind-RCA registry: `get_current_observation`, `get_history`,
  `get_variable_metadata`, `get_process_node`, `get_neighbors`,
  `get_related_measurements`, `get_related_actuators`, `get_safety_margins`,
  `get_capability_summary`, `check_scenario_capability`,
  `compile_process_deviation` (READ; none mutates any world); `snapshot_environment`,
  `fork_environment`, `run_rollout` (SIMULATE). No PROPOSE/MUTATE tool is
  registered; the lab `GatePolicy` grants READ + SIMULATE only.
- Results use `SUCCESS` or a listed failure code; consumer pre-execution denials
  use the same codes as `GateDecision.reason_code`.
- SIMULATE budget dimensions: `simulation_snapshots`, `simulation_branches`,
  `simulation_rollouts`, `simulated_horizon_seconds`. `run_rollout` declares the
  horizon as an expression and therefore reserves its `max_budget_draw` (SC-5);
  the result reports the actually simulated seconds (shorter only on shutdown or
  failure).
- Once `run_rollout` has applied a scenario or started advancing a branch, any
  failure that prevents a `SUCCESS` result (simulation, telemetry read or
  sanitization, safety evaluation, artifact persistence, post-processing) retires
  that branch and the snapshots it owns. A tep-sim validation rejection before any
  commit leaves the branch available.
- Hidden-truth isolation is an allowlist: Agent-visible observations and telemetry
  keep only measurements, manipulated variables, shutdown state, and safety
  margins. tep-sim `active_disturbances`, disturbance-kind safety events, raw
  artifact paths, run ids, and random state never leave the lab. Every result,
  artifact, ToolSpec, and failure reason is screened for disturbance identifiers
  and evaluator vocabulary; `tep_sim.evaluator_bindings` is never imported.
- Dense data is re-written as sanitized, checksummed lab artifacts with opaque
  sequential ids (`tep-artifact-NNNNNN`); `ArtifactStore.resolve` is the exact-ref
  resolver for C1 `REGISTER_OBSERVATION`/`REGISTER_ARTIFACT_REF`.
- The reference revision used by the runtime `ReferenceStateGuard` is salted, so
  the Agent cannot test disturbance guesses against a hash of otherwise-visible
  observation fields. It never appears in Agent-visible provenance.
- Supported semantic scenarios are not enumerated (SC-1); scenarios are queried by
  id, and `AMBIGUOUS` answers withhold their tested candidates. Compiled
  process-input deviations are reported as `PROCESS_DEVIATION` with the runtime
  target withheld.
- Scenario interventions (one per rollout) apply only on baseline-lineage branches
  (SC-4). The harness designates origins with `ReferenceWorld.designate_baseline`;
  `get_capability_summary` lists their snapshot ids only, without timing. Branch
  telemetry still carries simulation time, so D0 owns origin-timing visibility.
- Every result's `provenance.world` records the numerical stack (`numpy_version`),
  attested against `dependency-pins.json` by `scripts/check.py`.
