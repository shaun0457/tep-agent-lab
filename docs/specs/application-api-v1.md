# Application API v1 — Snapshot-Bound Application Reads and Local Transport

Status: accepted

Owner repo: `tep-agent-lab`

Milestone: P1.1C ([ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md)); the
implementation note is [`p1-1c-canonical-telemetry-integration.md`](../p1-1c-canonical-telemetry-integration.md).

Depends on [ADR-002](../decisions/ADR-002-ui-application-boundary.md),
[Plant Context + Telemetry v0](plant-telemetry-contract-v0.md) (telemetry semantics) and
[Playground backend v0](playground-backend-v0.md) §Canonical telemetry views (the P0
projections underneath).

Supersedes, as history: the application read semantics in
[`e0-2-application-views.md`](../e0-2-application-views.md) and the transport v0
contract in [`e0-2b-application-transport.md`](../e0-2b-application-transport.md). The
desktop notes (`e0-2c-*.md`) follow this spec for protocol, methods and smoke reads.

## Scope and ownership

This spec owns the AGENT-scope application read models of `ApplicationViewService`,
their parameters, defaults and failures, and the versioned local JSON transport around
them. Telemetry meaning (T/K, eligibility, read order, reduction, identity, coverage)
stays owned by plant-telemetry-contract-v0. Graph meaning stays owned by ProcessGraph
through P0 `ProcessGraphView`. The application layer copies no graph, signal registry
or telemetry store, never reads private world or session state, and grants no Agent or
SIMULATE/MUTATE authority (ADR-002).

## Telemetry snapshot

Every telemetry-bearing value is read under one exact `TelemetryReadSnapshot`.
`open_telemetry_snapshot(run_id, *, event_horizon_ticks=None, ingest_sequence=None)`
is the only operation that chooses (T, K):

- K defaults to the run store's committed revision at the call; T defaults to the
  maximum event tick committed at or before K, or tick 0 when K has no records (K = 0).
  Defaults are resolved once and returned. Explicit values are integers >= 0 (booleans
  rejected); K must not exceed the committed revision.
- Snapshots exist only while the run's in-process P0 session is live; otherwise the
  call fails `ViewUnavailable`.

`TelemetrySnapshot` (frozen) has exactly these fields: `run_id`, `snapshot_ref`,
`contract`, `store_id`, `incarnation`, `namespace`, `ingest_sequence`,
`event_horizon_ticks`, `event_clock_id`, `event_clock_kind`, `event_resolution`
(exact seconds per tick as `"n/d"`), `event_clock_origin`, `context_ref`,
`visibility_policy_ref`. Integers are >= 0. Every other field is a non-empty string of
at most 256 characters (no nulls). `event_resolution` is a reduced positive fraction,
so `"2000/2000"` is rejected. Callers pass it back unchanged. P0 rebuilds the core snapshot,
recomputes `snapshot_ref` and requires equality before resolving it. A mismatch,
another run, incarnation, context, policy or clock, or a future K fails with
`SnapshotUnavailable`. No read accepts a missing snapshot as "latest". Identifiers
in the snapshot never encode the prepare attempt or failure count.

## Reads

```python
open_telemetry_snapshot(run_id, *, event_horizon_ticks=None, ingest_sequence=None)
    -> TelemetrySnapshot
get_run(run_id, *, snapshot: TelemetrySnapshot | None = None) -> Run
get_entity(run_id, entity_id) -> Entity
get_signal(run_id, signal_id, *, snapshot: TelemetrySnapshot) -> Signal
get_signal_history(run_id, signal_id, *, snapshot: TelemetrySnapshot,
                   event_start_ticks=None, event_end_ticks=None, max_points=500)
    -> SignalHistory
```

- `signal_id` is the canonical signal id: the ProcessGraph binding
  `semantic_entity_id` exposed by `ProcessGraphView`. Runtime ids (for example
  `XMEAS(9)`) are not accepted as keys; they appear only as `source_locator`, an opaque
  adapter-native display/provenance label. Repeated bindings must agree on quantity,
  relation and unit, or the read fails `AmbiguousSignal`. Unknown ids fail
  `UnknownSignal`.
- `get_run`: run/task status and manifest fields come from `RunSummaryView`. With a
  snapshot, `telemetry_event_ticks` is the maximum eligible event tick at or before T
  under K (or null). Without one, `snapshot_ref` and `telemetry_event_ticks` are null.
  No telemetry is resolved implicitly.
- `get_signal`: the last eligible sample at or before T in read order, whatever its
  quality, or `NO_DATA`.
- `get_signal_history`: the inclusive interval `[event_start_ticks, event_end_ticks]`
  on the snapshot's event clock. `event_end_ticks` defaults to T. `event_start_ticks`
  defaults to `max(0, end - max_interval_ticks)` of the P0 read policy. Both resolved
  bounds are returned. `event_end > T`, inverted bounds, non-integers and
  `max_points` outside `[1, 1000]` fail. The P0 reader enforces its interval and scan
  bounds (`ResourceLimitExceeded`). Reduction is plant-telemetry's endpoint-index
  policy, applied after both cutoffs.
- Values are verbatim; no interpolation, gap fill or freshness claim.

### Composed-view read (amendment 2026-10-19)

Owner decision 2026-10-19 adds one transport-neutral ApplicationViewService read for
P1.3. Composition semantics stay owned by
[Plant Context Composition v0](plant-context-composition-v0.md) §Scope resolution and
§Composed view. Existing reads, telemetry snapshots and transport v1 are unchanged.

```python
get_composed_view(run_id=None, *, plant_context_ref=None, composed_view_ref=None,
                  max_items=10000, max_bytes=1048576) -> ComposedView
```

- Supply exactly one selector: an exact non-empty `run_id` whose RUN_READY pins
  `plant_context_ref` and the composed-view identity, or both exact
  `plant_context_ref` and `composed_view_ref` with no run id. Mixed, incomplete,
  empty or malformed selectors fail `InvalidRequest`. There is no default run,
  current revision or "latest" resolver.
- Resolve through public P0/application queries, with AGENT-scope authorization before
  assembly. The exact view identity pins the ordered engineering selection (including
  explicit empty), every revision's input/published rule-set refs, grouping definitions,
  policies and reachability bounds. Rebuild only from those exact retained inputs and
  verify the resulting checksum. A missing run/READY pin, unavailable dependency,
  checksum mismatch or plant/view identity mismatch fails `ViewUnavailable`, never an
  empty view or fallback to a different run/revision. Visibility violations fail
  `VisibilityViolation` without disclosing hidden refs or counts.
- `ComposedView` is frozen with tuple collections and runtime `to_jsonable`
  serialization. It returns the selected `run_id` (null for explicit identities),
  `plant_context_ref`, `composed_view_ref`, ordered engineering selection and
  per-revision rule-set refs, exact policy/definition/bound provenance, topology
  (entities, relationships, signal attachments and unbound references), grouping,
  applicability refs/association kinds and `TOPOLOGY_ONLY` reachability. Collections
  follow the composition spec's deterministic ordering. Knowledge/document/rule bodies
  and diagram layout coordinates are absent. Empty applicability is successful only
  when it is the exact resolved result, including an explicitly empty selection.
- `max_items` and `max_bytes` are positive integers (booleans rejected), bounded
  respectively by 10000 and 1048576; invalid values fail `InvalidRequest`. Item count
  includes every collection entry, including nested members, attachments and citations;
  byte count covers the complete UTF-8 JSON result. Finite service-policy work and
  allocation caps also apply. Exceeding any bound fails `ResourceLimitExceeded`;
  no partial view, silent truncation or unbounded scan is permitted.
- This is a static read pinned by immutable plant/view identities. It accepts no
  telemetry snapshot and selects no T/K; any telemetry overlay uses a separately pinned
  `TelemetryReadSnapshot` through the existing reads. Concurrent telemetry append,
  preparation or publication cannot change this exact static result.
- Failures use the existing typed families and fixed redacted messages in §Failures and
  visibility / §Transport v1; no exception text, values, paths or hidden metadata is
  echoed. This read grants no SIMULATE/MUTATE authority, is not an Agent tool and changes
  no Agent tool behavior (CL-9/CL-13).
- The method is not added to the five-method transport v1 allowlist. Desktop/portfolio
  transport exposure requires a new protocol version under §Versioning, with its wire
  schema and matching host/client/bridge allowlists specified before adapter delivery.

## Models

All models are frozen dataclasses with tuple collections, serialized with runtime
`to_jsonable`.

| Model | Fields |
|---|---|
| `TelemetrySnapshot` | see §Telemetry snapshot |
| `CanonicalPoint` | `event_ticks`, `sequence`, `value`, `quality` (`GOOD`/`UNCERTAIN`/`BAD`) |
| `Run` | `run_id`, `run_status`, `task_status`, `view_version`, `manifest_version`, `manifest_checksum`, `snapshot_ref`, `telemetry_event_ticks` |
| `Binding` | `signal_id` (canonical), `source_locator`, `quantity`, `relation`, `unit` |
| `Entity` | `entity_id`, `entity_type`, `name`, `kind`, `tag`, `source_entity_id`, `target_entity_id`, `upstream`, `downstream`, `bindings` |
| `Signal` | `run_id`, `signal_id`, `source_id`, `source_locator`, `bound_entity_ids`, `quantity`, `relation`, `unit`, `snapshot_ref`, `status` (`DATA`/`NO_DATA`), `eligible_count`, `current: CanonicalPoint | None` |
| `SignalHistory` | `run_id`, `signal_id`, `source_id`, `quantity`, `unit`, `snapshot_ref`, `event_start_ticks`, `event_end_ticks`, `max_points`, `eligible_count`, `returned_count`, `reduced`, `reduction_policy`, `coverage`, `status` (`DATA`/`NO_DATA`), `points: tuple[CanonicalPoint, ...]` |

Sample identity is `(source_id, signal_id, sequence)`: `Signal` and `SignalHistory`
carry the canonical `source_id` and `signal_id`, and each point carries `sequence`.
Ingest (acceptance) times are diagnostic store data and are never part of a public
result, because they would expose prepare timing.
Results equal the P0 canonical views for the same snapshot and arguments.

## Failures and visibility

Service failures are typed: `UnknownEntity`, `UnknownSignal`, `AmbiguousSignal`,
`ViewUnavailable`, `VisibilityViolation`, `SnapshotUnavailable`,
`ResourceLimitExceeded`, `InvalidRequest` (a `ValueError`) for invalid client arguments,
and `TelemetryRequestError` (a `TelemetryError` subtype) for explicit validation of
client-supplied snapshot, window or point bounds on a telemetry read. A base
`TelemetryError` from store/snapshot construction or binding invariants is a server
fault, as is a malformed server-produced snapshot. Any other `ValueError` is a server fault. Messages carry no counts, values, paths or hidden data. The service accepts
AGENT-scope queries only, checks run identity and the P0 leakage screen before assembly,
and never exposes evaluator overlays.

## Transport v1

`PROTOCOL_VERSION = "tep-agent-lab.application-transport/v1"`. The envelope (exactly
`protocol_version`, `request_id`, `method`, `params`; success `ok: true` + `result`;
failure `ok: false` + `error{code, message}`) is unchanged from v0. So are
`MAX_REQUEST_BYTES = 64 KiB` on raw UTF-8 input, the codec failures and the validation
order (envelope, protocol version, method allowlist, parameter shape/JSON safety, then
service invocation).

| Method | Required params | Optional params |
|---|---|---|
| `open_telemetry_snapshot` | `run_id` | `event_horizon_ticks`, `ingest_sequence` (integer) |
| `get_run` | `run_id` | `snapshot` |
| `get_entity` | `run_id`, `entity_id` | none |
| `get_signal` | `run_id`, `signal_id`, `snapshot` | none |
| `get_signal_history` | `run_id`, `signal_id`, `snapshot` | `event_start_ticks`, `event_end_ticks` (integer or null), `max_points` (integer) |

For `get_run`, an omitted `snapshot` or `"snapshot": null` means no telemetry.
The required snapshot of `get_signal` and `get_signal_history` cannot be null.
Otherwise `snapshot` is a JSON object with exactly the `TelemetrySnapshot` keys and types
(integers for `ingest_sequence` and `event_horizon_ticks`, strings otherwise). Integer
parameters reject booleans and floats, and all integer parameters (including snapshot
T/K fields) must be in the safe JSON range `[-(2^53 - 1), 2^53 - 1]`.
Snapshot T/K remain non-negative. A shape/range failure is `INVALID_REQUEST`, and the
service is not called.

| Failure | Code / fixed message |
|---|---|
| `UnknownEntity` | `UNKNOWN_ENTITY` / unknown entity |
| `UnknownSignal` (including an unbound canonical id) | `UNKNOWN_SIGNAL` / unknown signal |
| `AmbiguousSignal` | `AMBIGUOUS_SIGNAL` / ambiguous signal |
| `ViewUnavailable` | `VIEW_UNAVAILABLE` / view unavailable |
| `VisibilityViolation` | `VISIBILITY_VIOLATION` / visibility violation |
| `SnapshotUnavailable`, clock mismatch | `SNAPSHOT_UNAVAILABLE` / snapshot unavailable |
| `ResourceLimitExceeded` | `RESOURCE_LIMIT_EXCEEDED` / resource limit exceeded |
| schema failure, `InvalidRequest`, explicit `TelemetryRequestError` validation | `INVALID_REQUEST` / invalid request |
| method outside the five reads | `UNSUPPORTED_METHOD` / unsupported method |
| version mismatch | `UNSUPPORTED_PROTOCOL_VERSION` / unsupported protocol version |
| anything else | `INTERNAL_ERROR` / internal error |

Responses never echo exception text or parameters. `request_id` must match
`[A-Za-z0-9._:-]{1,128}`; otherwise the failure is `INVALID_REQUEST` with
`request_id: null`. A host that cannot encode a response writes the fixed
`INTERNAL_ERROR` frame with `request_id: null` and keeps serving. Any other
`ValueError` is `INTERNAL_ERROR`.

## Versioning and v0 retirement

v1 replaces v0. Hosts speak exactly one version, and a v0 request receives
`UNSUPPORTED_PROTOCOL_VERSION` in a v1 envelope. The desktop frontend, Rust bridge and
frozen sidecar ship from this repository at one revision, so no v0 client is supported.
Any later change to a method, model or code is a new protocol version.

## Native and desktop adapters

The Rust bridge allowlists exactly the five methods and their parameter names. It
owns `run_id` (and rejects one from the frontend) and passes `snapshot` through as
an opaque JSON object without interpreting it. It forwards only the fixed failures
above. The frontend renders and formats, for example ticks times resolution as
hours. It does not own telemetry semantics, choose T/K except through
`open_telemetry_snapshot`, or use runtime ids as keys (ADR-002).

## Acceptance

Proven by the tests listed in the implementation note §Acceptance tests: Python
application/transport/desktop-backend suites, `desktop/tests/client.test.ts`, Rust
`desktop/src-tauri/tests/{bridge,tauri_ipc}.rs` (development Python, `cargo test`)
and `packaged.rs` (frozen sidecar, CI only), and the sidecar/bundle smoke scripts.
