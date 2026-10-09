# Industrial Context Platform — Product North Star

Status: product/architecture north star; non-normative until adopted by owning specs/ADRs  
Owner repo: `tep-agent-lab`  
Purpose: prevent product/architecture drift across P1/P2 implementation prompts and reviews.

MVP scope and product maturity gates are defined separately in [`product-maturity-and-mvp.md`](product-maturity-and-mvp.md). Use that document to decide whether a proposed feature advances the current maturity level or is premature platform breadth.

## North Star

The long-term product is an **Industrial Context Platform**.

The first deep vertical slice is **investigation / RCA**, but the architecture should support a broader operational context layer over time.

The core product idea is:

```text
Engineering Knowledge
+
Plant Semantics / Topology
+
Time-series / Live State
+
Evidence-backed Investigation
```

The product should make it possible to answer:

> What could the Agent know at this point in time, from which engineering and operational sources, under which revisions/visibility rules, and why did it reach this conclusion?

The investigation-first wedge is intentionally narrower than a full MES/ERP/OT platform, but the architecture should not be designed as a TEP-only RCA demo.

## Product decomposition

Use three main conceptual engines:

```text
Context Compiler
      |
      v
Canonical Industrial Context
      |
      v
Context Server
      |
      v
Investigation Runtime
```

### 1. Context Compiler

The Context Compiler turns heterogeneous industrial source material into smaller, typed, versioned, reviewable context.

It is conceptually similar to a compiler:

```text
raw industrial sources
        |
        v
parse / normalize
        |
        v
typed extraction
        |
        v
semantic grounding / linking
        |
        v
deterministic consistency checks
        |
        v
Candidate Context
        |
        v
human / deterministic validation
        |
        v
Canonical Context Revision
```

LLMs/VLMs may generate extraction and linking candidates. They are **not sources of truth**.

### 2. Context Server

The Context Server assembles task- and time-specific context from canonical semantics, engineering knowledge and telemetry.

It should support multiple retrieval modes:

```text
graph retrieval
structured metadata/rule retrieval
temporal/time-series retrieval
semantic/full-text document retrieval
```

Graph/context semantics decide **where and what is eligible**. Semantic retrieval may then decide **which exact source passage** is relevant.

The Context Server produces a time-consistent `ContextSnapshot`, not a giant "latest data" prompt.

### 3. Investigation Runtime

The investigation subsystem remains responsible for:

```text
Observation
Hypothesis
Evidence
Experiment
Decision / Diagnosis
InvestigationReport
```

Existing `RcaState`, `ObservationRecord`, evidence-link, experiment and engineering-record contracts remain the owners of investigation state.

The Context Platform supplies trusted, traceable context; it must not create a second RCA state model.

## Source landscape

The architecture should eventually accept sources such as:

```text
Engineering:
  P&ID / CAD / DEXPI-like graph
  SOP
  manuals
  alarm guides
  control descriptions
  operating limits

OT:
  PLC / OPC UA
  historian
  SCADA
  MQTT
  sensor/tag metadata

IT / operations:
  MES
  ERP
  CMMS
  QMS
  work orders
  operator / maintenance notes
```

P1 does not need to implement every connector. It should define the architecture so they can become adapters around common semantic/context contracts.

## Engineering knowledge contextualization

Raw documents are not canonical engineering knowledge.

Do not treat this as:

```text
PDF -> chunks -> embeddings -> LLM
```

The intended pipeline is:

```text
Raw Engineering Sources
        |
        v
Source Processing
OCR / parser / layout / VLM where needed
        |
        v
Document Blocks / Structured Source Fragments
        |
        v
Typed Engineering Extraction
        |
        +-- entity mentions
        +-- signal mentions
        +-- procedures
        +-- operating limits
        +-- alarm / troubleshooting guidance
        +-- control / causal statements
        +-- maintenance observations
        |
        v
Semantic Grounding
        |
        v
Candidate Bindings / Candidate Claims
        |
        v
Validation / Human Review
        |
        v
Canonical Engineering Context
```

## Candidate Context versus Canonical Context

This distinction is mandatory.

A model-generated entity link, signal link, rule, procedure binding or causal statement is initially a **candidate**.

Example:

```text
source mention:
  "cooling-water control valve"

candidate entity:
  reactor_cooling.flow_actuator

possible signal:
  XMV(10)

confidence:
  0.78

source:
  Manual rev.7 section 5.4

status:
  NEEDS_REVIEW
```

This must not silently become `VERIFIED`.

Canonical context is published only after an explicit validation path.

Possible outcomes:

```text
APPROVE
REJECT
REMAP
KEEP_CANDIDATE
```

Every approved change produces or participates in a new immutable context revision.

## Human verification

Human verification is a first-class product capability, not a fallback after model failure.

The system should route review effort toward:

```text
ambiguity
conflicting aliases
new/unresolved entities
low-confidence bindings
cross-document disagreement
unsafe operational implications
unverified causal/control relations
```

High-confidence deterministic matches may be auto-accepted only under an explicit/versioned policy.

The goal is not "LLM extracts 10,000 claims and an engineer manually checks all 10,000".

The goal is:

```text
automatic candidate generation
+
deterministic validation
+
confidence / conflict routing
+
focused human review
```

## Multi-source semantic resolution

A strong context compiler should resolve meaning across multiple independent sources.

Example:

```text
SOP:
  "Cooling Water Inlet Temperature"

Tag list:
  TT_403
  RCW_IN_TEMP

OPC UA model:
  Reactor/Cooling/Inlet/Temperature

P&ID:
  TT-403
```

These may support one canonical `SignalDescriptor`.

No single LLM guess should establish that identity.

Resolution evidence should preserve all supporting source refs and uncertainty.

## Canonical knowledge representation

Do not force every document paragraph into a graph node.

Use a hybrid representation.

### Canonical graph / structured context

Store semantic facts and bindings such as:

```text
PlantEntity
SignalDescriptor
Relationship
KnowledgeRef
DocumentRef
RuleRef
validated mappings
authority / validation / provenance
```

### Content/document store and search index

Keep exact source material such as:

```text
paragraphs
tables
procedure text
drawings
manual sections
maintenance narratives
```

as immutable source content with exact refs.

The graph answers:

> What does this knowledge mean and what does it apply to?

Document/content retrieval answers:

> What exact source material supports or explains it?

## Rule ownership

Reuse the existing `RuleRegistry`.

Structured engineering claims, limits, policies and constraints should continue to preserve:

```text
origin
validation
authority
scope
inputs
source refs
validation refs
behavior
```

Do not create a second engineering-rule truth store.

Conceptually:

```text
unstructured/semi-structured source
    -> DocumentRef / KnowledgeRef

validated structured claim/constraint
    -> RuleRegistry / RuleRef
```

## ContextSnapshot

A `ContextSnapshot` represents the exact industrial context available for one consumer/task at a specific investigation point.

It should reference rather than duplicate canonical owners.

Conceptually:

```text
ContextSnapshot
  plant_context_ref

  telemetry_snapshot_ref
    event horizon T
    ingestion cutoff K

  engineering_knowledge_refs[]
  document_refs[]
  rule_refs[]

  visibility / projection policy
  assembly policy/version
  assembly/query provenance
```

The snapshot must answer:

> What information was available to the Agent at that time?

Historical replay must not resolve "latest" documents, rules or telemetry when old pinned revisions are unavailable.

## Context serving

An Agent should not receive the entire plant graph, all documents and all time series.

A typical context-serving flow is:

```text
investigation question
        |
        v
entity/task grounding
        |
        v
graph neighborhood / relevant subsystem
        |
        +--> relevant signals
        |       |
        |       v
        |   bounded time-series read
        |
        +--> KnowledgeRef / RuleRef
        |       |
        |       v
        |   structured engineering guidance
        |
        +--> DocumentRef
                |
                v
          exact semantic/full-text retrieval
        |
        v
ContextSnapshot
        |
        v
Agent / Application
```

## Evidence and "Why did the Agent know this?"

Every observation derived from engineering knowledge or telemetry should retain sufficient source/snapshot refs to explain its origin.

For telemetry:

```text
TelemetryReadSnapshot
signal/sample refs
context ref
tool/service request
```

For engineering knowledge:

```text
KnowledgeRef / DocumentRef / RuleRef
exact source revision/location
context ref
tool/service request
```

The UI should eventually be able to answer:

> Why did the Agent know this?

with exact:

```text
plant/topology source
telemetry window
knowledge cutoff
SOP/manual/rule source
observation ref
evidence link
```

The existing invariant remains:

```text
Observation != Evidence
```

## Incremental context compilation

The context compiler should eventually work incrementally.

Example:

```text
SOP rev.2
      |
      v
Context r12

SOP rev.3
      |
      v
semantic diff
      |
      v
affected entities/signals/rules
      |
      v
Candidate Context Patch
      |
      v
review
      |
      v
Context r13
```

Do not rebuild the plant context from zero after every source revision.

Every change must remain traceable.

## Feedback loop

Investigation outcomes may generate **candidate operational knowledge**.

Example:

```text
Context
  -> Agent investigation
  -> Observation / Evidence / Decision / Outcome
  -> Candidate knowledge
  -> validation
  -> future Canonical Context revision
```

Critical invariant:

```text
Agent assertion != canonical truth
```

An Agent conclusion or repeated pattern can propose new context; it cannot automatically promote itself into trusted engineering knowledge.

## Portfolio and product positioning

The architecture should be broad enough to represent a full industrial context layer, while the MVP remains investigation-first.

This project is not primarily:

```text
a P&ID/OCR product
a document digitization product
a generic ETL pipeline
a vector-RAG demo
a full MES/ERP replacement
```

An upstream system may already provide a verified engineering/plant graph. That output can be a source for Canonical Plant Context.

The project's differentiation should come from combining:

```text
verified/versioned engineering semantics
+
temporal operational state
+
ContextSnapshot / knowledge cutoff
+
evidence provenance
+
counterfactual investigation
+
Agent investigation runtime
```

## Full architecture, narrow first slice

The desired strategy is:

> Full context-platform architecture; one deep implementation vertical slice.

The first slice is:

```text
TEP simulated plant
        |
        +-- ProcessGraph / plant semantics
        +-- live time series
        +-- small engineering-knowledge corpus
        |
        v
Context Compiler / Canonical Context
        |
        v
Context Server / ContextSnapshot
        |
        v
real Agent investigation
        |
        v
Observation
Hypothesis
Evidence
Experiment
Diagnosis
```

This is intentionally deeper than implementing many shallow enterprise connectors.

## TEP boundary

TEP is the first environment/source used to prove the architecture.

TEP-specific implementation is allowed in the TEP adapter, benchmark and lab.

Generic product contracts must not require:

```text
XMEAS
XMV
IDV
TEP ControlMode
TEP disturbance vocabulary
```

Direction:

```text
TEP adapter -> generic context/telemetry contracts
generic contracts !-> TEP
```

Do not extract a universal `IndustrialEnvironmentAdapter` until a second non-TEP domain/source exists and actual commonality has been observed.

## Existing owners that must not be duplicated

Future work must preserve existing ownership.

```text
ProcessGraph / semantic registries
RuleRegistry
CanonicalContextRegistry / P0 source visibility
TimeSeriesStore / telemetry contracts
RcaState
ObservationRecord
HypothesisEvidenceLink
Experiment
InvestigationReport
runtime tasks/tools/budgets/gates/traces
```

Context Compiler and Context Server compose and reference these owners; they do not replace them.

## P1 product direction

> Sequencing authority: [ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md) adopts this
> progression, adds P1.1C (canonical telemetry integration), assigns the old dirty-stream and
> connector milestones, and marks the Level-1 path. Where this list and ADR-004 differ,
> ADR-004 governs.

The preferred progression is:

```text
P1.0
Context architecture / ownership / snapshot semantics

P1.1
live telemetry foundation

P1.2
engineering-knowledge ingestion and contextualization
  parsing
  typed extraction
  semantic grounding
  candidate bindings
  review

P1.3
context graph / canonical context composition

P1.4
context serving
  graph retrieval
  structured retrieval
  document retrieval
  temporal retrieval

P1.5
ContextSnapshot / investigation context integration

P1.6
decision/evidence write-back as candidate knowledge

P1.7
second non-TEP source/domain and abstraction review
```

External connector breadth may be added when it helps prove portability, but connector count is not the primary MVP metric.

## Guardrails for future prompts and reviews

Reject or require explicit design review when a change:

- turns TEP-specific identifiers into generic contracts;
- treats LLM extraction/linking as verified truth;
- creates a second ProcessGraph, RuleRegistry or RCA state store;
- collapses documents into vector chunks without semantic/provenance bindings;
- gives historical investigations "latest" context instead of pinned revisions;
- lets late telemetry retroactively appear in an earlier snapshot;
- promotes Agent-generated claims into canonical knowledge without validation;
- optimizes for connector breadth before the investigation vertical slice works;
- makes UI/application reads execution authority;
- claims a full industrial context platform from one TEP adapter alone.

If an implementation requirement conflicts with an owning frozen spec/ADR, use the existing `SPEC_CONFLICT` process rather than silently changing semantics.

## Portfolio acceptance signal

A compelling MVP should demonstrate all of the following in one visible flow:

1. engineering source material is contextualized into typed candidate knowledge;
2. ambiguous semantic bindings are reviewable by a human;
3. approved context becomes a new immutable canonical revision;
4. live telemetry is joined through a time-consistent snapshot;
5. an Agent investigates using topology + engineering knowledge + time series;
6. observations and evidence retain exact provenance;
7. the UI can explain why the Agent knew each relevant fact;
8. TEP-specific labels remain confined to the adapter/lab implementation.

That is the product direction future P1/P2 work should preserve.
