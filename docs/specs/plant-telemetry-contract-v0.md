# Plant Context + Telemetry Contract v0

Status: frozen P1.0 architecture; conceptual records/operations, not implemented.

Owner: application/domain infrastructure, initially `tep-agent-lab`.

Decision: [ADR-003](../decisions/ADR-003-industrial-environment-boundary.md).

Sequencing: [ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md) supersedes the
milestone numbering in §Frozen implementation sequence and amends the §Context Builder
boundary scope for P1.2. The semantics in this contract are unchanged. Implementation
(informative): P1.1A and P1.1B implement the telemetry subset.

Reviewed base: `83a96c19675eed03831f7106834904b667959fbd`.

Composition review: accepted telemetry head
`290e483be6d02e601392e34cb96ceb085548ce66`. The telemetry contracts below are
preserved; Engineering Context and investigation composition extend their use.

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
| Structured engineering claims, limits, constraints and rule governance | Existing lab RuleRegistry; exact RuleRef and SourceVersion citations |
| Engineering material content and revisions | Referenced source owners; P0 CanonicalContextRegistry attests/resolves inventory, not a new knowledge store |
| Source normalization, acceptance, deduplication, timing validation | Application/domain TelemetryIngestor with source adapter |
| Accepted immutable samples and commit revisions | Application/domain TimeSeriesStore |
| Bounded snapshot reads, ordering, reduction metadata | Application/domain TimeSeriesReader |
| Run lifecycle, source attestation, scoped projections/provenance | Existing P0 RunManager / CanonicalContextRegistry / RunQueries |
| Application view assembly and transport | ApplicationViewService and replaceable application adapters |
| Agent tools, lab visibility/request policy, observation ingestion | Existing lab Tool Surface/state contracts |
| RcaState, ObservationRecord and revision-bound projection/state updates | Existing investigation subsystem / RcaStateStore / RunLog |
| Hypothesis, HypothesisEvidenceLink and experiment contracts | Existing experiments subsystem, persisted through investigation contracts |
| InvestigationReport and archival record references | Existing records subsystem / EngineeringArchive |
| ContextSnapshot / InvestigationContextView | Derived application/domain reference assemblies; no new canonical owner |
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
| `DocumentRef` | `document_id`, immutable `content_ref`/checksum, `version`, `source_location`, extensible `kind`, `scope_refs`, `validation_status`, `provenance` |
| `KnowledgeRef` | `knowledge_id`, immutable `content_ref`/checksum, `version`, `source_location`, extensible `kind`, `scope_refs`, `validation_status`, `provenance` |

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
P1 consumes validated structured context, including verified engineering graphs
from upstream systems. Automatic drawing/document extraction is not the portfolio
wedge; P1 does not prescribe how every upstream graph is generated.

> **Scope amendment ([ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md) Decision 5):**
> review-gated compilation of a small engineering-knowledge text corpus for one subsystem is
> P1.2 scope. Plant-graph generation and drawing/P&ID/OCR/VLM extraction stay upstream or
> deferred. Model extraction still cannot directly become trusted truth.

## Engineering Context and existing RuleRegistry

Engineering material is scoped to plant semantics without copying document bodies
into the canonical plant model. `scope_refs` bind exact plant revision plus entity,
signal, relationship, or subsystem identifiers and an explicit association such as
"applies to". Subsystem membership must come from a versioned domain-owned grouping
or relationship, not an invented parallel graph. Plant-wide applicability must be
explicit. Missing/ambiguous scope fails closed for operational assembly.

Examples include SOPs, maintenance procedures, control-loop descriptions, operating
limits, alarm-response guidance, equipment manuals, engineering notes, and validated
engineering rules. These are extensible kinds, not a universal closed enum. For
example, an exact SOP section may apply to an entity and its pressure signal, while
a control-loop description applies to related sensor/actuator entities and signals.
Associations describe applicability; they do not promote execution authority.

DocumentRef identifies a document revision; KnowledgeRef may identify a versioned
section or semi-structured engineering item, citing its exact document when derived.
`source_location` identifies the section/page/tag or authoritative source locator
within that immutable revision. Provenance preserves original source, method and
validation refs. `validation_status` preserves candidate versus reviewed/verified
material and source-native review status without inventing rule validation enums.
Confidence alone cannot establish verification. Candidates may be visible under
an explicit labeled review/reference policy but cannot silently become trusted
operational knowledge. Ordinary assembly declares its validation/status policy.

```text
[unstructured / semi-structured engineering material]
  -- exact scoped content refs --> [DocumentRef / KnowledgeRef]
[structured engineering claim / limit / constraint]
  -- exact rule version --> [existing RuleRegistry / RuleRef]
[both reference paths] -- selected inventory --> [ContextSnapshot assembly]
```

`src/tep_agent_lab/rules.py` already owns structured engineering claims. RuleRef
pins `(rule_id, version)`; RuleRegistry is an immutable host snapshot, resolves
exact refs and matches scope exactly. Its `origin`, `validation`, `authority`,
`behavior`, `source_refs`, `validation_refs`, and `scope` remain authoritative.
SourceVersion preserves `source_ref`, `version`, and `source_location`; it is a
citation, not a runtime InformationRef. Assembly maps declared plant scope refs
to existing exact rule scopes and inputs using versioned binding provenance,
without altering registry matching or writing new rules. Registry source refs
are attested through the existing source inventory/content refs for reproduction.

An operating-limit document remains material; a structured limit claim references
its RuleRef, retaining the underlying source citation rather than duplicating the
claim in a new rule store. No EngineeringRuleStore, KnowledgeRuleStore, second
RuleRegistry or implicit rule promotion is introduced. Context assembly cannot
change authority, install gates, resolve conflicts silently or edit rule metadata.

## ContextSnapshot: exact available industrial context

ContextSnapshot is a derived immutable assembly of references, not a canonical data
store. It answers what engineering context and operational data were available at
one investigation point and how the selection was made.

```text
ContextSnapshot
  snapshot_ref / immutable identity and content checksum
  plant_context_ref
  telemetry_snapshot_ref -> exact TelemetryReadSnapshot (event horizon T, ingest K)
  engineering_knowledge_refs[]
  document_refs[]
  rule_refs[] -> existing RuleRef versions and registry/source provenance
  source_inventory_ref / availability_selection_ref
  visibility / projection_policy_ref
  assembly_policy_version
  assembly_query_provenance_ref
```

The assembly MUST pin the canonical plant revision, exact telemetry snapshot, every
selected knowledge/document/rule revision and content/source refs, and exact assembly
and visibility policy versions. Telemetry's pinned context must agree with the
plant revision used for its bindings; any intentional mapping between revisions
must be explicit, immutable and validated, never inferred from latest metadata.
Assembly/query provenance records consumer/run/investigation scope, requests and
parameters, clock/horizon refs, inventory selection and scope/validation decisions.
Selections are bounded by application/Agent projection policy; snapshots do not
auto-inject all available material into prompts.

P0 CanonicalContextRegistry remains the trusted source inventory/resolution owner
and keeps its READY-frozen run inventory. Assembly only selects within the pinned
authorized inventory; it cannot add/relabel sources. Where sources have publication
or availability semantics, the pinned inventory/availability selection records the
source-native publication domain and the inventory revision or acceptance point
at which each exact revision became available. T alone does not imply knowledge
availability and does not turn a document timestamp into a simulation timestamp.
Any time comparison uses explicit clock mapping; unknown eligibility fails closed.

For example, a manual revision added after a selected historical inventory cannot
enter replay merely because its text says it was effective earlier. Later reviewed
knowledge requires a new authorized inventory/run and a new ContextSnapshot under
the owning P0 lifecycle; it cannot mutate a READY inventory or an earlier snapshot.

ContextSnapshot MUST preserve visibility and MUST NOT contain evaluator truth,
hidden benchmark bindings or hidden source identifiers/counts. Resolution enforces
caller authorization as well as the pinned policy. It MUST be reproducible from
exact refs or report explicitly unavailable. Missing old knowledge/document/rule
revisions MUST NOT be replaced with today's latest content; partial resolution
cannot be represented as the same complete snapshot. A new partial assembly, if
policy permits, must have a new identity and disclosed missing permitted refs.

Retaining the derived manifest/reference does not make it a new ProcessGraph,
RuleRegistry, CanonicalContextRegistry, TimeSeriesStore or RcaState store. It owns
neither source content nor investigation records. No mutable latest knowledge is
included by implication. No ContextSnapshot implementation is added in P1.0.

## Investigation Context composition and evidence provenance

```text
[ContextSnapshot] -- immutable context ref ----------------+
[existing RcaState] -- current-at-assembly revision/ref ----+
                                                         v
                                         [InvestigationContextView]
                                           |                |
                                  application reads   bounded model context
                                           v                v
                                    [Application]   [Agent ContextProjection]
```

InvestigationContextView is a derived read/projection, not canonical investigation
state. It pins ContextSnapshot ref plus investigation identity and the exact RcaState
revision/ref captured at assembly. It may select ObservationRecord, hypothesis,
HypothesisEvidenceLink, experiment, conclusion/report refs and open questions from
that revision under existing visibility/projection bounds. Open questions currently
live in RcaState; revision plus question_id identifies them without inventing a
second record store. Historical replay uses that pinned state revision, not today's
current state. Assembly must reject inconsistent investigation/run/source scope or
ineligible state selections, not merge today's RCA evidence into an earlier view.

`investigation.py` owns RcaState, ObservationRecord, RcaStateStore, deterministic
ingestion and ContextProjection construction; `experiments.py` owns Hypothesis,
HypothesisEvidenceLink and experiment contracts; `records.py` owns InvestigationReport
and EngineeringArchive. [RCA v0](rca-v0.md), [investigation state](investigation-state-v0.md),
[experiments](hypothesis-experiment-v0.md) and [engineering records](engineering-records-v0.md)
remain the owning specs. P1 references these records; it does not redefine or persist
duplicates in P1 storage. Diagnosis remains the existing causal claim/conclusion
and InvestigationReport path, not a new context-owned diagnosis record.

Agent ContextProjection keeps its exact `base_revision`, persistence and existing
ModelStateUpdateProposal -> atomic TaskStateStore.apply_batch semantics. Composition
must not authorize model writes, bypass gates or replace runtime contracts. Views
need not contain every pinned document or record body; retrieval stays bounded.
Archival reports do not become automatic context for later blind benchmark runs.

### Evidence provenance bridge (hard invariant)

> Any ObservationRecord used as evidence and derived from telemetry or engineering
> knowledge must retain sufficient exact refs to reconstruct what source/context/
> snapshot produced it.

| Observation source | Exact traceability required |
|---|---|
| Telemetry | ContextSnapshot/plant context refs, TelemetryReadSnapshot with T/K, signal/binding and selected sample identities or immutable result artifact, exact tool/service request and selection/reduction policy |
| Engineering material | ContextSnapshot/plant context refs, KnowledgeRef/DocumentRef/RuleRef, exact content revision/checksum and source location, selected inventory/availability and exact tool/service request |
| Derived combination/feature | All contributing source/snapshot refs plus versioned derivation/request; no excluded data may affect the result |

Use existing ObservationRecord `producer_request_ref`, `tool_or_service_ref`,
`information_refs`, `artifact_refs` and immutable `provenance` to retain this chain.
Conceptual source refs/RuleRef/SourceVersion remain their owning types; when entering
runtime ref fields they need a validated source-visible InformationRef/artifact
envelope, not an invented type substitution or a runtime schema change. The lab
producer/ingestion path must preserve these refs in future integration. Today's
tools are not claimed already to emit P1 snapshot provenance.

Observation != Evidence. Successful tool results register immutable observations;
only explicit HypothesisEvidenceLink/state-update semantics link them as evidence.
Knowledge verification, source inclusion or snapshot selection does not automatically
create evidence or establish a causal conclusion. A view must not relabel observations
as evidence just because they were read. Evidence may originate at an earlier
snapshot; preserve that originating snapshot rather than rebinding it to the current
one. Its availability must still be eligible for the selected investigation point.

The UI's "why did the Agent know this?" path follows explicit evidence link ->
observation -> request/result -> telemetry window + T/K, or exact SOP/manual/rule
source -> plant/topology and context revision. Missing source retention reports
unavailable; never reconstruct an asserted fact from newer data. Agent-visible
provenance must remain free of evaluator-only fields and hidden refs.

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
| `src/tep_agent_lab/rules.py: RuleRegistry / RuleRef / SourceVersion` | Structured rules retain exact scope matching, versioned source/validation refs and authority/behavior; assembly selects refs and does not create a second registry |
| `src/tep_agent_lab/investigation.py: RcaState / ObservationRecord / RcaStateStore` | Derived view pins state revision; existing ingestion refs/provenance carry future snapshot traceability; observations and evidence remain distinct |
| `src/tep_agent_lab/experiments.py: Hypothesis / HypothesisEvidenceLink / ExperimentProposal / ExperimentResult` | Existing typed contracts and state update path own hypotheses, evidence links and experiments; ContextSnapshot stores none of them |
| `src/tep_agent_lab/records.py: InvestigationReport / EngineeringArchive`, [RCA v0](rca-v0.md) | Report stays tied to state revision and exact evidence/experiment/trace refs; no automatic archival retrieval or second report owner |

Today's recent live views have no immutable K, no archived arbitrary interval,
and no cross-view atomic snapshot. They do not already satisfy this new telemetry
contract. P1.1 must explicitly introduce ingestion/reader snapshots alongside the
existing owners, without claiming ReferenceWorld.revision() is an ingestion cutoff
or reusing evaluator-hidden salted state hashes as visible store revisions.
P1.4 must evolve the owning public P0/application query boundary to expose bounded
reader results; it must not route ApplicationViewService directly to private world
state. No existing contract, fixture or benchmark behavior changes in P1.0. Any
implementation contradiction with an owning spec must emit SPEC_CONFLICT and be
resolved explicitly rather than introducing a bypass.

> **Numbering update ([ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md) Decision 3):**
> the "P1.4" obligation above is owned by P1.1C (application reads) and P1.5 (Agent
> ContextProjection and tool reads). The obligation itself is unchanged.

## Frozen implementation sequence and acceptance

> **Superseded numbering:** [ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md) owns P1
> sequencing; its Decision 3 maps every row of this table and the sentence after it. This
> section is kept as history. The P1.0 freeze statement below and every semantic section of
> this contract remain authoritative.

| Milestone | Authorized future scope |
|---|---|
| P1.1 | TEPSimulationSource -> canonical SignalSample -> deterministic InMemoryTimeSeriesStore -> bounded reader with explicit clocks, identity and snapshot cutoffs |
| P1.2 | Engineering Context binding: TEP engineering knowledge fixture/reference set -> entity/signal-scoped KnowledgeRef and existing RuleRef |
| P1.3 | ContextSnapshot: plant context + exact telemetry snapshot + engineering knowledge -> immutable investigation-time reference assembly |
| P1.4 | Investigation Context integration: ContextSnapshot + existing RcaState -> Application / Agent read assembly, including live current/history through public P0/application reads |
| P1.5 | Dirty-stream simulation/replay; late/duplicate/missing/quality and temporal-leakage tests |
| P1.6 | First external connector: OPC UA or historian replay |

Context composition is prioritized before external connector breadth. P1.1 remains
the accepted live telemetry foundation; later engineering fixture/reference work
is planned here, not introduced by this documentation PR. The two-domain rule remains.

P1.0 freezes plant semantic ownership, ingestion/storage/reader ownership, immutable
sample shape, four clocks, late/out-of-order identity, historical K/T reconstruction,
temporal leakage isolation, deferred REST, TEP adapter direction, unchanged runtime,
and P1.1 scope. It also freezes scoped Engineering Context, reuse of RuleRegistry,
derived ContextSnapshot/InvestigationContextView and the evidence provenance bridge
without adding a canonical owner. It implements none of the sequence and touches
no production code, fixture JSON, D0 benchmark specs, roadmap, runtime, simulator,
desktop or CI.

Architecture review outcome: `SPEC_CONFLICT: none`. The migration gaps above are
explicit future obligations, not silent changes to current production guarantees.
