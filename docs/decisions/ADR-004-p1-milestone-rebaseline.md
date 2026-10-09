# ADR-004 — Re-baseline P1 sequencing for the Industrial Context Platform

- Status: Accepted
- Date: 2026-10-10
- Scope: P1 milestone numbering and sequencing; one explicit P1.2 scope amendment;
  a milestone-independent semantic-ownership rule; the P1.2/P1.3 boundary
- Supersedes: milestone numbering and sequencing in
  [ADR-003 §Implementation boundary](ADR-003-industrial-environment-boundary.md) and
  [plant-telemetry-contract-v0 §Frozen implementation sequence and acceptance](../specs/plant-telemetry-contract-v0.md)
- Amends (scope only, Decision 5): the upstream/deferred extraction statements in
  ADR-003 §API, adapters, and repository boundaries and plant-telemetry-contract-v0
  §Context Builder boundary
- Constrains (Decision 4): context-compiler-v0 and the future P1.3 owning spec.
  PlantContextRevision semantics stay owned by plant-telemetry-contract-v0
  §Canonical Plant Context
- Replaces: the unmerged draft ADR-004 in PR #24 (head `bf14ec2`), which must be dropped
  from that PR
- Base reviewed: `41cbae6dd2a6e2b70d29c4029289a968093c0058`
- Evidence: [program alignment review 2026-10-10](../reviews/program-alignment-2026-10-10.md)

## Context

Three incompatible meanings of "P1" exist:

1. The cross-repository implementation plan
   (`shaun0457/tep-sim:docs/ecosystem/implementation-plan.md`, Program Re-baseline v1)
   uses **P1 = Investigation UI, after D0**.
2. ADR-003 and `plant-telemetry-contract-v0.md` froze ownership/telemetry/snapshot
   semantics **and** a sequence: P1.2 Engineering Context binding from a fixture set,
   P1.3 ContextSnapshot, P1.4 Investigation Context integration, P1.5 dirty-stream,
   P1.6 first external connector.
3. The North Star and the MVP maturity guardrail use P1.0–P1.7 with P1.2 = Context
   Compiler, P1.3 = Canonical Context composition, P1.4 = context serving + operational
   state, P1.5 = ContextSnapshot + investigation integration. Both documents declare
   themselves non-normative until adopted.

PR #24 surfaced the conflict and drafted an ADR-004. The audit found that draft
incomplete: it declared itself sequencing-only while widening ADR-003's extraction scope;
it dropped the old P1.5 (dirty-stream) and P1.6 (connector) rows without a disposition;
it left live ingestion and the P0/application telemetry migration without an owner; it
did not cover milestone numbers embedded in semantic text; and it did not say which
milestones are on the Level-1 MVP critical path. This ADR replaces it.

## Decision 1 — Milestone numbers never own semantics

A milestone number states **when** work is scheduled. What a concept **means** is owned
by the section of its owning spec or ADR. Implementing a concept earlier or later never
changes its semantics, and an implementation milestone never becomes a concept's owner.

Example: ContextSnapshot semantics are owned by
`plant-telemetry-contract-v0.md` §ContextSnapshot, whichever milestone implements the
record first.

Future specs cite this ADR when they assign work to P1.x. Semantic text should not embed
milestone numbers; where older text does, Decision 3 maps it.

## Decision 2 — Authoritative P1 sequence

| Milestone | Scope | Owning contract(s) | L1 MVP path |
|---|---|---|---|
| P1.0 | Industrial context / telemetry ownership and snapshot semantics | ADR-003, plant-telemetry-contract-v0 | yes |
| P1.1A | Generic telemetry core: samples, append store, T/K snapshots, bounded reader | plant-telemetry-contract-v0 | yes |
| P1.1B | TEPSimulationSource adapter | plant-telemetry-contract-v0 | yes |
| P1.1C | Canonical telemetry integration: event-time-bounded scans; P0 run ingestion of reference-world records through TEPSimulationSource into a run-scoped store; snapshot-bound current/history through the public P0/application query boundary | plant-telemetry-contract-v0 §Existing architecture consistency and migration obligations; playground-backend-v0; ADR-002 | yes |
| P1.2 | Engineering Knowledge Context Compiler (sources, fragments, typed extraction, grounding, Candidate Context, validation, review, immutable EngineeringContextRevision) | context-compiler-v0 (PR #24, not yet accepted); knowledge-rule-registry-v0 for any Rule-source extension | yes |
| P1.3 | Canonical Context composition (Decision 4) | plant-telemetry-contract-v0 §Canonical Plant Context and §Engineering Context; owning spec to be written | yes |
| P1.4 | Context serving + deterministic Operational State for one subsystem | plant-telemetry-contract-v0 (reads, ContextSnapshot semantics); owning spec to be written | yes |
| P1.5 | ContextSnapshot + Investigation Context integration: InvestigationContextView, Agent ContextProjection integration, Agent telemetry/knowledge tool migration, evidence provenance bridge | plant-telemetry-contract-v0 §ContextSnapshot, §Investigation Context composition; investigation-state-v0; tool-surface-v0 | yes |
| P1.6 | Validated investigation write-back as Candidate Knowledge; no automatic promotion | North Star §Feedback loop; context-compiler candidate path | no (post-L1) |
| P1.7 | Second non-TEP source/domain and abstraction review | ADR-003 two-domain rule | no (Level-2 gate) |

Level 1 also requires work that is not P1-numbered and is not renumbered here:

- runtime **B5** first real provider (`industrial-agent-runtime`, D-049);
- one real-model investigation over the P1.5 path, without a capability claim;
- MVP integration: the E1 UI substrate plus context, operational-state, investigation
  and "why did the Agent know this?" panels.

`docs/program-status.md` tracks their state.

## Decision 3 — Disposition of every superseded row and embedded reference

| Old reference | New home | Semantics |
|---|---|---|
| P1.1 | P1.1A/B (done) + P1.1C (added) | unchanged |
| P1.2 Engineering Context binding from a TEP fixture/reference set | P1.2, widened by Decision 5; a small fixture/reference corpus stays the first P1.2 input | unchanged |
| P1.3 ContextSnapshot | P1.5; the record may be implemented earlier under Decision 1 | unchanged |
| P1.4 Investigation Context integration, including live current/history through public P0/application reads | Live telemetry current/history through public P0/application reads: P1.1C. ContextSnapshot + RcaState -> InvestigationContextView for Application and Agent reads: P1.5 | unchanged, including "must not bypass P0 with private world access" |
| P1.5 Dirty-stream simulation/replay and tests | Temporal-leakage, late, out-of-order, duplicate and same-tick tests on the live path: P1.1C (Decision 6). Injected dirty-stream replay harness: deferred telemetry-robustness milestone, required before any non-simulated source (Level 2); unnumbered until scheduled | unchanged; plant-telemetry §Delivery and dirty streams still binds |
| P1.6 First external connector (OPC UA or historian replay) | Deferred to Level 2, after dirty-stream robustness; unnumbered until scheduled | unchanged |
| plant-telemetry: "P1.4 must evolve the owning public P0/application query boundary to expose bounded reader results" | P1.1C (application reads) and P1.5 (Agent reads) | obligation unchanged |
| plant-telemetry: "later engineering fixture/reference work is planned here" | P1.2 | unchanged |
| ADR-003 P1.4 bullet: "Live current/history integration must not bypass P0 with private world access" | binds P1.1C, P1.4 and P1.5 | unchanged |
| `docs/p1-1a-telemetry-core.md`: consumer migration "(P1.4)" | P1.1C and P1.5 | unchanged |

Statements such as "P1.0 implements none of these" remain true as history.

## Decision 4 — P1.2 / P1.3 boundary

This decision records architecture constraints that `context-compiler-v0` and the future
P1.3 owning spec must satisfy. It defines no new record semantics. PlantContextRevision is
the "Canonical Plant Context" of plant-telemetry-contract-v0 §Canonical Plant Context, which
owns its semantics. Until the P1.3 spec is accepted, the constraints below are the binding
boundary.

P1.2 publishes reviewed engineering knowledge. It does not define plant identities,
assemble a ContextSnapshot, or read telemetry. Until P1.3 exists, its grounding target
("selected plant-context revision") is the exact AGENT-visible `PROCESS_GRAPH`
ContextSourceRef selected for the compilation, and every binding cites
`(that source ref, ProcessGraph id)`.

P1.3 produces exactly:

1. **PlantContextRevision**: the immutable "Canonical Plant Context" revision that
   plant-telemetry §Canonical Plant Context already specifies (`context_ref`, and the
   ContextSnapshot `plant_context_ref`). It is a deterministic, identity-preserving
   projection of the exact pinned `PROCESS_GRAPH` source and its signal bindings into
   PlantEntity / SignalDescriptor / Relationship. It records the ProcessGraph
   ContextSourceRef it projects, which makes the P1.2 reference mapping explicit.
2. **Scope resolution**: deterministic, fail-closed validation that every scope ref of a
   selected EngineeringContextRevision, including Rule-scope bindings, resolves in the
   selected PlantContextRevision.
3. **A derived composed view/index** (entity or signal -> applicable
   KnowledgeRef / DocumentRef / RuleRef) for serving. It is rebuildable and non-canonical.
4. **Telemetry re-keying**: the telemetry store `context_ref` becomes a
   PlantContextRevision ref. Any interim P1.1C ref maps to it through the recorded
   ProcessGraph source ref, never through "latest" metadata.

P1.3 must not:

- re-key or rename ProcessGraph identities; P1.1 signal ids and P1.2 bindings resolve
  unchanged;
- hand-edit plant semantics (changes go through the tep-sim ProcessGraph review path);
- introduce a mutable "current context" registry or resolver;
- copy rule, document or knowledge bodies into a new store;
- create a combined static-context revision that replaces ContextSnapshot's separately
  pinned refs;
- promote Agent output.

Naming. New types are not named `CanonicalContext*`:

| Term | Meaning |
|---|---|
| `CanonicalContextRegistry` (P0, code) | run-scoped, attested source inventory frozen at READY |
| `EngineeringContextRevision` | immutable reviewed compiler publication; defined by context-compiler-v0 once accepted |
| `PlantContextRevision` | the plant-telemetry "Canonical Plant Context" revision (semantics owned there); first implemented in P1.3 |
| "Canonical Context" (North Star, maturity guardrail) | product phrase for reviewed, pinned revisions; not a type |

## Decision 5 — P1.2 scope amendment

ADR-003 places the Plant Context Builder upstream ("Automatic drawing/document extraction
is upstream/deferred, not the portfolio wedge"), and plant-telemetry §Context Builder
boundary keeps extraction out of P1.0. This ADR amends that **scope** with one carve-out.

In scope for P1.2: review-gated compilation of a small engineering-knowledge text corpus
(operating description, troubleshooting SOP, control-loop description, operating limits,
alarm or maintenance guidance) for one bounded subsystem. The pipeline is a deterministic
shell with bounded model assistance, deterministic validation, explicit review and
immutable publication.

Still out of scope:

- generating the plant graph: ProcessGraph stays tep-sim-owned and human-verified (D-003);
- drawing, P&ID or CAD OCR/VLM extraction (D-003, D-019);
- arbitrary-document breadth.

Trust semantics are unchanged. Extraction never directly establishes trusted mappings or
execution authority, candidates stay separate from verified mappings, and confidence is
not proof.

## Decision 6 — Ordering constraints

- P1.1C changes application reads only. Agent tools keep their current behavior until P1.5.
- P1.1C acceptance re-proves, through the live P0 wiring rather than the store alone, the
  hard temporal-leakage invariant and late, out-of-order, duplicate-redelivery and same-tick
  behavior. Only the injected dirty-stream replay harness (gaps, BAD/UNCERTAIN injection,
  clock skew, disconnect) is deferred, and it is required before any non-simulated source.
  Until then, MVP materials state that live data is a clean deterministic simulated stream.
- Every P1.4 read pins an exact TelemetryReadSnapshot and exact context revisions; no read
  resolves "latest". If P1.4 needs a persisted assembly identity, it implements the
  ContextSnapshot record as specified (Decision 1); no second assembly-record type.
- P1.5 Agent-facing changes (snapshot-bound tool reads, engineering context in
  ContextProjection) ship as new tool, projection or benchmark versions. Frozen D0 fixtures
  and their tests stay byte-identical.
- Engineering context reaching a blind benchmark run is evaluation-v0 capability **C6**.
  It needs a versioned leakage-audit extension, and a capability claim needs clean C1–C5
  baselines first (D-018, OQ-11).

## Decision 7 — Product and research tracks

- Product track: P0/P1 milestones, E-series clients, MVP integration.
- Research track: D-series benchmark and evaluation (D0.2C, D0 freeze, D1, D2).

D0 does not own product sequencing and does not block P1.1C–P1.5. D0 blocks only Agent
capability or superiority claims (C0 required, D-030) and D1 benchmark runs. Runtime B5
blocks both D1 and the MVP real-model step, and may start now.

## What is not changed

- `TelemetryReadSnapshot` T/K semantics, four-clock separation, sample identity,
  late/out-of-order behavior and the hard temporal-leakage invariant;
- TEP-specific identifiers stay below generic boundaries; generic contracts never import TEP;
- ProcessGraph ownership; RuleRegistry ownership and `origin × validation × authority`;
- `CanonicalContextRegistry` as the run-scoped attested source inventory frozen at READY;
- `Observation != Evidence`; RcaState / Hypothesis / HypothesisEvidenceLink / Experiment /
  InvestigationReport ownership;
- Application API != Agent Tool Surface; runtime gates and tool authority;
- the two-domain rule before any universal industrial adapter extraction;
- every ContextSnapshot / InvestigationContextView / evidence-provenance requirement in
  plant-telemetry-contract-v0.

## Consequences

Positive:

- one normative place for P1 sequencing, with every old row and embedded reference mapped;
- live telemetry integration has an owner (P1.1C) and can run in parallel with P1.2;
- the P1.3 deliverable is concrete and cannot become a second graph, registry or store;
- research work proceeds without blocking product work, and product demos make no
  capability claims without research evidence.

Costs:

- ADR-003 and plant-telemetry-contract-v0 now carry supersession notes that readers must
  follow;
- P1.1C adds one milestone before P1.4 can read live telemetry.

## Governance follow-ups (not done here)

- The cross-repository Decision Register
  (`shaun0457/tep-sim:docs/ecosystem/decision-register.md`) stops at D-050 and does not
  record ADR-002, ADR-003, ADR-004, the North Star adoption or D0.x decisions. Its
  `implementation-plan.md` still uses P1 = Investigation UI and E1 = HAZOP. Dependency
  repositories are unchanged by this ADR. A follow-up tep-sim PR should register these
  decisions or explicitly delegate lab-owned decisions to `tep-agent-lab/docs/program-status.md`.
- PR #24 drops its draft ADR-004 and its ADR-003 / plant-telemetry notes, then rebases
  onto this ADR.

## Revisit trigger

Revisit when P1.7 brings a second domain, when the Level-1 scope changes, or when an
implementation emits a `SPEC_CONFLICT` against this sequencing.
