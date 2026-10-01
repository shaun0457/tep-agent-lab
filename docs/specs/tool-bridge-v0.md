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

## C5 implementation contract (v0)

Status: implemented on `feat/tool-bridge-v0` (lab base `57bc526`), against
`tep-sim` `4261dc7` and `industrial-agent-runtime` `2f243bd`. This section freezes the
numerical semantics of the first-RCA bridge tools; `src/tep_agent_lab/tool_bridge.py`
implements exactly this section.

### Scope and authority

Three Agent-visible tools, all `side_effect_class = COMPUTE`:

```text
compute_response_features
analyze_cross_correlation
compare_trajectories
```

They are analysis adapters over **already existing, verified lab artifacts**. They
never run, fork, snapshot, or step a simulator, declare no budget draw, and draw no
simulation dimension (the runtime G3 gate denies a non-SIMULATE tool that declares
one). Every call goes through `ToolSpec -> B2 gates -> lab validate_request ->
Executor -> bridge adapter -> ToolResult -> B3 verifier (+ lab verify_result) -> C1
ingestion`. A successful result becomes one `ObservationRecord` (plus
`REGISTER_ARTIFACT_REF` for a derived dense artifact); it never creates an
`EvidenceLink`.

The detector baseline adapter is not implemented: no D0 requirement for it exists yet.
SALib/Optuna/PCA/PLS/Granger/graph toolboxes/MCP/remote providers are out of scope.

Every bridge ToolSpec requires the policy tag `tep.agent_visible_analysis`.
`BridgedToolSurface` composes the unchanged C4 surface with the bridge, routes each
hook by tool name, and grants `READ + COMPUTE + SIMULATE` plus that tag. It requires
the bridge's reference guard to be the surface's own `reference_revision`, so both
catalogs check one reference truth.

### Backend and provenance

- Backend: NumPy only (`implementation_id = tep-agent-lab.bridge.numpy-local/v0`),
  the already attested `numpy` pin. SciPy is **not** added: every operation below is a
  short closed-form NumPy expression, so a new dependency would add risk without value
  (build-versus-bridge rule).
- `ToolSpec.provider_metadata`: `tool_version`
  (`tep-agent-lab.tool-bridge/v0/<tool>@1`), `bridge_version`, `implementation_id`,
  `library_name`, `library_version` (the imported NumPy), and
  `numerical_semantics_version` (`tep-agent-lab.bridge-numerics/v0`).
- `ToolResult.provenance`: tool name/version, bridge version, request id, created_at,
  the same implementation block, the exact `configuration` (windows, preprocessing,
  selection/alignment, feature parameters), `inputs` (artifact id, kind, checksum of
  every input artifact), and `sampling` (window intervals, alignment,
  `resampled: false`).
- A different NumPy version changes the ToolSpec checksum, the registered tool-set
  version, and every result's provenance.

### Inputs

- Data inputs are exact Agent-visible `InformationRef` envelopes issued by the lab
  `ArtifactStore` (`owner = tep-agent-lab`, `version = tep-agent-lab.artifacts/v0`,
  `visibility = AGENT`, 64-hex checksum, `ref_id = tep-artifact-NNNNNN`). The runtime
  G0 gate already requires each envelope to equal a known Agent-visible ref of the run;
  the lab additionally re-resolves it in the store, re-verifies the persisted bytes'
  checksum, and accepts only these kinds:
  - `HistoryWindowArtifact` (rows `{simulation_time_hours, <variable>: value}`);
  - `RolloutTelemetryArtifact` (sanitized observation rows with `measurements` and
    `manipulated_variables`).

  Bridge output artifacts are not accepted as inputs. There is no path, file, URL,
  module, function, expression, or code field anywhere in the input schemas
  (`additionalProperties: false` everywhere); non-AGENT refs fail closed.
- Variables are canonical visible `XMEAS(n)`/`XMV(n)` ids that exist in the tep-sim
  registry and in every record of the artifact. A missing variable or a
  non-numeric/non-finite value rejects the request (no imputation, no NaN skipping).
  Artifacts cannot contain NaN/Inf (the store serializes with `allow_nan = false`).

### Time, windows, sampling

- Simulation time is carried in hours in artifacts and windows. The bridge converts
  every timestamp and window bound to integer seconds: `s = round(h * 3600)`, accepted
  only when `|h * 3600 - s| <= 1e-6 s` (float representation tolerance of the hour
  encoding, not an engineering threshold). Anything else is rejected.
- Source timestamps must be strictly increasing. Uniform sampling is required inside
  each **selected window** only: its samples share one integer-second step `dt`
  (`null` for a one-sample window). A rollout whose last record is off-grid (early
  shutdown, or a horizon that is not a multiple of the record interval) therefore stays
  analyzable before that record, while a window containing it is
  `SAMPLING_INCOMPATIBLE`. Reported intervals are window intervals.
- A window `{start_hours, end_hours}` is closed: samples with
  `start_s <= t_s <= end_s`. `start <= end` is required and the window must lie inside
  the artifact's time span; a window that extends beyond the data is rejected, never
  silently truncated. A window with no sample is rejected.
- **No resampling, interpolation, or extrapolation exists in v0.** No resampling
  policy is offered, so incompatible sampling is always rejected. Results report
  `resampled: false`.
- Durations/lags are reported in integer seconds (`*_seconds`); absolute times in hours
  (`*_hours`). Times beyond `2**53` s are rejected (not exactly representable).
- Lab policy bounds (`BridgeLimits`, model-visible in the schemas): at most 8
  variables, 241 lags, `|lag| <= 14400 s`, and 20000 records per input artifact;
  `min_overlap_samples <= 20000`. A larger artifact is rejected (`INVALID_REQUEST`).
  Current C4 artifacts are far smaller (history <= 4 h, rollout <= 1 h).

### `compute_response_features`

Request: `trajectory_ref`, `variables` (1-8, distinct), `analysis_window` (required),
`baseline_window` (required only when a requested feature uses the baseline), and
`features` (1-7 objects, distinct by `feature`, each carrying only its own
parameters).

Let `a_i` be the analysis-window samples of variable `x` at times `t_i`
(`i = 0..n-1`), `t_start` the requested analysis start (seconds), and
`b = mean(baseline-window samples)` (arithmetic mean, float64).

| Feature | Parameters | Definition | Unit |
|---|---|---|---|
| `DELTA` | none; baseline | `a_{n-1} - b` | variable unit |
| `DIRECTION` | `deadband >= 0` (required); baseline | `d = a_{n-1} - b`; `INCREASE` if `d > deadband`, `DECREASE` if `d < -deadband`, else `UNCHANGED` | category |
| `PEAK` | none | `max a_i`; time = earliest `t_i` attaining it | variable unit |
| `MINIMUM` | none | `min a_i`; time = earliest `t_i` attaining it | variable unit |
| `ONSET_TIME` | `threshold > 0` (required); baseline | first `i` with `abs(a_i - b) > threshold` (strict); value `t_i - t_start`; none -> status `NOT_DETECTED`, value `null` | s |
| `STEADY_STATE_RANGE` | none | `max a_i - min a_i` over the requested window; the tool does not judge whether the window is steady | variable unit |
| `INTEGRATED_ERROR` | `integrand` = `SIGNED`/`ABSOLUTE` (required); baseline; `n >= 2` | `e_i = a_i - b` (or `abs(a_i - b)`); trapezoid `sum((e_i + e_{i+1}) / 2 * (t_{i+1} - t_i))` | unit * s |

Explicitly **unsupported** in v0 (deterministic `UNSUPPORTED_CAPABILITY` denial,
never a guessed default):

- `SETTLING_TIME`: no public contract freezes the settling target (final value versus
  a new steady state) or how settling is confirmed inside a finite window;
- `LAG`: needs a second signal; use `analyze_cross_correlation`;
- `TRAJECTORY_DISTANCE`: needs a reference trajectory; use `compare_trajectories`;
- `CORRELATION`: needs a second signal; use `analyze_cross_correlation`;
- `EVENT_OR_SHUTDOWN`: reported by the `run_rollout` safety summary;
- `QUALITATIVE_UNSCORED`: not computable by definition.

A feature that needs the baseline without a `baseline_window`, a `baseline_window`
that no requested feature uses, a missing or non-finite threshold/deadband, a missing
integrand, or a parameter that does not belong to the feature is rejected
(`INVALID_REQUEST`). Numeric values are finite floats and `DIRECTION` uses
the C3 `Prediction` vocabulary (`INCREASE`/`DECREASE`/`UNCHANGED`), so outputs feed
`evaluate_prediction` directly.

### `analyze_cross_correlation`

Request: `data_ref`, `x`, `y` (distinct variables of the same artifact, hence aligned
by construction), `window`, `lag_range {min_lag_seconds, max_lag_seconds}`,
`min_overlap_samples` (>= 3), `preprocessing`, `selection`.

- Preprocessing (applied to each windowed series before lagging; allowlisted enum):
  - `NONE`;
  - `FIRST_DIFFERENCE`: `x'_i = x_{i+1} - x_i`, stamped at `t_{i+1}` (N - 1 samples);
  - `LINEAR_DETREND`: subtract the least-squares line in elapsed seconds,
    `x'_i = (x_i - mean x) - beta (t_i - mean t)`,
    `beta = sum((t_i - mean t)(x_i - mean x)) / sum((t_i - mean t)^2)`.
- **Sign convention:** `r(L) = corr(x(t), y(t + L))`. `L > 0` means `y` follows `x`
  by `L` seconds (`X_LEADS_Y`); `L < 0` means `x` follows `y` (`Y_LEADS_X`); `L = 0`
  is `ZERO_LAG`. Lag unit is seconds; `lag_samples = L / dt`.
- Allowed lags: integers with `min_lag_seconds <= max_lag_seconds`, both exact
  multiples of `dt`, every lag leaving `N - |k| >= min_overlap_samples` overlapping
  samples (N after preprocessing), at most 241 lags, `|L| <= 14400 s`. Otherwise the
  request is rejected before execution.
- For each lag `k` (samples) the overlap is `a = x'[0:N-k], b = y'[k:N]` for `k >= 0`
  and `a = x'[-k:N], b = y'[0:N+k]` for `k < 0`. The correlation is Pearson over the
  overlap with overlap means:
  `r = sum((a - mean a)(b - mean b)) / sqrt(sum((a - mean a)^2) sum((b - mean b)^2))`,
  clipped to `[-1, 1]`. If the denominator is exactly 0 (a constant overlap) that lag
  is undefined (`null`) and excluded from selection.
- Degeneracy (floating-point precision, not an engineering threshold): a preprocessed
  series or lag overlap `v` of signal `s` is constant when
  `max(v) - min(v) <= 1e-9 * max|s_raw|` over the raw (pre-preprocessing) window. An
  exact `== 0` test is not used because non-dyadic constants (e.g. 0.1) leave rounding
  residue after centering, differencing, or detrending. A lag whose overlap is
  constant on either side (or whose sums are non-finite) is undefined (`null`).
- Degenerate signals: if `x'` or `y'` is constant over the window, the result is
  `outcome = UNDEFINED`, `undefined_reason = CONSTANT_SIGNAL`, `constant_signals`
  names `x`/`y`, `best = null`. If every lag is undefined: `UNDEFINED` /
  `NO_DEFINED_LAG`.
- Selection: `MAX_CORRELATION` maximizes `r`; `MAX_ABS_CORRELATION` maximizes `|r|`.
  Scores are compared after rounding to 12 decimals; every lag with the maximal
  rounded score is listed in `tied_lags_seconds`, and the chosen lag is the
  lexicographic minimum of `(|L|, L)` (smallest magnitude, then the negative lag).
  Rounding is a deterministic bucket, not a tolerance: values within 1e-12 can fall in
  different buckets at a bucket edge. This is accepted and versioned with
  `tep-agent-lab.bridge-numerics/v0`.
- Dense output: the full per-lag curve (`lag_seconds`, `lag_samples`, `correlation`,
  `overlap_samples`) is a `CrossCorrelationCurveArtifact`; the model context gets only
  the best lag, ties, the zero-lag value, counts, and the artifact ref.
- Correlation is an observation, not a causal claim (`interpretation_note`).

### `compare_trajectories`

Request: `reference_ref`, `candidate_ref`, `variables` (1-8), `alignment_policy`,
`reference_window`, `candidate_window` (only for elapsed alignment), `metrics` (1-5,
distinct), `preprocessing`.

- Alignment (allowlisted; never resampling):
  - `EXACT_TIMESTAMPS`: one window (`reference_window`; `candidate_window` must be
    absent) applied to both; the selected integer-second timestamps must be identical.
  - `ELAPSED_FROM_WINDOW_START`: each trajectory uses its own window; both windows must
    have equal duration, equal source interval, equal sample count, and the same
    sample phase relative to the window start. Samples are paired by index.

  Any mismatch is `SAMPLING_INCOMPATIBLE`.
- Preprocessing: `NONE`, or `REMOVE_INITIAL_VALUE` (each trajectory minus its own first
  aligned sample).
- Error `e_i = candidate_i - reference_i` over `n` aligned samples (equal weight per
  sample; sampling is uniform):
  - `MEAN_ERROR = mean e_i`;
  - `MEAN_ABSOLUTE_ERROR = mean |e_i|`;
  - `ROOT_MEAN_SQUARE_ERROR = sqrt(mean e_i^2)`;
  - `MAX_ABSOLUTE_ERROR = max |e_i|`, time = earliest reference-timeline sample;
  - `FINAL_ERROR = e_{n-1}`.

  All in the variable's unit.
- The tool returns metrics only: no scores, thresholds, match verdicts, or ranking.
  Benchmark scoring configuration is a separately versioned contract owned by
  `benchmark-design-v0.md`/`evaluation-v0.md`; this Agent-visible metric contract
  (`tep-agent-lab.trajectory-metrics/v0`) never reads or embeds it.

### Lab validation and verification

`validate_request` (after runtime G0-G3) enforces only domain/data invariants:
registered bridge spec, COMPUTE class, canonical variables, ref resolution/kind/
checksum, windows, sampling, lag range, supported features/metrics/preprocessing.
The executor re-validates against the current artifacts before computing.

The bridge requires a reference-revision guard at construction (the C4 world
revision); without one the reference-unchanged invariant would be vacuous. A curve
artifact written before a late `STALE_STATE` outcome stays in the append-only store
but is never returned or referenced.

`verify_result` fails closed unless: `SUCCESS`; the result is the unmodified executor
output; the reference-world revision did not change; provenance matches the dispatched
tool, bridge, and implementation, and records the request's exact input artifacts,
which still verify; zero budget draw; no hidden vocabulary; every artifact ref is an
Agent-visible store artifact of the tool's output kind, and the declared refs equal the
embedded output ref; embedded input refs equal the request's refs; every number is
finite; ingestion derives only `REGISTER_OBSERVATION`/`REGISTER_ARTIFACT_REF`.
