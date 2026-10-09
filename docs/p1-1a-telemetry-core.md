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
  in the envelope, never in the sample. The store builds every sample from a
  `SourceObservation` plus trusted `ingest_time`. `ingest_sequence` (K) is store-wide and
  distinct from `sample.sequence`.
- Store: atomic batches share one revision; exact duplicates (type-strict value
  equality, ignoring the newly proposed ingest_time) resolve to the original record;
  identity conflicts fail the whole batch; an all-duplicate batch does not advance K.
  Registration (store id, incarnation, namespace, context ref, event/ingest clocks,
  source/signal bindings) is frozen at construction; unknown bindings fail closed.
- `TelemetryReadSnapshot` pins store ref, K, T, event clock, `context_ref` and
  `visibility_policy_ref`; all of them enter `snapshot_ref`. The visibility policy
  ref is an opaque immutable identifier frozen in store registration; P1.1A does
  not evaluate it. `mapping_ref` is absent (cross-clock mapping is unsupported).
- Snapshot resolution rejects another incarnation/namespace/context/visibility
  policy ref, a different clock and a future K. Historical K stays readable for
  the life of the incarnation.
- Reader: `current` returns the last eligible sample at or before T regardless of
  quality, or `NO_DATA`. `history` requires a same-clock inclusive interval with
  `event_end <= T`, positive `max_points`. Reduction is the frozen
  `floor(i * (n - 1) / (m - 1))` (m=1 -> last). Results carry the snapshot (and
  `snapshot_ref`), binding, bounds, eligible/returned counts, sample identities,
  qualities, reduction policy and retention coverage.
- Resource bounds (`TimeSeriesReader`): `max_points_limit`, `max_interval_ticks`
  and `max_scan_records`. The scan bound applies to the binding's K-committed
  candidate prefix, sized by bisection before any record is read; above the
  limit the read raises `ResourceLimitExceeded` with no partial scan, no
  truncation, and no counts or values in the error. Because the prefix is fixed
  by (binding, K), a snapshot's pass/fail outcome never changes after later
  appends, and other bindings never count against it.
- Retention coverage: results report `COMPLETE_FOR_STORE_INCARNATION`, meaning every
  record this store incarnation accepted at or before K is retained and was
  considered. It does not claim the physical/source stream had no missing
  deliveries, gaps or late data; source completeness is never inferred.

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

The reader-internal `InMemoryTimeSeriesStore._eligible` applies both cutoffs (and the binding filter)
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
- No caller authorization: `visibility_policy_ref` is pinned and matched, not
  evaluated; authorization arrives with application integration.
- No multi-source resolution policy (callers select `source_id` explicitly), no
  quarantine store for conflicts (they are rejected), no derived state features.
- No indexing: within the scan bound, reads filter the binding's K-committed prefix
  linearly. The bound counts candidates committed at or before K, including those
  later excluded by T or by the interval.
- No retention eviction, so coverage is always `COMPLETE_FOR_STORE_INCARNATION`;
  partial-coverage states arrive with retention policies.

## Why `ReferenceWorld.history` is unchanged

The existing sanitized history path (`ReferenceWorld.history()`, `RunQueries.telemetry()`,
Agent `get_history`) is current production behavior used by the desktop, D0
benchmarks and Agent tools. P1.1A builds the canonical path in parallel so that it
can be proven in isolation; migrating consumers through public P0/application
queries is planned for later milestones (P1.1C for application reads, P1.5 for Agent
tools; [ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md)) and would change
observable behavior.

## Next step

P1.1B: `TEPSimulationSource` adapter translating sanitized TEP observations into
`SourceObservation`s on an explicit SIMULATION clock, ingested through this core.
