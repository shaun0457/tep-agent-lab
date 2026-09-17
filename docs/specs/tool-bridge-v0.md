# Tool Bridge v0

Status: proposal  
Owner repo: `tep-agent-lab`

## Goal

Reuse mature scientific/process-analysis libraries through narrow typed adapters while keeping authorization, budgeting, side-effect classification, and execution authority in the generic runtime.

The Tool Bridge is **not** the safety/permission layer. It is a domain adapter/provider layer behind runtime `ToolSpec` + gates + Executor.

## Layering

```text
Main Agent
   |
   v
industrial-agent-runtime Tool Registry
   |
   v
G0-G3 + lab validate_request
   |
   v
Executor
   |
   v
tep-agent-lab Tool Bridge adapter
   |
   v
Pinned library / tep-sim / external provider
   |
   v
normalized ToolResult + provenance + actual budget draw
   |
   v
post-execution verify_result
```

Correct responsibility split:

- Runtime: registry, schema, allowlist, budgets/resource reservation, side-effect policy, trace, dispatch.
- Lab Tool Bridge: map stable engineering contracts to pinned implementations and normalize results/provenance.
- `tep-sim`: environment truth/simulator operations.
- External library/provider: implementation only; it never grants runtime authority.

## Why bridge instead of rewrite

Bridge when a mature implementation exists and can be constrained by a stable narrow contract.

Benefits:

- less duplicated numerical/statistical code;
- pinned/versioned reproducibility;
- allows research to focus on tool selection/interpretation;
- scientific libraries can be replaced without changing Agent-facing semantics.

A mature library does not make the Agent's interpretation automatically correct.

## Local versus remote providers

### In-process/local tool

For ordinary Python scientific functions, prefer a direct typed adapter:

```text
ToolSpec -> adapter -> SciPy/NumPy/etc.
```

Do not add protocol/network overhead without a need.

### External/remote provider

A remote service may later be registered through an adapter/protocol such as MCP, HTTP, RPC, or a custom service API.

MCP is optional future Tool Provider plumbing, not a core dependency and not an authorization mechanism. MCP/provider metadata never bypasses runtime gates.

## Candidate providers

Possible implementation backends include:

- upstream TEP detector/analysis functionality;
- NumPy / pandas for deterministic transforms;
- SciPy for signal processing/correlation/lag/response features;
- scikit-learn for selected PCA/PLS baselines;
- NetworkX/equivalent for graph algorithms over normalized ProcessGraph;
- SALib for later sensitivity analysis;
- Optuna/other bounded search tools for later numeric optimization;
- statsmodels time-series tests only after preprocessing/stationarity policy is explicit.

A library is not a dependency merely because it is listed here.

## v0 first-RCA bridge set

Keep the first implementation deliberately small:

1. deterministic trajectory/response-feature comparison;
2. SciPy-style cross-correlation/lag analysis;
3. optional existing TEP detector baseline adapter if needed by the benchmark.

Defer SALib/Optuna/PCA/PLS/Granger bridges until the experiment requiring them is active.

## `BridgeToolSpec`

The lab implementation maps to the generic runtime `ToolSpec`.

```text
BridgeToolSpec
  tool_name
  semantic_version
  input_schema
  output_schema
  implementation_id
  library_name
  library_version
  allowed_functions
  deterministic_seed_policy?
  timeout/resource_limits
  side_effect_class
  declared_budget_draw
  max_budget_draw
  isolation_guarantee?
  provenance_fields
```

The runtime-visible ToolSpec must preserve side-effect and budget metadata.

## `BridgeToolResult`

```text
BridgeToolResult
  request_id
  status
  summary
  structured_result
  artifact_refs[]
  information_refs[]
  implementation/library versions
  input_refs
  seed/config
  warnings[]
  actual_budget_draw
  provenance
```

The adapter's actual usage is reconciled against the runtime reservation after execution.

## Compound tools

A compound adapter may execute multiple internal operations.

Example:

```text
optimize_parameters(..., trial_budget=50)
```

If those trials execute simulator rollouts, the registered tool MUST be:

```text
side_effect_class = SIMULATE
max_budget_draw includes optimizer_trials + simulation_rollouts + horizon
isolation_guarantee = isolated branch/sandbox only
```

One top-level tool call never hides N simulator runs from runtime accounting/evaluation.

Before dispatch:

```text
runtime reserves declared/max draw
```

After dispatch:

```text
adapter returns actual_budget_draw
runtime reconciles/records usage
```

If requested/reserved draw cannot fit remaining budget, execution is denied before the adapter starts.

## Initial analysis contracts

### Cross correlation / lag

```text
analyze_cross_correlation(
  data_ref,
  x,
  y,
  lag_range,
  preprocessing_policy
)
```

Returns typed lag/correlation/features with preprocessing/version provenance.

### Response features

```text
compute_response_features(
  trajectory_ref,
  variables,
  features,
  windows
)
```

Possible features:

```text
DIRECTION
DELTA
PEAK
MINIMUM
ONSET_TIME
LAG
SETTLING_TIME
STEADY_STATE_RANGE
INTEGRATED_ERROR
TRAJECTORY_DISTANCE
```

These align with typed `Prediction` objects.

### Trajectory comparison

```text
compare_trajectories(
  reference_ref,
  candidate_ref,
  variables,
  metrics,
  preprocessing_policy
)
```

This tool may be available both to deterministic baselines and Agents, but evaluator scoring configuration must be versioned separately so a benchmark does not silently collapse into "call the exact scorer" without disclosure.

## Later sensitivity/optimization contracts

When introduced:

```text
run_sensitivity_analysis(...)
optimize_parameters(...)
```

Both are `SIMULATE` if they internally run TEP branches.

The Agent selects the engineering question/search variables/bounds/objectives. Numeric trial selection is performed by the pinned search/optimizer backend.

## Graph analysis

Graph algorithms operate on normalized `ProcessGraph` data, not raw DEXPI implementation objects.

Examples:

```text
find_paths(...)
get_upstream_subgraph(...)
get_downstream_subgraph(...)
find_control_paths(...)
```

Whether a graph tool is visible in a blind benchmark is controlled by the benchmark tool policy.

## Input/data policy

Adapters MUST:

- validate canonical IDs/schema/units where applicable;
- reject evaluator-only/hidden fields;
- materialize only required windows/data;
- specify missing/NaN handling;
- record alignment/resampling/scaling/preprocessing;
- avoid silent sampling-frequency changes;
- return dense arrays as artifacts rather than model-context dumps.

## Security boundary

The bridge MUST NOT expose:

- arbitrary `eval`/`exec`;
- arbitrary Python module import;
- package installation by model request;
- unrestricted filesystem/network access;
- model-supplied arbitrary function names outside the allowlisted schema.

Agent shell/Python is not the normal tool model.

## Interpretation boundary

Bridge outputs are observations/results, not automatically evidence/conclusions.

Examples:

- correlation does not prove causality;
- PCA components do not prove root cause;
- Granger non-causality tests do not establish physical mechanism;
- optimizer results are scoped to objective/search space/scenario distribution.

The Agent must explicitly link observations to hypotheses when using them as evidence.

## Build-versus-bridge rule

Bridge when:

- mature implementation exists;
- semantic contract can be narrowed;
- versions/provenance can be pinned;
- outputs can be normalized/verified;
- resource use can be declared/accounted.

Implement locally when:

- behavior is TEP-specific glue;
- external API is too broad/unstable;
- canonical semantics depend on our own registry/contracts;
- operation is trivial enough that dependency risk exceeds value.

## Invariants

- Tool Bridge is not an authorization layer.
- Every bridge call goes through runtime ToolSpec/gates/Executor.
- Simulator-running compound tools are SIMULATE.
- Nested rollout/trial usage is declared and accounted.
- External provider protocol never grants authority.
- Tool result is observation/result data, not automatic evidence.

## Acceptance tests

1. Cross-correlation adapter returns typed result + pinned library/provenance from known fixture.
2. Response-feature adapter yields features usable by typed Prediction evaluation.
3. Hidden/evaluator data is rejected by an Agent-visible bridge.
4. Compound optimizer/sensitivity request whose trial/rollout budget exceeds remaining quota is denied before execution.
5. Compound SIMULATE bridge returns actual nested resource usage and never mutates reference state.
6. Attempted arbitrary Python/import request has no bridge path.
7. Changing backend/library version changes recorded ToolSpec/result provenance.
8. Runtime can register a future remote/MCP-provided adapter without changing authorization contracts.
