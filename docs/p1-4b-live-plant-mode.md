# P1.4B — Live plant mode

## LOS-B-S1 (issue #36)

This slice implements the clean deterministic simulated stream from
[Live Operational State v0](specs/live-operational-state-v0.md) §Live plant session
(OD-4), §Backpressure, retention and resource bounds (OD-6), LOS-B1, the snapshot
stability part of LOS-B3 and LOS-B6. The
[Playground live-mode amendment](specs/playground-backend-v0.md#live-plant-session-mode-amendment-2026-10-12)
owns its attachment beside P0 runs. This is delivery evidence, with no Agent capability claim.

```text
[RunManager.prepare(live_policy)] -- attested graph/config/seed --> [live PREPARED]
[P0 READY inventory + RunManifest] -- immutable attachment refs --> [session provenance v0]
[trusted start_live] -- single stepping owner --> [live ACTIVE producer thread]
[public tep-sim step, fixed 1 s] -- sanitized observation --> [TEPSimulationSource]
[TEPSimulationSource] -- one assigned source batch --> [session append store, atomic K]
[session append store] -- exact T/K --> [RunQueries -> ApplicationViewService]
[configured 300 steps] -- close physics, release owner --> [live STOPPED, retained reads]
```

Live mode is chosen by the typed `live_policy` argument during preparation. S1
requires exactly the attested AGENT PROCESS_GRAPH source, an explicit empty tool
selection and a record interval matching the fixed one-second cadence. It accepts
no case setup or benchmark binding. The provenance records the exact interim
PROCESS_GRAPH source and an explicit empty engineering selection; no P1.3 revision
or ContextSnapshot is invented.

Preparation keeps the P0 assembly and immutable RunManifest contract. It closes
that assembly's investigation environment and sandbox before creating a separate
harness-owned live `TEPEnvironment` with the same pinned configuration and seed.
The live environment has no ReferenceWorld, Agent tool or investigation handoff.
Its initial observation is sanitized through the existing allowlist, translated
once and committed before READY. Any error closes owned resources and publishes
neither READY nor a partial run manifest. Trusted `live-session-provenance.json`
pins lifecycle/provenance versions, manifest/inventory checksums, repository and
numerical revisions, configuration/seed, bindings, source/clock/store identities,
reader/delivery/resource policies and exact context selections.
Its byte checksum is bound into RUN_READY and verified alongside manifest reads.

The live append store is selected once as `RunSession.telemetry` at preparation.
It never reopens the separate sealed `RunSession._telemetry` P0 store. Shared
snapshot resolution and existing bounded readers serve both attachments through
the same public RunQueries and ApplicationViewService operations. Public live
telemetry is labeled **clean deterministic simulated stream**; neither diagnostic
ingest time nor hidden configuration/private physics state is projected.

An explicit trusted `start_live` acquires a single owner and launches one finite
producer thread. Reads, model turns and printing do not start or release physics
steps. Each public `step()` advances one integration second, then sanitization,
one translation and one synchronous atomic commit occur in order. A bounded
immutable `LiveCommit` records step, source sequence, event tick, K and batch
checksum. At most one assigned batch is pending. Existing store locking makes a
mid-delivery snapshot resolve either the complete preceding prefix or the complete
new batch. No source registration occurs on this delivery path.

The independent lifecycle/provenance are versioned as v0. S1 implements
`PREPARED -> ACTIVE -> STOPPED` at its fixed step limit; PAUSED is a reserved v0
state with no transition yet. A minimal fail-closed guard prevents an unexpected
producer error from advancing again and records FAILED with redacted terminal
reason and trusted diagnostic category/pending checksum. It implements no retry,
recovery or restart policy. P0 `RunStatus`, Coordinator start, RunOutcome and
COMPLETED meanings stay unchanged; Coordinator start is rejected for a live
attachment. Agent tools and projections are unchanged.
Every live lifecycle event is persisted in a separate trusted append-only RunLog
under the run's lifecycle directory, with last successful step/K, pending batch
checksum and exact diagnostic artifact checksum. Failed preparation history stays
there even when its unpublished session directory is removed. Reasons distinguish
source availability, cadence, ingest/translation and resource close; raw exception
text and these trusted records never enter public views. RunSummaryView reports
the live physics attachment's resource state; legacy telemetry history explicitly
returns unavailable for live runs, which use snapshot-bound canonical reads.

## Proposed OD-6 demonstration policy

These numbers are proposed for owner confirmation through this slice's review,
not a claim that confirmation has already occurred. They are typed immutable
`LivePlantPolicy` values under `tep-agent-lab.live-plant-demo-policy/v0`, with their
exact parameter checksum recorded in session provenance.

| Resource | Proposed value | Rationale |
|---|---:|---|
| Integration / record cadence | 1 simulated second / batch | Use the supported public single integration step; never increase it for delivery speed. S1 adds no wall delay. |
| Configured step count / event duration | 300 steps / 300 simulated seconds | Five simulated minutes show small clean-stream changes and finish quickly offline. |
| Accepted commits / associations | 301 each, including initial batch | One batch per step and one initial observation. |
| Retained samples | 16,000 | The pinned 53 bindings produce `301 * 53 = 15,953` samples; retain the entire trajectory. |
| Serialized sample bytes | 16 MiB | Worst-case reservation `15,953 * 1,024 = 16,336,896` bytes fits within 16,777,216. This is serialized sample size, not a process RSS limit. |
| Per sample / source batch | 1,024 bytes / 64 KiB | Attested opaque ids, finite float, quality, sequence and clocks fit the record bound; 53 reserved records fit the batch bound. No arbitrary metadata is emitted. |
| Pending assigned batches | 1 | Synchronous delivery blocks the producer until commit completes. |
| Indexed accepted records | 16,000 | One store identity entry and one indexed reference per accepted record, bounded by retention; no duplicate all-record index. Commit frontier has at most 301 entries; at most 53 binding indices. |
| Commit association size | 1,024 bytes | Step/tick/sequence/K and checksum are finite; preparation reserves the worst final association. |
| Lifecycle events / event size | 3 / 1,024 bytes | PREPARED, ACTIVE and terminal; terminal room is reserved before start. |
| Trusted diagnostic artifacts / artifact size | At most 2 / 1,024 bytes | A producer failure and a possible close failure; terminal events cite exact checksums, and a close diagnostic retains the preceding diagnostic ref. |
| Lab-owned simulator history / rollout artifacts | 0 / 0 | Use public step observations immediately; never call ReferenceWorld.advance/history or rollout for live delivery. Simulator calls are bounded by 300. |
| Retained live sessions per RunManager | 1 | Bound producers and retained stores together; STOPPED still occupies this slot. |

Preparation reserves the complete configured trajectory, including terminal metadata,
before admitting steps. The final all-prefix current read has only 301 eligible
points per binding, within the existing 50,000-point candidate limit; even a
store-wide slow last-event read has only 15,953 points. The full history fits the
1,000 returned-point limit and the 2,592,000-tick interval bound. Smaller host
reader limits fail preparation if they cannot serve the per-signal current
prefix, store-wide last-event scan, full returned history or full interval.
Resource metadata is bounded by the declared record/commit/event counts;
no infinite producer loop, lifecycle log, world history or rollout accumulation
exists. Dynamic capacity-stop is deliberately left to LOS-B-S3.

## Evidence and limitations

`tests/test_live_plant_session.py` uses authorized sanitized clean simulator data
and synthetic failures, without loading evaluator fixtures. Event barriers hold
delivery after a sample has validated but before batch publication, permitting
fresh snapshots and old T/K current/history reads while appending. No test uses a
wall-clock sleep to decide correctness. The tests cover independent multi-batch
delivery, changing values/increasing T/K, whole batches, identical historical
results, duplicate start rejection, no read-driven step, failed preparation,
reserved reader/storage bounds, the full 300-step retained history, immutable
manifest/inventory, frozen registration and the separate sealed P0 store.
The D0 diff remains a coordinator/verifier command outside the unit suite, so
tests need no remote Git refs and continue to work with shallow checkouts.

## Known limitations

- The per-manager live slot is released only on prepare failure. An unstarted
  PREPARED live session retains its TEPEnvironment until the run is closed.
  Disposal and new preparation after STOPPED/FAILED belong to LOS-B-S3 (#38).
- Live preparation builds the full P0 investigation assembly and closes it before
  creating the live physics environment. This initializes the simulator twice and
  leaves orphaned world/state/trace artifacts. A later slice should let assembly
  skip the investigation path in live mode.

Reads retain all committed prefixes for the lifetime of the in-process attachment.
The stored manifest/provenance alone do not recover its telemetry after process
loss. No rolling eviction, durable restart, alternate pacing, pause/resume,
interactive stop, backpressure retry, detailed failure/restart lifecycle or
disturbance timeline is implemented. LOS-B-S2 (#37) owns pacing and pause/resume;
LOS-B-S3 (#38) owns capacity-stop, retry, failure and restart; LOS-B-S4 (#39) owns
the scheduled timeline. New UI panels, Operational State features and investigation
handoff retain their existing owners. No upstream repository changes are required.

## Run the demo

From this repository, using the pinned dependency checkouts (PowerShell):

```powershell
$deps = 'C:/Users/chengting/projects/industrial-agent-playground/.worktrees/p0-deps'
$env:PYTHONPATH = "src;$deps/industrial-agent-runtime/src;$deps/tep-sim/src;$deps/tep-sim/vendor/tep-sim-upstream/src"
py -3.13 -m tep_agent_lab.live_plant_demo live
```

If `py` cannot resolve Python in the sandbox, use
`& C:/Users/chengting/AppData/Local/Programs/Python/Python313/python.exe -m tep_agent_lab.live_plant_demo live`.
Run the repository's pinned `scripts/check.py` command first to attest checkout
SHAs, package versions and NumPy. The demo records the actual lab HEAD, uses seed
17, writes only to an automatically cleaned temporary directory and leaves no log
files in the repository. Opaque identities and preparation time change between
runs; event ticks, assigned sequences and clean sample values reproduce.

The demo supervises commit milestones, then explicitly opens K=1/101/201/301
snapshots through ApplicationViewService. Default T is the frontier of that exact
prefix, so output does not depend on how quickly the printing thread is scheduled.
The producer never waits for a read acknowledgment. Representative values:

```text
source: clean deterministic simulated stream
T=  0 K=  1 reactor.temperature_measurement=120.40000000 reactor.pressure_measurement=2705.00000005 reactor_cooling.flow_actuator=41.10581288
T=100 K=101 reactor.temperature_measurement=120.39210504 reactor.pressure_measurement=2705.49125655 reactor_cooling.flow_actuator=40.84914357
T=200 K=201 reactor.temperature_measurement=120.38288151 reactor.pressure_measurement=2706.35731892 reactor_cooling.flow_actuator=41.43133955
T=300 K=301 reactor.temperature_measurement=120.41794747 reactor.pressure_measurement=2705.78338817 reactor_cooling.flow_actuator=42.10945982
session: STOPPED; owner=None; physics_closed=True
manifest unchanged: True
inventory unchanged: True
P0 status: READY; outcome=None
```
