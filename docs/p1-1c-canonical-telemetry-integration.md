# P1.1C — Canonical Telemetry Integration (implementation note)

Status: planned (P1.1C); not implemented. Written before code, as
[ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md) Decision 6 requires. Normative
contracts: [Plant Context + Telemetry v0](specs/plant-telemetry-contract-v0.md) (owner),
[Playground backend v0](specs/playground-backend-v0.md),
[ADR-002](decisions/ADR-002-ui-application-boundary.md). Builds on
[P1.1A](p1-1a-telemetry-core.md) and [P1.1B](p1-1b-tep-simulation-source.md).

## Goal

Discharge the plant-telemetry migration obligation for application reads (§Existing
architecture consistency and migration obligations, as renumbered by ADR-004 Decision 3):

1. event-time-bounded scans in the generic core;
2. P0 run ingestion: reference-world records -> `TEPSimulationSource` -> one run-scoped
   `InMemoryTimeSeriesStore` per prepared session;
3. snapshot-bound current/history through the public P0 (`RunQueries`) and application
   (`ApplicationViewService`) query boundary. Every read pins one exact
   `TelemetryReadSnapshot`; no read resolves "latest".

## Non-goals

- No Agent tool change. `get_history`, tool specs, `TOOL_SURFACE_VERSION`,
  `BRIDGE_VERSION` and `ReferenceWorld.history()` stay as they are until P1.5.
- No ContextSnapshot, InvestigationContextView or P1.3 composition; no
  `PlantContextRevision`, no `SignalDescriptor`.
- No injected dirty-stream replay harness (gaps, BAD/UNCERTAIN injection, clock skew,
  disconnect). It is deferred and required before any non-simulated source (Decision 6).
- No non-simulated source, counterfactual-branch ingestion, durable store, retention,
  cross-clock mapping, transport protocol change or desktop change.
- No change to `RunManifest`, `TelemetryView` v0, the four v0 application reads, D0
  fixtures or D0 tests.

## Design

```text
prepare(): case_setup(world) -> ReferenceWorld.history()        (unchanged owner)
  -> RunTelemetry.sync_reference_history(history)               (new, P0-owned)
       -> TEPSimulationSource.translate_sanitized(record)       (P1.1B, one batch/record)
       -> TelemetryIngestor.ingest(batch, ingest_time=P0 clock)  (P1.1A)
       -> run-scoped InMemoryTimeSeriesStore (one commit K per record)
  -> RunManifest published (bytes unchanged) -> READY
reads: RunQueries.telemetry_snapshot() -> exact (T, K) view
       RunQueries.telemetry_current/history(snapshot=...) -> TimeSeriesReader
       ApplicationViewService.open_telemetry_snapshot/get_signal_current/get_signal_window
```

### 1. Event-time-bounded scans (`telemetry.py`)

The core keeps a per-binding event-time index next to the commit-ordered stream.
Required properties (the data structure is an implementation choice; recommended:
commit-ordered blocks, each sorted by `(ticks, sequence)`):

- `history`: the scan bound `max_scan_records` counts exactly
  `C = |{commit <= K, event_start <= event <= event_end}|` for the binding, computed
  from the index before any value is read. A narrow window on a long binding succeeds.
- `current`: finds the last eligible sample at or before T from the index without a
  linear prefix scan; `eligible_count` stays exact. It no longer fails on prefix size.
- Outcome (pass/fail, counts, values) is a function of `(binding, K, T, window)` only.
  Records committed after K never contribute, not even to a `ResourceLimitExceeded`.
  Error messages still carry no counts or values.
- New `store.event_frontier(ingest_sequence) -> TimePoint | None`: the maximum event
  time committed at or before K.

This replaces the P1.1A "whole K-committed prefix" bound (a documented limitation, not
contract). The P1.1A note is updated in the implementation PR.

### 2. Run-scoped store and ingestion (`run_telemetry.py`, `playground.py`)

`RunManager._session` builds one `RunTelemetry` per prepare attempt and stores it in
`RunSession.telemetry`. Registration, all derived from the frozen run inputs:

| Field | Value |
|---|---|
| source | `TEPSimulationSource(graph, source_id=f"tep-reference:{run_id}/{attempt}", clock_id=f"tep-sim:{run_id}/{attempt}", clock_origin=f"tep-reset:{run_id}/{attempt}")` |
| store | `store_id="p0-reference-telemetry"`, `namespace=f"p0-run:{run_id}/{attempt}"`, bindings `source.store_bindings()` (53) |
| `context_ref` | see §4 |
| `visibility_policy_ref` | `f"{VISIBILITY_POLICY_VERSION}#AGENT"` (store holds only sanitized AGENT-visible data) |
| ingest clock | `UTC`, 1 ms/tick, origin `1970-01-01T00:00:00Z/utc`; `ingest_time` = trusted `RunManager.clock()`; diagnostic only, K orders knowledge |
| reader | `max_points_limit=1000`, `max_interval_ticks=2_592_000` (30 d), `max_scan_records=50_000` (service policy, not contract) |

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
- `deliver(batch)` is the single commit seam (`TelemetryIngestor` plus next trusted
  ingest time). It is reachable only through the in-process `RunSession` (trusted
  harness), never through `RunQueries`, `ApplicationViewService` or transport. Tests use
  it to re-deliver, reorder or add batches (§Acceptance). It is not the deferred
  dirty-stream harness: it injects no gaps, quality, skew or disconnect.
- Reference and counterfactual streams stay separate: branches are not ingested.
- The store lives as long as the session object; `RunSession.close()` releases simulator
  handles but keeps the store. A run reopened from disk has no store: snapshots report
  unavailable, never today's contents.

### 3. Snapshot-bound public reads (`playground_views.py`, `application_views.py`)

`RunQueries` (both AGENT and EVALUATOR scope; the data is AGENT-visible):

```python
telemetry_snapshot(*, event_horizon_ticks: int | None = None,
                   ingest_sequence: int | None = None) -> TelemetrySnapshotView
telemetry_current(signal_id: str, *, snapshot: Mapping) -> CanonicalCurrentView
telemetry_history(signal_id: str, *, snapshot: Mapping, event_start_ticks: int,
                  event_end_ticks: int, max_points: int) -> CanonicalHistoryView
```

- `telemetry_snapshot` is the only call that chooses (T, K). Defaults are resolved once
  and disclosed: K = committed revision now, T = `event_frontier(K)`. Explicit K must be
  `<= current`; explicit T is any non-negative tick on the run clock.
- `current`/`history` require `snapshot` (the `snapshot` mapping of a
  `TelemetrySnapshotView`). P0 rebuilds `TelemetryReadSnapshot` from it with an exact
  key set and contract version, recomputes `snapshot_ref`, requires equality, then calls
  `store.resolve`. There is no default and no server-side "latest".
- `signal_id` is the canonical id (ProcessGraph `semantic_entity_id`, already present
  in `ProcessGraphView` bindings). Unknown ids fail closed. XMEAS/XMV ids are not
  accepted on this path.
- Views go through `RunQueries._view` (leakage screen, frozen JSON). They carry
  `snapshot_ref`, the snapshot body, binding, bounds, eligible/returned counts, sample
  identities, event ticks, ingest times, qualities, reduction policy and coverage.
- These reads never touch the world, so they do not take `world_lock`. They work in
  READY, RUNNING and terminal states while the session is in-process; otherwise
  `ViewUnavailable`.

`ApplicationViewService` adds domain-generic reads over those queries (AGENT only):
`open_telemetry_snapshot(run_id, ...) -> TelemetrySnapshot`,
`get_signal_current(run_id, signal_id, *, snapshot) -> SignalCurrent`,
`get_signal_window(run_id, signal_id, *, snapshot, event_start_ticks, event_end_ticks,
max_points=500) -> SignalWindow`. New frozen models in `application_models.py`:
`TelemetrySnapshot` (all snapshot fields plus clock kind/resolution/origin and
`snapshot_ref`), `CanonicalPoint(event_ticks, sequence, value, quality)`,
`SignalCurrent`, `SignalWindow`. Existing `get_run`/`get_signal`/`get_signal_history`
and transport v0 are unchanged (open question O2).

### 4. `context_ref` in P1.1C (finding I9)

`context_ref = f"context-source:{graph_source.source_id}:sha256:{checksum(to_jsonable(graph_source))}"`,
where `graph_source` is the exact AGENT-visible `PROCESS_GRAPH` `ContextSourceRef` that
P0 attested and froze at READY, and the same source from which the `TEPSimulationSource`
binding graph was built (P0 already checks graph provenance against it). It is no longer
an opaque caller string. It is not a `PlantContextRevision`. P1.3 maps it through this
recorded ProcessGraph source ref (ADR-004 Decision 4 item 4), never through "latest"
metadata. The manifest is not changed. The ref is recomputable from the manifest's
`canonical_context_sources`.

## Files

| File | Change |
|---|---|
| `src/tep_agent_lab/telemetry.py` | event-time index, window-bounded scans, `event_frontier`; still stdlib-only, no TEP vocabulary |
| `src/tep_agent_lab/run_telemetry.py` (new) | `RunTelemetry`: source/store/ingestor/reader registration, `sync_reference_history`, `deliver`, `snapshot`, `resolve_snapshot(mapping)`, `context_ref_for(graph_source)` |
| `src/tep_agent_lab/playground.py` | `RunSession.telemetry`; build and sync in `_session`; prepare fails closed on ingest failure |
| `src/tep_agent_lab/playground_views.py` | three canonical telemetry views; `TelemetryView` v0 untouched |
| `src/tep_agent_lab/application_views.py`, `application_models.py` | three snapshot-bound reads and four models; v0 reads untouched |
| `tests/test_telemetry_core.py` | index tests; scan-bound tests rewritten to window semantics |
| `tests/test_tep_telemetry_source.py` | parallel-path test replaced (below) |
| `tests/test_canonical_telemetry_integration.py` (new) | live P0 acceptance tests |
| `docs/p1-1a-telemetry-core.md`, `docs/p1-1b-tep-simulation-source.md`, `docs/architecture.md`, `docs/specs/playground-backend-v0.md` (O1), `docs/program-status.md` | implemented-state updates in the implementation PR |

Not changed: `tep_world.py`, `tool_surface.py`, `tool_bridge.py`, `benchmark.py`,
`application_transport.py`, desktop, `src/tep_agent_lab/fixtures/**`, D0 tests.

## Acceptance tests

Core (`tests/test_telemetry_core.py`):

| Test | Proves (contract clause) |
|---|---|
| `test_window_scan_bound_counts_only_k_committed_window_candidates` | §Bounded deterministic reads: eligible set and enforced resource bounds |
| `test_scan_outcome_never_depends_on_records_after_k` | §Bounded reads "hidden records cannot affect counts or metadata"; §Hard temporal leakage invariant |
| `test_current_on_long_binding_does_not_scan_prefix` | §Bounded reads: current semantics; resource bounds |
| `test_indexed_reads_match_bruteforce_under_out_of_order_commits` | §Bounded reads (eligible set, read order, reduction) vs a brute-force filter over all (K, T, window), random commit orders |

Live P0 wiring (`tests/test_canonical_telemetry_integration.py`; real `RunManager`
prepare with `FakeProvider`; reads through `RunQueries` and `ApplicationViewService`):

| Test | Proves (contract clause) |
|---|---|
| `test_p0_prepare_ingests_reference_history_through_tep_source` | Positive integration test replacing the parallel-path test (alignment review §8 and "Next three engineering steps" item 2): `K == len(history)`, one commit per record, sequences `0..n-1`, all 53 signal values equal the sanitized values at every K prefix; §Existing architecture (`ReferenceWorld.__init__/advance/history`) |
| `test_production_wiring_dependency_direction` | Replaces `test_existing_production_path_does_not_use_adapter`: `playground` imports `run_telemetry`; only `run_telemetry` imports `tep_telemetry`; `telemetry` imports neither; `tool_surface`, `tool_bridge`, `benchmark` import neither; §TEP boundaries |
| `test_store_context_ref_is_the_attested_process_graph_source` | §Canonical Plant Context ("telemetry's pinned context must agree with the plant revision used for its bindings"); ADR-004 Decision 4 item 4; I9 |
| `test_prepare_fails_closed_when_ingestion_fails` | playground-backend-v0 prepare atomicity; §Sources (fail closed) |
| `test_reads_require_an_exact_snapshot` | §Append store and read snapshots: no read without a snapshot; forged `snapshot_ref`, other incarnation/context/policy, future K rejected; ADR-004 Decision 6 "no latest" |
| `test_live_temporal_leakage_invariant` | §Hard temporal leakage invariant: at (T, K), a late sample (event <= T) committed at K+1 and a sample with event > T are invisible; repeated reads equal and `repr`-identical, via both boundaries |
| `test_live_late_and_out_of_order_delivery` | §Identity: late/out-of-order arrivals append with original event time and new commit; older snapshots unchanged; new snapshot reads in `(ticks, source_id, sequence)` order |
| `test_live_duplicate_redelivery_is_idempotent` | §Identity bullet 1: redelivered batch returns originals, K unchanged, reads identical; bullet 3: re-translated record is a new observation (duplicates undetectable at the TEP boundary, P1.1B) |
| `test_live_identity_conflict_rejects_batch` | §Identity bullet 2: changed content under an existing identity fails the whole batch; K unchanged |
| `test_live_same_tick_observations_retained` | §Four time concepts "tick collisions do not collapse samples": real MANUAL MV observe/apply/observe at one second, captured in `case_setup`; both retained; current returns the higher sequence |
| `test_snapshot_survives_run_and_is_unavailable_after_reopen` | §Append store retention: READY snapshot reads identically after COMPLETED; reopened run reports unavailable, never today's contents |
| `test_canonical_views_hide_hidden_state` | Hidden-state isolation and ADR-002: harness-injected hidden disturbance; views pass `leakage_findings`, carry canonical ids only; ApplicationViewService rejects non-AGENT queries |
| `test_no_public_ingestion_path` | §Application boundaries ("cannot drive source physics"); `RunQueries` is read-only: no public method reaches `deliver`/`sync_reference_history` |

Regression:

| Test / gate | Proves |
|---|---|
| `test_agent_tool_surface_unchanged` | ADR-004 Decision 6: tool specs, `tool_set_version`, `TOOL_SURFACE_VERSION`, `BRIDGE_VERSION` equal the `e5ccf19` values; `get_history` output for a fixed case is identical |
| `test_v0_telemetry_reads_unchanged` | `TelemetryView` v0 and the four v0 application reads return the same values for the same run |
| D0 freeze gate | `git diff --exit-code origin/main -- src/tep_agent_lab/fixtures tests/test_benchmark.py tests/test_benchmark_family.py tests/test_benchmark_healthy.py tests/test_tool_surface.py tests/test_tool_bridge.py` is empty and those suites pass (Decision 6: frozen D0 fixtures and tests stay byte-identical) |
| full check | `scripts/check.py` with pinned runtime/tep-sim; existing P1.1A/B boundary tests stay green |

The late, out-of-order, duplicate, conflict and same-tick tests inject batches through
`RunSession.telemetry.deliver`, because `ReferenceWorld.history()` cannot produce them in
P0 v0 (O3). Injected records never enter `ReferenceWorld.history()`, so Agent tools
cannot see them.

## Open questions / SPEC_CONFLICT

`SPEC_CONFLICT: none` identified. The design changes no accepted semantic. Owner
decisions needed:

- **O1 — playground-backend-v0 amendment.** The three canonical views are new P0 read
  projections. Recommended: the implementation PR adds an additive "Canonical telemetry
  views" subsection under §Read projections, citing the plant-telemetry obligation.
  `TelemetryView` text is unchanged. Alternative: keep them documented in this note only.
- **O2 — v0 application reads.** `get_run`/`get_signal`/`get_signal_history` keep
  reading `TelemetryView` (runtime ids, recent window, unpinned) so transport v0 and the
  desktop keep working. Recommended: keep them in P1.1C; retire or re-point them in a
  versioned application/transport change with MVP integration. Until then the
  application boundary has two telemetry read semantics, and only the new reads satisfy
  the plant-telemetry contract.
- **O3 — Live-path injection seam.** In P0 v0 the reference history is in order and
  frozen after READY. Late, out-of-order, duplicate and same-tick deliveries are
  therefore proven by injecting at the run-scoped `deliver` seam, with reads through the
  public boundaries. Confirm that this meets "through the live P0 wiring" in Decision 6.
  The alternative is a harness-only `ReferenceWorld` append path, which changes the
  world owner.
- **O4 — Snapshot defaults.** `telemetry_snapshot()` resolves K = now and
  T = `event_frontier(K)` once, at creation, and discloses both. Confirm that this
  creation-time default is acceptable, or require explicit T/K from every caller.

## Next step

Implement P1.1C against this note, tests first. After merge, P1.4 can read live
telemetry through these snapshot-bound reads; Agent read migration remains P1.5.
