# Plant Context Composition v0

Status: proposal

Version: v0

Owner repo: `tep-agent-lab`

Milestone: P1.3 Canonical Context composition, sequenced by
[ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md) and bounded by its
Decision 4 (P1.2 / P1.3 boundary). Product constraints come from
[ADR-005](../decisions/ADR-005-context-layer-product-positioning.md) Decisions 1 and 2.

Depends on: [Plant Context + Telemetry v0](plant-telemetry-contract-v0.md) (which owns the
semantics of the Canonical Plant Context, Engineering Context and ContextSnapshot),
[Context Compiler v0](context-compiler-v0.md) (EngineeringContextRevision and rule scope
bindings), [Playground backend v0](playground-backend-v0.md) (RunManager, RunManifest and
CanonicalContextRegistry), [Application API v1](application-api-v1.md), the existing
RuleRegistry contract ([Knowledge / Rule Registry v0](knowledge-rule-registry-v0.md)) and
[Live Operational State v0](live-operational-state-v0.md), whose LOS-A-S2/S3 slices consume
this spec.

This proposal records the owner decisions of 2026-10-12, 2026-10-13 and 2026-10-19
listed in §Owner decisions. It ships with the source-binding amendment to
[Plant Context + Telemetry v0](plant-telemetry-contract-v0.md#source-binding-levels-amendment-2026-10-13)
(§Source binding levels) and an additive
[Application API amendment](application-api-v1.md#composed-view-read-amendment-2026-10-19)
for the composed-view read. The owner accepts these through this PR's merge.

## Problem Statement

The platform can already compile engineering documents into reviewed knowledge (P1.2),
stream canonical telemetry from a continuously running simulated plant (P1.1, P1.4B) and
read the human-verified TEP ProcessGraph. These three are still held together by an
interim convention: compiler bindings and telemetry cite the raw `PROCESS_GRAPH`
ContextSourceRef directly, and nothing composes them into one exact, checkable plant
context.

From the user's side this means:

- an engineer cannot ask "which approved limits, procedures and documents apply to this
  signal or this unit, under exactly which plant revision?" and get one reproducible
  answer;
- nothing proves that every scope a reviewer approved in an EngineeringContextRevision
  still resolves to a real entity, signal or relationship of the plant revision in use,
  so a stale or mistyped scope could silently attach knowledge to nothing, or to the
  wrong thing;
- P1.4A Operational State cannot compare live values against a published limit through a
  canonical mapping (LOS-A3), and cannot select the related signals of a bounded
  subsystem (LOS-A-S3), because no versioned grouping or relationship view exists;
- P1.5 cannot pin a `plant_context_ref` in a ContextSnapshot, because no
  PlantContextRevision exists, and the telemetry store's `context_ref` still names the
  raw graph source rather than a canonical plant revision;
- when a disturbance enters one unit, nothing tells an engineer or an Agent which other
  units and measurements are structurally reachable from it, even though the plant
  topology already holds that information.

## Solution

P1.3 adds exactly the four deliverables of ADR-004 Decision 4, plus two rebuildable views
inside deliverable 3 that the owner selected:

1. **PlantContextRevision**: an immutable, content-addressed, identity-preserving
   projection of the exact pinned `PROCESS_GRAPH` source into the generic
   PlantEntity / SignalDescriptor / Relationship records of plant-telemetry
   §Canonical Plant Context. It is derived deterministically at run preparation and
   identified by its content checksum. It adds no plant semantics and renames nothing.
2. **Scope resolution**: deterministic, fail-closed validation that every scope ref of a
   selected EngineeringContextRevision, including its rule scope bindings, resolves in
   the selected PlantContextRevision. A selection that does not fully resolve is
   rejected as a whole and never partially served.
3. **Composed view (derived, non-canonical, rebuildable)**, with three parts:
   - applicability: entity, signal or relationship -> applicable KnowledgeRef,
     DocumentRef and RuleRef (refs only, never bodies);
   - subsystem grouping: groups derived from the plant topology by a versioned
     grouping policy (owner decision), which LOS-A-S3 uses for related signals;
   - topology reachability: directed upstream and downstream reachability with hop
     counts and loop membership, labelled topology-only (owner decision).
4. **Telemetry re-keying**: the telemetry store's `context_ref` becomes the
   PlantContextRevision ref for new runs. Only runs prepared after P1.3 record an
   interim-ref mapping through the projected source ref; re-keying is never retroactive
   and consumers never resolve "latest" metadata.

An engineer, the application read layer, P1.4A and P1.5 then read one exact answer:
"under plant revision P and engineering selection E, these refs apply to this signal,
these signals belong to this subsystem, and these units are structurally downstream",
reproducible from exact refs or reported unavailable.

## User Stories

1. As a plant engineer, I want every run to pin one exact PlantContextRevision, so that I know precisely which plant model a result was computed against.
2. As a plant engineer, I want the PlantContextRevision to keep every ProcessGraph node, edge and binding id unchanged, so that signal ids I already use in telemetry and reviews keep meaning the same thing.
3. As a plant engineer, I want the revision to be derived from the human-verified ProcessGraph rather than hand-edited, so that plant semantics change only through the plant-model review path.
4. As a plant engineer, I want the same pinned graph to always produce the same revision checksum, so that I can reproduce any past run's plant context.
5. As a plant engineer, I want each signal descriptor to carry the same quantity and unit that the telemetry source already reports, so that the plant model and the live data never disagree about meaning.
6. As a plant engineer, I want signals bound to a stream (not a unit) to remain associated with that stream and its endpoint units, so that the cooling-water outlet temperature is not mistaken for a reactor measurement.
7. As a plant engineer, I want entities without instruments to stay explicitly unbound, so that "no measurement here" is visible rather than silently missing.
8. As a knowledge reviewer, I want composition to check that every scope I approved resolves in the selected plant revision, so that approved knowledge is never attached to a non-existent or wrong target.
9. As a knowledge reviewer, I want a scope that names the wrong kind of target (an entity id used as a signal) to be rejected, so that kind confusion cannot slip through.
10. As a knowledge reviewer, I want a scope whose exact source content identity differs from the selected revision's projected source to be rejected, so that knowledge reviewed against another plant model is not reused by accident.
11. As a knowledge reviewer, I want an empty scope to be rejected, so that "applies to nothing" is never read as "applies to everything" (v0 has no explicit plant-wide carrier).
12. As a knowledge reviewer, I want the rule scope bindings published with a Rule-routed item to be validated the same way, so that a limit rule cannot be scoped to a signal that does not exist.
13. As a knowledge reviewer, I want a failed resolution to report every failing scope with a typed reason, so that I can fix the revision in one review pass.
14. As an operations engineer, I want to look up a signal and see the approved limits, procedures and documents that apply to it, so that I can interpret its current value.
15. As an operations engineer, I want applicability results to cite exact KnowledgeRef, DocumentRef and RuleRef versions, so that I can open the source text behind each one.
16. As an operations engineer, I want rejected or unreviewed candidates never to appear as applicable knowledge, so that only reviewed material informs operation.
17. As an operations engineer, I want the reactor cooling subsystem to be a defined group of entities, streams and signals, so that related-signal views compare the right measurements.
18. As an operations engineer, I want subsystem membership to come from a versioned rule over the plant topology, so that I can see why a signal is in the group and reproduce it.
19. As an operations engineer, I want to select a unit or stream and see which other units and measurements are structurally downstream of it, with hop counts, so that I know where a disturbance could show up next.
20. As an operations engineer, I want to see upstream reachability too, so that I can list the units that could structurally have caused a change.
21. As an operations engineer, I want recycle loops identified, so that I know a disturbance can re-enter the reactor.
22. As an operations engineer, I want reachability clearly labelled as topology only, so that I never mistake it for proven flow, dynamics, lag or causation.
23. As an investigation Agent (through P1.5 later), I want reachability and applicability refs to be bounded and exact, so that my context stays small and every item is citable.
24. As an investigation Agent, I want reachability to narrow which measurements to check, while evidence still requires an explicit HypothesisEvidenceLink, so that topology never counts as evidence.
25. As the P1.4A Operational State layer, I want a published limit to resolve through the PlantContextRevision and its RuleRef, so that limit comparison uses canonical mapping (LOS-A3).
26. As the P1.4A Operational State layer, I want grouping and relationship refs that I can cite exactly in a RelatedSignalChange, so that related-signal summaries are reproducible (LOS-A-S3).
27. As the P1.5 ContextSnapshot assembler, I want a `plant_context_ref` that telemetry's context ref agrees with, so that a snapshot pins one consistent plant revision.
28. As the P1.5 ContextSnapshot assembler, I want the composed view to stay derived and separately identified, so that a snapshot still pins plant, knowledge and rule refs separately rather than one combined static revision.
29. As a telemetry consumer, I want new runs' snapshots to carry the PlantContextRevision ref as their `context_ref`, so that every sample's meaning is tied to the canonical plant revision.
30. As a telemetry consumer, I want runs prepared after P1.3 to record the interim-ref mapping in RUN_READY, while an earlier run's interim ref stays valid as recorded and a request to map it reports unavailable, so that old results are never retroactively re-keyed.
31. As a run operator, I want composition to happen during preparation and fail the preparation if it cannot complete, so that a run never reaches READY with an unresolved plant context.
32. As a run operator, I want the RUN_READY record to pin the plant context ref and the exact engineering selection, so that the trusted lifecycle history proves what context the run had.
33. As a run operator, I want an explicitly empty engineering selection to be allowed and recorded, so that telemetry-only runs remain valid.
34. As a live-plant operator, I want live runs to pin the same PlantContextRevision as P0 runs over the same graph, so that live and investigation runs share one plant identity.
35. As a benchmark maintainer, I want frozen D0 fixtures and tests to stay byte-identical, so that P1.3 cannot disturb the research baseline.
36. As a security reviewer, I want composition to read only AGENT-visible sources and never evaluator truth or hidden bindings, so that the context layer cannot leak benchmark answers.
37. As a security reviewer, I want the composed view to grant no execution authority, so that applicability or reachability never becomes a permission.
38. As a platform architect, I want the generic composition core to import no TEP module, so that a second domain pack can reuse it.
39. As a platform architect, I want the TEP-specific projection to live in the process pack, so that domain semantics stay in domain packs (ADR-005).
40. As a platform architect, I want no mutable "current context" registry or resolver, so that every consumer reads an exact pinned revision.
41. As a platform architect, I want the design to leave room for a later shared equipment-hierarchy anchor without adopting one now, so that P1.3 does not foreclose it.
42. As a developer, I want the composition core testable as pure functions over pinned inputs, without starting a simulator, so that tests stay fast and deterministic.
43. As a developer, I want golden checksums for the TEP PlantContextRevision and composed view, so that accidental changes to projection or policies are caught.
44. As a developer, I want every policy (projection, grouping, reachability, applicability) versioned and part of the derived identity, so that changing a policy changes the identity.
45. As an operations engineer, I want an entity-scoped limit to apply only to the signal it actually limits, so that a pressure limit is never shown against the temperature signal of the same unit.
46. As a run operator, I want each stream incarnation registered against the shared plant revision, so that runs share one plant identity while every sample still names its own stream.

## Implementation Decisions

### Modules

- **Generic plant-context core** (new, domain-neutral, imports no TEP module): the
  PlantContextRevision record types (PlantEntity, SignalDescriptor, Relationship),
  revision identity, scope resolution, and the composed view (applicability, grouping,
  reachability). Pure functions over typed inputs; no I/O.
- **Process-pack projection adapter** (new, TEP-specific): projects the pinned,
  human-verified ProcessGraph into the generic records. It reuses the existing
  deterministic binding table that the TEP simulation source already derives from
  ProcessGraph bindings, so the signal id, quantity, unit and binding relation in the
  revision are exactly the ones telemetry reports. It also supplies the process pack's
  grouping definitions.
- **Run preparation** (modified): RunManager derives the PlantContextRevision from the
  attested `PROCESS_GRAPH` source during prepare, resolves the explicit engineering
  selection, builds the composed view, and fails preparation on any error. The
  CanonicalContextRegistry stays the READY-frozen source inventory; the
  PlantContextRevision is a derived record, not a new registered source.
- **Telemetry attachment** (modified): the run telemetry store and the live telemetry
  store take the PlantContextRevision ref as their `context_ref`.

### PlantContextRevision

- Schema version `tep-agent-lab.plant-context-revision/v0`. Fields: the projected
  `PROCESS_GRAPH` ContextSourceRef; projection adapter id and version; the ProcessGraph
  fixture id, version and content checksum and its review record ref; and sorted
  entities, signals, relationships and unbound records.
- PlantEntity: `entity_id` = ProcessGraph node id; `kind` = namespaced process-pack kind
  (for example `tep.process-graph/REACTOR`), never a closed generic enum; `provenance`.
- Relationship: `relationship_id` = ProcessGraph edge id; `from_entity_id`,
  `to_entity_id` = edge endpoints; namespaced `kind` (for example
  `tep.process-graph/MATERIAL_STREAM`, `tep.process-graph/UTILITY_STREAM`); `provenance`.
  Relationships express topology and association, not inferred dynamics; a directed
  edge does not prove flow.
- SignalDescriptor: `signal_id` = ProcessGraph binding semantic entity id (the P1.1
  `signal_id`); `source_binding` = the **source definition** id of the process-pack
  adapter (for example `tep-simulation-source/<adapter version>`) and the runtime
  variable id as an opaque adapter-native locator (see §Source definitions and stream
  registration); `entity_refs` with an association role (`MEASURED_AT` or
  `ACTUATED_AT` on an entity, or `ON_RELATIONSHIP` on an edge, which also lists the edge
  endpoints with role `RELATIONSHIP_ENDPOINT`); `quantity`; `unit` (explicit unit or
  explicit unknown marker, never implied); `value_kind` numeric; `capability` READ
  (v0 telemetry is read-only; actuation is an association role, not a write
  capability); `provenance`.
- Unbound records preserve the source's `unbound_entities` entries as references with
  target kind ENTITY or RELATIONSHIP, unchanged target id and reason. They introduce
  no identities; their target ids also occur in the entity or relationship inventory.
- Provenance on every record: projected source ref and revision, method
  `projection:<adapter>/<version>`, `validation_status` VERIFIED with the ProcessGraph
  human-verification review record as validation evidence, and the source-native
  binding method preserved unchanged.
- Identity: `plant_context_ref` = `plant-context:sha256:<checksum>` of the canonical
  JSON of the record. Deriving the record twice from the same pinned inputs yields the
  same bytes and ref.
- The projection preserves every node, edge and binding id and rejects a graph that is
  not pinned and human-verified, has duplicate or colliding ids, or has a binding whose
  attached target does not exist.

### Source definitions and stream registration

plant-telemetry §Canonical Plant Context and §Source binding levels distinguish the
static source definition from the stream incarnation `source_id` of §Identity. Existing
P0 and live stream source ids embed run and session tokens, so an incarnation id cannot
sit in a PlantContextRevision shared by P0 and live runs over the same graph. By owner
decision of 2026-10-13 (PCC-OD-5), the owning contract is amended in
[plant-telemetry §Source binding levels (amendment 2026-10-13)](plant-telemetry-contract-v0.md#source-binding-levels-amendment-2026-10-13),
which owns these semantics. This spec implements it:

- **Static level (in the PlantContextRevision):** `source_binding` names a source
  *definition*: the versioned process-pack adapter that can produce the signal, plus
  the opaque locator. It is identical for every run over the same pinned graph and
  adapter version.
- **Incarnation level (at stream registration, outside the revision):** when a run or
  live session registers a telemetry stream, it records an immutable
  StreamRegistration: incarnation `source_id`, source definition id, the
  `plant_context_ref`, and the registered signal ids. Every registered signal must exist
  in the revision with that source definition; otherwise registration fails. The
  StreamRegistration is retained in the run's RUN_READY provenance (P0) or the live
  session provenance bound to RUN_READY (live), and accepted records keep the incarnation
  `source_id` exactly as today.

A consumer that needs the incarnation for a signal resolves it through the
StreamRegistration of the exact run, never through the revision or "latest" metadata.

### Scope resolution

- Inputs: one PlantContextRevision and an explicit, ordered selection of
  EngineeringContextRevision refs (possibly empty) registered in the run inventory,
  with each revision's exact input_rule_set and published_rule_set refs. Each revision's
  RuleRefs and exact `Rule.scope` strings are validated through **its own
  published_rule_set**, never another selected revision's set or its input_rule_set
  (unless publication explicitly identifies the latter as the published set). Registry
  matching is unchanged.
- Each PlantTargetRef `(plant_context source ref, kind, target_id)` resolves only when
  the cited source and the revision's projected source have equal content-identity
  fields: `kind`, `schema_version`, `checksum_method` and `content_checksum`.
  Both refs must be attested against the exact pinned content using their declared
  checksum method. For `sha256:canonical-json` this is the SHA-256 of the exact
  canonical JSON bytes; for `sha256:bytes` it is the SHA-256 of the exact source bytes.
  The methods are not interchangeable. Repository, git revision and locator retain
  exact source provenance but are not content-equality fields. Carrier metadata
  (`source_id`, `provenance`, `governance`) is recorded for both refs in the
  ScopeResolutionReport, not compared. Visibility is checked separately. The plant
  revision's own HUMAN_VERIFIED governance, backed by its attested graph and human
  review record, makes the target authoritative; a compiler carrier's empty governance
  neither establishes nor removes that authority. Matching never uses a name, "latest"
  or fallback. Content-identity mismatch fails `SOURCE_MISMATCH`; cross-content mapping
  is out of scope for v0.
- After that match, `target_id` must exist under the matching kind (ENTITY -> entity,
  SIGNAL -> signal, RELATIONSHIP -> relationship). Validate every scope ref, every
  `approved_bindings[].target` PlantTargetRef, and every published rule scope binding
  with these same source, id and kind checks. A missing id fails `UNKNOWN_TARGET`;
  an existing id of a different kind fails `KIND_MISMATCH`.
- Different publications can derive rule sets independently. If their published sets
  give the same `(rule_id, version)` unequal serialized Rule content (including scope),
  or selected revisions pin different versions of the same rule id, reject the whole
  selection with `RULE_SET_MISMATCH`. Identical shared Rule entries in different sets
  are allowed. Selection order never overrides a conflict or merges registries.
- **Typed inputs.** A Rule-routed item's `variables_entities` (for an operating limit,
  the limited signal) must each resolve to one entity, signal or relationship, and lie
  in the union of its resolved scope targets' memberships:

  - ENTITY E: E itself, its incident relationships, signals directly attached to E,
    and signals attached to those incident relationships; no other endpoint entity
    or that endpoint's signals is included;
  - RELATIONSHIP R: R itself and signals attached to R; endpoint associations remain
    available but do not expand membership to endpoint entities or their signals;
  - SIGNAL X: X itself only, so a limit scoped to X with input X is in scope.

  "Attached" uses only the roles defined under §Composed view. ENTITY membership through
  an incident relationship preserves US-6's endpoint association: XMEAS(21) is inside a
  reactor-only ENTITY scope via the outlet relationship, but is never directly attached
  to, or a measurement of, the reactor. This also keeps the staged demo's reactor-scoped
  CONTROL_RELATION with XMV(10) in scope. These are the scope-to-rule/input versioned
  binding provenance of plant-telemetry §Engineering Context; resolution validates them
  and never rewrites them.
- Typed failure reasons: `SOURCE_MISMATCH`, `UNKNOWN_TARGET`, `KIND_MISMATCH`,
  `EMPTY_SCOPE`, `UNKNOWN_RULE_SCOPE`, `RULE_SET_MISMATCH`, `UNKNOWN_INPUT`,
  `INPUT_OUT_OF_SCOPE`, `UNVERIFIED_REVISION` (the revision fails its own publication
  verification). Colliding ids are rejected by projection before resolution.
- The ScopeResolutionReport lists every scope ref and approved binding target with
  RESOLVED or its failure reason, retains both exact source carriers and their compared
  content identities, and reports rule-set and typed-input failures. Any failure rejects
  the whole selection; no partial composed view is produced, and preparation fails.
- **Plant-wide applicability is not supported in v0.** plant-telemetry and
  context-compiler-v0 require it to be declared explicitly, and the v0 compiler records
  (scope refs of kind ENTITY, SIGNAL or RELATIONSHIP) have no carrier for such a
  declaration. P1.3 v0 therefore rejects every empty scope with `EMPTY_SCOPE` and never
  infers plant-wide applicability. Adding it requires a compiler amendment that defines
  an explicit plant-wide scope ref and its review semantics, followed by a version of
  this spec that expands it.

### Composed view

- Derived, non-canonical, rebuildable. Identity is a checksum over the canonical JSON
  of the plant_context_ref; ordered engineering selection refs, each paired with its
  exact input_rule_set and published_rule_set refs (explicitly empty for no selection);
  applicability, grouping and reachability policy ids and versions; the complete
  versioned process-pack grouping definitions (group id, anchor and relationship-kind
  filter); and pinned reachability bounds (maximum hops, entities/result, total result
  size and cycle count). Changing any definition, bound or version changes the
  content-addressed identity. The view is never an input to scope resolution and is
  never written back as a canonical record.
- **Applicability** (`tep-agent-lab.applicability-index/v0`): for each entity, signal and
  relationship, the sorted KnowledgeRef, DocumentRef and RuleRef entries that apply to
  it, each with its association kind and exact version. The v0 policy has three
  association kinds:
  - `SCOPED_TO`: the target appears in the item's resolved scope;
  - `CLAIM_SUBJECT`: the target is one of a Rule-routed item's resolved typed inputs;
  - `BINDING_TARGET`: the target of an approved ENTITY_MENTION / SIGNAL_MENTION binding,
    citing the exact approved binding ref and its original DocumentRef/fragment refs.

  Nothing is inherited beyond these associations: an entity-scoped limit applies only
  to the signal it names, not every signal of that entity. It carries refs only, never
  document or knowledge bodies. Only items in the selected revisions appear;
  rejected or unreviewed candidates are absent by construction.
- **Attachment roles.** For grouping, reachability and `INPUT_OUT_OF_SCOPE`, a signal
  attaches directly to an entity only through `MEASURED_AT` or `ACTUATED_AT`, and to a
  relationship only through `ON_RELATIONSHIP`. `RELATIONSHIP_ENDPOINT` retains the edge
  endpoints as associations only; it never attaches an edge signal to either entity.
- **Grouping** (`tep-agent-lab.topology-grouping/v0`, owner decision): a group is
  derived from a process-pack definition `(group id, definition version, anchor entity,
  included relationship kinds)`. Members are the anchor, its incident relationships of the
  included kinds, the far endpoints of those relationships, and the signals attached to
  the anchor or to those relationships. Each group records its definition, policy
  version and member refs. The v0 process pack defines `reactor-cooling` = anchor
  `reactor`, utility streams. Groups are not a new entity kind and not a parallel graph.
- **Reachability** (`tep-agent-lab.topology-reachability/v0`, owner decision): for each
  entity, the directed downstream and upstream entity sets over Relationships, each with
  its minimum hop count, the relationships traversed, the signals attached to reached
  entities and relationships, and membership in directed cycles: the strongly connected
  components and, within each, its elementary cycles (bounded by a pinned cycle count),
  which identify the recycle loops. Traversal is bounded by a pinned
  maximum hop count (v0: 6), entity count per result (v0: 64), and total result size;
  exceeding a bound is reported, never silently truncated. Every reachability result carries the label
  `TOPOLOGY_ONLY`: it describes structural connectivity, not flow, dynamics, lag,
  magnitude or causation, and it is never evidence.

### Run integration and telemetry re-keying

- Preparation derives the PlantContextRevision from the attested `PROCESS_GRAPH` source,
  resolves the selection and builds the composed view before READY. Any failure fails
  preparation with a typed error; no READY run exists with an unresolved plant context.
- RUN_READY and the run manifest pin the `plant_context_ref`, the ordered engineering
  selection refs (explicitly empty when none) and the composed view identity. The
  CanonicalContextRegistry inventory is unchanged and still READY-frozen.
- New runs' telemetry stores (P0 run-scoped and live) use the `plant_context_ref` as
  their `context_ref`. Snapshot-bound application reads expose it unchanged.
- Only runs prepared after P1.3 record an explicit mapping from their interim
  `context-source:<source_id>:sha256:<checksum>` ref to the derived PlantContextRevision
  in RUN_READY provenance, beside the exact projected source ref and revision ref. The
  interim checksum hashes the entire serialized source ref, not just graph content; it
  cannot recover that content identity on its own. Earlier runs retain their valid
  recorded interim refs; requests to map them report unavailable. Re-keying never
  rewrites old stores, snapshots or RUN_READY, creates no global mapping table, and
  never resolves "latest" or falls back to a new run's mapping.
- Live runs over the same pinned graph pin the same `plant_context_ref` as P0 runs.
- Frozen D0 fixtures and tests remain byte-identical. If a persisted P0 record shape
  must change, it changes under a new version; otherwise the change is a SPEC_CONFLICT.

### Composed-view application read (amendment 2026-10-19)

- Owner decision PCC-OD-10 adds one read-only `get_composed_view` application read,
  specified by Application API §Composed-view read (amendment 2026-10-19). It serves the
  topology, grouping, applicability and topology-only reachability for desktop/portfolio
  rendering through public queries and ApplicationViewService.
- Input is either an exact run id whose RUN_READY pins the plant_context_ref and
  composed-view identity, or both exact identities. Never resolve "latest". Resolve
  the full ordered selection, each revision's rule-set refs, definitions, policies and
  bounds from that identity; any missing or mismatched ref reports unavailable, never
  an empty view. An explicitly empty engineering selection remains valid and disclosed.
- Results retain these exact pins and deterministic ordering, with finite item and byte
  caps enforced before serving; exceeded caps fail without truncation or partial data.
  This static read contains no telemetry and selects no T/K snapshot. Any telemetry
  overlay still requires its own exact TelemetryReadSnapshot under the existing reads.
- The read grants no SIMULATE/MUTATE authority, is not an Agent tool, and changes no
  Agent tool behavior (CL-9/CL-13). Local transport exposure requires a new protocol
  version under Application API §Versioning; existing transport v1 stays unchanged.

### Visibility and authority

- Composition reads only AGENT-visible sources from the run inventory. It never opens
  evaluator truth, hidden bindings, benchmark fixtures or scorer material, and the
  derived records disclose no hidden source ids or counts.
- Applicability, grouping and reachability grant no execution authority, do not change
  Rule `origin × validation × authority`, and install no gate.

### What P1.3 must not do (ADR-004 Decision 4, restated as acceptance constraints)

- re-key or rename ProcessGraph identities;
- hand-edit plant semantics or modify tep-sim or industrial-agent-runtime;
- introduce a mutable "current context" registry or resolver;
- copy rule, document or knowledge bodies into a new store;
- create a combined static-context revision that replaces ContextSnapshot's separately
  pinned refs;
- promote Agent output.

## Testing Decisions

- Good tests assert external behavior at the two seams below: the exact records,
  identities, reports and refs that callers receive, never internal data structures.
  Tests use the pinned packaged ProcessGraph and the P1.2B demo corpus and staged
  revision; they need no evaluator material.
- **Seam 1, the composition core** (owner decision): a pure call over pinned inputs (the
  ProcessGraph source and graph, the engineering selection, each revision's rule sets
  and the policies) that returns the PlantContextRevision, the ScopeResolutionReport and the
  composed view. It runs without a simulator. It covers projection, identity, scope
  resolution, applicability, grouping and reachability.
- **Seam 2, run preparation and snapshot-bound reads** (existing): RunManager.prepare and
  the application reads, including `get_composed_view`. It covers RUN_READY pinning,
  failed preparation on unresolved scope, telemetry `context_ref` re-keying for P0 and live runs, the interim
  mapping, and D0 byte-identity.
- No other new test seam is introduced.
- Prior art: the P1.2B golden tests (strict summaries and checksums of the staged
  revision), the P1.1C snapshot-bound read tests, the LOS-B live session tests (RUN_READY
  provenance, manifest unchanged) and the D0 gate command.

### Acceptance evidence

- **PCC-1 Identity-preserving projection.** The entity, relationship and signal inventories
  contain respectively every node id, edge id and binding semantic entity id exactly
  once, unchanged and with its kind; no invented plant identity appears. Unbound records
  are references excluded from that exactly-once rule: all 17 source entries retain
  their target and reason, including entity `condenser` and relationships `stream_5`,
  `stream_7`, `condenser_outlet` and `separator_vapor`. Every P1.1
  `signal_id` and every P1.2B published target resolves unchanged. SignalDescriptor
  quantity and unit equal the TEP simulation source's binding table for all 53 signals.
  A golden test pins the TEP `plant_context_ref`. Deriving twice yields identical bytes.
  An unpinned or unverified graph, a duplicate id, or a binding to a missing target is
  rejected.
- **PCC-2 Fail-closed scope resolution.** The P1.2B staged revision resolves fully,
  including every approved binding target and rule input: its `compiler-demo.plant`
  carrier and the run's `tep-sim.process-graph` carrier share PROCESS_GRAPH kind,
  `tep-sim.process-graph/v0` schema, `sha256:canonical-json` method and checksum
  `edd41ae4dd7473d2a350ab5f11b694d651d241872ed2ea757f2a38d49426fe09`. Different
  carrier metadata is retained in the report and does not fail resolution. Each Rule
  resolves through its own revision's published set. Mutations of scopes and approved
  binding targets exercise source mismatch, unknown id and wrong kind; other cases cover
  empty scope, unknown rule scope/input, conflicting selected rule sets and an input
  outside scope (a reactor-scoped limit naming a separator signal). SIGNAL X with input
  X and a RELATIONSHIP scope with its attached signal pass; ENTITY scope includes its
  incident edge signals without turning them into entity measurements. Any failure
  rejects the whole selection and produces no composed view.
- **PCC-3 Applicability.** The reactor temperature signal lists exactly the approved
  150 degC normal upper and 175 degC shutdown limits as `CLAIM_SUBJECT` with exact refs;
  the reactor pressure signal lists exactly the 2895 and 3000 kPa limits; neither signal
  lists the other's limits, although all four are scoped to the reactor entity. The
  reactor entity lists all four as `SCOPED_TO`. The rejected 145 degC limit and the
  rejected RCW-IN-TT mention are absent. No bodies are copied. An empty selection
  yields empty applicability, recorded as such. Approved signal/entity mentions expose
  `BINDING_TARGET` entries with exact binding and source citations, including the
  cooling-water outlet temperature mention on its signal.
- **PCC-4 Grouping.** `reactor-cooling` contains exactly `reactor`, the reactor cooling
  water inlet and outlet relationships, their supply and return entities, and exactly
  the six signals attached to them: reactor pressure, level and temperature
  (XMEAS(7), XMEAS(8), XMEAS(9)), the agitator speed actuator (XMV(12)), the cooling
  water outlet temperature (XMEAS(21), on the outlet relationship) and the cooling
  water flow actuator (XMV(10), on the inlet relationship). The group records its
  definition and policy version. Endpoint roles alone add no signal; XMEAS(21) attaches
  only to the outlet relationship, with scope membership as defined in §Scope resolution.
  Changing a definition (anchor or kind filter), its version, a policy version, any
  selected revision's rule-set refs or a reachability bound changes the view identity.
- **PCC-5 Reachability.** Hand-checkable on the 17-node TEP topology: downstream of the
  reactor cooling water supply reaches the reactor at hop 1, the condenser and reactor
  cooling water return at hop 2, and the separator and condenser cooling water return
  at hop 3; the recycle loop (reactor, condenser, separator, compressor, feed mixer) and
  the stripper loop (separator, stripper, feed mixer, reactor, condenser) share nodes, so
  they are reported as one strongly connected component of exactly those six units,
  with both loops listed as its elementary cycles; upstream of the separator includes
  all four feed supplies; bounds are enforced and
  disclosed; every result is labelled `TOPOLOGY_ONLY`.
- **PCC-6 Run integration and re-keying.** A prepared P0 run and a prepared live run over
  the same graph pin the same `plant_context_ref`, while each records its own
  StreamRegistration with its distinct incarnation `source_id`; registering a signal
  absent from the revision fails; snapshot reads report it as
  `context_ref`; an unresolved selection fails preparation and no READY event exists;
  an explicitly empty selection is recorded; the staged demo selection satisfies PCC-2
  and reaches READY despite its different source carrier. A new run's interim ref maps
  through its own RUN_READY mapping; an earlier run's ref remains unchanged and mapping
  reports unavailable. Frozen D0 fixtures and tests are byte-identical (D0 gate command).
- **PCC-7 Canonical publication end to end** (blocked by P1.2D): a canonical
  EngineeringContextRevision published through the P1.2D repository transaction resolves
  and composes, and its limit resolves through scope and RuleRef provenance as LOS-A3
  requires.
- **PCC-8 Composed-view application read.** Reading by a prepared run id or its exact
  plant/view identities returns the same bounded topology, grouping, applicability and
  reachability with exact provenance. Repeated reads remain equal after new runs or
  publications. Missing/mismatched pins and earlier runs without the view report
  unavailable; explicit empty selection is distinguished from failure. Invalid limits,
  item/byte overflow and visibility failures produce typed redacted errors with no
  partial view. No telemetry, Agent tool behavior or execution authority changes.

## Out of Scope

- P1.2D canonical publication (review workflow and repository transaction); P1.3
  consumes its output.
- P1.2C model-assisted extraction (deferred by owner decision 2026-10-12).
- ContextSnapshot assembly, InvestigationContextView and Agent projection (P1.5).
- Operational State features, limit comparison logic and related-signal summaries
  themselves (P1.4A); P1.3 only supplies the refs they cite.
- Cross-revision plant mapping, plant model editing, drawing or P&ID recognition,
  DEXPI import and any upstream plant-model generation.
- An ISA-95-style equipment hierarchy (post-MVP candidate; P1.3 must only not foreclose
  it).
- Inferred dynamics, lag, correlation or causal direction from topology.
- Diagram/P&ID layout coordinates: client presentation assets keyed by ProcessGraph
  ids, never part of the generic core or the revision identity.
- New Agent tools, new application transport methods, and any write or actuation path.

## Further Notes

- The composed view is where the "multi-layer impact" idea lives, as topology-only
  reachability. It narrows which units and measurements are worth checking. Proof still
  comes from observations, branch experiments and explicit evidence links, as the
  investigation specs already require.
- Early P1.4A slices can keep citing the interim graph source and an explicitly empty
  engineering selection; final P1.4A evidence uses PCC-6 and PCC-7.
- The grouping policy is deliberately small (one anchor, one relationship-kind filter).
  A second domain pack would supply its own definitions through the same generic core.

## Recommended implementation split

| Slice | Delivers | Acceptance | Blocked by |
|---|---|---|---|
| PCC-S1 | Generic records and identity; TEP process-pack projection reusing the source binding table; golden ref | PCC-1 | — |
| PCC-S2 | Scope resolution and ScopeResolutionReport against the P1.2B staged revision and rule scope bindings | PCC-2 | PCC-S1 |
| PCC-S3 | Composed view: applicability and grouping | PCC-3, PCC-4 | PCC-S2 |
| PCC-S4 | Composed view: bounded topology reachability with cycles | PCC-5 | PCC-S1 |
| PCC-S5 | Run integration: prepare-time derivation, RUN_READY pinning, StreamRegistration, telemetry re-keying for P0 and live runs, interim mapping, D0 gate | PCC-6 | PCC-S3, PCC-S4 |
| PCC-S6 | End-to-end evidence over a P1.2D canonical publication | PCC-7 | PCC-S5, P1.2D |
| PCC-S7 | Composed-view application read | PCC-8 | PCC-S5 |

PCC-S3 and PCC-S4 can proceed in parallel after their blockers; PCC-S5 needs both,
because the composed view pinned at READY includes applicability, grouping and
reachability. LOS-A-S2 (#41) needs PCC-S5 and P1.2D; LOS-A-S3 (#42) needs PCC-S3
grouping and PCC-S5, and still inherits its own LOS-A-S2 dependency.

## Owner decisions (2026-10-12, 2026-10-13, 2026-10-19)

| ID | Decision |
|---|---|
| PCC-OD-1 | Two test seams: the pure composition core and the existing prepare / snapshot-bound read path. No other new seam. |
| PCC-OD-2 | Subsystem grouping is derived from the plant topology by a versioned grouping policy, not authored as a parallel graph or reviewed knowledge item. |
| PCC-OD-3 | PlantContextRevision is derived at preparation and content-addressed; it is not stored as a separate repository artifact. |
| PCC-OD-4 | Directed topology reachability (hops, cycles) is part of the composed view, labelled topology-only. |
| PCC-OD-5 (2026-10-13) | Amend plant-telemetry-contract-v0 with §Source binding levels: the plant revision holds a static source definition; each stream incarnation registers against it; the effective binding is resolved through the exact StreamRegistration. All runs over the same plant semantics share one `plant_context_ref`. |
| PCC-OD-6 (2026-10-19; PCC-OQ-1) | Only `reactor-cooling` (anchor `reactor`, utility streams), matching the P1.2 corpus subsystem; more groups need a later decision. |
| PCC-OD-7 (2026-10-19; PCC-OQ-2) | Maximum 6 hops and 64 entities per result, which covers the full 17-node TEP topology while staying bounded for larger packs. |
| PCC-OD-8 (2026-10-19; PCC-OQ-3) | `tep.process-graph/<ProcessGraph kind>` for entities and relationships. |
| PCC-OD-9 (2026-10-19; PCC-OQ-5) | In the RUN_READY provenance of each new run prepared after P1.3, beside the `plant_context_ref`; no global mapping table. Re-keying is never retroactive: an earlier run's interim ref stays valid as recorded and mapping reports unavailable. Nothing resolves "latest". |
| PCC-OD-10 (2026-10-19; PCC-OQ-4 changed) | P1.3 adds one bounded read-only application read of the exactly pinned composed view for desktop/portfolio topology, grouping, applicability and reachability. It grants no SIMULATE/MUTATE authority, is not an Agent tool and changes no Agent tool behavior. Diagram/P&ID layout coordinates remain client presentation assets keyed by ProcessGraph ids, outside the generic core and revision identity. |

## Open questions for owner decision

None.

## SPEC_CONFLICT

An implementation must stop and report SPEC_CONFLICT if a deliverable would require
re-keying ProcessGraph ids, editing plant semantics, modifying tep-sim or
industrial-agent-runtime, a mutable current-context resolver, copying knowledge bodies,
a combined static revision replacing ContextSnapshot's separate refs, reading evaluator
or hidden material, or changing frozen D0 fixtures.
