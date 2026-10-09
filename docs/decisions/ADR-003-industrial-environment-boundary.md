# ADR-003 — Freeze the industrial environment and telemetry boundary

- Status: Accepted (architecture contract only; not implemented)
- Date: 2026-10-07
- Scope: P1.0
- Normative contract: [Plant Context + Telemetry v0](../specs/plant-telemetry-contract-v0.md)
- Base reviewed: `83a96c19675eed03831f7106834904b667959fbd`
- Composition review: accepted telemetry head `290e483be6d02e601392e34cb96ceb085548ce66`
- Sequencing update: [ADR-004](ADR-004-p1-milestone-rebaseline.md) supersedes the P1.2–P1.6
  milestone numbering in §Implementation boundary and amends one scope statement (P1.2
  engineering-knowledge compilation). Every ownership, telemetry, visibility, authority and
  snapshot semantic in this ADR remains authoritative.
- Implementation (informative): P1.1A and P1.1B implement the telemetry subset; see
  `docs/p1-1a-telemetry-core.md` and `docs/p1-1b-tep-simulation-source.md`.

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

## Positioning

P1 is an **Industrial Investigation Context layer**, focused on Engineering
Knowledge × Time Series × Topology × Evidence-backed Investigation. Its product
question is: what engineering context and operational data could the Agent know
at investigation time T, and exactly why did it know it?

The portfolio wedge is context composition and evidence-backed investigation.
P1 is not a P&ID/OCR product, document digitization product, generic ETL platform,
or complete MES/ERP context platform. A verified engineering graph from an upstream
system can feed Canonical Plant Context. P1 consumes validated structured context
and does not own how every upstream graph is generated or depend on an Operon-like
drawing/document extraction implementation.

## Decision

```text
[Plant Context Plane: assets / topology / signals]
  -- exact plant revision ----------------------------------+
[Engineering Knowledge Plane: SOP / manuals / RuleRegistry] |
  -- scoped document / knowledge / rule revisions -----------+
[Telemetry Plane: source -> ingestor -> store -> reader]    |
  -- TelemetryReadSnapshot: event horizon T + ingest K -------+
                                                           v
                                              [ContextSnapshot]
                                              T + K + exact refs
                                                           |
                                                pinned context ref
                                                           v
[existing RcaState] -- exact revision --> [Investigation Context assembly]
                                           |                 |
                                  application reads    ContextProjection
                                           v                 v
                                  [Application / UI]       [Agent]
                                                             |
                                                gated tools / state proposals
                                                             v
                                  [existing Observation / Hypothesis / EvidenceLink]
                                  [Experiment / diagnosis / InvestigationReport]
```

The Industrial Environment is a conceptual boundary, not a new universal adapter
class. Plant context contains `PlantEntity`, `SignalDescriptor`, `Relationship`,
`DocumentRef`, and `KnowledgeRef`. Telemetry contains immutable `SignalSample`
records. The planes join through versioned signal/entity identities; neither embeds
the full other plane. Candidate mappings remain distinct from verified mappings.

Engineering Knowledge is a logical responsibility boundary, not a new storage
subsystem. DocumentRef/KnowledgeRef associate exact material revisions with entities,
signals, subsystems or relationships. Structured claims, limits and constraints
remain in existing RuleRegistry, referenced through RuleRef and its existing
origin/validation/authority/behavior/source/validation/scope semantics.

ContextSnapshot is a derived immutable reference assembly: it pins plant context,
TelemetryReadSnapshot, engineering material and rules, visibility policy, available
source inventory and assembly/query provenance. It contains no evaluator truth and
owns none of the referenced canonical state. Historical replay uses exactly the
selected available inventory and revisions, never today's latest knowledge.

ContextSnapshot plus an exact current-at-assembly RcaState revision produces the
derived InvestigationContextView for application reads or Agent ContextProjection.
RcaState, ObservationRecord, Hypothesis, HypothesisEvidenceLink, Experiment and
InvestigationReport keep their existing owners. Observation != Evidence: only an
explicit evidence link makes an observation evidence. Telemetry/knowledge-derived
observations must retain the exact snapshot/source/binding/request refs needed to
explain why the Agent knew a fact. Missing historical refs report unavailable.

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
Automatic drawing/document extraction is upstream/deferred, not the portfolio wedge.

> **Scope amendment ([ADR-004](ADR-004-p1-milestone-rebaseline.md) Decision 5):** review-gated
> compilation of a small engineering-knowledge text corpus for one subsystem is P1.2 scope.
> Plant-graph generation and drawing/P&ID/OCR/VLM extraction stay upstream or deferred. The
> trust rule in this paragraph is unchanged.

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

> **Superseded numbering:** [ADR-004](ADR-004-p1-milestone-rebaseline.md) owns P1 sequencing.
> Its Decision 3 maps every row below: P1.2 -> P1.2 (widened); P1.3 ContextSnapshot -> P1.5;
> P1.4 -> P1.1C for live telemetry current/history through public P0/application reads, and
> P1.5 for ContextSnapshot + RcaState -> InvestigationContextView (Application and Agent);
> P1.5 dirty-stream replay and P1.6 connector -> deferred to Level 2. The constraint "must not
> bypass P0 with private world access" binds P1.1C, P1.4 and P1.5. The list is kept as history.

- P1.1: TEPSimulationSource -> canonical samples -> deterministic
  InMemoryTimeSeriesStore -> bounded reader, including snapshot semantics.
- P1.2: Engineering Context binding; TEP engineering knowledge fixture/reference
  set -> entity/signal-scoped KnowledgeRef / existing RuleRef.
- P1.3: ContextSnapshot; plant context + telemetry snapshot + engineering knowledge
  -> immutable investigation-time context.
- P1.4: Investigation Context integration; ContextSnapshot + existing RcaState ->
  Application / Agent read assembly through owning public query/projection contracts.
  Live current/history integration must not bypass P0 with private world access.
- P1.5: dirty-stream simulation/replay and missing/duplicate/late/quality tests.
- P1.6: first external connector, OPC UA or historian replay.

Context composition takes priority over external connector breadth. The two-domain
rule still applies before any universal environment adapter extraction.

P1.0 implements none of these. It changes no Python, fixtures, D0 specs, roadmap,
runtime, simulator, desktop, CI, database, or transport. Compatibility evidence and
the future migration obligations are recorded in the normative contract.

## Portfolio MVP (non-normative)

```text
[TEP simulated plant] -- live telemetry -------------------+
[ProcessGraph] -- plant semantics / topology --------------+
[engineering material + RuleRegistry] -- scoped knowledge -+
                                                          v
                                               [ContextSnapshot]
                                                  | exact refs
                                                  v
                                         [real Agent investigation]
                                                  | existing contracts
                                                  v
                             [Observation / Hypothesis / EvidenceLink / Experiment]
                                                  | evidence-backed diagnosis
                                                  v
                                        [UI: why did the Agent know this?]
```

This is an intended portfolio MVP, not production-ready behavior or implemented
P1.0 capability. The UI should explain the telemetry window and knowledge cutoff,
plant/topology source, SOP/manual/rule source, observation and explicit evidence
link. External plant interoperability is unproven until a non-TEP source exists.
