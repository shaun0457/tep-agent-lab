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
compare_rollouts(rollout_refs, metrics?)
```

Simulation tools operate only on isolated sandbox/branch state.

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
OK
```

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
