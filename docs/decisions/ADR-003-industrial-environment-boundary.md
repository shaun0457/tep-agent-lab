# ADR-003 — Freeze the industrial environment and telemetry boundary

- Status: Accepted (architecture contract only; not implemented)
- Date: 2026-10-07
- Scope: P1.0
- Normative contract: [Plant Context + Telemetry v0](../specs/plant-telemetry-contract-v0.md)
- Base reviewed: `83a96c19675eed03831f7106834904b667959fbd`

## Context

The first product-facing environment must support real and simulated industrial
data without making TEP physics, identifiers, or controls generic infrastructure.
Static plant semantics and dynamic samples have different lifecycles, provenance,
and query requirements. Combining them into a raw stream or giant prompt payload
would obscure ownership and permit historical investigations to see future data.

P0 already owns application run lifecycle, canonical source resolution, visibility,
and derived projections. ProcessGraph and its source registries own current plant
semantics. The existing telemetry path is sanitized `ReferenceWorld.history()`
through P0, not an archival time-series store or immutable ingestion snapshot.
[ADR-002](ADR-002-ui-application-boundary.md) separates application reads from UI
and preserves the application/Agent authority boundary.

## Decision

```text
[Industrial Environment]
   |-- versioned semantics --> [Plant Context Plane]
   |                            assets / topology / signals / references
   |-- canonicalizable data -> [TelemetrySource]
                                |-- accepted samples --> [TelemetryIngestor]
                                |                         |
                                |            immutable append commits
                                |                         v
                                |                  [TimeSeriesStore]
                                |                         |
                                |                  snapshot-bound reads
                                |                         v
[Plant Context Plane] -- scoped context --> [TimeSeriesReader + consumers]
                                             |-- application views --> [Application API / UI]
                                             |-- gated typed reads --> [Agent Tool Surface]
```

The Industrial Environment is a conceptual boundary, not a new universal adapter
class. Plant context contains `PlantEntity`, `SignalDescriptor`, `Relationship`,
`DocumentRef`, and `KnowledgeRef`. Telemetry contains immutable `SignalSample`
records. The planes join through versioned signal/entity identities; neither embeds
the full other plane. Candidate mappings remain distinct from verified mappings.

Application/domain infrastructure in `tep-agent-lab` initially owns ingestion,
storage, and reader contracts. TEP's ProcessGraph remains its semantic source of
truth; canonical plant views project it rather than replacing it. `tep-sim` owns
physics. P0 retains run/provenance/visibility/projection ownership. No new runtime
ownership is introduced.

`TelemetrySource != TimeSeriesStore != TimeSeriesReader`. A source produces data;
an ingestor validates and commits it; an append-oriented store retains it; a reader
applies deterministic bounds and visibility. A query API grants no execution
authority. Agent tools remain subject to existing ToolSpec, runtime gates, lab
validation, verification, and ObservationRecord ingestion. Application API !=
Agent Tool Surface; UI reads cannot advance or mutate a process world.

## Time and historical knowledge

Simulation model time, event time, ingestion acceptance time, and wall-clock pacing
time are separate. Explicit `TimePoint(clock_id, ticks)` and `ClockDescriptor`
contracts prohibit treating simulation seconds as UTC without a declared mapping.

Every telemetry read pins an immutable store snapshot and event horizon. Inclusion
requires both event-time eligibility and prior committed ingestion. A sample at
8 minutes committed at snapshot 900 is invisible to a turn at event horizon
10 minutes and snapshot 812. A sample at 15 minutes is also invisible to that turn.
Late data never rewrites an earlier view. The contract specifies identities,
deduplication, total read order, and bounded selection independently of timestamps.

Delivery modes are DETERMINISTIC, ACCELERATED, and REAL_TIME. Pacing changes must
not change deterministic simulated physics, values, identities, or event ordering.
Missing, duplicate, late, out-of-order, quality, clock-skew, and disconnect behavior
belongs to the telemetry pipeline, separate from process faults.

## API, adapters, and repository boundaries

**P1.0 does not require a REST API.** Network transport is deferred until components
cross a process/machine boundary. Existing desktop IPC remains an application
adapter; it does not require a new telemetry network service. OPC UA subscriptions,
MQTT, gRPC, REST, and historian APIs may later be replaceable adapters around these
contracts. FastAPI is not domain architecture.

`TEPSimulationSource -> generic telemetry contract`; the generic telemetry contract
must not depend on TEP. Future `OPCUASource`, `HistorianReplaySource`, and
`MQTTSource` share the same boundary. No generic field requires XMEAS, XMV, IDV,
or ControlMode. No `IndustrialEnvironmentAdapter` is extracted until a TEP source
and at least one non-TEP source/domain provide evidence for actual common needs.

`industrial-agent-runtime` remains unchanged: tasks, tools, budgets, state, gates,
and traces remain its concerns. OPC UA, MQTT, historians, stream ingestion, and
SignalSample storage remain application/domain infrastructure.

Plant Context Builder is upstream: documents/models -> candidate extraction ->
provenance and confidence -> human/deterministic validation -> canonical context.
Extraction never directly establishes trusted mappings or execution authority.

## Consequences and alternatives

Accepted costs are explicit clock domains, scoped identities, immutable context
versions, and two-dimensional read bounds. These make replay and Agent evidence
traceable without prescribing a database or transport. Source-native quality and
timing detail must be retained where canonical labels are insufficient.

Rejected alternatives:

- one plant-and-stream payload: conflates static context with dynamic history;
- OPC UA -> LLM, MQTT -> prompt, or Kafka -> ContextProjection: bypasses typed bounds;
- timestamps as identity, UTC-shaped simulation time, or reads of today's final
  store: breaks duplicate handling or historical knowledge isolation;
- REST/FastAPI or a selected database as domain architecture: premature coupling;
- telemetry storage in runtime or a TEP-shaped generic adapter: violates ownership;
- direct UI/world reads or a second ProcessGraph: violates P0 and ADR-002.

## Implementation boundary

- P1.1: TEPSimulationSource -> canonical samples -> deterministic
  InMemoryTimeSeriesStore -> bounded reader, including snapshot semantics.
- P1.2: integrate live current/history into ApplicationView through the owning
  public P0/application read boundary; never bypass it with private world access.
- P1.3: dirty-stream replay/fault injection and missing/duplicate/late/quality tests.
- P1.4: first external connector, OPC UA or an open historian source.

P1.0 implements none of these. It changes no Python, fixtures, D0 specs, roadmap,
runtime, simulator, desktop, CI, database, or transport. Compatibility evidence and
the future migration obligations are recorded in the normative contract.
