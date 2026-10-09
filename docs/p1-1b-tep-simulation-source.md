# P1.1B — TEPSimulationSource → Canonical Telemetry

Status: implemented (P1.1B). Builds on [P1.1A](p1-1a-telemetry-core.md); normative
contract: [Plant Context + Telemetry v0](specs/plant-telemetry-contract-v0.md),
[ADR-003](decisions/ADR-003-industrial-environment-boundary.md).

> TEP-specific identifiers exist inside the adapter, but they do not enter the
> generic telemetry contract.

## What is implemented

`src/tep_agent_lab/tep_telemetry.py`:

```text
TEPEnvironment / ReferenceWorld          owners; they advance physics
  -> tep_world.sanitize_observation       existing Agent-visible allowlist (reused)
  -> TEPSimulationSource.translate_sanitized
  -> tuple[SourceObservation, ...]        one atomic batch per source record
  -> TelemetryIngestor                    trusted caller supplies ingest_time
  -> InMemoryTimeSeriesStore -> TelemetryReadSnapshot -> TimeSeriesReader
```

- `TEPSignalBinding`: immutable adapter-local record per visible binding (canonical
  signal id, runtime variable id, `attached_to`, relation, quantity, unit, mapping
  method and source refs, ProcessGraph fixture id/version/content checksum).
- `build_signal_bindings(graph)`: deterministic table from `ProcessGraph.bindings()`,
  sorted by signal id. No hand-written mapping.
- `simulation_ticks(hours)`: exact simulation-second conversion.
- `TEPSimulationSource(graph, source_id=, clock_id=, clock_origin=)`: `source_id`,
  `event_clock`, `signal_ids`, `next_sequence`, `bindings()`, `binding(id)`,
  `store_bindings()`, `event_time(hours)`, `translate_sanitized(record)`.

## Ownership

The source is a translator, not a world owner. It never calls `reset`, `step`,
`rollout`, `apply`, `apply_scenario`, `fork` or `snapshot`, holds no TEP physics,
and owns no store, `context_ref`, `visibility_policy_ref` or ingest time. Callers
build the P1.1A store from `source.event_clock` and `source.store_bindings()` plus
their own registration inputs:

```python
batch = source.translate_sanitized(sanitize_observation(observation))
ingestor.ingest(batch, ingest_time=trusted_ingest_time)
```

Dependency direction is one way: `tep_telemetry` imports `telemetry` and the public
`tep_sim` API; `telemetry` imports neither (tested). Evaluator disturbance bindings
are never imported.

## Canonical signal identity

`signal_id` is the human-verified ProcessGraph `VariableBinding.semantic_entity_id`.
XMEAS/XMV ids stay adapter-local metadata:

```text
XMEAS(9) -> ProcessGraph binding -> reactor.temperature_measurement
XMV(10)  -> ProcessGraph binding -> reactor_cooling.flow_actuator
```

The pinned graph (`tep-process-graph` 0.2.0, `HUMAN_VERIFIED`) gives 41 MEASURES +
12 ACTUATES = 53 visible bindings. Tests check coverage against the graph's own
bindings, not just the count. Table construction fails closed on an unpinned or
non-human-verified graph, a non-`HUMAN_VERIFIED_MAPPING` binding, any relation
other than MEASURES/ACTUATES (so DISTURBS never enters), duplicate canonical signal
ids, duplicate runtime-variable bindings, or an empty table.

Values are chosen by binding relation, never by runtime-id prefix:

```text
MEASURES  runtime_variable_id -> sanitized["measurements"]
ACTUATES  runtime_variable_id -> sanitized["manipulated_variables"]
```

Units come from the simulator registry through `VariableBinding.describe()`
(e.g. `deg C` for XMEAS(9)).

## Record translation (fail closed)

One sanitized record → one batch with every visible signal, all sharing
`source_id`, `event_time` and `sequence`, quality `GOOD`, no status/metadata ref.
`translate_sanitized` rejects:

- a non-mapping (for example a raw tep-sim `Observation`);
- any key set other than exactly `simulation_time_hours`, `measurements`,
  `manipulated_variables`, `shutdown_state`, `safety_margins` (so raw
  `active_disturbances` or run internals fail closed; unexpected names are counted,
  not echoed in the error);
- a value map whose keys are not exactly the visible bindings of that relation
  (missing bound value, or an unbound/hidden key);
- a value that is not a finite `float`; a non-boolean `shutdown_state`;
- an invalid simulation time.

`shutdown_state` and `safety_margins` are validated but not emitted as signals;
whether they become operational events, derived state or safety context is later
product work. Dirty-stream behavior (missing/BAD samples) is not invented here.

## Simulation clock

One `ClockKind.SIMULATION` clock per source incarnation, resolution 1 s/tick, with
a caller-supplied explicit origin. Simulation time is never UTC and the wall clock
is never event time.

Conversion of `simulation_time_hours`:

```text
require finite, non-negative int/float (bool rejected)
seconds = hours * 3600
ticks   = round(seconds)
accept iff |seconds - ticks| <= 0.05 s   (TICK_TOLERANCE_SECONDS)
```

tep-sim accumulates time as a float sum of 1/3600 h. Measured drift is 1e-4 s at
30 simulated days, 1.2e-3 s at 90 days and 1.1e-2 s at 365 days, so 50 ms covers a
simulated year with margin while staying 20x tighter than a tick. Off-grid values
(for example half a second) are rejected, never floored or truncated. The
sanitized record has no integer step count to use instead.

## Event time, sequence and deduplication

```text
event_time:
  normalized simulation tick

sequence:
  independent monotonic source-observation sequence

deduplication:
  unavailable at the TEP adapter boundary because sanitized observations expose no
  durable delivery identity; each received observation is distinct
```

Event time is not sample identity (P1.0 contract): tick collisions must never
collapse samples. TEP can change state without advancing time (for example
`observe`, then a MANUAL MV `apply`, then `observe` again at the same second), and
both observations are valid.

`TEPSimulationSource` owns the sequence allocator of its incarnation:

- it starts at 0; each successful `translate_sanitized` allocates exactly one
  sequence, shared by all 53 signals of that record; the next record gets the next
  value;
- the sequence is never derived from event time, values, hashes, runtime
  variables or signal order;
- allocation happens only after the whole record is validated, so a rejected
  record consumes no sequence;
- a `threading.Lock` guards allocation, so concurrent callers never receive
  duplicate sequences;
- deterministic replay comes from ordered delivery: a fresh source with the same
  construction inputs, fed the same ordered records, assigns the same
  `0, 1, 2, ...`.

Reliable source-delivery deduplication is unavailable for the current TEP adapter
because the sanitized TEP record exposes no durable native delivery identifier.
The adapter does not infer duplicates from equal event time, values or hashes
(the P1.0 contract forbids it), and it keeps no cache. Consequences:

- the same sanitized record translated twice gets two sequences, and both batches
  are accepted (K advances twice);
- the rollout origin record (tep-sim telemetry repeats the pre-rollout
  observation) is a new received observation with a new sequence; callers that
  already ingested that state can skip `records[0]`, as `ReferenceWorld.advance`
  does for its history;
- same-tick changed observations are retained side by side and read in sequence
  order; `current` returns the highest sequence at the latest tick;
- P1.1A store semantics are unchanged: redelivering one already translated batch
  is still an exact duplicate, and changed content under an existing identity is
  still an `IdentityConflict`.

Future sources such as OPC UA or historian adapters may use native durable
sequence/delivery identities where they exist.

A simulator reset restarts event time at tick 0, so each reset/run uses a new
source incarnation (new `source_id` and clock identity). Its sequence restarts at
0. P1.1A stores hold one event clock, so a new incarnation is ingested into its
own store.

## Atomic record batches

Each record is ingested as one batch, so all 53 signals of one source record share
one commit revision K. A snapshot never sees part of a record.

## Hidden-state isolation

- Adapter mapping contains only MEASURES/ACTUATES; no `IDV(...)` is a signal.
- Input passes the existing `sanitize_observation` allowlist; no second sanitizer.
- Integration test injects a hidden disturbance via the harness, shows raw rollout
  records fail closed, and screens every canonical sample with `leakage_findings`.

## Delivery

Deterministic, in-process only. No sleep/wall-clock pacing, websocket, REST or UI
refresh. ACCELERATED/REAL_TIME pacing stays compatible but is deferred.

## Current production path is unchanged

`ReferenceWorld.history()/advance()`, `RunQueries.telemetry()`, tool-surface
`get_history`, `ApplicationViewService` and the desktop are untouched; no existing
module imports `tep_telemetry` (tested). This is a parallel canonical path.

## Tests

`tests/test_tep_telemetry_source.py`: binding table and provenance, coverage,
semantic ids, relation-driven value selection, fail-closed inputs, time conversion,
sequence allocation (one per record, monotonic and independent of ticks, not
consumed by failures, fresh-source replay, concurrency), same-tick retention,
identical records accepted as new observations, unchanged P1.1A store semantics,
incarnations, atomic commits, a real pinned `TEPEnvironment` reset + rollout with
progressive ingestion and value equivalence for all 53 signals, a real MANUAL
same-tick MV intervention, `ReferenceWorld.history()` translation, hidden-state
isolation and dependency direction. P1.1A boundary tests stay green.

## Known limitations

- Deterministic delivery only; no pacing or live transport.
- One source incarnation per store (single event clock domain in P1.1A).
- No generic `SignalDescriptor`; `TEPSignalBinding` is adapter-local.
- Shutdown/safety-margin data are not canonical telemetry yet.
- No dirty-stream simulation (gaps, BAD quality, late or reordered data).
- Counterfactual branches are not wired to the canonical path.
- No durable source-delivery deduplication: sanitized TEP records carry no native
  delivery identity, so a redelivered record is stored again as a new observation.
- Sequence state is in-memory and per source object; it is not persisted, and
  restarting the process requires a new source incarnation.
- Exact coverage: a ProcessGraph that binds only part of the 41 XMEAS + 12 XMV
  makes every sanitized record fail closed as unbound. That is intended for the
  clean adapter; partial graphs need an explicit exclusion list.
- Samples of a record with `shutdown_state = True` are still `GOOD`; shutdown is
  not yet represented in canonical telemetry.
- Inherited from P1.1A: the reader's `max_scan_records` bound counts a binding's
  whole K-committed prefix (no event-time index). One record per signal per TEP
  record means a long run eventually makes every read of that binding exceed the
  bound, however narrow the interval. Size the reader for the run, and add an
  event-time index in the core before long-running or live sources.

## Next milestone

Canonical context integration: generic `SignalDescriptor` derived from the
binding table, then migrate consumers (P0 queries, Agent telemetry tools,
application views) to snapshot-bound reads.
