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

S1 preparation kept the P0 assembly and immutable RunManifest contract. It closed
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

## OD-6 demonstration policy v0 (confirmed in PR #44)

The owner confirmed these S1 numbers in PR #44. They are typed immutable
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

## S1 limitations resolved by LOS-B-S3

- In S1, the per-manager live slot was released only on prepare failure. An unstarted
  PREPARED live session retains its TEPEnvironment until the run is closed.
  LOS-B-S3 (#38) below implements disposal and slot release after STOPPED/FAILED.
- S1 live preparation built the full P0 investigation assembly and closed it before
  creating the live physics environment. This initializes the simulator twice and
  left orphaned world/state/trace artifacts. LOS-B-S3 skips that investigation path.

Reads retain all committed prefixes for the lifetime of the in-process attachment.
The stored manifest/provenance alone do not recover its telemetry after process
loss. S1 implemented no rolling eviction, durable restart, alternate pacing,
pause/resume, interactive stop, backpressure retry, detailed failure/restart
lifecycle or disturbance timeline. LOS-B-S2 (#37) below adds pacing and lifecycle;
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

## LOS-B-S2 (issue #37)

This slice delivers pacing, trusted ownership and explicit pause/resume/stop under
Live Operational State v0 §Live plant session (OD-4), §Clock and pacing, LOS-B2
and the lifecycle part of LOS-B5. It leaves the S1 telemetry attachment and public
read assembly intact. The P0 and runtime status enums keep their existing meanings.

```text
[PREPARED] -- trusted start, return owner token --> [ACTIVE]
[ACTIVE] -- commit in-flight batch, acknowledge pause --> [PAUSED]
[PAUSED] -- same owner resume, reset wall anchor --> [ACTIVE]
[ACTIVE / PAUSED] -- resolve batch, close physics, release owner --> [STOPPED]
[ACTIVE] -- configured steps reached, close physics --> [STOPPED]
[retained committed telemetry] -- exact old T/K --> [unchanged public reads]
```

`RunManager.start_live()` returns an opaque stepping-owner token. Trusted
`pause_live`, `resume_live`, `stop_live` and the live handle require that token.
One producer alone calls the prepared advance callback; no external step operation
exists. A condition protects transitions and rejects overlapping lifecycle requests,
except that stop upgrades a pending pause. That pause caller receives the typed
`LivePlantSuperseded` error (wrapped as `LifecycleError` by RunManager); resume
while pause is pending remains rejected. Duplicate starts, wrong owners and illegal states
raise `LifecycleError` on RunManager or `LivePlantError` on the handle. No lifecycle
operation is added to RunQueries, ApplicationViewService, transport or Agent tools.
The token is an in-process trusted-host capability, not enterprise authentication.

Pacing waits at an empty batch boundary before advancing physics. Pause and stop
interrupt that wait and acknowledge without assigning or committing another batch.
If a step/delivery is already in flight, it resolves without retranslation before
acknowledgment. A timeout must be finite, non-negative and no greater than
`threading.TIMEOUT_MAX`, validated before arming a request. Every non-acknowledged
exit, including `BaseException`, withdraws its own request without cancelling a
replacement stop. A withdrawn pause resumes waiting for the same wall deadline;
it cannot accelerate delivery.
PAUSED retains ownership, simulation origin, source/clock/store identities and cursor;
the producer waits without stepping, translating or committing. Resume continues
the next fixed integration step from that cursor. Stop from ACTIVE or PAUSED closes
physics, drops environment callbacks and releases the stepping owner, while retaining
all committed reads in process. Trusted terminal reasons distinguish `operator_stop`
and `configured_steps_reached`; neither sets a P0 outcome or COMPLETED status.
`RunSession.close()` and its live resource release also stop ACTIVE/PAUSED sessions
with reason `host_close` and join the producer with a bounded 30-second wait. The
trusted close path needs no owner token, is idempotent after termination and retains
committed reads. It performs no disposal or session-slot release.

### Proposed pacing and lifecycle policy values

Pacing is selected in immutable `LivePlantPolicyV1.pacing` at preparation. Its version
`tep-agent-lab.live-plant-pacing/v0`, mode, resolved parameters and checksum are pinned
in session provenance before READY. These new concrete values require owner confirmation
in this slice's review; implementation does not assert that confirmation occurred.

| Mode / bound | Proposed value | Rationale |
|---|---:|---|
| DETERMINISTIC | No wall delay | Preserve the existing fast offline `live` demonstration. |
| REAL_TIME | 1 wall second / simulated second | Direct elapsed-time interpretation, with no simulation-to-UTC mapping. |
| ACCELERATED | Factor 20, hence 0.05 wall second / simulated second | The 300-step stream takes at least 15 wall seconds, enough to supervise lifecycle interactively. |
| Wall interval upper bound | 3,600 seconds / tick, also bounded by `threading.TIMEOUT_MAX` | At most one hour per delivery wait; prevents overflow in platform waits. Positive finite base parameters and a positive resolved paced interval are required. |
| v1 lifecycle event cap | 35 events, 1,024 bytes each | PREPARED + initial ACTIVE + 16 pause/resume pairs + terminal; bounded at 35 KiB of event payload. |

Production uses `time.monotonic` and an interruptible Event wait. Trusted composition
can inject a monotonic clock and sleeper; tests advance a fake clock without real
sleeps. The UTC acceptance clock remains separate, including if UTC reverses.
Simulation time alone assigns T; cadence remains one integration second and one source
batch per step for every mode. Pacing waits before simulator advance/translation.
After each actual commit it anchors the next interval at monotonic now: a late delivery
slows the stream instead of accumulating catch-up debt, bursting, dropping samples or
jumping T. Resume resets only that wall anchor, never the simulation origin.
Clock or sleeper failures in producer pacing record `pacing_error`, separately from
ingest/translation failures. No derived step anchor is stored.

`LivePlantPolicy` preserves `tep-agent-lab.live-plant-demo-policy/v0` exactly: cap 3,
deterministic delivery only, no pacing fields in its serialization or provenance,
and delivery provenance `synchronous-deterministic/v0`. Its default content checksum
from main `5e5bd64` is
`683ed7f5ae0131504097a1bceaed927e03fee83344d7a2e375b38a43b998ce15`.
`LivePlantPolicyV1` uses `tep-agent-lab.live-plant-demo-policy/v1`, cap 35 and explicit
pacing (including DETERMINISTIC); delivery provenance is `synchronous-paced/v1`.
Version/cap mismatches are rejected. All other OD-6 resource defaults stay the same. Every nonterminal event
reserves terminal capacity. Pause additionally reserves its matching resume; exhaustion
rejects that operation without changing state and still permits stop. This is lifecycle
admission only; dynamic telemetry capacity-stop belongs to S3. Each transition appends
to the existing durable trusted RunLog with last committed step and K.

### Evidence, demo and remaining scope

The focused suite replays the pinned simulator config/seed/cadence with identical
source/clock/store identities under all three modes. Entire assigned batches and commit
associations agree; wall delays differ. Event barriers prove pause/stop acknowledgment
after an in-flight commit or immediately during pre-step pacing, stop superseding
pending pause, timeout/exception withdrawal, host close, stable T/K/sequence during a fake 100-second
pause, continued simulation ticks after UTC reversal, slow-delivery reanchoring,
owner enforcement, terminal event reservation and durable lifecycle cursors. Old
snapshot current/history bytes agree during run, pause, resume and stop. Existing
S1 tests retain full 300-step reads and frozen manifest/inventory evidence.

Run the existing `live` command unchanged, or use the same dependency setup above:

```powershell
py -3.13 -m tep_agent_lab.live_plant_demo lifecycle
py -3.13 -m tep_agent_lab.live_plant_demo lifecycle --pacing REAL_TIME
```

`live` supports all three modes (default DETERMINISTIC/v0; paced modes select v1).
Each milestone wait uses the step gap times `seconds_per_tick` plus a 30-second
margin, so the 100-step REAL_TIME gaps have a 130-second timeout.
The lifecycle demo defaults to ACCELERATED and rejects DETERMINISTIC before
preparation (also stated in `--help`), opens two frozen reads after pause,
resumes until K grows by at least 10, then requests an operator stop. Its final T/K
may include an in-flight batch resolved by pause or stop, so scheduling can affect
which committed boundary appears; simulation values at an exact prefix reproduce.
Both commands use automatically cleaned temporary artifacts. Cleanup performs an
idempotent trusted close and always joins, preserving any original exception even
if cleanup itself fails; there is no owner-check/stop TOCTOU.

No capacity-stop, backpressure retry, new failure/restart policy, disposal or slot
release is implemented here; these belong to LOS-B-S3 (#38). STOPPED still occupies
the existing retained-session slot. The S1 fail-closed guard remains. The scheduled
disturbance timeline belongs to LOS-B-S4 (#39). Retention lasts only in process;
no durable telemetry recovery, rolling eviction, new UI or investigation handoff is
claimed. No upstream repository, specs, ADRs, AGENTS.md or D0 surfaces are changed.

## LOS-B-S3 (issue #38): limits, failure and read lifetime

This slice implements Live Operational State v0 §Backpressure, retention and resource
bounds (OD-6), §Fail closed and §Live plant session (Restart/Recovery). LOS-B3–LOS-B6
are exercised through canonical public reads, with S1/S2 covering LOS-B1/LOS-B2.
This delivers the mandatory P1.4B exit evidence **pending owner confirmation** of the
proposed v2 values and sustained envelope below. It makes no external-plant or Agent
capability claim. LOS-B-S4 and Operational State remain outside this change.

```text
[PREPARED] -- trusted start / one owner --> [ACTIVE]
[ACTIVE] -- resolve batch / pause --> [PAUSED]
[PAUSED] -- same owner / resume --> [ACTIVE]
[ACTIVE] -- configured end or pre-step capacity boundary --> [STOPPED]
[ACTIVE / PAUSED] -- resolved stop / host close --> [STOPPED]
[ACTIVE / PAUSED] -- source, translation, commit, clock or ownership failure --> [FAILED]
[STOPPED / FAILED] -- exact T/K reads --> [retained committed prefix]
[PREPARED] -- close or dispose / close environment --> [STOPPED + disposed]
[STOPPED / FAILED] -- explicit dispose / release store and slot --> [disposed]
[disposed / process loss] -- old snapshot read --> [SnapshotUnavailable]
[new explicit run preparation] -- new source, clock and store --> [new PREPARED]
```

`LivePlantPolicyV2` pins all finite limits, retry and read lifetime in preparation
provenance under `tep-agent-lab.live-plant-demo-policy/v2`. Its default deterministic
parameter checksum is `4ace02165be9f994ee8ee9be29eef3e0194098c14f05d2281374ce5d1473ce1f`;
the sustained demo explicitly selects ACCELERATED pacing and therefore records its
own different exact policy checksum. Confirmed v0 values, serialized fields and
checksum `683ed7f5ae0131504097a1bceaed927e03fee83344d7a2e375b38a43b998ce15`
are unchanged. v1 pacing/lifecycle semantics remain unchanged.

Before each integration step, v2 reserves one complete worst-case batch against
sample, serialized byte, index, commit and commit-association metadata caps. The
check uses retained actual sample bytes plus `53 * sample_byte_cap` for the next
batch, not an optimistic estimate of its values. A count cap reserves all 53
bindings. Capacity exhaustion ends STOPPED before calling the simulator, with a
distinct trusted `capacity_samples`, `capacity_bytes`, `capacity_commits`,
`capacity_index`, `capacity_commit_metadata` or `capacity_events` reason. Public
capacity reason is `capacity_reached`. No eviction or rollback modifies the
committed prefix. Every nonterminal event retains terminal room; the v2 producer
stops when only that room remains. Disposal has its separate pinned single host
event, so it does not consume or reopen terminal capacity.

Record/frame and association sizes are validated before acceptance. Preparation
rejects an initial batch/layout or reader envelope that cannot fit before creating
the simulator. The single synchronous pending batch is a structural bound: no
next step is admitted until it resolves. History/artifact bounds are also structural
zero bounds: no ReferenceWorld, sandbox, RCA state, trace or rollout is assembled
for live runs. `LiveRunSession` holds only the attested graph, registry, immutable
manifest Task metadata and live handle. One environment is initialized. Existing
P0 assembly, sealed telemetry and Coordinator behavior are unchanged; the manifest
schema/storage fields retain their meanings, while uncreated investigation owners
report unavailable. No investigation authority or future handoff is introduced.

### Proposed final caps and recovery policy

The inherited numbers below retain the owner-confirmed v0 defaults; v2 adds
explicit commit metadata, diagnostic, disposal and retry/lifetime parameters.
These additions and the demonstration envelope require owner confirmation.

| Resource | v2 value | Rationale |
|---|---:|---|
| Cadence / configured duration | 1 second / 300 steps | Supported public integration grid, five simulated minutes; pacing is never physics input. |
| Pending batches | 1 | Synchronous commit/refusal retry blocks further stepping. |
| Retained samples / index records | 16,000 / 16,000 | Entire `301 * 53 = 15,953` sample prefix, including initial batch. Fixed store indices and opaque pinned ids; no unbounded metadata. |
| Retained serialized sample bytes | 16 MiB | Worst-case full prefix `15,953 * 1,024 = 16,336,896` fits; actual byte accounting uses accepted SignalSample JSON, including trusted ingest time. This is not RSS. |
| Per sample / batch frame | 1,024 bytes / 64 KiB | Finite floats and fixed source layout; `53 * 1,024 = 54,272` fits the frame. |
| Commits / association record | 301 / 1,024 bytes | One immutable association per batch; validated before commit. |
| Commit metadata bytes (new) | 308,224 | `301 * 1,024`; reserves complete bounded association metadata, apart from sample payload. |
| Producer session events / record | 35 / 1,024 bytes | Retains v1's cap, including terminal room; covers 16 pause/resume pairs. |
| Trusted diagnostics (explicit in v2) | 2 artifacts, each <= 1,024 bytes | Producer failure plus possible close failure; checksum chain retains both. Type and first 48 detail characters, with truncation flag, are trusted-only. |
| Host disposal event (new) | 1, bounded by 1,024 bytes | Idempotent terminal disposal outside the producer's 35-event budget. |
| Lab-owned simulator history / rollout artifacts | 0 / 0 | Consume public step observations directly; live prepare now skips the entire investigation assembly. |
| Retained live attachments / RunManager | 1 | STOPPED and FAILED keep the slot until disposal; PREPARED close also disposes. |
| Retry count / wait (new) | 3 retries after first attempt / 0.1 seconds each | Four total attempts, at most 0.3 seconds of requested retry waiting; enough for transient refusal while keeping recovery short and finite. |
| Read lifetime (new explicit field) | Until explicit disposal or process loss | Retain exact snapshots in process; no expiry, rolling eviction, durable telemetry or checkpoint/resume. |

Only `IngestBackpressure` means a typed retryable **atomic refusal**, with no
accepted batch or unknown outcome. The current in-memory adapter does not raise
it naturally; test-only refusing adapters exercise the contract. Retry retains the
identical translated tuple/checksum/identities, never invokes translation again and
never steps while pending. The injectable monotonic clock/sleeper enforce the pinned
wait; lifecycle wake signals cannot shorten it. A successful late commit reanchors
S2 pacing rather than catching up. Ordinary errors and unknown commit outcomes are
not retried. A synchronous adapter must return or refuse; preempting a hung external
adapter is not implemented and is not claimed by the retry wait budget.

Identical committed sample identities resolve idempotently without advancing K or
retention again; different content rejects the entire batch atomically. Exhausted
retry, simulator/shutdown, binding/field/grid/value/identity/clock errors and lost
ownership stop stepping and end FAILED. Public reason is always
`live_delivery_failed`. Trusted append-only history retains last successful step/K,
pending checksum and exact diagnostic refs. Detail, private-world data and injection
metadata never enter public queries, application reads or transport. Failures do not
set P0 COMPLETED/RunOutcome or alter runtime TaskStatus.

### Disposal, restart and process loss

`RunManager.dispose_live` accepts STOPPED/FAILED and an unstarted PREPARED session.
It closes an unstarted environment, appends one durable DISPOSED host record,
invalidates the store incarnation and releases sample/identity/index/frontier
storage and the manager slot. ACTIVE/PAUSED disposal is rejected: first resolve a
trusted stop. `close_live`, the session close path and `close_prepared` also dispose
an unstarted PREPARED session, fixing its environment/slot leak. Closing an already
started run retains S2 host-close semantics and retained reads until explicit
disposal. Concurrent/repeated disposal is idempotent.

Disposal leaves manifests, attested provenance and failed/negative lifecycle history
durable. Cached query/store/reader handles also fail `SnapshotUnavailable`; transport
returns `SNAPSHOT_UNAVAILABLE`, never empty history. Slot admission remains typed
`LifecycleError` while any undisposed live attachment holds it. A new preparation
after disposal succeeds with a new run id and opaque session/source/clock origin/id/
store incarnation/namespace. No same-run READY re-preparation or automatic latest
resolution exists. Another RunManager over the same durable root reads the exact
manifest/provenance/history but has no live telemetry and reports unavailable.
Process loss does not claim a durable terminal event if the process could not write
one; that incarnation cannot recover unknown simulator/commit outcomes.

### Sustained evidence and CLI demonstrations

Measured on 2026-10-12 using the pinned simulation source, seed 17, v2 and
ACCELERATED pacing (20x, 0.05 wall seconds per simulated second). All 53 complete
301-point histories were read through ApplicationViewService after termination;
initial T/K reads remained identical. Windows peak working set is obtained using
standard-library `ctypes` and the native process counters, including preparation
and public read assembly. Other supported systems use standard-library `resource`;
if unavailable the CLI explicitly reports a payload proxy, not an RSS claim.

| Measured / proposed envelope | Result / rationale |
|---|---|
| Simulated duration | 300 seconds, final T=300 / K=301 |
| Measured wall duration | 22.614 seconds (producer plus complete public history verification); pacing minimum 15 seconds, physics/serialization/scheduling adds elapsed time |
| Peak retained samples | 15,953, exactly matching all public history samples |
| Peak retained serialized bytes | 6,147,205 bytes; under 16 MiB cap |
| Process peak RSS | 49,487,872 bytes (about 47.2 MiB), measured after live preparation skips P0 assembly |
| Proposed process envelope | 256 MiB (268,435,456 bytes); >5x measured peak for interpreter/platform overhead. An exit-demonstration acceptance envelope, not a portable enforced OS memory limit. CLI returns failure if measured RSS exceeds it. |

This is a **clean deterministic simulated stream** qualification only. There is no
external connector robustness claim, durable telemetry recovery, rolling retention,
timeline, Operational State algorithm, new UI or investigation handoff. Actual wall
duration/RSS vary across runs and hosts; exact event values/identities at pinned T/K
and the finite payload limits do not depend on that variation.

Using the same pinned dependency setup as the existing demo:

```powershell
py -3.13 -m tep_agent_lab.live_plant_demo capacity
py -3.13 -m tep_agent_lab.live_plant_demo failure
py -3.13 -m tep_agent_lab.live_plant_demo restart
py -3.13 -m tep_agent_lab.live_plant_demo sustained
```

`capacity` pins commit_cap=11: STOPPED / capacity_reached at T=10/K=11, with the
prefix readable. `failure` explicitly labels its isolated CLI-only synthetic failing
callback DEMO-ONLY, reaches FAILED / live_delivery_failed at T=0/K=1 and prints no
trusted diagnostic. Production RunManager never installs that fault. This is not
a plant incident/disturbance injection or scheduled timeline. `restart` completes,
disposes, prepares a different run, proves new identities and old SnapshotUnavailable.
Every command cleans its temporary artifacts and joins its producer. Existing
`live` and `lifecycle` commands retain their output and pacing options.

Focused evidence is in `test_live_plant_limits.py` and `test_live_plant_session.py`:
each storage/metadata/event cap, zero history/artifacts, frame admission, retained
old snapshot bytes, retry translation counts and barriers, atomic conflict/idempotency,
all requested failures, diagnostics isolation, slot release, new identities and
process-loss lifetime. Tests use fake clocks/events, never real-time correctness
sleeps, and close/join started sessions in cleanup. The canonical manifest/inventory
and P0 outcome/status boundaries are checked across capacity and failure; the D0
diff gate remains a verifier command, not a test or source of test data.
