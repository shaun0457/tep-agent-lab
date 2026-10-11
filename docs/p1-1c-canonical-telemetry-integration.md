# P1.1C — Canonical Telemetry Integration (implementation note)

Status: implemented (P1.1C). Written before code, as
[ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md) Decision 6 requires, then
implemented on the same branch. Owner decisions are recorded in §Decisions; deviations
found during implementation are listed in §Implementation notes. Note and code ship in one PR on
`feat/p1-1c-canonical-telemetry`. Normative contracts:
[Plant Context + Telemetry v0](specs/plant-telemetry-contract-v0.md) (owner),
[Playground backend v0](specs/playground-backend-v0.md),
[Application API v1](specs/application-api-v1.md) (owning spec for the application
reads and transport v1, added by this PR),
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
| `context_ref` | see §6 |
| `visibility_policy_ref` | `f"{VISIBILITY_POLICY_VERSION}#AGENT"` (store holds only sanitized AGENT-visible data) |
| ingest clock | `UTC`, 1 ms/tick, origin `1970-01-01T00:00:00Z/utc`; `ingest_time` = trusted `RunManager.clock()`, sampled once per sync; diagnostic only (never in a view), K orders knowledge |
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
- P0 calls `seal()` right after the sync: afterwards `sync_reference_history` raises
  and no production ingestion path exists. `RunSession.telemetry` is read-only. The
  only post-seal delivery path is the harness pair `_harness_translate(record)` (the
  session's `TEPSimulationSource.translate_sanitized`) and `_harness_deliver(batch)`
  (`TelemetryIngestor` plus a trusted ingest time, under the telemetry lock).
  Production code never references it (tested). It is never reached through
  `RunQueries`, `ApplicationViewService`, transport or
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
  identities, event ticks, qualities, reduction policy and coverage (no ingest times). Every
  identifier comes from the opaque token (§2), never from the attempt number.
- Failures keep the core exception types and fixed messages (no counts or values):
  `SnapshotUnavailable`/`ClockMismatch`/`TelemetryError`/`ResourceLimitExceeded`.
- These reads never touch the world, so they do not take `world_lock`. They work in
  READY, RUNNING and terminal states while the session is in-process; otherwise
  `ViewUnavailable`.
- `TelemetryView` v0 is unchanged. It stays a P0 projection for D0 surfaces and the
  static E0 observatory. No application read uses it after this PR.

### 4. Application reads, transport v1 and desktop (summary)

The contract is owned by [Application API v1](specs/application-api-v1.md): methods,
models, parameters, the snapshot object, defaults, error codes, bounds, versioning, v0
retirement and the adapter rules. This note only records how the PR realises it:

- `application_views.py` / `application_models.py`: `open_telemetry_snapshot` plus
  `get_run` (optional snapshot; without one no telemetry field is resolved),
  `get_entity`, `get_signal` and `get_signal_history` over the §3 P0 views. Graph data
  still comes only from `ProcessGraphView`, matched on binding `semantic_entity_id`.
  `Signal` and `SignalHistory` carry `source_id` and each `CanonicalPoint` carries
  `sequence`, so the sample identity `(source_id, signal_id, sequence)` is disclosed.
  The v0 recent window (last <= 1000 `TelemetryView` records) is replaced by any interval
  at or before T from the run store, within reader policy.
- `application_transport.py`: `tep-agent-lab.application-transport/v1`, five methods,
  `snapshot` JSON object, `SNAPSHOT_UNAVAILABLE` and `RESOURCE_LIMIT_EXCEEDED`; v0 is
  retired (`UNSUPPORTED_PROTOCOL_VERSION`).
- Desktop: `protocol.ts` (v1, message table), `applicationClient.ts`
  (`openTelemetrySnapshot`, snapshot passed through verbatim), new `probes.ts` (canonical
  ids `reactor.temperature_measurement` and `reactor.pressure_measurement`, labelled
  XMEAS(9)/XMEAS(7) via `source_locator`) and `main.tsx` (one pinned snapshot shown with
  K, T as hours and `snapshot_ref`; a "New snapshot" button re-pins explicitly).
- Rust `bridge.rs`: v1, five-method and per-method parameter allowlists, opaque
  `snapshot`, host-owned `run_id`, two new codes.
- `smoke_sidecar.py`: interactive (one frame, one reply) because reads need the snapshot
  from the first reply; `verify_bundle.py` reuses it. `desktop_backend.py` keeps its framing.
- CI (`.github/workflows/ci.yml`): the "Validate frontend" step runs `npm ci`,
  `npm run build` and `npm test` in one PowerShell step without exit-code checks, so a
  build failure can be masked. It gets a `$LASTEXITCODE` guard after each command.

### 5. Runtime ids and the E0 observatory

The static E0/E0.1 report (`examples/e0_environment_observatory.py`, `e0_demo.py`)
reads P0 `TelemetryView` v0 and Agent-tool artifacts, not the application reads, so it
is unchanged and keeps showing XMEAS ids. In the desktop, XMEAS/XMV appear only as
`source_locator` labels next to canonical ids. Mapping always runs canonical id ->
ProcessGraph binding -> `runtime_variable_id` and is never accepted in reverse as a read
key.

### 6. `context_ref` in P1.1C (finding I9)

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
| `src/tep_agent_lab/run_telemetry.py` (new) | `RunTelemetry`, `TelemetryReadPolicy`, opaque token, `sync_reference_history`, `seal`, `_harness_translate`, `_harness_deliver`, `snapshot`, `resolve_snapshot(mapping)`, `context_ref_for(graph_source)` |
| `src/tep_agent_lab/playground.py` | `RunSession.telemetry`; optional trusted `telemetry_policy`; build and sync in `_session`; prepare fails closed on ingest failure |
| `src/tep_agent_lab/playground_views.py` | four canonical telemetry views; `TelemetryView` v0 untouched |
| `src/tep_agent_lab/application_views.py`, `application_models.py` | v1 reads and models (Application API v1) |
| `src/tep_agent_lab/application_transport.py` | protocol v1, five methods, `snapshot` param, two error codes |
| `desktop/src/lib/protocol.ts`, `applicationClient.ts`, `probes.ts` (new), `desktop/src/main.tsx` | v1 client and snapshot-pinned UI (§4) |
| `desktop/src-tauri/src/bridge.rs` | v1 allowlists and error table |
| `desktop/sidecar/smoke_sidecar.py` | interactive v1 smoke |
| `.github/workflows/ci.yml` | `$LASTEXITCODE` guard after `npm ci`, `npm run build` and `npm test` |
| Tests | `tests/test_telemetry_core.py`, `test_tep_telemetry_source.py`, `test_canonical_telemetry_integration.py` (new), `test_application_views.py`, `test_application_transport.py`, `test_desktop_backend.py`, `desktop/tests/client.test.ts`, `desktop/src-tauri/tests/{bridge,tauri_ipc,packaged}.rs` |
| Docs | `docs/specs/application-api-v1.md` (new owning spec; registered in `docs/specs/README.md` and `AGENTS.md`), `docs/specs/playground-backend-v0.md` (O1), `docs/e0-2-application-views.md`, `docs/e0-2b-application-transport.md`, `docs/e0-2c-desktop-backend.md`, `docs/e0-2c-tauri-shell.md`, `docs/e0-2c-packaged-sidecar.md` ("Superseded by" summaries linking the owning spec), `docs/p1-1a-telemetry-core.md`, `docs/p1-1b-tep-simulation-source.md`, `docs/architecture.md`, `docs/program-status.md` |

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
| `test_live_same_tick_observations_retained` | §Four time concepts (tick collisions do not collapse samples). A MANUAL run whose `case_setup` captures two sanitized `world.observe()` records, before and after an MV apply at the same second. `observe()` does not append to history, so sync never ingests them. After prepare they go through the session's `_harness_translate` and `_harness_deliver`. Both boundaries show distinct sequences and both values, and current returns the higher sequence. `ReferenceWorld` is unchanged |
| `test_snapshot_survives_run_and_is_unavailable_after_reopen` | §Append store retention: READY snapshot reads identically after COMPLETED; reopened run reports `SNAPSHOT_UNAVAILABLE`/`VIEW_UNAVAILABLE`, never today's contents |
| `test_canonical_views_hide_hidden_state` | Hidden-state isolation and ADR-002: harness-injected hidden disturbance; views, models and transport payloads pass `leakage_findings` and carry canonical ids plus `source_locator` only; non-AGENT queries rejected |
| `test_no_public_ingestion_path` | §Application boundaries (cannot drive source physics): no method on `RunQueries`, `ApplicationViewService`, transport or the Rust allowlist reaches ingestion |
| `SealAndSideChannelTests` (`test_run_telemetry_is_sealed_at_ready`, `test_harness_seam_is_never_used_by_production_code`, `test_ingest_time_is_not_public_and_sampled_once_per_sync`, `test_no_agent_output_names_failed_prepares`) | sealed seam; no ingest-time side channel; no `PREPARE_FAILED` or `prepare-NNNN` in any AGENT view, application result or transport output (playground-backend-v0 §RunOutcome) |

Migrated application reads, transport and desktop:

| Test (file) | Proves |
|---|---|
| `test_v1_reads_are_snapshot_bound` (`test_application_views.py`) | `get_signal` and `get_signal_history` require a `TelemetrySnapshot`; `get_run` resolves telemetry only with one. Results equal the canonical P0 views at that snapshot, including full sample identities `(source_id, signal_id, sequence)`. `get_entity` bindings carry canonical `signal_id` plus `source_locator`. Plant §Identity, §Bounded reads, §Application boundaries |
| `test_v1_history_replaces_recent_window` (`test_canonical_telemetry_integration.py`), `test_history_window_defaults_and_reduction`, `test_invalid_history_bounds_before_telemetry` (`test_application_views.py`) | windows older than the former 1000-record projection are served from the store; default start/end are resolved against T and disclosed; `event_end > T`, inverted bounds and policy breaches fail closed |
| `test_application_reads_no_longer_use_telemetry_view` (`test_application_views.py`) | AST/mock check: `ApplicationViewService` never calls `RunQueries.telemetry` |
| `test_application_transport.py` (`test_protocol_is_v1`, `test_method_and_protocol_allowlist`, `test_method_parameter_schemas`, `test_valid_requests_reach_the_service_with_typed_snapshot`, `test_known_errors_and_unexpected_errors_are_redacted`, `test_all_five_reads_match_application_models`, `test_real_domain_errors_and_visibility`) | exact v1 schemas, `snapshot` key set, integer-not-bool checks, v0 request -> `UNSUPPORTED_PROTOCOL_VERSION`, two new fixed error codes, redaction, the 64 KiB bound unchanged, and real E0/P0 equivalence of all five methods |
| `test_desktop_backend_v1_session` (`test_desktop_backend.py`) | NDJSON host serves the v1 snapshot-then-read sequence in one process |
| `desktop/tests/client.test.ts` | five reads use only `application_request` with a host-owned run id; the snapshot passes through verbatim; v1 envelope and message table, including the two new codes; unsafe errors fail closed |
| `desktop/src-tauri/tests/bridge.rs` (`real_python_v1_reads_same_process_session` + unit tests) | real Python process: snapshot then four reads, same PID, `desktop-1..5`, hidden-term screen; v1 allowlists reject client `run_id` and unknown params; error table |
| `desktop/src-tauri/tests/tauri_ipc.rs` (not ignored; development Python under plain `cargo test --locked`) | the same v1 sequence through Tauri IPC |
| `desktop/src-tauri/tests/packaged.rs` (`#[ignore]`; CI only, with `TEP_FROZEN_SIDECAR`) | the same v1 sequence through the frozen sidecar |
| `smoke_sidecar.py` / `verify_bundle.py` | interactive v1 smoke against the frozen EXE and the installed NSIS bundle |
| `test_desktop_probe_ids_match_process_graph` (`test_application_views.py`) | the canonical ids in `desktop/src/lib/probes.ts` equal the pinned ProcessGraph bindings for XMEAS(9) and XMEAS(7) |

How they run: the Python suites run in `scripts/check.py` (CI `test` job, Python 3.11
and 3.13). The desktop job (`windows-latest`) runs `npm ci`, `npm run build` and `npm test`
(typecheck plus `node --test`; each guarded by `$LASTEXITCODE` after this PR), `cargo fmt --check`, `cargo check --locked`,
`cargo test --locked` (includes `bridge.rs` and `tauri_ipc.rs` against development Python), the `custom-protocol` check, `build_sidecar.py` plus
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
same-tick tests inject records through `RunTelemetry._harness_translate` and `_harness_deliver`,
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
  reads in this PR. Transport v1 and desktop are in scope (§4; owning spec Application API v1). After this PR
  the application boundary has one telemetry read semantics, and P1.4 builds on it.
- **O3 — injection seam.** Accepted: the run-scoped harness seam (now `_harness_translate` +
  `_harness_deliver` on a sealed store), with a
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
   section that summarises v1 and links the owning spec
   [Application API v1](specs/application-api-v1.md). The v0 text stays as history;
   telemetry meaning remains owned by plant-telemetry-contract-v0. The owner accepts the
   new spec by merging the PR. ADR-002 needs no amendment: the boundary is preserved,
   and its "versioned carefully" rule is met by the version bump. playground-backend-v0
   gets only the additive O1 amendment, because `TelemetryView` v0 is unchanged.

No other conflict was found. If implementation finds that a v1 read cannot meet a
plant-telemetry clause, it stops with `SPEC_CONFLICT` instead of adding a local bypass.

## Implementation notes

- Index block size `_INDEX_BLOCK = 128` (tuning constant, not contract). `current`
  and `last_event_time` read at most one record per full block plus the partial tail.
- `TimeSeriesReader.last_event_time` and `RunQueries.telemetry_last_event` back
  `Run.telemetry_event_ticks`.
- `SignalHistory` also carries `status` (`DATA`/`NO_DATA`); Application API v1 lists
  it.
- The ingest clock is `p0-ingest-utc` (UTC ms) fed by `RunManager.clock()`. A
  non-ISO clock value fails ingestion, and therefore `prepare()`, closed.
- Review fixes: index blocks are read outside the store lock and history merges sorted
  runs (`heapq.merge`); snapshot objects and `request_id` are validated strictly, and
  the transport parses the snapshot once; `InvalidRequest` separates client errors from
  server `ValueError`s; `serve()` never exits on an unencodable reply; the sidecar
  smoke is deadline-bounded (`tests/test_sidecar_smoke.py`).
- Re-review fixes: transport validates the fixed snapshot shape before recursive
  JSON serialization, so deeply nested invalid frames return `INVALID_REQUEST` and
  the host continues serving. Typed `TelemetrySnapshot` construction, JSON parsing
  and direct service reads share field validation; the P0 resolver rejects unreduced
  resolution strings and negative T before normalization or reference resolution.
  Only validation raised by a public P0 telemetry read is translated to the client
  error family; `TelemetryError` from graph/status assembly or the service itself is
  `INTERNAL_ERROR`. Existing snapshot/resource/clock error codes are preserved.
- The smoke owns the entire process tree: a suspended launcher is assigned to a
  kill-on-close job before its first thread resumes; other platforms use a separate
  process group. Cleanup terminates workers even after the launcher exits. Reaping
  and reader joins each have a two-second bound; stdout is closed only after the
  reader stops, so cleanup never waits on its buffered-read lock. The regression
  `test_worker_holding_stdout_is_killed_even_after_parent_exits` covers an orphaned
  worker inheriting stdout; the existing silent/chatty/crash tests still pass.
- Re-review regression tests: `test_deep_snapshot_is_rejected_and_next_frame_is_served`
  (`test_desktop_backend.py`), `test_direct_service_rejects_invalid_snapshot_fields`,
  `test_p0_rejects_noncanonical_resolution_and_negative_horizon` and
  `test_only_telemetry_read_validation_maps_to_invalid_request`
  (`test_application_views.py`), and
  `test_telemetry_errors_outside_telemetry_reads_are_server_faults`
  (`test_application_transport.py`). Each finding was reproduced before its fix.
- Tests: the core differential test patches `_INDEX_BLOCK` to 7 to cross many blocks.
  Live tests reach the trusted seam through `RunManager._runs[...].session`, as the
  existing P0 execution tests do.

## Review findings F1-F10

The review findings were implemented test-first. Regression coverage lives
in `tests/test_p1_1c_review_regressions.py` (17 tests), with two added desktop tests in
`desktop/tests/client.test.ts`. Existing tests were adjusted only where they expected
unlimited current reads, base-class client error mapping or rejection of a null
optional run snapshot. Frozen D0 fixtures/tests are unchanged.

- **F2 — FIXED.** `current` and slow `last_event_time` now enforce the same eligible
  candidate ceiling as history. Slow store-wide reads share the budget across bindings;
  the O(1) frontier shortcut needs no scan. History counts/slices each block once and
  stops on the first over-limit block/sample. Sparse windows also have bounded index
  work: at most `max(max_scan_records, _INDEX_BLOCK)` block probes/tail checks, with a
  one-block allowance for partial tails. K-visible full blocks are traversed lazily,
  so snapshot capture does not first copy the entire prefix. Resource failures use the
  fixed `ResourceLimitExceeded` family, never partial results. Within bounds, ordering,
  values, identities, eligible counts and reduction remain unchanged.
- **F3 — FIXED.** Repeated bindings agree on quantity, relation and unit only.
  The displayed locator is chosen by the minimum `(entity_id, source_locator)` pair
  in lexicographic order; graph/list traversal order cannot change the choice.
- **F4 — FIXED.** Only explicit client validation raises `TelemetryRequestError`,
  translated to `InvalidRequest` at application reads. Store/snapshot construction,
  binding invariants and malformed server-produced snapshots return `INTERNAL_ERROR`.
  Bad client intervals still return `INVALID_REQUEST`; existing clock, snapshot and
  resource codes are preserved.
- **F5 — FIXED.** Frozen `TelemetryReadPolicy` validates at construction: positive
  integer point/scan limits, non-negative integer interval; booleans, floats and strings
  are rejected. A zero-width interval remains valid, as in the core reader.
- **F7 — FIXED.** Transport `get_run(snapshot=null)` invokes the no-telemetry read,
  as omission does; required signal snapshots still reject null. The Rust bridge
  already passes null through opaquely. The desktop client already treats null as
  omission (verified with a regression); neither adapter needed a behavior change.
- **F8 — FIXED.** Removed the duplicate store-wide `_all` index. Slow last-event reads
  take the maximum over per-binding `_last_at_or_before` results with one shared budget.
  History uses one count/slice pass.
- **F9 — FIXED.** P0 and application resolution validation use one Python parser:
  reduced positive `n/d`, no leading zeros, 1-18 digits per component. Desktop's
  presentation regex uses the same digit grammar/cap. P0 integer checks reuse
  `_checked_int` through an explicit client-validation wrapper.
- **F10 — FIXED.** Removed the unused parameter serialization pass. Integer parameters
  and snapshot T/K fields outside the safe JSON range fail `INVALID_REQUEST` before
  service invocation; booleans/floats remain invalid.

### Known limitations

- **`last_event_time` slow path.** When T is below the frontier, the read visits each
  stream index once (records are budgeted by `max_scan_records`; index visits are not).
  Streams are keyed by the bindings frozen at store construction, so the visit count is
  bounded by the binding set, not by record volume. A K-visible stream inventory would
  remove the visits to streams created after K.

### Kept as specified

- **F1 — kept: prepare fails closed.** A reference-history record that fails
  canonical translation (nonfinite value, off-grid time, binding-set mismatch) fails
  prepare. This follows playground-backend-v0 prepare atomicity and §Sources (fail
  closed), the §Files row and `test_prepare_fails_closed_when_ingestion_fails`. A run
  never reaches READY with partial or unavailable canonical telemetry.
- **F6 — kept: fixed v1 point bounds.** application-api-v1 §Reads fixes the default
  `max_points=500` and the range `[1, 1000]`. A host `TelemetryReadPolicy` with a point
  limit below 500 rejects reads that omit `max_points` with `RESOURCE_LIMIT_EXCEEDED`
  (explicit, never silent), and a limit above 1000 does not widen the v1 API. Hosts
  serving application-api-v1 should allow at least 1000 points.

## Next step

After merge, P1.4 reads live telemetry through these snapshot-bound reads;
Agent read migration remains P1.5.
