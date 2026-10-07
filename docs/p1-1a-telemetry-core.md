# P1.1A — Generic Telemetry Core and Snapshot-Bound Reader

Status: implemented (P1.1A). Normative contract:
[Plant Context + Telemetry v0](specs/plant-telemetry-contract-v0.md),
[ADR-003](decisions/ADR-003-industrial-environment-boundary.md).

## What is implemented

`src/tep_agent_lab/telemetry.py` (stdlib only):

```text
SourceObservation            source event fields; no ingest_time
  -> TelemetryIngestor        trusted caller assigns ingest_time
  -> InMemoryTimeSeriesStore  immutable AcceptedRecord envelopes, store-wide K
  -> TelemetryReadSnapshot    event horizon T + committed revision K
  -> TimeSeriesReader         bounded deterministic current/history
```

- `ClockKind`, `ClockDescriptor` (exact `Fraction` seconds per tick, explicit
  origin for SIMULATION/UTC), `TimePoint(clock_id, ticks)`. Comparisons across
  clock ids raise `ClockMismatch`; no cross-clock mapping is implemented.
- `SignalSample` with identity `(source_id, signal_id, sequence)`, quality
  `GOOD | UNCERTAIN | BAD`, scalar/null values, nonfinite numbers rejected.
- `AcceptedRecord(sample, commit_revision, store, context_ref)`: commit data lives
  in the envelope, never in the sample. `ingest_sequence` (K) is store-wide and
  distinct from `sample.sequence`.
- Store: atomic batches share one revision; exact duplicates (type-strict value
  equality, ignoring the newly proposed ingest_time) resolve to the original record;
  identity conflicts fail the whole batch; an all-duplicate batch does not advance K.
  Registration (store id, incarnation, namespace, context ref, event/ingest clocks,
  source/signal bindings) is frozen at construction; unknown bindings fail closed.
- Snapshot resolution rejects another incarnation/namespace/context, a different
  clock and a future K. Historical K stays readable for the life of the incarnation.
- Reader: `current` returns the last eligible sample at or before T regardless of
  quality, or `NO_DATA`. `history` requires a same-clock inclusive interval with
  `event_end <= T`, positive `max_points`, and enforces reader limits
  (`max_points_limit`, `max_interval_ticks`). Reduction is the frozen
  `floor(i * (n - 1) / (m - 1))` (m=1 -> last). Results carry the snapshot (and
  `snapshot_ref`), binding, bounds, eligible/returned counts, sample identities,
  qualities and reduction policy.

## Ownership

The telemetry core is new application/domain infrastructure owned by this module.
It replaces nothing: `ReferenceWorld`, `SimulationSandbox`, ProcessGraph, P0
`CanonicalContextRegistry`/`RunQueries`, Agent tools and RcaState keep their owners.
K is not `ReferenceWorld.revision()`, a simulator snapshot or an RcaState revision.
`tests/test_telemetry_core.py` checks that the module imports the stdlib only and
contains no simulator-specific vocabulary.

## Hard temporal-leakage invariant

> A read pinned to event horizon T and ingestion revision K never observes a sample
> whose commit revision exceeds K or whose event time exceeds T.

`InMemoryTimeSeriesStore.eligible` applies both cutoffs (and the binding filter)
before sorting; `current`, `history`, counts and reduction only see that set.
Regression tests: an 8-minute sample committed at K=3 is invisible to a (T=10 min,
K=2) snapshot, whose repeated reads stay equal and `repr`-identical; a 15-minute
sample is never visible at T=10 min.

## Known limitations

- In-memory only; no persistence or restart recovery. A restarted process is a new
  incarnation and old snapshots report `SnapshotUnavailable`.
- One event clock domain per store; multi-domain horizons and explicit cross-clock
  mappings are deferred.
- Bindings are plain source/signal registrations; `SignalDescriptor`, value-kind
  checks against descriptors, and binding to plant context revisions are later work.
- No caller authorization/visibility policy, no multi-source resolution policy
  (callers select `source_id` explicitly), no quarantine store for conflicts (they
  are rejected), no derived state features.
- Snapshots carry no `visibility_policy_ref`/`mapping_ref`, and results disclose no
  retention coverage: the in-memory incarnation retains everything it accepted, so
  coverage is complete or the snapshot is unavailable. Both arrive with later
  visibility/retention work.
- Resource bounds cover the requested interval and returned points only. History
  scans the selected binding's records in memory; indexing and a scan cap are deferred.

## Why `ReferenceWorld.history` is unchanged

The existing sanitized history path (`ReferenceWorld.history()`, `RunQueries.telemetry()`,
Agent `get_history`) is current production behavior used by the desktop, D0
benchmarks and Agent tools. P1.1A builds the canonical path in parallel so that it
can be proven in isolation; migrating consumers through public P0/application
queries is planned for later milestones (P1.4) and would change observable behavior.

## Next step

P1.1B: `TEPSimulationSource` adapter translating sanitized TEP observations into
`SourceObservation`s on an explicit SIMULATION clock, ingested through this core.
