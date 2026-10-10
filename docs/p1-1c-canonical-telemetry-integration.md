# P1.1C — Canonical Telemetry Integration (implementation note)

Status: planned (P1.1C); not implemented. Written before code, as
[ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md) Decision 6 requires. Owner
decisions are recorded in §Decisions. Note and code ship in one PR on
`feat/p1-1c-canonical-telemetry`. Normative contracts:
[Plant Context + Telemetry v0](specs/plant-telemetry-contract-v0.md) (owner),
[Playground backend v0](specs/playground-backend-v0.md),
[ADR-002](decisions/ADR-002-ui-application-boundary.md). Builds on
[P1.1A](p1-1a-telemetry-core.md) and [P1.1B](p1-1b-tep-simulation-source.md).

## Goal

Discharge the plant-telemetry migration obligation for application reads (§Existing
architecture consistency and migration obligations, renumbered by ADR-004 Decision 3):

1. event-time-bounded scans in the generic core;
2. P0 run ingestion: reference-world records -> `TEPSimulationSource` -> one run-scoped
   `InMemoryTimeSeriesStore` per prepared session;
3. snapshot-bound current/history through the public P0 boundary (`RunQueries`);
4. migration of every application telemetry read (`get_run`, `get_signal`,
   `get_signal_history`) to those reads, released as application transport **v1**, with
   the desktop client, Rust bridge and sidecar smoke tests moved to v1 (decision O2).

Every telemetry-bearing read pins one exact `TelemetryReadSnapshot`; no read resolves
"latest".

## Non-goals

- No Agent tool change. `get_history`, tool specs, `TOOL_SURFACE_VERSION`,
  `BRIDGE_VERSION` and `ReferenceWorld.history()` stay as they are until P1.5.
- No ContextSnapshot, InvestigationContextView or P1.3 composition; no
  `PlantContextRevision`, no `SignalDescriptor`.
- No injected dirty-stream replay harness (gaps, BAD/UNCERTAIN injection, clock skew,
  disconnect). It is deferred and required before any non-simulated source (Decision 6).
- No non-simulated source, counterfactual-branch ingestion, durable store, retention or
  cross-clock mapping.
- No change to `RunManifest`, P0 `TelemetryView` v0 (D0 records it, and the static E0
  observatory reads it), D0 fixtures, D0 tests or the static E0/E0.1 report.

## Design

```text
prepare(): case_setup(world) -> ReferenceWorld.history()        (unchanged owner)
  -> RunTelemetry.sync_reference_history(history)               (new, P0-owned)
       -> TEPSimulationSource.translate_sanitized(record)       (P1.1B, one batch/record)
       -> TelemetryIngestor.ingest(batch, ingest_time=P0 clock)  (P1.1A)
       -> run-scoped InMemoryTimeSeriesStore (one commit K per record)
  -> RunManifest published (bytes unchanged) -> READY
reads: RunQueries.telemetry_snapshot/current/history  (exact T, K)
  -> ApplicationViewService v1 -> ApplicationTransport v1 -> Rust bridge -> desktop
```

### 1. Event-time-bounded scans (`telemetry.py`)

The core keeps a per-binding event-time index next to the commit-ordered stream.
Required properties (the data structure is an implementation choice; recommended:
commit-ordered blocks, each sorted by `(ticks, sequence)`):

- `history`: `max_scan_records` counts exactly
  `C = |{commit <= K, event_start <= event <= event_end}|` for the binding, computed
  from the index before any value is read. A narrow window on a long binding succeeds.
- `current`: finds the last eligible sample at or before T from the index without a
  linear prefix scan; `eligible_count` stays exact.
- Outcome (pass/fail, counts, values) is a function of `(binding, K, T, window)` only.
  Records committed after K never contribute, not even to a `ResourceLimitExceeded`.
  Error messages still carry no counts or values.
- New `store.event_frontier(ingest_sequence) -> TimePoint | None` (the maximum event
  time committed at or before K) and `reader.last_event_time(snapshot) -> TimePoint |
  None` (the maximum eligible event time at or before T, store-wide).

This replaces the P1.1A "whole K-committed prefix" bound (a documented limitation, not
contract). The P1.1A note is updated in the PR.

### 2. Run-scoped store and ingestion (`run_telemetry.py`, `playground.py`)

`RunManager._session` builds one `RunTelemetry` per prepare attempt and stores it in
`RunSession.telemetry`. Each attempt draws an opaque incarnation token
`token = secrets.token_hex(16)`. The token is never derived from the attempt counter,
the session directory (`prepare-000N`) or a failure count. The token-to-attempt mapping
lives only in trusted in-process session state, never in a public view. This keeps
failed prepares invisible to AGENT readers, as the `PREPARE_FAILED` filtering in
`RunQueries.events` already does (playground-backend-v0 §RunOutcome: hidden
setup/error isolation). Registration:

| Field | Value |
|---|---|
| source | `TEPSimulationSource(graph, source_id=f"tep-reference:{run_id}:{token}", clock_id=f"tep-sim:{run_id}:{token}", clock_origin=f"tep-reset:{run_id}:{token}")` |
| store | `store_id="p0-reference-telemetry"`, `namespace=f"p0-run:{run_id}:{token}"`, `incarnation=f"inc-{token}"`, bindings `source.store_bindings()` (53) |
| `context_ref` | see §7 |
| `visibility_policy_ref` | `f"{VISIBILITY_POLICY_VERSION}#AGENT"` (store holds only sanitized AGENT-visible data) |
| ingest clock | `UTC`, 1 ms/tick, origin `1970-01-01T00:00:00Z/utc`; `ingest_time` = trusted `RunManager.clock()`; diagnostic only, K orders knowledge |
| reader | `TelemetryReadPolicy(max_points_limit=1000, max_interval_ticks=2_592_000 (30 d), max_scan_records=50_000)`: service policy, not contract; a trusted `RunManager(telemetry_policy=...)` argument may lower it (tests) |

Ingestion rules:

- `sync_reference_history(history)` translates and ingests `history[cursor:]` in order,
  one atomic batch per record, then advances the cursor. P0 calls it once, after
  `case_setup` and before the manifest is written. A failure fails `prepare()` (no
  manifest, no READY), consistent with prepare atomicity.
- `ReferenceWorld.advance` already drops the repeated rollout origin, so each history
  record is ingested exactly once; `K == len(history)` at READY.
- In P0 v0 the reference history cannot change after READY (blind RCA has no MUTATE;
  harness advances only in `case_setup`), so no ingestion happens during or after
  RUNNING. A future reference-mutating path must call `sync_reference_history`.
- `translate(record)` (the session's `TEPSimulationSource.translate_sanitized`) and
  `deliver(batch)` (`TelemetryIngestor` plus the next trusted ingest time) form the
  single commit seam. They are reachable only through the in-process `RunSession`
  (trusted harness), never through `RunQueries`, `ApplicationViewService`, transport or
  the desktop. This is not the deferred dirty-stream harness: it injects no gaps,
  quality, skew or disconnect.
- Reference and counterfactual streams stay separate: branches are not ingested.
- The store lives as long as the session object; `RunSession.close()` keeps it. A run
  reopened from disk has no store: snapshots report unavailable, never today's contents.

### 3. Snapshot-bound P0 reads (`playground_views.py`)

`RunQueries` (both AGENT and EVALUATOR scope; the data is AGENT-visible):

```python
telemetry_snapshot(*, event_horizon_ticks: int | None = None,
                   ingest_sequence: int | None = None) -> TelemetrySnapshotView
telemetry_current(signal_id: str, *, snapshot: Mapping) -> CanonicalCurrentView
telemetry_history(signal_id: str, *, snapshot: Mapping, event_start_ticks: int,
                  event_end_ticks: int, max_points: int) -> CanonicalHistoryView
telemetry_last_event(*, snapshot: Mapping) -> CanonicalLastEventView
```

- `telemetry_snapshot` is the only call that chooses (T, K). Defaults are resolved once
  and disclosed: K = committed revision now, T = `event_frontier(K)`, and T = run-clock
  tick 0 when `event_frontier(K)` is `None` (K = 0). Explicit K must be `<= current`;
  explicit T is any non-negative tick on the run clock. After `prepare()`, K >= 1, so
  K = 0 is reachable only through an explicit `ingest_sequence=0`; it reads `NO_DATA`.
- The other reads require `snapshot` (the `snapshot` mapping of a
  `TelemetrySnapshotView`). P0 rebuilds `TelemetryReadSnapshot` from it with an exact
  key set and contract version, recomputes `snapshot_ref`, requires equality, then calls
  `store.resolve`. There is no default and no server-side "latest".
- `signal_id` is the canonical id (ProcessGraph `semantic_entity_id`, already present
  in `ProcessGraphView` bindings). Unknown ids fail closed; runtime ids are not keys.
- Views go through `RunQueries._view` (leakage screen, frozen JSON) and carry
  `snapshot_ref`, the snapshot body, binding, bounds, eligible/returned counts, sample
  identities, event ticks, ingest times, qualities, reduction policy and coverage. Every
  identifier comes from the opaque token (§2), never from the attempt number.
- Failures keep the core exception types and fixed messages (no counts or values):
  `SnapshotUnavailable`/`ClockMismatch`/`TelemetryError`/`ResourceLimitExceeded`.
- These reads never touch the world, so they do not take `world_lock`. They work in
  READY, RUNNING and terminal states while the session is in-process; otherwise
  `ViewUnavailable`.
- `TelemetryView` v0 is unchanged. It stays a P0 projection for D0 surfaces and the
  static E0 observatory. No application read uses it after this PR.

### 4. Application reads v1 (`application_views.py`, `application_models.py`)

`ApplicationViewService` (AGENT only) keeps one method per read. Telemetry-bearing
reads take a `TelemetrySnapshot` model, obtained first through the new
`open_telemetry_snapshot`:

```python
open_telemetry_snapshot(run_id, *, event_horizon_ticks=None, ingest_sequence=None)
    -> TelemetrySnapshot
get_run(run_id, *, snapshot: TelemetrySnapshot) -> Run
get_entity(run_id, entity_id) -> Entity                      # signature unchanged
get_signal(run_id, signal_id, *, snapshot) -> Signal          # canonical current
get_signal_history(run_id, signal_id, *, snapshot, event_start_ticks=None,
                   event_end_ticks=None, max_points=500) -> SignalHistory
```

Models (frozen, domain-generic, ticks plus an explicit clock instead of hours):

| Model | Fields |
|---|---|
| `TelemetrySnapshot` (new) | `run_id`, `snapshot_ref`, `contract`, `store_id`, `incarnation`, `namespace`, `ingest_sequence`, `event_horizon_ticks`, `event_clock_id`, `event_clock_kind`, `event_resolution` (`"1/1"` s/tick), `event_clock_origin`, `context_ref`, `visibility_policy_ref` |
| `CanonicalPoint` (new) | `event_ticks`, `sequence`, `value`, `quality` |
| `Run` | run/task status, view and manifest fields as before; `simulation_time_hours` is replaced by `snapshot_ref` and `telemetry_event_ticks` (`telemetry_last_event`: last eligible event at or before T under K, or `None`) |
| `Binding` | `signal_id` is now the canonical id; new `source_locator` carries the adapter-native runtime id (`"XMEAS(9)"`) as an opaque display/provenance label; `quantity`, `relation`, `unit` |
| `Signal` | `signal_id` (canonical), `source_locator`, `bound_entity_ids`, `quantity`, `relation`, `unit`, `snapshot_ref`, `status` (`DATA`/`NO_DATA`), `eligible_count`, `current: CanonicalPoint | None` |
| `SignalHistory` | `signal_id`, `quantity`, `unit`, `snapshot_ref`, `event_start_ticks`, `event_end_ticks`, `max_points`, `eligible_count`, `returned_count`, `reduced`, `reduction_policy`, `coverage`, `points: tuple[CanonicalPoint, ...]` |

Replacing the recent window: v0 read the last <= 1000 `TelemetryView` records and failed
for older windows. v1 reads any interval at or before the pinned T from the run-scoped
store, within reader policy. Omitted `event_end_ticks` defaults to T. Omitted
`event_start_ticks` defaults to `max(0, end - max_interval_ticks)`. Both defaults are
resolved against the pinned snapshot and returned in the result. `event_end > T`,
inverted bounds and policy breaches fail closed. Coverage is
`COMPLETE_FOR_STORE_INCARNATION`, so `earlier_records_omitted` and the v0 hours
tolerance are removed. Reduction is the same frozen endpoint-index policy, applied by
the core reader after both cutoffs.

Graph data (entities, bindings, quantity, unit) still comes only from
`ProcessGraphView`; the service matches canonical ids against binding
`semantic_entity_id` and never copies a signal registry.

### 5. Application transport v1 (`application_transport.py`)

Changing the parameters and results of the v0 reads in place would break the frozen
E0.2B v0 contract. ADR-002 requires careful versioning. The PR therefore introduces
`PROTOCOL_VERSION = "tep-agent-lab.application-transport/v1"` and retires v0.
The host speaks v1 only, and a v0 request gets `UNSUPPORTED_PROTOCOL_VERSION`. The
desktop, Rust bridge and frozen sidecar ship from this repository together, so no v0
client remains.

| Method | Required params | Optional params |
|---|---|---|
| `open_telemetry_snapshot` | `run_id` | `event_horizon_ticks`, `ingest_sequence` (integers >= 0) |
| `get_run` | `run_id`, `snapshot` | none |
| `get_entity` | `run_id`, `entity_id` | none |
| `get_signal` | `run_id`, `signal_id`, `snapshot` | none |
| `get_signal_history` | `run_id`, `signal_id`, `snapshot` | `event_start_ticks`, `event_end_ticks` (integer or null), `max_points` (integer) |

- `snapshot` is a JSON object with exactly the `TelemetrySnapshot` keys. The transport
  rebuilds the model; the service verifies `snapshot_ref` and P0 resolves it. Booleans
  are not integers. The envelope, the 64 KiB request bound, the codec and the
  validation order are unchanged.
- New stable error codes: `SNAPSHOT_UNAVAILABLE` / "snapshot unavailable"
  (`SnapshotUnavailable`, `ClockMismatch`, `snapshot_ref` mismatch) and
  `RESOURCE_LIMIT_EXCEEDED` / "resource limit exceeded". `TelemetryError` maps to
  `INVALID_REQUEST`; core `UnknownBinding` maps to `UNKNOWN_SIGNAL`.

### 6. Desktop, Rust bridge and sidecar

- `desktop/src/lib/protocol.ts`: `PROTOCOL` becomes v1; the fixed message table adds the
  two new codes.
- `desktop/src/lib/applicationClient.ts`: `Method` adds `open_telemetry_snapshot`;
  the API becomes `openTelemetrySnapshot(options?)`, `getRun(snapshot)`,
  `getEntity(id)`, `getSignal(id, snapshot)` and
  `getSignalHistory(id, snapshot, options?)`. The client passes the snapshot object
  through unchanged and never interprets T/K (ADR-002: the frontend does not own
  telemetry semantics).
- `desktop/src/main.tsx`: on load, open one snapshot, show its K, T (formatted as hours
  from ticks x resolution, presentation only) and `snapshot_ref`, then run every probe
  against that pinned snapshot. A "New snapshot" button re-pins explicitly. Probe ids
  are canonical and live in `desktop/src/lib/probes.ts`: `reactor.temperature_measurement`
  (shown with `source_locator` XMEAS(9)) and the canonical id bound to XMEAS(7) in the
  pinned ProcessGraph.
- `desktop/src-tauri/src/bridge.rs`: `PROTOCOL` v1; method allowlist plus
  `open_telemetry_snapshot`; per-method parameter allowlists with `snapshot` as an
  opaque JSON object (Rust does not inspect it); `run_id` is still host-owned and
  rejected from the frontend; the error-message table adds the two codes.
- `desktop/sidecar/smoke_sidecar.py`: v1 needs the snapshot from the first reply, so the
  smoke runs interactively (one frame, one reply). It opens a snapshot, then sends
  `get_run`, `get_entity`, `get_signal` and `get_signal_history` with it.
  `verify_bundle.py` reuses `smoke()` unchanged. `desktop_backend.py` keeps its framing.

### 7. Runtime ids and the E0 observatory

The static E0/E0.1 report (`examples/e0_environment_observatory.py`, `e0_demo.py`)
reads P0 `TelemetryView` v0 and Agent-tool artifacts, not the application reads, so it
is unchanged and keeps showing XMEAS ids. In the desktop, XMEAS/XMV appear only as
`source_locator` labels next to canonical ids. Mapping always runs canonical id ->
ProcessGraph binding -> `runtime_variable_id` and is never accepted in reverse as a read
key.

### 8. `context_ref` in P1.1C (finding I9)

`context_ref = f"context-source:{graph_source.source_id}:sha256:{checksum(to_jsonable(graph_source))}"`,
where `graph_source` is the exact AGENT-visible `PROCESS_GRAPH` `ContextSourceRef` that
P0 attested and froze at READY. The `TEPSimulationSource` binding graph was built from
that same source (P0 already checks graph provenance against it). It is no longer an
opaque caller string and not a `PlantContextRevision`. P1.3 maps it through this
recorded ProcessGraph source ref (ADR-004 Decision 4 item 4), never through "latest"
metadata. The manifest is unchanged; the ref is recomputable from
`canonical_context_sources`.

## Files

| File | Change |
|---|---|
| `src/tep_agent_lab/telemetry.py` | event-time index, window-bounded scans, `event_frontier`, `last_event_time`; still stdlib-only |
| `src/tep_agent_lab/run_telemetry.py` (new) | `RunTelemetry`, `TelemetryReadPolicy`, opaque token, `sync_reference_history`, `translate`, `deliver`, `snapshot`, `resolve_snapshot(mapping)`, `context_ref_for(graph_source)` |
| `src/tep_agent_lab/playground.py` | `RunSession.telemetry`; optional trusted `telemetry_policy`; build and sync in `_session`; prepare fails closed on ingest failure |
| `src/tep_agent_lab/playground_views.py` | four canonical telemetry views; `TelemetryView` v0 untouched |
| `src/tep_agent_lab/application_views.py`, `application_models.py` | v1 reads and models (§4) |
| `src/tep_agent_lab/application_transport.py` | protocol v1, five methods, `snapshot` param, two error codes |
| `desktop/src/lib/protocol.ts`, `applicationClient.ts`, `probes.ts` (new), `desktop/src/main.tsx` | v1 client and snapshot-pinned UI (§6) |
| `desktop/src-tauri/src/bridge.rs` | v1 allowlists and error table |
| `desktop/sidecar/smoke_sidecar.py` | interactive v1 smoke |
| Tests | `tests/test_telemetry_core.py`, `test_tep_telemetry_source.py`, `test_canonical_telemetry_integration.py` (new), `test_application_views.py`, `test_application_transport.py`, `test_desktop_backend.py`, `desktop/tests/client.test.ts`, `desktop/src-tauri/tests/{bridge,tauri_ipc,packaged}.rs` |
| Docs | `docs/specs/playground-backend-v0.md` (O1), `docs/e0-2-application-views.md`, `docs/e0-2b-application-transport.md`, `docs/e0-2c-desktop-backend.md`, `docs/e0-2c-tauri-shell.md`, `docs/e0-2c-packaged-sidecar.md` (v1 amendments, §SPEC_CONFLICT), `docs/p1-1a-telemetry-core.md`, `docs/p1-1b-tep-simulation-source.md`, `docs/architecture.md`, `docs/program-status.md` |

Not changed: `tep_world.py`, `tool_surface.py`, `tool_bridge.py`, `benchmark.py`,
`e0_demo.py`, `examples/e0_environment_observatory.py`, `desktop_backend.py` framing,
`verify_bundle.py`, `src/tep_agent_lab/fixtures/**`, D0 tests.

## Acceptance tests

Core (`tests/test_telemetry_core.py`):

| Test | Proves (contract clause) |
|---|---|
| `test_window_scan_bound_counts_only_k_committed_window_candidates` | §Bounded deterministic reads: eligible set and enforced resource bounds |
| `test_scan_outcome_never_depends_on_records_after_k` | §Bounded reads "hidden records cannot affect counts or metadata"; §Hard temporal leakage invariant |
| `test_current_on_long_binding_does_not_scan_prefix` | §Bounded reads: current semantics; resource bounds |
| `test_indexed_reads_match_bruteforce_under_out_of_order_commits` | §Bounded reads (eligible set, read order, reduction) vs a brute-force filter over all (K, T, window), random commit orders; includes `last_event_time` |

Live P0 wiring (`tests/test_canonical_telemetry_integration.py`; real `RunManager`
prepare with `FakeProvider`). "Both boundaries" means `RunQueries` and v1
`ApplicationViewService`:

| Test | Proves (contract clause) |
|---|---|
| `test_p0_prepare_ingests_reference_history_through_tep_source` | Positive integration test replacing the parallel-path test (alignment review §8 and "Next three engineering steps" item 2): `K == len(history)`, one commit per record, sequences `0..n-1`, all 53 signal values equal the sanitized values at every K prefix; §Existing architecture (`ReferenceWorld.__init__/advance/history`) |
| `test_production_wiring_dependency_direction` | Replaces `test_existing_production_path_does_not_use_adapter`: `playground` imports `run_telemetry`; only `run_telemetry` imports `tep_telemetry`; `telemetry` imports neither; `tool_surface`, `tool_bridge`, `benchmark` import neither; §TEP boundaries |
| `test_store_context_ref_is_the_attested_process_graph_source` | §Canonical Plant Context (telemetry context agrees with the bindings' plant revision); ADR-004 Decision 4 item 4; I9 |
| `test_prepare_fails_closed_when_ingestion_fails` | playground-backend-v0 prepare atomicity; §Sources (fail closed) |
| `test_public_telemetry_ids_hide_prepare_attempts` | playground-backend-v0 §RunOutcome hidden setup/error isolation: a failed prepare followed by a successful retry. No public telemetry identifier (source, clocks, origin, namespace, incarnation, snapshot body, `snapshot_ref`, view, model and transport fields) contains `prepare-`, the attempt number or a failure count. The ids have the same shape as a first-try run and differ only by the opaque token |
| `test_reads_require_an_exact_snapshot` | §Append store and read snapshots: no telemetry read without a snapshot; forged `snapshot_ref`, other incarnation/context/policy, future K rejected; ADR-004 Decision 6 "no latest" |
| `test_k0_snapshot_reads_no_data` | §Bounded reads (explicit no-data; empty interval): `ingest_sequence=0` defaults to T = tick 0; current `NO_DATA`, empty history, `telemetry_event_ticks is None`, via both boundaries |
| `test_live_temporal_leakage_matrix` | §Hard temporal leakage invariant and §Bounded reads (both cutoffs before sorting, reduction or aggregate). Set A: commit <= K, event > T, from real history with explicit T below the frontier. Set B: commit > K, event <= T, delivered after pinning. For {current, history, last event} x both boundaries x `max_points` in {1, 2}, no A/B sample affects values, identities, counts, the reduced flag or the endpoints. Repeated reads are equal and `repr`-identical |
| `test_live_post_k_data_cannot_change_old_snapshot_outcomes` | §Bounded reads (no hidden effect on metadata) and determinism, error channel. Small trusted `telemetry_policy`. Post-K deliveries push the old snapshot's window past `max_scan_records`. Afterwards, old successful reads are identical, and old failing reads (`ResourceLimitExceeded`; `event_end` beyond T) keep the exception type and message at both boundaries and the transport error code |
| `test_live_late_and_out_of_order_delivery` | §Identity: late/out-of-order arrivals append with original event time and new commit; older snapshots unchanged; new snapshot reads in `(ticks, source_id, sequence)` order |
| `test_live_duplicate_redelivery_is_idempotent` | §Identity bullet 1: redelivered batch returns originals, K unchanged, reads identical; bullet 3: re-translated record is a new observation (P1.1B) |
| `test_live_identity_conflict_rejects_batch` | §Identity bullet 2: changed content under an existing identity fails the whole batch; K unchanged |
| `test_live_same_tick_observations_retained` | §Four time concepts (tick collisions do not collapse samples). A MANUAL run whose `case_setup` captures two sanitized `world.observe()` records, before and after an MV apply at the same second. `observe()` does not append to history, so sync never ingests them. After prepare they go through the session's `translate` and `deliver`. Both boundaries show distinct sequences and both values, and current returns the higher sequence. `ReferenceWorld` is unchanged |
| `test_snapshot_survives_run_and_is_unavailable_after_reopen` | §Append store retention: READY snapshot reads identically after COMPLETED; reopened run reports `SNAPSHOT_UNAVAILABLE`/`VIEW_UNAVAILABLE`, never today's contents |
| `test_canonical_views_hide_hidden_state` | Hidden-state isolation and ADR-002: harness-injected hidden disturbance; views, models and transport payloads pass `leakage_findings` and carry canonical ids plus `source_locator` only; non-AGENT queries rejected |
| `test_no_public_ingestion_path` | §Application boundaries (cannot drive source physics): no method on `RunQueries`, `ApplicationViewService`, transport or the Rust allowlist reaches `translate`/`deliver`/`sync_reference_history` |

Migrated application reads, transport and desktop:

| Test (file) | Proves |
|---|---|
| `test_v1_reads_are_snapshot_bound` (`test_application_views.py`) | `get_run`, `get_signal` and `get_signal_history` require a `TelemetrySnapshot` and return values equal to the canonical P0 views at that snapshot; `get_entity` bindings carry canonical `signal_id` plus `source_locator`; plant §Application boundaries ("same underlying read semantics") |
| `test_v1_history_replaces_recent_window` (`test_application_views.py`) | windows older than the former 1000-record projection are served from the store; default start/end are resolved against T and disclosed; `event_end > T`, inverted bounds and policy breaches fail closed |
| `test_application_reads_no_longer_use_telemetry_view` (`test_application_views.py`) | AST/mock check: `ApplicationViewService` never calls `RunQueries.telemetry` |
| `test_v1_protocol_and_methods` (`test_application_transport.py`) | exact v1 schemas, `snapshot` key set, integer-not-bool checks, v0 request -> `UNSUPPORTED_PROTOCOL_VERSION`, two new fixed error codes, redaction, the 64 KiB bound unchanged, and real E0/P0 equivalence of all five methods |
| `test_desktop_backend_v1_session` (`test_desktop_backend.py`) | NDJSON host serves the v1 snapshot-then-read sequence in one process |
| `desktop/tests/client.test.ts` | five reads use only `application_request` with a host-owned run id; the snapshot passes through verbatim; v1 envelope and message table, including the two new codes; unsafe errors fail closed |
| `desktop/src-tauri/tests/bridge.rs` (`real_python_v1_reads_same_process_session` + unit tests) | real Python process: snapshot then four reads, same PID, `desktop-1..5`, hidden-term screen; v1 allowlists reject client `run_id` and unknown params; error table |
| `desktop/src-tauri/tests/tauri_ipc.rs`, `packaged.rs` (ignored; CI frozen sidecar) | the same v1 sequence through Tauri IPC and through the frozen sidecar |
| `smoke_sidecar.py` / `verify_bundle.py` | interactive v1 smoke against the frozen EXE and the installed NSIS bundle |
| `test_desktop_probe_ids_match_process_graph` (`test_application_views.py`) | the canonical ids in `desktop/src/lib/probes.ts` equal the pinned ProcessGraph bindings for XMEAS(9) and XMEAS(7) |

How they run: the Python suites run in `scripts/check.py` (CI `test` job, Python 3.11
and 3.13). The desktop job (`windows-latest`) runs `npm ci && npm run build && npm test`
(typecheck plus `node --test`), `cargo fmt --check`, `cargo check --locked`,
`cargo test --locked`, the `custom-protocol` check, `build_sidecar.py` plus
`smoke_sidecar.py`, `cargo test --locked --test packaged -- --ignored`, and the NSIS
build plus `verify_bundle.py`. The PR merges only with both jobs green; the packaged
and bundle steps are CI-authoritative.

Regression:

| Test / gate | Proves |
|---|---|
| `test_agent_tool_surface_unchanged` | ADR-004 Decision 6: tool specs, `tool_set_version`, `TOOL_SURFACE_VERSION`, `BRIDGE_VERSION` equal the `e5ccf19` values; `get_history` output for a fixed case is identical |
| `test_telemetry_view_v0_unchanged` | P0 `TelemetryView` v0 returns the same values for the same run (D0 surfaces, static E0) |
| D0 freeze gate | `git diff --exit-code origin/main -- src/tep_agent_lab/fixtures tests/test_benchmark.py tests/test_benchmark_family.py tests/test_benchmark_healthy.py tests/test_tool_surface.py tests/test_tool_bridge.py` is empty and those suites pass (Decision 6) |
| full check | `scripts/check.py` with pinned runtime/tep-sim, including unchanged static E0/E0.1 tests and P1.1A/B boundary tests |

The leakage-matrix (set B), post-K, late, out-of-order, duplicate, conflict and
same-tick tests inject records through `RunSession.telemetry.translate` and `deliver`,
because `ReferenceWorld.history()` cannot produce them in P0 v0. Each uses a real
prepared session, that session's source translation and ingestor, and the public
reads (decision O3). Injected records never enter `ReferenceWorld.history()`, so Agent
tools cannot see them.

## Decisions (owner, 2026-10-10)

- **O1 — playground-backend-v0.** The PR amends the owning spec additively: a
  "Canonical telemetry views" subsection under §Read projections citing the
  plant-telemetry obligation. `TelemetryView` text is unchanged.
- **O2 — v0 application reads.** The owner did not take the compatibility recommendation.
  `get_run`, `get_signal` and `get_signal_history` move to the snapshot-bound canonical
  reads in this PR (§4). Transport (v1, §5) and desktop (§6) are in scope. After this PR
  the application boundary has one telemetry read semantics, and P1.4 builds on it.
- **O3 — injection seam.** Accepted: the run-scoped `translate` + `deliver` seam, with a
  real prepared session, source translation, ingestor and the public reads.
- **O4 — snapshot defaults.** Accepted: T/K are resolved once at snapshot creation and
  disclosed; K = 0 uses T = tick 0.

## SPEC_CONFLICT

1. **Resolved (design):** attempt-encoded telemetry identifiers would reveal failed
   prepares, against playground-backend-v0 §RunOutcome. Fixed by the opaque token (§2)
   and `test_public_telemetry_ids_hide_prepare_attempts`.
2. **Resolved by versioning (O2):** the frozen E0.2 notes pin the v0 read semantics:
   - `e0-2b-application-transport.md` §v0 contract: four methods, hours bounds, the error table;
   - `e0-2-application-views.md` §Sources and failures, §Arbitrary-history investigation
     and §Deterministic bounds: `TelemetryView` source, recent window, hours tolerance,
     `Binding.signal_id` = runtime id;
   - `e0-2c-desktop-backend.md`, `e0-2c-tauri-shell.md` and
     `e0-2c-packaged-sidecar.md`: v0 protocol, four-method allowlist, v0 smoke reads.

   Changing them in place would silently break v0. The PR instead publishes transport v1
   and amends each note with a "Superseded by application transport v1 (P1.1C)"
   section. The v0 text stays as history; the v1 methods, models, defaults and error
   codes are stated there, and the semantics are owned by this note and
   plant-telemetry-contract-v0. ADR-002 needs no amendment: the boundary is preserved,
   and its "versioned carefully" rule is met by the version bump. playground-backend-v0
   gets only the additive O1 amendment, because `TelemetryView` v0 is unchanged.

No other conflict was found. If implementation finds that a v1 read cannot meet a
plant-telemetry clause, it stops with `SPEC_CONFLICT` instead of adding a local bypass.

## Next step

Step 2, after coordinator review: implement against this note, tests first, in the
same PR. After merge, P1.4 reads live telemetry through these snapshot-bound reads;
Agent read migration remains P1.5.
