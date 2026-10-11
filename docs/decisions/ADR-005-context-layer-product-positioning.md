# ADR-005 — The industrial context layer is the product; D1 measures context value

- Status: Accepted
- Date: 2026-10-10
- Scope: product positioning (what the product is and who consumes it); a three-layer
  target profile (domain-neutral context core, domain packs, workflows) and its mapping
  onto existing milestones; the research-track redirect of D1 and D2; MVP exit-criteria
  wording; live plant mode as an MVP-required P1.4 item; post-MVP candidates
- Constrains: the future P1.3, P1.4 and P1.5 owning specs (Decisions 1 and 2); the
  future D1 owning spec (Decision 3)
- Amends (wording and clarification only): `docs/product-maturity-and-mvp.md`
  §Required MVP capabilities (a clarification note under C), §Portfolio MVP exit
  criterion, §Product maturity summary and §MVP implementation priority, as listed under
  Consequences. The exit-criteria substance is unchanged.
- Does not supersede: any accepted spec, ADR-003, or ADR-004 sequencing
- Base reviewed: `e5ccf19b3bfe4d45c9bbfb36bcacbd0805adf6f0`

## Context

This repository started as an agent-RCA research lab. Its first blueprint
(2026-09-17) and its README describe an "Autonomous Industrial Process Investigator":
a Main Agent that inspects a TEP process, forms hypotheses, runs counterfactual
experiments and links evidence. The early specs (investigation state, rule registry,
hypothesis/experiment, tool surface, tool bridge, benchmark, evaluation) and the
hard rules in `AGENTS.md` all serve that Agent. The research plan measured Agent
capability levels (D1) and then orchestration architectures (D2).

The Industrial Context Platform direction came later. The North Star, the maturity
guardrail and ADR-003 were added on 2026-10-07; ADR-004 (2026-10-10) made the P1
context milestones the Level-1 critical path. The documents still talk mostly about
the Agent, because they were written first and because the Agent remains the most
demanding reader. That leaves the product framing ambiguous: is the product the Agent,
or the context the Agent reads?

A second ambiguity concerns domain. TEP is a continuous chemical process, so the
existing assets use process-industry concepts (ProcessGraph, streams, control loops,
and later P&IDs, DEXPI and HAZOP). A unified OT/IT context graph for brownfield plants
(machines, MES, BI) serves discrete manufacturing at least as much. The product
identity should not be tied to either domain's vocabulary.

The owner has decided. This ADR records three decisions and the minimal wording
changes they require.

## Decision 1 — The product is the industrial context layer

The product is the **industrial context layer**: the domain-neutral contracts of the
first three North Star engines,

```text
Context Compiler -> Canonical Industrial Context -> Context Server / ContextSnapshot
```

Decision 2 calls this the **context core**. The Agent / Investigation Runtime is a
**consumer** of that layer, not the product. It is the first consumer and the strictest
one: it needs exact T/K bounds, visibility filtering, leakage discipline, runtime gates
and per-turn provenance. A context layer that satisfies the Agent can serve other
consumers. The reverse is not guaranteed.

Other consumers must be possible without changing the context layer:

| Consumer | Reads through (existing owner) | When |
|---|---|---|
| Agent / Investigation Runtime | ContextProjection, Agent tool surface, runtime gates (investigation-state-v0, tool-surface-v0, plant-telemetry §Investigation Context composition) | L1 (P1.5) |
| Engineers and the UI | Application API (ADR-002); InvestigationContextView for application reads | L1 (P1.1C, P1.4, P1.5, MVP integration) |
| Evaluation harnesses | evaluator-side reads under benchmark-case-v0 visibility and leakage rules | D1 |
| External systems (MES, BI, process mining) | later API or event-stream adapters around the same contracts | unscheduled (see Gaps) |

Constraints on the future P1.3, P1.4 and P1.5 owning specs:

- Context records carry no consumer-specific semantics. Consumer shaping (Agent
  projection, UI view, evaluator view) happens in derived views and projections that
  the owning specs already name.
- Every consumer reads exact, pinned revisions and snapshots through an owning public
  boundary. No consumer resolves "latest".
- Investigation state stays owned by RcaState, ObservationRecord,
  HypothesisEvidenceLink, Experiment and InvestigationReport (investigation-state-v0,
  hypothesis-experiment-v0, engineering-records-v0). The context layer must not grow a
  second investigation state model. The North Star already states this; this ADR makes
  it a product-level rule.
- Application API != Agent Tool Surface still holds. Adding a consumer grants it no
  execution authority.
- Transport stays deferred (ADR-003 §API, adapters, and repository boundaries). External
  APIs and event streams, when scheduled, are replaceable adapters around the context
  contracts. They are not domain architecture.

## Decision 2 — Target profile: context core, domain packs, workflows

The target profile has three layers. Only the first is the product's identity.

```text
Layer 3  Workflows and applications (RCA = MVP; others unscheduled)
           process: RCA (MVP wedge), HAZOP assistance, MOC impact
           discrete: downtime/OEE RCA, quality traceability, multi-site rollout
                         | consume core + one pack
Layer 2  Domain packs
           process pack (current, via TEP)  |  discrete-manufacturing pack (candidate)
                         | semantic extension, source compilers/adapters, derived state
Layer 1  Domain-neutral context core  <- the product
           compiler pipeline, canonical entity/relationship/signal model,
           telemetry, ContextSnapshot, visibility, Context Server serving and
           projection contracts (consumers sit outside the core)
```

### Layer 1 — Domain-neutral context core (the product)

The core contains:

- the **compiler pipeline**: source -> candidate -> review -> immutable revision, with
  exact provenance; LLM output is never canonical (context-compiler-v0);
- the **canonical entity / relationship / signal model**: PlantEntity, SignalDescriptor,
  Relationship, DocumentRef, KnowledgeRef and RuleRef, published as PlantContextRevision
  (plant-telemetry §Canonical Plant Context; ADR-004 Decision 4);
- the **telemetry contracts**: clocks, SignalSample, append store, T/K snapshots, bounded
  reads (plant-telemetry-contract-v0);
- **time-consistent ContextSnapshots** (plant-telemetry §ContextSnapshot);
- the **visibility rules** (P0 CanonicalContextRegistry, context-compiler-v0 visibility
  taint, benchmark leakage rules);
- the **Context Server serving and projection contracts**: the public read boundaries and
  derived views through which consumers read pinned context.

Consumers are not part of the core. The Agent, the UI and, later, external systems
reached through APIs and event streams (Decision 1) sit in the workflow and application
layer and read the core through those contracts.

The core carries no domain vocabulary. This extends the existing rule "TEP semantics stay
below the generic boundary; generic contracts never import TEP" (ADR-003, ADR-004 §What
is not changed) to **"domain semantics stay in domain packs"**. The accepted contracts
already satisfy it: PlantEntity and Relationship kinds are extensible and namespaced, not
a reactor/stream/valve enum (plant-telemetry §Canonical Plant Context).

Candidate shared anchor (not decided here). An ISA-95-style equipment hierarchy
(enterprise / site / area / line or process cell / unit or work cell) is used by both
process and discrete plants. It is a post-MVP candidate anchor for the canonical model.
P1.3 does not adopt it and gains no Level-1 requirement from it; P1.3 only must not
foreclose adding it later. Adopting it needs a later owning spec.

### Layer 2 — Domain packs

A domain pack adds three things to the core:

1. a **semantic model extension**: namespaced entity, relationship and signal kinds;
2. **source compilers and adapters**: telemetry sources and engineering-source
   extraction for that domain;
3. **derived-state features**: the domain's deterministic operational-state features.

| Pack | Status | Semantic extension | Sources and adapters | Later candidates |
|---|---|---|---|---|
| Process | current, through TEP | ProcessGraph, streams, instruments, control loops, operating limits | TEPSimulationSource; P1.2 engineering text corpus | P&ID/diagram recognition (P&ID -> graph); DEXPI import |
| Discrete manufacturing | candidate, not chosen | line / cell / machine / station hierarchy | PLC tag mapping; MES work orders and routing; BOM and material genealogy | possibly Asset Administration Shell import |

ProcessGraph is the **process-domain instance** that feeds the canonical model. It is not
the generic model. This matches the accepted contracts: plant semantics belong to "domain
canonical sources; TEP uses existing ProcessGraph/registries" (plant-telemetry
§Scope and ownership), and PlantContextRevision is an identity-preserving projection of the pinned
ProcessGraph source into the generic PlantEntity / SignalDescriptor / Relationship records
(ADR-004 Decision 4). Until P1.3 exists, P1.2 grounds directly against ProcessGraph ids
(ADR-004 Decision 4; context-compiler-v0). That is an interim process-pack binding.
PlantContextRevision preserves those ids, so it needs no change.

A pack is a conceptual boundary, not an extracted abstraction. No generic pack
interface, and no universal `IndustrialEnvironmentAdapter`, is extracted before a second
domain exists (ADR-003 two-domain rule). The second domain is not chosen here (see Open
questions for the owner).

### Layer 3 — Workflows and applications

Workflows consume the core plus one pack.

- RCA is the MVP wedge. It is the Level-1 real-model investigation over the P1.5 path
  and MVP integration (ADR-004 Decision 2), and this ADR does not change that.
- Every other workflow is an example candidate and is not scheduled: process HAZOP
  assistance, MOC impact analysis and recovery; discrete downtime/OEE RCA, quality
  traceability and multi-site rollout of improvements.

### Mapping onto existing milestones

| Layer | Engines | Milestones | In P1 today | Not in P1 (see Gaps) |
|---|---|---|---|---|
| Core: compilation and canonical model | Context Compiler + Canonical Context | P1.2, P1.3 | review-gated compilation of a small engineering text corpus for one subsystem (ADR-004 Decision 5); PlantContextRevision, scope resolution and a derived composed view (ADR-004 Decision 4) | plant-model generation from documents; corpus breadth; incremental re-compilation (L2) |
| Core: telemetry and serving | Telemetry + Context Server / ContextSnapshot | P1.1, P1.4, P1.5 | canonical telemetry for one simulated source; context serving and deterministic operational state; ContextSnapshot and investigation integration | external consumer APIs and event streams; IT-system sources |
| Process pack | TEP adapter, ProcessGraph, P1.2 corpus | P1.1B, P1.2, P1.3 | everything the MVP uses | diagram recognition, DEXPI import, HAZOP, MOC |
| Discrete pack | none | none | nothing | the whole pack |
| Workflows | Investigation Runtime | P1.5 + MVP integration | RCA | every other workflow |

### Differentiators the current contracts already give

1. **Exact source provenance.** Every published item and every derived observation
   cites exact source revisions and locations (context-compiler-v0;
   plant-telemetry §Investigation Context composition and evidence provenance).
2. **Human-reviewed, immutable revisions.** Candidate and Canonical Context are
   separate trust domains. LLM output is never canonical; publication needs an
   effective review and produces a new immutable revision (context-compiler-v0 §Hard
   trust invariant).
3. **Time-consistent ContextSnapshots.** A snapshot pins event horizon T, ingest cutoff K
   and every context revision. Late data never rewrites an earlier view, and replay
   never substitutes today's latest context (plant-telemetry §ContextSnapshot).
4. **Visibility and leakage discipline.** Derived context takes the most restrictive
   visibility of its inputs. Evaluator truth never reaches Agent-visible consumers or
   projections, and engineering context enters a blind run only as C6 under a versioned
   leakage-audit extension
   (context-compiler-v0 §Benchmark use; ADR-004 Decision 6).
5. **Measured context value.** D1 measures what each layer of context is worth to a
   fixed consumer (Decision 3).

None of these depends on a domain.

This profile is a direction, not a scope change. P1 and the Level-1 MVP keep their
current scope. ADR-003 §Positioning ("P1 is not a P&ID/OCR product, document
digitization product, generic ETL platform, or complete MES/ERP context platform")
and ADR-004 Decision 5 (plant-graph generation and drawing/P&ID/OCR/VLM extraction
stay out of P1.2) remain in force. Scheduling any pack candidate, or any breadth beyond
the Level-2 plan, needs an owner decision and, where it touches those statements, its
own ADR that amends them explicitly.

## Decision 3 — Research-track redirect

1. **Finish D0.2C** as specified in benchmark-comparative-pilot-v0: D0.2C1 comparable
   fixtures, scorer-v1 and leakage policy v2; D0.2C2 deterministic C0; D0.2C3
   identifiability. C0 and identifiability are prerequisites for any valid measurement.
   The D0 freeze criteria and ADR-004 Decision 7 are unchanged: D0 still blocks D1 runs
   and any capability claim.

2. **D1 becomes a context-value study.** D1 no longer asks "which Agent capability
   level is best". It asks "how much does each layer of context improve a fixed
   investigation?"
   - Held fixed: the Agent (model, provider, settings, prompt template), one
     orchestration condition, budgets, and the paired cases and seeds.
   - Default design (recommended; needs no spec amendment): run every evaluation-v0
     capability row, C0 to C6, and classify each step by the one factor that
     evaluation-v0 §Capability axis says it adds:

     | Step | Factor added (evaluation-v0) | D1 reading |
     |---|---|---|
     | C0 | deterministic no-Agent enumerate/simulate/match baseline | floor; not a step |
     | C0 -> C1 | not a single factor: the non-Agent baseline becomes an Agent with static compact LLM context | no contrast |
     | C1 -> C2 | read telemetry/history tools | telemetry context value (secondary metric) |
     | C2 -> C3 | non-answer-leaking process topology tools | topology context value (primary metric) |
     | C3 -> C4 | first Tool Bridge analysis tools | not context; reported, no context claim |
     | C4 -> C5 | counterfactual simulation tools | not context; reported, no context claim |
     | C5 -> C6 | external/validated rule/knowledge evidence, here compiled, reviewed engineering context from a published EngineeringContextRevision | engineering context value (primary metric) |

     Telemetry is part of the context core (Decision 2, Layer 1), so C1 -> C2 is read
     as telemetry context value. evaluation-v0 defines C2 by its tools, not by the
     canonical telemetry path. Until P1.5 migrates Agent telemetry tools to
     snapshot-bound reads (ADR-004 Decision 6), C2 tools read the existing bounded TEP
     history. A C1 -> C2 result from such runs measures telemetry access, not the
     canonical context path, and the D1 spec records the tool version of every run.
   - Reported per arm: accuracy (E2 CausalClaim match, including `NO_ABNORMAL_CAUSE`);
     evidence quality (§Evidence/reference scoring); query waste (E3 relevant and
     irrelevant observation query rate, E4 efficiency); traceability (trace
     completeness, and the share of final claims whose evidence chain resolves to exact
     ContextSnapshot and source refs). C0 accuracy and cost are reported for every case.
   - Results are descriptive until the sample size supports paired inference
     (evaluation-v0 §Randomization / stochasticity).
   - D1 produces context-value evidence only. It is not Agent capability evidence,
     and while D2 is parked no Agent capability claim is made.

   The default follows the existing rules, and this ADR does not reinterpret them.
   evaluation-v0 defines the rows cumulatively (C6 = C5 + validated knowledge) and
   requires one intended factor per comparison, so C5 is the comparator for C6.
   ADR-004 Decision 6 and context-compiler-v0 §Benchmark use each require engineering
   context to enter a blind benchmark run only as C6, under a versioned leakage-audit
   extension. ADR-004 Decision 6 also states: "a capability claim needs clean C1–C5
   baselines first". Whether that rule applies to a context-value contrast is an open
   question (OQ-16); this ADR does not decide it. The default runs C1–C5 in any case.

   **SPEC_CONFLICT alternative (not adopted).** A context-only arm on top of C3
   (C3 + engineering context, without C4/C5) would vary only context with fewer runs.
   It conflicts with the rules above and would need amendments to evaluation-v0, to
   ADR-004 Decision 6 and to context-compiler-v0 §Benchmark use. Unless OQ-16 concludes
   that "a capability claim needs clean C1–C5 baselines first" does not apply to
   context-value contrasts, it would also need an explicit change to that rule. This
   ADR makes none of these changes. The owner picks between the default and this
   alternative (OQ-16); the recommendation is the default.

   The C6 arm consumes product outputs: a published EngineeringContextRevision (P1.2),
   its scope resolution (P1.3) and Agent-facing snapshot-bound reads and projection
   (P1.5), shipped as new tool, projection and benchmark versions. Frozen D0 fixtures
   stay byte-identical (ADR-004 Decision 6). The product track does not wait for D1
   (ADR-004 Decision 7).

3. **D2 (orchestration ablation) is parked.** It is not scheduled and no work starts on
   it. The orchestration axis in evaluation-v0 and hard rules 18–19 keep their meaning
   for any future study. Un-parking needs an owner decision. B4 is not required for
   Level 1 or for D1.

4. **B5 stays, minimized.** Runtime B5 delivers one adequate real provider. That
   provider serves the MVP real-model step and every D1 arm, frozen per study. Provider
   comparison stays OQ-12 and is not part of B5.

**Owner decision — 2026-10-11 (scheduling only).** The research track (D0.2C and D1)
is paused to focus on the context layer's two halves: "what the plant is" (P1.2 Context
Compiler, P1.3 canonical plant context) and "what is happening in the plant" (P1.1C
canonical telemetry, P1.4 Operational State and live plant mode). Decision 3, the D0
freeze criteria, C0 as the mandatory baseline and ADR-004 Decision 7 are unchanged;
on resumption, D0 still blocks D1 runs and any capability claim. No capability claim
is made meanwhile. Runtime B5, the investigation Agent's first real provider, is
deferred until the context layer is in place; it remains required for the MVP exit
criterion's real-model investigation. D1 design and baseline-rule questions remain
open, deferred with the track (OQ-16).

## What is not changed

- Every accepted spec, including evaluation-v0 (capability and orchestration axes,
  invariants), benchmark-design-v0, benchmark-case-v0, benchmark-comparative-pilot-v0,
  context-compiler-v0 and plant-telemetry-contract-v0;
- ADR-003 ownership, telemetry, visibility, authority and snapshot semantics, its
  §Positioning for P1 and its two-domain rule;
- ADR-004 sequencing (Decision 2), the P1.2/P1.3 boundary (Decision 4), the P1.2 scope
  amendment (Decision 5), the ordering constraints (Decision 6) and the track rules
  (Decision 7);
- the hard rules in `AGENTS.md`;
- the in-flight P1.1C and P1.2A work and their owning contracts;
- frozen D0 fixtures (`rca-dev-001..004`) and their tests, which stay byte-identical;
- the D0.2C design, the D0 freeze criteria and C0 as the mandatory strong baseline;
- ProcessGraph ownership (tep-sim, human-verified) and RuleRegistry ownership;
- the MVP domain: Level 1 stays on the process pack through TEP.

## Consequences

### MVP stays on the process pack

The Level-1 MVP stays on the process pack through TEP, because the existing assets
(simulator, ProcessGraph, telemetry source, D0 fixtures) are there. This ADR does not
choose a second domain. The Level-2 requirement "at least one non-TEP source/domain"
and P1.7 are unchanged; which domain fills them is an open question for the owner.

### Live plant mode is MVP-required

MVP capability C requires telemetry "delivered continuously through the canonical
telemetry path", and exit criterion 4 requires live telemetry to enter through the
canonical SignalSample/store/reader contracts. ADR-004 Decision 2 scopes P1.1C as P0 run
ingestion of reference-world records into a run-scoped store, with snapshot-bound reads.
No accepted spec yet requires a plant that keeps running and streaming after READY, so
this part of capability C needs the P1.4 owning spec assigned below. This is not a new
requirement; it gives an existing one a name.

**Live plant mode** is MVP-required. The only required item is the one capability C
already states: TEP runs as a long-lived simulated plant and its live data enters the
canonical telemetry path continuously, through TEPSimulationSource into the canonical
telemetry store. The existing telemetry semantics then apply unchanged: every read and
every ContextSnapshot pins T and K over the growing stream, and samples committed later
never enter an earlier snapshot (plant-telemetry §Hard temporal leakage invariant).

Recommended demonstration (not an acceptance condition): the evaluator or harness may,
for example, inject an incident mid-stream so the investigation starts from live data.
If it does, injection is harness authority, never an Agent tool, and its identity and
timing stay EVALUATOR-only (hard rules 1, 9 and 11; ADR-001). Neither the MVP exit
criteria nor this ADR prescribe how or when an incident is produced.

Scheduling. Owner decision — 2026-10-11: live plant mode belongs to P1.4, split into
P1.4A (serving + Operational State) and P1.4B (live plant mode: TEP as a long-lived
simulated plant streaming continuously through TEPSimulationSource into the canonical
telemetry store). This follows ADR-004 Decision 1 (milestone numbers never own semantics)
and Decision 2 (the sequence table). The constraints below are unchanged and now bind
the P1.4 owning spec, which remains to be written.

Constraints the owning spec must keep:

- P0 lifecycle: the source inventory and RunManifest stay frozen at READY. Streamed
  samples are run data read through pinned TelemetryReadSnapshots, not new canonical
  sources (playground-backend-v0).
- Delivery modes (DETERMINISTIC, ACCELERATED, REAL_TIME) change pacing only, never
  physics, values, identities or event order (plant-telemetry §Delivery and dirty
  streams).
- ADR-004 Decision 6: the plant is still a simulated source, so the injected dirty-stream
  replay harness is not triggered. That harness stays required before any non-simulated
  source, and until then MVP materials state that live data is a clean deterministic
  simulated stream.
- ADR-004 Decision 6: Agent tools keep their current behavior until P1.5, and frozen D0
  fixtures stay byte-identical. Live plant mode changes no benchmark case.

### MVP exit-criteria wording

The exit criteria keep their substance. Their wording now centres on "the context layer
makes the investigation possible and traceable". Original wording changes on 2026-10-10 in
`docs/product-maturity-and-mvp.md` (the live plant mode scheduling decision above now
resolves the owner question):

| Location | Before | After |
|---|---|---|
| §Portfolio MVP exit criterion, lead sentence | "Level 1 is achieved only when one visible demo proves:" | "Level 1 is achieved only when one visible demo proves that the context layer makes one investigation possible and traceable:" |
| §Portfolio MVP exit criterion, item 8 | "a real Agent performs evidence-backed investigation for Q4;" | "a real Agent, as the first consumer of that context, performs evidence-backed investigation for Q4;" |
| §Portfolio MVP exit criterion, item 9 | "every important claim can be traced to exact source/snapshot refs;" | "every important claim remains traceable to exact source/snapshot refs, including context-layer provenance where applicable;" |
| §Product maturity summary, L1 row | "one subsystem: context compilation + live state + ContextSnapshot + real investigation" | "one subsystem: context compilation + live state + ContextSnapshot that make a real investigation possible and traceable" |
| §MVP implementation priority, step after P1.5 | "D1 / investigation capability" / "real model + evidence-backed RCA" | "B5 + one real-model investigation over the P1.5 path" / "first context consumer; no capability claim" |
| §MVP implementation priority, new entry after P1.4 | (none) | "live plant mode" / "MVP-required; owning milestone (P1.4 or a new P1.x) decided by the owner" |
| §Required MVP capabilities, C. Live operational state, new note after the first sentence | (none) | "Clarification (ADR-005 §Live plant mode): 'continuously' means a long-lived simulated plant whose live data keeps entering the canonical telemetry path. It is MVP-required and not yet scheduled. Injecting an incident mid-stream is one possible demonstration, not an acceptance condition." |

The B5 row also corrects existing drift: ADR-004 Decision 2 already puts B5 and one
real-model investigation without a capability claim on the Level-1 path, not D1. A
pointer to this ADR is added under §Portfolio MVP exit criterion. No other sentence
changes.

### Owning specs for P1.3, P1.4 and P1.5 are written early

The P1.3, P1.4 and P1.5 owning specs define the product's consumer-facing contracts.
They should be written early, in parallel with the P1.1C and P1.2A implementation,
rather than just before each milestone starts. This changes no implementation order:
ADR-004 Decision 2 still sequences the milestones. Each spec states how it meets the
Decision 1 constraints and keeps domain vocabulary in the process pack (Decision 2). The
P1.3 spec does not adopt an ISA-95-style hierarchy anchor (a post-MVP candidate) and only
avoids foreclosing it. The P1.4 spec covers P1.4A and P1.4B and
should show the same serving contract feeding both an application read and the Agent
read assembly.

### Gaps against the target profile (unscheduled candidate milestones)

These are candidates, not milestones. None is scheduled or numbered by this ADR, and
none is on the Level-1 path. Scheduling one needs an owner decision and, where it
conflicts with ADR-003 §Positioning or ADR-004 Decision 5, a new ADR.

| Candidate | Layer | Current position |
|---|---|---|
| External consumer APIs and event streams (MES, BI, process mining) | core | transport deferred (ADR-003); Level 4 lists MES/ERP/CMMS adapters |
| Plant-model generation from engineering documents | core pipeline + pack compilers | out of P1 (ADR-004 Decision 5) |
| Shared equipment-hierarchy anchor (ISA-95-style) | core | post-MVP candidate for a later owning spec; P1.3 does not adopt it and only avoids foreclosing it |
| Non-TEP / brownfield connectors, incl. mixed legacy systems | pack adapters | already Level 2 (P1.7; deferred dirty-stream harness and first external connector) |
| Diagram / P&ID recognition (P&ID -> graph) | process pack | out of P1.2 (ADR-004 Decision 5; D-003, D-019); ProcessGraph stays tep-sim-owned and human-verified |
| DEXPI import | process pack | not specified |
| HAZOP assistance, MOC impact analysis | process-pack workflows | hazop-v0 is a research proposal, deferred; no MOC spec |
| Discrete-manufacturing pack (hierarchy, PLC tags, MES work orders and routing, BOM and material genealogy, possibly Asset Administration Shell import) | discrete pack | candidate second domain; not chosen |
| Discrete workflows (downtime/OEE RCA, quality traceability, multi-site rollout) | discrete-pack workflows | depend on the discrete pack |
| OPC UA / MQTT gateway in front of a simulated plant (brownfield-connectivity proof) | pack adapters | equivalent to the deferred first external connector: Level 2, after dirty-stream robustness (ADR-004 Decision 3) |
| MES-like IT source, e.g. production orders driving TEP operating-mode changes (OT + IT unification) | pack adapters | not specified; TEP operating modes stay in the process pack; Level 4 lists MES/ERP/CMMS adapters |
| Outward Context API with event streams, plus an MCP server as an agent-facing interface | core serving contracts | transport deferred (ADR-003); read-only context, no execution authority, evaluator-only content never exposed (ADR-002, Decision 1) |

HAZOP, MOC and DEXPI are process-pack candidates, not core features.

Owner priority: this is a portfolio project, so Level-1 MVP scope wins. Every candidate
above stays unscheduled and post-MVP, and no product-track work beyond the MVP is
scheduled now. The research track keeps its own schedule (ADR-004 Decision 7), paused
by the owner decision of 2026-10-11.

### TEP limitation and honest claims

TEP has no real P&ID and no real engineering corpus. Its ProcessGraph is a small
human-authored graph in tep-sim, and the P1.2 corpus (operating description,
troubleshooting SOP, control-loop description, limits, guidance) must be authored for
the demo. Consequences for claims:

- Demo materials say the engineering corpus was authored for the demo and give its
  authoring provenance, as context-compiler-v0 §Benchmark use already requires.
- No claim about document-understanding accuracy on real plant documents, diagram
  recognition, brownfield readiness or domain neutrality follows from TEP alone. The
  North Star guardrail against claiming "a full industrial context platform from one TEP
  adapter alone" applies, and domain neutrality stays unproven until a second pack exists.
- A D1 C6 result measures the value of this authored context for this case family. It is
  not a general claim about engineering documents. An authored troubleshooting SOP can
  encode the answer a case scores, so the C6 leakage-audit extension and an authoring
  process blind to evaluator truth are preconditions, not formalities.
- TEP is public and well known to models. The memorization mitigations in
  benchmark-design-v0 still apply to every D1 arm.

### Positive

- One stated product: a domain-neutral context core, with the Agent as its first and
  strictest consumer and other consumers possible without redesign.
- Domain-specific features (process or discrete) have a defined home, so neither domain's
  vocabulary leaks into the product identity.
- When resumed, the research track produces product evidence (measured context value)
  instead of a ranking of Agent configurations.
- Parking D2 and B4 reduces near-term research work and resource contention; the
  Level-1 critical path is unchanged (ADR-004 Decision 2 never included them).

### Costs

- D1 depends on product milestones for its C6 arm (P1.2, P1.3, P1.5), so the full study
  cannot run until those land.
- The default D1 design runs the full C0–C6 ladder, more runs than the three context
  arms alone (OQ-16).
- The target profile is broader than anything TEP can demonstrate; honest claims stay
  narrower than the profile.

## Open questions for the owner

- **Second domain (Level 2, P1.7, two-domain rule).** Options:
  - discrete manufacturing: the strongest proof that the core is domain-neutral, at higher
    risk (new hierarchy, MES/BOM sources, no existing assets);
  - an adjacent batch or process domain: lower risk and more reuse of the process pack, but
    a weaker generality proof.
  The choice is not needed for Level 1.
- **Live plant mode owner — resolved (2026-10-11)**: P1.4, split into P1.4A
  (serving + Operational State) and P1.4B (live plant mode), under the ADR-004 rules.
- **D1 design** (OQ-16; open, deferred with the research track): the default full ladder
  with every step classified (recommended), or the SPEC_CONFLICT alternative of a C3-based context-only arm, which
  needs amendments to evaluation-v0, ADR-004 Decision 6 and context-compiler-v0.
- **Baseline rule** (OQ-16; open, deferred with the research track): whether "a capability
  claim needs clean C1–C5 baselines first" applies to context-value contrasts.

## Governance follow-ups (not done here)

- The cross-repository Decision Register in tep-sim does not record ADR-002 to ADR-005.
  The ADR-004 follow-up still stands.
- When written, the D1 owning spec resolves OQ-16 and cites this ADR.

## Revisit trigger

Revisit when D1 produces its first context-value results, when the second domain is
chosen, when a gap candidate is proposed for scheduling, when the Level-1 scope changes,
or when an implementation emits a `SPEC_CONFLICT` against this positioning.
