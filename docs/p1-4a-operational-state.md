# P1.4A — Serving + Operational State

## LOS-A-S1 (issue #40): current, staleness, endpoint trend/rate and min/max

This slice implements the first part of Q2 ("what is changing now?") from
[Live Operational State v0](specs/live-operational-state-v0.md) §Operational State
contracts, §Window selection and deterministic policy, §Initial Q2 policies (OD-1),
§Quality, gaps and freshness and §Serving and time consistency (OD-5, direct service
first), with the LOS-A1/LOS-A2/LOS-A4/LOS-A5 subsets. It serves deterministic features
with exact provenance and diagnoses nothing. It adds no transport method, Application
API version or Agent tool (CL-9, CL-13); LOS-A-S4 owns transport v2.

```text
[caller] -- run, signal, exact public snapshot (T, K), finite window, refs --> [OperationalStateService]
[OperationalStateService] -- validate shape, policy, window bounds (no reads yet) --> [boundary]
[P0OperationalStateBoundary] -- AGENT current read: snapshot, binding, source, work bound --> [public P0 reads]
[P0OperationalStateBoundary] -- then inventory (plant context), graph view (descriptor), history --> [public P0 reads]
[public P0 reads] -- one pinned snapshot, unreduced complete window, aggregate work bound --> [OD-1 interpreter (pure)]
[OD-1 interpreter] -- typed features + sample/window/policy/context refs --> [SignalOperationalState]
[SignalOperationalState] -- response bound + AGENT leakage screen --> [caller]
```

### Service seam

`operational_state.OperationalStateService.get_operational_state(run_id, signal_id, *,
snapshot, plant_context_ref, engineering_selection, interpretation_policy_refs,
event_start_ticks=None, event_end_ticks=None, source_id=None)` follows the OD-5 signature,
with an optional exact `source_id` for the read request's scoped source selection.

- `snapshot` is the public P0 snapshot object returned by `RunQueries.telemetry_snapshot`
  (the only operation that chooses T/K). The service never opens a snapshot, never
  resolves "latest" and passes the same object to every read.
- `plant_context_ref` is an exact `PlantContextRef`. For this telemetry-only slice the
  only accepted kind is `INTERIM_PROCESS_GRAPH_SOURCE`, whose ref must equal the
  snapshot's P1.1C context ref; `tep_operational_state.interim_plant_context(snapshot)`
  builds it. The boundary re-derives that ref from the attested AGENT PROCESS_GRAPH
  `ContextSourceRef` and cites its exact source id, repository, revision, path and
  content checksum. It is not relabeled as a P1.3 PlantContextRevision.
- `engineering_selection` must be an explicit `EngineeringSelection`; only the empty
  selection is resolvable before LOS-A-S2, so threshold returns `NO_CANONICAL_LIMIT`
  citing `engineering-selection:EXPLICIT_EMPTY`. A non-empty selection fails
  `ContextUnavailable`; a missing one fails as an invalid request.
- `interpretation_policy_refs` holds exactly one `InterpretationPolicyRef`
  (id, version, parameter checksum). A checksum mismatch fails `PolicyUnavailable`.

The generic module (`operational_state.py`) imports only the standard library and the
generic telemetry core. Source, signal and unit ids are opaque. The service sees an
injected `OperationalStateBoundary`: public queries, signal descriptor, pinned plant
context and the AGENT screen. The process pack (`tep_operational_state.py`) composes
it over `RunManager.queries` (AGENT scope only): the descriptor (value kind NUMERIC,
exact unit) comes from the AGENT ProcessGraphView through
`application_views.resolve_signal_binding`, the same rule as the application signal
reads, so any binding disagreement on (quantity, relation, unit) is an unknown/ambiguous
binding there too. The context check uses the AGENT context inventory, and the final
screen is the existing leakage screen. The boundary maps P0-specific view and visibility
failures into the service families. The interpreter never touches a store, private
world state or a cache.

Order of work in one request:

1. Shape, policy ref, window bounds and the engineering selection are validated with
   no boundary call.
2. The current read at (T, K) is the public P0 path that validates the exact snapshot
   (run, incarnation, clock, context, visibility policy, K) and the signal binding. A
   `source_id` that differs from the answering source fails `UnknownBinding` here,
   before any other work, and so does a current prefix above the aggregate work bound.
3. The plant context, then the signal descriptor and its policy applicability (an
   unpinned unit fails `IncompatiblePolicy`).
4. The complete history read, then the aggregate work check over both reads.

### Window and quality policy (OD-1)

- Window: `0 <= start <= end <= T` on the snapshot clock. Omitted bounds resolve once:
  `end = T`, `start = max(0, end - default_window_ticks)`. The resolved bounds and the
  `start_defaulted`/`end_defaulted` flags are returned. `end - start` above
  `max_window_ticks` fails `ResourceLimitExceeded` before any read.
- History is read with `max_points = max_input_points`. Any reduced answer
  (`returned_count != eligible_count`) fails `ResourceLimitExceeded`; endpoint-index
  reduction is never interpreted. An unreduced answer whose counts disagree with its
  samples is `InconsistentRead`. The reader's own interval, point and scan bounds
  also apply and fail the same way.
- Aggregate work: `max_aggregate_candidates` bounds the candidate records of the whole
  request as the public reads report them: the current read's eligible prefix count
  (records at or before T under K) plus the history read's eligible window count. The
  current count is checked before the history read and the sum after it; above the
  bound the request fails `ResourceLimitExceeded`. The bound must be at least twice
  `max_input_points`, because a complete window's samples are also in the current
  prefix. Each result discloses the counts under `work`.
- Current/quality: the reader's last eligible sample at or before T under K, whatever
  its quality or nullness. An older GOOD value is never substituted.
- Staleness: `age_ticks = T - current.event_ticks`; FRESH when `age <= stale_after_ticks`.
  It is computed from the current tick even when the current sample is BAD/UNCERTAIN.
- Rate/trend: numeric endpoint secant. At the earliest and latest distinct ticks it
  takes the last read-order sample. `rate = (last - first) / ((t_last - t_first) * resolution)`
  in unit per second. Trend is RISING if `rate > tol`, FALLING if `rate < -tol`,
  otherwise STABLE, so `|rate| == tol` is STABLE. This is an endpoint trend over the
  stated window, not a monotonicity or anomaly claim.
- Min/max: exact extrema of every sample in the complete window. Ties cite the first
  sample in read order.
- Strict GOOD: rate, trend and min/max need every window sample to be GOOD, non-null
  and numeric (int/float, never bool). No interpolation, imputation or quality upgrade.
- Gaps cover the full requested window. Adjacent distinct event ticks, the span from
  the window start to the first observed tick and the span from the last observed tick
  to the window end are each compared with `max_gap_ticks`. Any larger span makes
  rate/trend `GAP_EXCEEDS_POLICY`; the detail lists each span (`LEADING`, `INTERIOR`,
  `TRAILING`, its ticks and length) and the feature cites the bounding samples (for a
  leading or trailing span: the window boundary in the detail plus the nearest sample).
  A span exactly at the bound passes. Min/max are not gap-gated: they stay exact over
  the retained window. Leading/trailing unobserved ticks are also disclosed in the
  window diagnostics. (Coordinator decision for v0, flagged for the owner.)
- Numeric method `operational-state.numeric.exact-rational-binary64/v0`: exact rational
  arithmetic over the unrounded inputs and the exact clock resolution. The rate is
  rounded to binary64 once; trend classification compares the exact rational with the
  exact tolerance. A rate whose binary64 value is not finite fails the read with
  `NonfiniteDerivation`.

### Status precedence

Each window feature evaluates, in order: declared value kind (non-numeric signal
-> `UNSUPPORTED_VALUE_KIND`); canonical-limit availability (threshold only ->
`NO_CANONICAL_LIMIT`); empty window -> `NO_DATA`; a non-GOOD or null sample ->
`QUALITY_UNUSABLE`; a GOOD non-null value that is not numeric (including a boolean) ->
`UNSUPPORTED_VALUE_KIND`; fewer than two distinct ticks -> `INSUFFICIENT_DATA`
(rate/trend; min/max need one usable sample); then gaps -> `GAP_EXCEEDS_POLICY`; then
computation. This is the spec order (unusable quality/null before insufficiency); the
observed-kind check applies only to samples that passed the quality check, so a window
with a BAD numeric sample and a GOOD string sample is `QUALITY_UNUSABLE`. Current and
staleness are `NO_DATA`
only when no sample exists at or before T. Diagnostics (quality counts, null count,
observed extent, leading/trailing unobserved ticks, largest adjacent gap) are always
returned, whichever status wins.

### Provenance shape

`SignalOperationalState.as_json()` contains:

- `snapshot` (the full public snapshot: store incarnation/namespace, T, K, clock,
  context and visibility refs), `as_of_event_ticks`, `run_id`, `source_id`, `signal_id`,
  `descriptor` (value kind, unit);
- `context`: the plant citation, the echoed `plant_context_ref`, the explicit empty
  engineering selection and the visibility policy ref;
- `policy`: the exact ref, checksum and full typed parameters (method versions,
  applicable kinds/units, every bound);
- `window`: resolved bounds, `ends_before_horizon`, history read refs (view/version,
  max_points, eligible/returned counts, reader reduction policy, coverage), the ordered
  identities `(source_id, signal_id, sequence)` of **every** window sample (quality-rejected
  ones included), their `selection_checksum`, the content-addressed `window_ref` and
  diagnostics;
- `current_read`: the current view ref, snapshot ref, prefix eligible count and
  current identity; `current`: the verbatim public sample (no ingest time);
- `features`: per feature status, basis (`AS_OF_HORIZON` or `WINDOW`), typed value or
  null, `value_samples` (full identities), `reason_sample_positions` (indexes into
  `window.sample_identities`, the single list of full identities, so unusable samples
  are cited exactly without repeating their identities per feature), resolved detail
  (endpoint ticks, elapsed seconds, tolerance, unit, age, stale-after, oversized gap
  spans), the policy ref, the window ref for window features and context citations;
- `work`: the current and history eligible counts, their sum and the aggregate bound;
- `request` (as asked, including omitted bounds as null), `derivation` (algorithm,
  numeric method, inputs) and `derivation_ref`, a SHA-256 over all of the above.

When a window ends before T, the window features describe only `[start, end]` and the
result discloses both coordinates. Each result is a rebuildable derivation record,
not a second context assembly. Repeated reads at one snapshot produce identical
content and `derivation_ref`; another policy or context is a different derivation.

### Proposed OD-2 values (owner confirmation required)

Policy record `tep-agent-lab.tep-clean-stream-operational-state@v0` in the process pack
(`TEP_CLEAN_STREAM_POLICY`), pinned by its parameter checksum. These are interpretation
settings for the clean 1-second simulated stream. They are not engineering operating
limits, safety gates or canonical knowledge.

| Parameter | Proposed value | Rationale |
|---|---:|---|
| Cadence assumption | 1 tick = 1 simulated second, one record per tick | The live stream advances the supported public 1 s integration step and records every step (owner-confirmed LOS-B-S1..S3 cadence). |
| Default window | 120 ticks (121 points) | Two simulated minutes: long enough to average per-sample noise, short enough to answer "now" and to fit early in the 300 s demo stream. |
| Maximum window | 999 ticks | At 1 s cadence at most 1,000 points, the existing reader/application returned-point limit; far below the 2,592,000-tick reader interval bound. |
| Maximum input points | 1,000 | Equals `TelemetryReadPolicy.max_points_limit`, so a complete window is requested in one unreduced read; more eligible points fail instead of being reduced. Also far below the 50,000-record scan bound. |
| Aggregate candidate records per request | 50,000 | Current prefix + window eligible counts. Equals the default reader `max_scan_records`, so it stays within the existing reader bound. At the 1 s cadence a signal has T+1 records at or before T, so a full 1,000-point window is served up to T of about 49,000 s (13.6 simulated hours); later requests fail `ResourceLimitExceeded`, never truncate. Index probes are not reported by the public reads (see Known limitations). |
| Maximum gap | 1 tick | Exactly one cadence: any missing record inside the window blocks rate/trend. Pause adds no event gap (simulation time does not advance). |
| Stale-after | 1 tick | At most one missed cadence: a read at the frontier has age 0, and a current one record behind T is still FRESH. |
| Signals / peers / transitions per request | 1 / 0 / 0 | Peers and transitions belong to LOS-A-S3/S2. |
| Inline sample refs | 1,001 | Distinct full identities per result: every window identity plus current. Features cite unusable samples by position into the window list, and the bound is checked before serialization. |
| Response bytes | 1,048,576 | Measured worst case: a 999-tick, 1,000-sample all-BAD window with 256-character source and signal ids is 586,222 bytes and returns `QUALITY_UNUSABLE` (all-GOOD: 574,321 bytes). Measured live results are 27,190 bytes for the 121-point default window and 50,466 bytes for a 301-point window. Exceeding it fails, never truncates. |

Trend tolerance per unit (signal unit per second):

| Unit | 1 s step std (max over signals) | Max clean `\|rate\|` over every 120-tick window | Proposed tolerance | Margin |
|---|---:|---:|---:|---:|
| `%` | 2.484 | 0.134524 | 0.25 | 1.86x |
| `deg C` | 0.01563 | 0.00733021 | 0.02 | 2.73x |
| `kPa` | 0.4361 | 0.0522333 | 0.1 | 1.91x |
| `kW` | 0.2814 | 0.0107919 | 0.02 | 1.85x |
| `kg/hr` | 31.7 | 1.41223 | 2.5 | 1.77x |
| `kscmh` | 0.2997 | 0.0102732 | 0.02 | 1.95x |
| `m3/hr` | 0.4212 | 0.0473222 | 0.1 | 2.11x |
| `mol%` | 0.02833 | 0.0121732 | 0.02 | 1.64x |

How it was measured (2026-10-19, reproducible with `operational_state_demo measure`):
two clean live sessions, seeds 11 and 17, 3,600 steps each at the 1 s grid, pinned
simulator. Every one of the 53 histories was read back completely and unreduced through
`ApplicationViewService.get_signal_history`, in windows of at most 1,000 points. The
signal set comes from the public AGENT graph view and must equal the set the service
serves, each with a pinned unit tolerance. The command rejects anything but one GOOD
sample per tick, and exits non-zero when any tolerance is below 1.5x its clean
maximum. It then takes the largest
`|v[i+120] - v[i]| / 120` over every 120-tick window and every signal sharing a unit.
The tolerance is the smallest value from {1, 2, 2.5, 5} x 10^k that is at least
1.5x that clean maximum, so the clean stream reads STABLE everywhere under the default
window. A real drift faster than the tolerance reads RISING/FALLING. The
per-sample noise column shows why short secants are noise-dominated. Analyzer
compositions (`mol%`) are piecewise constant, which explains the zero
rate over the first 300 s and the variation later. The measurement session needs a
trusted host reader policy wide enough for 3,601 points (`max_scan_records` and
`max_points_limit` raised for that command only). Ordinary reads keep the default
reader policy. The unit map covers all eight units of the pinned ProcessGraph, and a
test checks every one of the 53 bound signals.

P0 runs prepared with the default 60 s record interval are not covered by this
cadence-specific policy. Reading them reports `GAP_EXCEEDS_POLICY` for rate/trend while
current, staleness and min/max stay available (tested). A 60 s bundle would be a new
policy version with its own rationale and owner confirmation.

### Errors (typed, redacted)

Fixed messages never echo ids, values, hidden counts, paths or exception text. Every
failure raised by the boundary or a public P0 read is re-raised in its family with a
fixed message and without the original exception chained; any exception outside the
families is an internal fault (`InconsistentRead`). The families are:
`InvalidOperationalStateRequest` (shape, window order, ticks, missing selection, invalid
run id), `UnknownRun`, `UnknownBinding` (unknown signal, a binding whose metadata
disagrees, or a `source_id` that differs from the run's source), `ContextUnavailable`
(wrong plant ref/kind or non-empty engineering selection), `PolicyUnavailable`,
`IncompatiblePolicy` (bad policy record or a unit without a pinned tolerance),
`SnapshotUnavailable` (wrong run, incarnation, context, future K, tampered ref, disposed
store), `ClockMismatch` (including a horizon clock that differs from the event clock),
`ResourceLimitExceeded` (window, reduced input, aggregate work, inline refs, reader
bounds, response bound), `NonfiniteDerivation`, `VisibilityViolation` (AGENT scope or
leakage screen) and `InconsistentRead` (a public read answered another
snapshot/binding/window, counts that disagree, or an internal fault). Shape, policy,
selection and window bounds are validated before any boundary call; the snapshot and
source are validated by the first public read, before any other work.

### Evidence

- `tests/test_operational_state.py` (generic, opaque ids, real generic store/reader, no
  TEP imports): increasing/falling/stable and exact tolerance boundaries, equal ticks,
  one/zero points, ties, interior gaps, leading/trailing silence (at and one tick over
  the bound), status precedence, nonfinite arithmetic, a window ending before T and
  default resolution (LOS-A1); BAD/UNCERTAIN/null current, unusable history, the
  staleness boundary and a reduced window hiding a spike (LOS-A2); late arrival after
  K, sample after T, repeated reads and changed policy (LOS-A4); unknown source/signal,
  wrong run/incarnation/clock/context, future K, tampered ref, bounds before work,
  snapshot validation before context/descriptor (counting boundary), redacted boundary
  and read failures, the aggregate work bound (current alone, sum, exactly at the
  bound), counts that disagree, reader/response bounds (exact byte boundary), a
  maximum all-unusable window with 256-character ids, and policy validation and
  hashing (LOS-A5); import purity.
- `tests/test_operational_state_integration.py`: a real P0 run checked against the public
  current/history views and exact hand computation; all 53 served signals from the
  public graph view, each with the application read's unit; resolution parity on
  disagreeing bindings; default 60 s cadence; typed redacted failures through P0
  queries (horizon-only clock change, unknown source, future K, wrong context caught by
  snapshot validation); old-snapshot reads identical while a live stream grows, then
  `SnapshotUnavailable` after disposal; the measurement's public reads and 1.5x margin
  rule; the demo; no transport, API version or Agent tool change.

### Demo

```powershell
$deps = 'C:/Users/chengting/projects/industrial-agent-playground/.worktrees/p0-deps'
$env:PYTHONPATH = "src;$deps/industrial-agent-runtime/src;$deps/tep-sim/src;$deps/tep-sim/vendor/tep-sim-upstream/src"
py -3.13 -m tep_agent_lab.operational_state_demo live
py -3.13 -m tep_agent_lab.operational_state_demo measure
```

`live` prepares a 300-step clean session (seed 17), pins snapshot A at K=121 (T=120)
while the stream grows, prints three reactor signals at A and at the final snapshot B
(T=300, K=301), then re-reads A and prints `identical=True`. Opaque session ids differ
per run, so snapshot/derivation refs differ between runs; values, ticks and sequences
reproduce. Representative output:

```text
snapshot A: T=120 K=121
  reactor.temperature_measurement: current=120.388467 GOOD (seq 120, tick 120); FRESH age=0; window=[0,120] n=121; rate=-0.000096 deg C/s; trend=STABLE; min=120.377898 max=120.421980; threshold=NO_CANONICAL_LIMIT
  reactor.pressure_measurement: current=2706.253279 GOOD (seq 120, tick 120); FRESH age=0; window=[0,120] n=121; rate=+0.010444 kPa/s; trend=STABLE; min=2704.627862 max=2706.440871; threshold=NO_CANONICAL_LIMIT
  reactor_cooling.flow_actuator: current=41.160032 GOOD (seq 120, tick 120); FRESH age=0; window=[0,120] n=121; rate=+0.000452 %/s; trend=STABLE; min=40.763626 max=41.689745; threshold=NO_CANONICAL_LIMIT
stream grew: session=STOPPED; snapshot B: T=300 K=301
  reactor.temperature_measurement: current=120.417947 GOOD (seq 300, tick 300); FRESH age=0; window=[180,300] n=121; rate=+0.000125 deg C/s; trend=STABLE; ...
snapshot A re-read after growth: identical=True
```

The clean stream is nearly flat, so every trend is STABLE; the scheduled disturbance
timeline (LOS-B-S4) is what makes changes visible.

### Known limitations

- Telemetry-only: no P1.3 PlantContextRevision, no published limit, no transitions or
  related signals (LOS-A-S2/S3); threshold is always `NO_CANONICAL_LIMIT` or
  `UNSUPPORTED_VALUE_KIND`.
- No dense-provenance artifact: a result above the response bound fails rather than
  spilling to a checksummed artifact.
- Direct service only; no transport v2, client allowlist or UI panel (LOS-A-S4/MVP).
- The aggregate work bound counts candidate records only. The public P0 views report
  eligible counts but not index probes, so probe work stays bounded by the reader's own
  service policy, not by the pinned interpretation policy.
- The P0 run's telemetry source id is disclosed only by telemetry read views, so a
  wrong `source_id` is detected on the first (current) read, before any context,
  descriptor or history work, not before every read.
- The policy covers only the 1 s clean simulated stream; it is not evidence about
  external-plant noise or dirty streams.
- Only a single signal per request; aggregate peer work is declared as zero.
