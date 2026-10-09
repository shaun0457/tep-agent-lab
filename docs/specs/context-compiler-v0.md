# Context Compiler v0 — Engineering Knowledge Contextualization

Status: **DRAFT FOR P1.2 DESIGN REVIEW**  
Owner repo: tep-agent-lab  
Milestone: P1.2

Depends on the Industrial Context Platform North Star, MVP maturity guardrail,
Plant Context + Telemetry v0, Playground backend v0, Knowledge Rule Registry v0,
and ADR-003.

## Purpose

P1.2 defines the first trustworthy Engineering Knowledge contextualization path.

This is not:

~~~text
PDF -> chunks -> embeddings -> prompt
~~~

The intended flow is:

~~~text
exact engineering source
  -> source fragments
  -> typed extraction
  -> semantic grounding
  -> Candidate Context
  -> deterministic validation / conflict detection
  -> focused human review
  -> immutable reviewed Engineering Context revision
~~~

LLM/VLM output may propose extraction or linking candidates. It is never canonical
truth by itself.

## Hard trust invariant

> Candidate Context and Canonical Context are different trust domains.

No model-generated binding, claim, operating limit, control relation, procedure
relation, or troubleshooting statement becomes trusted context solely because it
has a high confidence score.

Canonical publication requires an explicit versioned validation path.

## Scope

P1.2 covers one bounded subsystem and a deliberately small engineering corpus.

Initial source families may include:

~~~text
subsystem / operating description
troubleshooting SOP
control-loop description
operating limits
alarm / response guidance
maintenance guidance or observation
~~~

Whole-factory ontology induction and arbitrary-document support are non-goals.

## Ownership

P1.2 composes existing owners and must not replace them.

| Concern | Owner |
|---|---|
| exact repository-backed source inventory | CanonicalContextRegistry / ContextSourceRef |
| plant topology / current TEP semantics | ProcessGraph |
| structured engineering rules | RuleRegistry / RuleRef |
| rule extraction provenance | ExtractionMetadata / SourceVersion |
| run/investigation state | RcaState / ObservationRecord |
| evidence semantics | HypothesisEvidenceLink |
| runtime authority | industrial-agent-runtime |
| telemetry | P1.1 source/store/reader contracts |

The Context Compiler owns candidate generation, semantic grounding, review workflow
metadata, and immutable publication of reviewed engineering-context selections.

It must not create a second ProcessGraph, RuleRegistry, RcaState store, or runtime
authority path.

## RuleRegistry reuse

Structured claims that naturally become limits, constraints, operating relations,
or rule-shaped engineering guidance must reuse the existing Rule model.

The current code already supports SourceVersion, ExtractionMetadata,
literature_candidate(...), inert candidate Rules, RuleRef, and immutable RuleRegistry
snapshots.

P1.2 must compose this path rather than create an EngineeringRuleStore or another
canonical rule schema.

A candidate Rule is inert. Review/publication never mutates an existing Rule version
in place.

## Exact source input

Compiler input starts from an exact trusted ContextSourceRef or a future equivalent
immutable connector reference.

The compiler cannot accept an arbitrary local path and silently treat it as
canonical engineering material.

A source revision change is a new input. It cannot rewrite historical interpretation
of an older source revision.

## SourceFragmentRef

P1.2 introduces the logical SourceFragmentRef concept.

A fragment identifies a bounded exact portion of one immutable source, for example:

~~~text
document section
paragraph
table or row range
procedure step range
structured JSON subtree
drawing/source region
~~~

Conceptually it contains:

~~~text
fragment_id
source_ref
source_location
content_checksum
fragment_kind
fragmentation/parser policy version
~~~

Fragment identity must be deterministic for the same exact source revision and
fragmentation policy. Normalized parser text is derived material and must retain the
exact original source ref.

## Typed extraction

The first implementation distinguishes at least:

~~~text
ENTITY_MENTION
SIGNAL_MENTION
OPERATING_LIMIT
CONTROL_RELATION
PROCEDURE_GUIDANCE
TROUBLESHOOTING_GUIDANCE
MAINTENANCE_OBSERVATION
~~~

This is a minimal MVP extraction vocabulary, not a complete industrial ontology.

Unknown material may remain unsupported/unclassified. It must not be forced into an
incorrect known type.

## CandidateBinding

CandidateBinding proposes a mapping from a source mention to existing plant semantics.

Conceptually:

~~~text
candidate_id / version
mention_fragment_ref
mention text / normalized mention
target_kind: ENTITY | SIGNAL | RELATIONSHIP
proposed_target_id
alternative_target_ids[]
extraction method/version
grounding method/version
confidence?
supporting_fragment_refs[]
conflicting_fragment_refs[]
validation_findings[]
review_status
~~~

The proposed target must be resolved against an exact selected plant-context
revision.

A candidate must not silently invent a new plant entity when no target exists.
Unresolved mentions stay unresolved until a later explicit entity-creation contract.

## CandidateKnowledge

CandidateKnowledge proposes an engineering statement contextualized to plant
semantics.

Conceptually:

~~~text
candidate_id / version
claim_kind
normalized claim
scoped entity/signal/relationship refs[]
source_fragment_refs[]
extraction method/version
confidence?
conditions / assumptions
typed units/value fields where applicable
conflicts[]
validation_findings[]
review_status
~~~

If the knowledge is naturally representable by the existing Rule contract, the
canonical structured form must be Rule/RuleRef, not a duplicate claim truth store.

Non-rule reviewed knowledge may remain a reviewed KnowledgeRef for later P1.3
composition.

## Candidate immutability

Candidate versions are immutable.

A source revision change, parser/extractor change, prompt/model retry that changes
content, grounding policy change, or provenance change produces a new candidate
version/ref. Historical candidates are not overwritten.

## Confidence semantics

Confidence is review-routing metadata only.

It is not:

~~~text
truth probability
authority
verification
evidence strength
execution permission
auto-promotion permission
~~~

The score method/version must be retained.

For v0, model-confidence-only auto-promotion is forbidden.

## Semantic grounding

Grounding may use:

~~~text
exact canonical ids
reviewed aliases
tag dictionaries
ProcessGraph names / quantities / units
known source mappings
deterministic normalization
bounded LLM-assisted candidate ranking
~~~

LLM-assisted grounding proposes candidates only. The compiler preserves which
evidence and method produced every proposed target.

## Multi-source resolution

Multiple sources may support one canonical identity.

Example:

~~~text
SOP:      Cooling Water Inlet Temperature
tag list: TT_403 / RCW_IN_TEMP
OPC UA:   Reactor/Cooling/Inlet/Temperature
P&ID:     TT-403
             |
             v
candidate canonical signal identity
~~~

No single source string or model guess establishes identity automatically.

Supporting and conflicting refs remain independently traceable.

Cross-source disagreement must become an explicit validation/review finding, not a
silent highest-confidence winner.

Examples include duplicate tags, different equipment scopes, unit disagreement,
competing operating limits, revision disagreement, and conflicting control direction.

## Deterministic validation

Before review/publication, deterministic validators check at least:

~~~text
exact source ref resolves
fragment checksum/location valid
candidate target exists in selected plant revision
target kind is compatible
typed units/value shape valid where applicable
visibility is not widened
candidate identity/version is unique
Rule candidates satisfy the existing Rule schema
conflicts are explicit rather than overwritten
~~~

A validation failure cannot publish partial canonical context.

## Human review

Human verification is a first-class product capability.

The v0 decision vocabulary is:

~~~text
APPROVE
REJECT
REMAP
KEEP_CANDIDATE
~~~

APPROVE accepts the candidate under exact source, plant-context, compiler and review
policy versions. It does not grant runtime authority.

REJECT preserves the auditable candidate but excludes it from trusted publication.

REMAP selects a different already-existing canonical target while preserving the
original proposal and reviewer rationale.

KEEP_CANDIDATE leaves the item unresolved/unverified. It cannot enter trusted
canonical context.

## ReviewRecord

Review decisions are immutable.

Conceptually:

~~~text
review_id
candidate_ref
decision
selected_target_ref?
reviewer_id
reason
reviewed_at
review_policy_version
source / plant-context refs
~~~

A superseding decision creates a new record rather than mutating historical review
evidence.

## Review routing

The review queue should prioritize:

~~~text
unresolved target
multiple plausible targets
cross-source conflict
new alias / tag
unsafe operational implication
low or uncalibrated confidence
unit/value conflict
rule/limit disagreement
new causal/control relation
~~~

v0 may require review for every candidate while the policy is being validated.

Any later automatic acceptance must be explicit, versioned, tested, auditable, and
prefer deterministic conditions over model confidence.

## Publication

Review does not mutate "the current context" in place.

Publication creates an immutable EngineeringContextRevision.

Conceptually:

~~~text
revision_id
parent_revision_ref?
selected plant-context revision
source inventory refs[]
compiler version
parser/extraction versions
grounding policy version
validation policy version
review policy version
approved binding refs[]
approved knowledge refs[]
pinned RuleRefs[]
review refs[]
conflict/exclusion summary
publication provenance
~~~

The revision references existing canonical owners rather than copying their full
bodies.

P1.3 will compose this reviewed Engineering Context with broader canonical plant
context.

Publication is atomic: either a complete immutable revision is created, or nothing
is published. Rejected, unresolved, or invalid candidates never enter the approved
set.

## Visibility

A derived candidate or reviewed record cannot become more visible than its required
supporting sources.

Evaluator-only sources cannot create Agent-visible published knowledge.

Agent-visible outputs must not leak hidden source ids/counts.

## Structured context versus exact document content

Do not collapse document bodies into the graph.

Maintain two responsibilities:

~~~text
structured context:
  meaning
  scope
  canonical bindings
  reviewed RuleRefs
  validation / authority / provenance

exact source/content:
  paragraphs
  tables
  procedures
  drawings/source regions
~~~

BM25, embeddings, vector indexes, or other search indexes are derived retrieval aids.
They are not canonical truth.

## Incremental compilation

The trust model must support future incremental compilation:

~~~text
SOP rev.2 -> Engineering Context r12

SOP rev.3
  -> semantic/source diff
  -> affected fragments
  -> Candidate Context Patch
  -> review
  -> Engineering Context r13
~~~

The first implementation may recompute the small MVP corpus, but source/candidate/
review identity must be strong enough that later incremental compilation does not
change trust semantics.

## Agent-generated knowledge

Agent conclusions do not directly become trusted engineering knowledge.

Future direction:

~~~text
Agent conclusion / repeated pattern
  -> candidate operational knowledge
  -> normal validation + review
  -> future Engineering Context revision
~~~

Hard invariant:

~~~text
Agent assertion != canonical truth
~~~

P1.2 does not implement this feedback loop.

## Model/provider boundary

P1.2 selects no mandatory LLM provider.

A bounded model call may assist with typed extraction, mention normalization,
candidate ranking, claim normalization, or reviewer-facing conflict explanation.

The deterministic application shell owns exact source selection/versioning, schema
validation, candidate identity, review state, publication, visibility, target
validation, and provenance retention.

Do not implement a free-running autonomous multi-agent compiler in P1.2.

Preferred v0 pattern:

~~~text
deterministic pipeline
+ bounded model calls where useful
+ deterministic validators
+ explicit human review
~~~

## Initial P1.2 vertical slice

Use one bounded subsystem and a corpus small enough to review completely.

The first demo must contain:

1. one unambiguous entity/signal grounding;
2. one ambiguous binding requiring review;
3. one structured operating/control/troubleshooting claim;
4. one source conflict or competing candidate;
5. one reviewed RuleRegistry-backed claim where appropriate;
6. one rejected/unresolved candidate that never enters publication.

TEP may provide first plant semantics, but generic compiler fields must not require
XMEAS/XMV/IDV. Those ids may occur only in source-specific provenance/binding
evidence.

## Acceptance criteria

P1.2 is complete when one reproducible test/demo proves:

1. an exact registered engineering source is resolved;
2. deterministic fragments retain exact provenance;
3. typed extraction produces immutable candidates;
4. candidate targets bind to an exact plant-context revision;
5. at least one ambiguous candidate cannot auto-promote;
6. APPROVE/REJECT/REMAP/KEEP_CANDIDATE are immutable review records;
7. REMAP preserves original proposal and reviewer rationale;
8. rejected/unresolved candidates are absent from approved publication;
9. rule-shaped knowledge reuses existing Rule/RuleRegistry contracts;
10. publication is immutable and atomic;
11. every approved item traces to source fragments, extraction/grounding versions,
    validation findings and review records;
12. confidence grants neither truth nor authority;
13. evaluator-only sources cannot enter Agent-visible publication;
14. source/compiler/review-policy changes create a new revision;
15. no telemetry, runtime, ProcessGraph, RuleRegistry or RcaState owner is duplicated.

## Non-goals

P1.2 does not implement:

~~~text
whole-factory ontology induction
arbitrary CAD/P&ID OCR
unbounded document ingestion
generic vector-RAG platform
full document management
model-only auto-promotion
new runtime memory
Agent tool migration
ContextSnapshot
State Interpreter
"What is happening now?" summarization
OPC UA / historian connector
MES / ERP / CMMS
autonomous plant action
~~~

## Recommended implementation split

~~~text
P1.2A
source fragment + candidate + review/publication contracts

P1.2B
deterministic parser/fixture path for one small engineering corpus

P1.2C
typed model-assisted extraction / semantic grounding behind the same contracts

P1.2D
review workflow + immutable Engineering Context revision demo
~~~

P1.3 then composes reviewed engineering context with canonical plant semantics.

## SPEC_CONFLICT

Stop and report SPEC_CONFLICT if implementation would require:

- model output to directly mutate trusted context;
- a second RuleRegistry or ProcessGraph;
- changing existing Rule authority semantics;
- using CanonicalContextRegistry as mutable compiler state;
- hiding provenance to simplify schema;
- treating a vector index as canonical storage.

Do not invent a local bypass.
