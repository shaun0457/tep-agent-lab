# Plant Context + Telemetry Contract v0

Status: frozen P1.0 architecture; conceptual records/operations, not implemented.

Owner: application/domain infrastructure, initially `tep-agent-lab`.

Decision: [ADR-003](../decisions/ADR-003-industrial-environment-boundary.md).

Reviewed base: `83a96c19675eed03831f7106834904b667959fbd`.

## Scope and ownership

```text
[Industrial Environment] -- semantics --> [Plant Context Plane]
[Industrial Environment] -- data --> [TelemetrySource]
[TelemetrySource] -- samples/batches --> [TelemetryIngestor]
[TelemetryIngestor] -- immutable append --> [TimeSeriesStore]
[TimeSeriesStore] -- pinned read view --> [TimeSeriesReader]
[Plant Context Plane] -- scoped descriptors --> [Application / Agent read assembly]
[TimeSeriesReader] -- bounded results --> [Application / Agent read assembly]
[Application read assembly] -- Application API --> [UI]
[Agent read assembly] -- gated ToolResult --> [Agent]
```

Static context and dynamic telemetry are related through identifiers and immutable
context revisions, never one giant payload. MUST/MUST NOT express requirements
for subsequent implementation, not claims about today's code.

| Concern | Owner |
|---|---|
| Plant semantics, topology, bindings, units, reviewed references | Domain canonical sources; TEP uses existing ProcessGraph/registries |
| Source normalization, acceptance, deduplication, timing validation | Application/domain TelemetryIngestor with source adapter |
| Accepted immutable samples and commit revisions | Application/domain TimeSeriesStore |
| Bounded snapshot reads, ordering, reduction metadata | Application/domain TimeSeriesReader |
| Run lifecycle, source attestation, scoped projections/provenance | Existing P0 RunManager / CanonicalContextRegistry / RunQueries |
| Application view assembly and transport | ApplicationViewService and replaceable application adapters |
| Agent tools, lab visibility/request policy, observation ingestion | Existing lab Tool Surface/state contracts |
| Tasks, tools, budgets, state, gates, traces | Unchanged industrial-agent-runtime |
| Simulated physics, environment and branch lifecycle | Existing tep-sim / ReferenceWorld / SimulationSandbox owners |

Storage is data infrastructure, not a second process-world or investigation state
owner. Application API != Agent Tool Surface; a read model or UI never grants
SIMULATE/MUTATE authority. Source write capability metadata grants no authority.

## Canonical Plant Context

Context is an immutable versioned inventory identified by `context_ref` (schema
version, immutable content reference/checksum). IDs are opaque stable identifiers
within that inventory's plant namespace. Changes publish a new revision; historical
reads retain the old ref. Samples do not contain whole descriptors or documents.
An ingestor binds each source stream to an immutable descriptor/context revision;
the accepted-record provenance and read result retain that ref. A unit/meaning
change requires a new signal identity; revision changes must not reinterpret old
values. Source binding changes require versioned bindings and stream registration.

The following is the minimal logical schema; wire encodings and Python types are
deferred. Optional display labels do not establish identity.

| Concept | Required semantic fields |
|---|---|
| `PlantEntity` | `entity_id`, extensible namespaced `kind`, `provenance` |
| `SignalDescriptor` | `signal_id`, `source_binding`, `entity_refs`, `quantity`, `unit`, `value_kind`, `capability`, `provenance` |
| `Relationship` | `relationship_id`, `from_entity_id`, `to_entity_id`, extensible namespaced `kind`, `provenance` |
| `DocumentRef` | `document_id`, immutable `content_ref`/checksum, `kind`, `provenance` |
| `KnowledgeRef` | `knowledge_id`, immutable `content_ref`/checksum, `kind`, `provenance` |

`provenance` identifies the owning source and revision/content ref, extraction or
mapping method when applicable, validation status (`CANDIDATE` or `VERIFIED`), and
validation evidence/ref when verified. Optional confidence is a source-defined
assessment, not proof. Visibility accompanies all records/refs using owning source
and P0 projection policy. Verified means checked against stated validation evidence;
it does not mean Agent-visible or authorized for execution. Source-native review
statuses remain intact in provenance; do not relabel pending review as verified.

`PlantEntity.kind` is not a TEP enum or closed reactor/stream/valve list. Namespaced
kinds and relationship meanings can describe process plants, CNC/discrete
manufacturing, robotics, HVAC, and other domains. Relationships express topology
or associations, not inferred dynamics; directed connectivity does not prove flow.

`source_binding` identifies `source_id` and an adapter-native locator/ref; the generic
reader treats the locator as opaque. `entity_refs` identifies associated entities
and association roles, allowing node- and edge-associated signals. `quantity` is
an extensible semantic identifier. `unit` is an explicit unit identifier or explicit
unitless/unknown marker, never an implicit assumption. `value_kind` declares a
scalar numeric, boolean, or string value; unknown/missing values may be null under
explicit quality/status. `capability` declares READ, WRITE, or READ_WRITE source
capability; v0 telemetry provides reads only. XMEAS/XMV are not required.

Candidate entities/bindings may be retained for review, but MUST remain labeled and
separate from verified operational mapping. An unverified binding MUST NOT silently
drive a trusted operational read. Unknown/unverified mapping fails closed; an
explicit candidate-review view may expose it with status and provenance. Document
and knowledge refs do not auto-fetch bodies into prompts and do not replace Rule
Registry `origin × validation × authority` semantics.

### Context Builder boundary

```text
[P&ID / tag list / OPC UA model / SOP / manuals]
  -- source material --> [extraction / candidate mapping]
  -- provenance + confidence --> [human / deterministic validation]
  -- verified versioned records --> [Canonical Plant Context]
```

Builder extraction, VLM, OCR, discovery and mapping implementation are out of P1.0.
Model extraction cannot directly become trusted truth. For TEP, normalized
ProcessGraph and source registries remain canonical; these concepts are projections
of existing semantics, not a second hand-maintained graph or signal map.

## Sources and immutable samples

`TelemetrySource` produces canonicalizable samples or finite batches. Adapter-owned
source registration specifies the source instance, native clock, signal bindings,
sequencing policy, and provenance. Planned adapters are TEPSimulationSource (first
in P1.1), OPCUASource, HistorianReplaySource, and MQTTSource. None is implemented
in P1.0 and no network protocol is specified here.

```text
SignalSample (immutable after acceptance)
  signal_id
  source_id
  event_time: TimePoint
  ingest_time: TimePoint
  sequence: nonnegative integer
  value: descriptor-compatible scalar or explicit null
  quality: GOOD | UNCERTAIN | BAD
  source_status_code? / source_metadata_ref?
```

The ingestor assigns `ingest_time`; a source cannot backdate acceptance. Event time
is adapter-normalized source event time. Source metadata refs must identify immutable
content and respect visibility; they may retain native timestamps, source sequence,
status detail, or original value/unit. `GOOD`, `UNCERTAIN`, and `BAD` are canonical
quality classes, not a complete representation of OPC UA or historian status. The
adapter documents its mapping and preserves detail where needed. Quality is never
silently upgraded; bad/uncertain samples remain representable and queryable.

Units belong to SignalDescriptor. If source fidelity requires retaining a native
unit, preserve it in source metadata; it cannot override canonical semantics.
Any unit conversion must be declared/versioned with provenance and preserve the
original as needed. Missing delivery creates no invented GOOD sample; an explicit
missing observation may use null with BAD/UNCERTAIN and source status.

## Four time concepts and clock representation

| Concept | Meaning and use |
|---|---|
| `simulation_time` | Source-native model coordinate, e.g. TEP hours/seconds; not automatically a timestamp |
| `event_time` | When an event occurred in its declared source clock domain; primary analysis axis |
| `ingest_time` | When ingestion accepted the sample, in the ingestion clock; acceptance-time diagnostics |
| `wall_clock_time` | Real runtime/pacing time used for delivery and process supervision |

Simulation time may supply event time on a SIMULATION clock. It is still a distinct
concept from a UTC event timestamp. Ingest time may be measured using real UTC,
but does not stand in for event time. Wall-clock pacing is not simulated physics
and its adjustments do not change simulation integration steps.

```text
TimePoint
  clock_id
  ticks: integer

ClockDescriptor (immutable versioned reference)
  clock_id
  kind: SIMULATION | UTC | SOURCE_DEFINED
  resolution: positive rational seconds per tick, or declared native tick unit
  origin: explicit epoch/model origin where applicable
  mapping_ref?: immutable explicit cross-clock mapping
```

Each clock fixes origin and resolution before use; UTC uses an explicit epoch and
UTC/leap-second convention. SOURCE_DEFINED describes ordering and native units,
and is not presumed synchronized with another clock. Adapters preserve native
floating model times while documenting deterministic conversion/rounding to ticks.
Tick collisions do not collapse samples: sequence remains independent identity.
Restart/reset creates a new clock identity when its coordinate meaning changes.

Comparisons and intervals require the same clock_id. Cross-clock queries are
rejected unless an explicit immutable mapping is pinned, including offset/scale,
valid range and uncertainty where applicable. Uncertain mappings cannot justify
including possibly future data; fail closed at ambiguous horizon boundaries.
`simulation t=600 s` cannot silently become `2026-01-01T00:10:00Z`.

UTC clock skew or reversal does not rewrite event times or committed ordering.
Ingest wall timestamps are diagnostic; an authoritative store commit sequence
establishes knowledge ordering even when wall clocks move backward. Any mapping
correction publishes a new version, never retroactively changes old snapshots.

## Identity, deduplication, and acceptance ordering

Sample identity is `(source_id, signal_id, sequence)`, not event time. `source_id`
identifies a registered stream incarnation, not just a reusable connection name.
Native sequence reset/reuse requires a new incarnation. Sequence is strictly
ordered per source/signal identity assignment; gaps are allowed. Distinct sequence
values at the same event time remain distinct samples. Ingest arrival need not
follow sequence or event order.

- Duplicate delivery of identical source content under one identity is idempotent:
  return the existing accepted identity/commit; do not assign another ingest_time
  or advance the store revision. Equality excludes the newly proposed ingest_time
  and compares canonical source fields plus fidelity metadata.
- Different content under the same identity is a conflict: reject/quarantine it
  with diagnostic provenance; never overwrite the accepted record. Corrections
  must be new explicitly related samples, not in-place edits.
- Without native sequence, the adapter assigns a durable sequence at first source
  observation and retains the delivery-to-identity association for retries/replay.
  It cannot infer identity from timestamp/value equality. If no durable delivery
  identity exists, each received observation is distinct; report that duplicates
  cannot reliably be detected. Deterministic replay must retain assigned identities.
- Late or out-of-order arrivals append normally with their original event time,
  identity and new acceptance commit. Older snapshots remain unchanged. A source
  disconnect does not reset identity, infer freshness, or fill gaps.

`sequence` is source identity order. `ingest_sequence` is a store-wide monotonic
commit revision, assigned by the store/ingestor acceptance boundary. They are not
interchangeable. Concurrent ingestion must serialize commit assignment; deterministic
replay fixes input/batch order. Accepted-record envelopes retain sample identity,
context/clock refs and commit revision without enlarging the source sample schema.

## Append store and read snapshots

TimeSeriesStore conceptually supports `append(sample)` and `append_batch(samples)`.
Validation completes before visibility. A batch is atomic: new records share one
commit revision and become visible together; failure publishes none. Exact duplicate
records are no-ops, identity conflicts fail the batch, and an all-duplicate batch
does not increment revision. Store metadata freezes its identity, source/clock and
context registrations. No SQL abstraction or database technology is selected.
P1.1 may use a deterministic in-memory implementation.

```text
TelemetryReadSnapshot (immutable reference)
  store_id / incarnation
  ingest_sequence: committed revision K
  visible_event_watermark: TimePoint T, per requested clock/domain
  context_ref / clock_refs / mapping_ref? / visibility_policy_ref
```

The event watermark is the consumer's authorized inclusive event horizon, not a
claim that all events before it have arrived. K is the committed ingestion prefix.
Snapshot creation freezes both together; a ref must resolve exact immutable
contents. Multiple clock domains need separate horizons, not one unqualified
number. Scope and source/run/branch namespace accompany the snapshot; a reader
must also enforce current caller authorization. A ref is never a visibility bypass.

K includes only records whose append commit is at or before K. Ingest timestamps
alone cannot establish knowledge cutoff. Retention must preserve snapshots for the
declared investigation lifetime or explicitly return snapshot unavailable; never
replace a missing historical snapshot with today's contents. In-memory P1.1 need
not promise durable restart recovery and must report loss of its incarnation.
Telemetry snapshots are distinct from simulator branch snapshots and RcaState
revisions; none substitutes for another.

## Bounded deterministic reads

Conceptual operations (final API/tool names are deferred):

```text
current(signal_id, as_of=TelemetryReadSnapshot)
history(signal_id, event_start, event_end, max_points,
        knowledge_cutoff=TelemetryReadSnapshot)
```

Each request pins one snapshot, a permitted signal/source set, and an explicit
clock. A signal with multiple eligible sources requires explicit source selection
or a pinned declared resolution policy; no implicit source switching. History
requires a finite inclusive `[event_start, event_end]`, positive integer max_points,
and service-policy limits on interval size and returned points. The reader rejects
inverted/nonfinite/incompatible bounds and event_end beyond T. No unbounded stream,
latest-final-store default, or hidden future data is permitted.

The eligible set is exactly authorized records for the requested binding with
`event_start <= event_time <= event_end <= T` and `commit_revision <= K`. Apply
visibility and both cutoffs **before** sorting, reduction or aggregate computation;
hidden records cannot affect returned counts, summaries, or metadata. Unknown
bindings, ambiguous clocks, invalid snapshots and unauthorized access fail closed.
Missing retained coverage is explicit unavailable/partial coverage, never silently
presented as complete; an empty valid interval is an empty result.

Read order is ascending `(event_time.ticks, source_id, sequence)` within the selected
clock. Current returns the last eligible sample at or before T using that order,
or explicit no-data. Its selected source scope and context must be declared.
GOOD/UNCERTAIN/BAD all participate; do not silently return an older GOOD value as
current. No interpolation, gap fill, or freshness guarantee is implicit.

For n eligible history points and limit m, return all if n <= m. Otherwise select
indices `floor(i * (n - 1) / (m - 1))` for i in `0..m-1`; m=1 selects the last point.
This preserves endpoints for m>=2 and is applied only after cutoff filtering.
Results disclose eligible/returned counts, reduction policy, coverage and quality,
snapshot_ref, requested bounds, binding/context/clock refs and sample identities.
Computational/indexing strategy is deferred; implementations must enforce resource
bounds and return an explicit resource-limit failure rather than scan without limit.

### Hard temporal leakage invariant

> A consumer operating at event time T and ingestion snapshot K must never receive
> a sample that was not visible under that event-time / knowledge cutoff.

At event horizon 10 minutes and K=812, an 8-minute sample accepted at revision 900
is excluded even though its event time is earlier. A 15-minute sample is excluded
regardless of its commit revision. Repeating the same authorized query/snapshot
must yield the same result after late arrival. No query reduction, current shortcut,
metadata, or derived feature can inspect excluded values. Temporal cutoff is
additional to benchmark ground-truth/visibility isolation, not a replacement.

Every eventual telemetry-dependent Agent query and derived artifact/tool result
must be traceable to snapshot_ref and descriptor/context refs. A turn pins the
snapshot before reads; any intentional later snapshot is explicit and separately
traced. Existing successful ToolResult -> ObservationRecord ingestion and explicit
ObservationRecord -> EvidenceLink rules remain intact. P1.0 implements no snapshots.

## Delivery and dirty streams

| Mode | Pacing |
|---|---|
| DETERMINISTIC | No wall-clock sleeping; tests, benchmark, deterministic replay |
| ACCELERATED | Configured simulation/event progression faster than wall time; demos/UI/development |
| REAL_TIME | Configured source/event pacing tracks wall time; integration tests |

For the same deterministic simulated source and configuration, pacing may change
delivery latency and ingest wall timestamps, but MUST NOT change physics, sample
values, identities, event times or event ordering. Replay commits/batches are fixed
for deterministic snapshots; equivalence across pacing is asserted at the same
accepted prefix, not at equal wall-clock instants. Wall-clock supervision must not
change simulator integration steps to catch up; overdue delivery is handled by the
adapter policy. No guarantee of real-time deadlines is implied.

Future dirty-stream replay must support missing samples, duplicate delivery, late
and out-of-order arrival, BAD/UNCERTAIN quality, clock skew and temporary disconnect.
Injection affects delivery/quality/timing with traceable provenance; it does not
silently alter process physics or represent a process fault. Intentional corruption
is a separate configured replay input; it cannot be attributed to pacing changes.
Tests must cover equal event times, identity conflict/reset, K/T cutoffs, no-data,
and clock ambiguity as well as the listed dirty-stream conditions. None is
implemented in P1.0.

## Application, Agent, transport, and TEP boundaries

Agent does not consume a raw stream: OPC UA -> LLM, MQTT -> prompt and Kafka ->
ContextProjection directly are forbidden. Typed bounded operations such as
get_current/get_history wrap the reader under existing lab policy and runtime
gates. Generic contracts own read semantics, not final Agent tool names. Existing
TEP tools may keep their names as implementation details.

Application/UI uses the same underlying read semantics through Application API
and P0 projections. It cannot query private ReferenceWorld state, drive source
physics, or execute Agent actions through a read endpoint. Application views remain
derived, not canonical stores or authority paths.

**P1.0 does not require a REST API.** Telemetry network transport is deferred until
components cross a process/machine boundary; existing desktop transport is untouched.
Future OPC UA subscription, MQTT, gRPC, REST and historian API adapters normalize or
expose these same contracts. FastAPI is not part of domain architecture. No InfluxDB,
TimescaleDB, DuckDB, SQLite, Kafka, general SQL abstraction or other storage backend
is selected. P1.0 introduces no transport/API implementation.

`TEP adapter -> generic telemetry contract`; `generic telemetry contract !-> TEP`.
TEPSimulationSource can translate sanitized TEP observations to samples and bind
simulation time explicitly. SignalSample does not require XMEAS/XMV; TelemetrySource
does not require IDV; TimeSeriesStore does not know TEP ControlMode. Reference and
counterfactual streams use distinct registered scope/identities and visibility; they
must never be silently merged into one current value or benchmark view.

Do not extract a grand IndustrialEnvironmentAdapter yet. After TEP plus one
non-TEP source/domain exist, compare actual requirements and extract only proven
common abstractions. These conceptual records do not establish a fourth core repo.

## Existing architecture consistency and migration obligations

Review at the stated base:

| Existing owner/evidence | Consistency requirement |
|---|---|
| [P0 ownership and canonical context](playground-backend-v0.md), [P0 handoff](../p0-handoff.md) | Preserve run lifecycle, READY-frozen source inventory, immutable refs and AGENT/EVALUATOR filtering; snapshot refs bind that inventory, not model-authored replacements |
| [ADR-002](../decisions/ADR-002-ui-application-boundary.md), [ApplicationViewService](../e0-2-application-views.md) | Application API != Agent Tool Surface; keep ApplicationViewService on public P0/application queries |
| `src/tep_agent_lab/playground_views.py: RunQueries.process_graph` | Existing node/edge bindings and provenance remain source-owned; edge direction is topology, not pipe-flow physics; evaluator overlays remain hidden |
| `src/tep_agent_lab/tep_world.py: ReferenceWorld.__init__/advance/history` | Initial sanitized observation plus sanitized rollout records, skipping the repeated initial rollout point; harness owns advancing reference physics |
| `src/tep_agent_lab/playground_views.py: RunQueries.telemetry` | Current code reads live sanitized history, filters variables, and returns recent records (hard max 1000); dense data stays artifact-backed |
| `src/tep_agent_lab/tool_surface.py: _history` | Existing Agent history is a bounded TEP window with artifacts under lab tool policy; successful results keep ObservationRecord ingestion |

Today's recent live views have no immutable K, no archived arbitrary interval,
and no cross-view atomic snapshot. They do not already satisfy this new telemetry
contract. P1.1 must explicitly introduce ingestion/reader snapshots alongside the
existing owners, without claiming ReferenceWorld.revision() is an ingestion cutoff
or reusing evaluator-hidden salted state hashes as visible store revisions.
P1.2 must evolve the owning public P0/application query boundary to expose bounded
reader results; it must not route ApplicationViewService directly to private world
state. No existing contract, fixture or benchmark behavior changes in P1.0. Any
implementation contradiction with an owning spec must emit SPEC_CONFLICT and be
resolved explicitly rather than introducing a bypass.

## Frozen implementation sequence and acceptance

| Milestone | Authorized future scope |
|---|---|
| P1.1 | TEPSimulationSource -> canonical SignalSample -> deterministic InMemoryTimeSeriesStore -> bounded reader with explicit clocks, identity and snapshot cutoffs |
| P1.2 | ApplicationView live current/history integration through public P0/application reads |
| P1.3 | Dirty-stream replay/fault injection; late/duplicate/missing/quality and temporal-leakage tests |
| P1.4 | First external connector: OPC UA or open historian source |

P1.0 freezes plant semantic ownership, ingestion/storage/reader ownership, immutable
sample shape, four clocks, late/out-of-order identity, historical K/T reconstruction,
temporal leakage isolation, deferred REST, TEP adapter direction, unchanged runtime,
and P1.1 scope. It implements none of the sequence and touches no production code,
fixture JSON, D0 benchmark specs, roadmap, runtime, simulator, desktop or CI.

Architecture review outcome: `SPEC_CONFLICT: none`. The migration gaps above are
explicit future obligations, not silent changes to current production guarantees.
