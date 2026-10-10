# Context Compiler v0 — Engineering Knowledge Contextualization

Status: accepted

Owner repo: tep-agent-lab

Milestone: P1.2 ([ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md))

Depends on Plant Context + Telemetry v0, Playground backend v0, Knowledge Rule
Registry v0, ADR-002, ADR-003 and ADR-004. The North Star and the MVP maturity
guardrail are non-normative direction only.

ADR-004 owns P1 sequencing. Its Decision 5 is the scope amendment that puts this
compiler in scope, and its Decision 4 is the P1.2 / P1.3 boundary this spec follows.
ADR-003 and the telemetry contract remain authoritative for the ownership,
visibility, telemetry and snapshot semantics they freeze.

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
| serialized rule-set revision (`ENGINEERING_RULE_SET`) | Knowledge Rule Registry v0 §Rule-set source revision |
| plant identities before P1.3 | the selected `PROCESS_GRAPH` ContextSourceRef (ADR-004 Decision 4) |

The Context Compiler owns candidate generation, semantic grounding, the compiler
workspace, review workflow metadata, and immutable publication of reviewed
engineering-context selections. Review and publication are Application operations
(see Human review).

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

Context-compiler APPROVE means extraction/grounding fidelity was accepted under the
pinned source and plant-context revisions. It does **not** promote Rule validation or
authority. A Rule routed through the compiler retains the literature-candidate axes
(`origin=LITERATURE`, `validation=NONE`, `authority=REFERENCE`) unless the existing
Rule-governance/promotion path separately publishes a new Rule version.

Rule-shaped publication pins two rule-set revisions, each an `ENGINEERING_RULE_SET`
source as defined in Knowledge Rule Registry v0 §Rule-set source revision:

- the **input rule-set revision** (R0): the exact ContextSourceRef the compile session
  froze. Grounding, validation and review ran against R0, and the compatibility tuple
  uses R0.
- the **published rule-set revision**: the one every pinned RuleRef resolves through.
  It is R0 when the publication adds no Rule. Otherwise it is a new revision R1 whose
  parent is R0, containing every rule and conflict record of R0 unchanged plus the
  approved inert candidate Rules.

R1 is committed in the same atomic publication as the EngineeringContextRevision,
under the same review gate (see Publication). It never edits an existing
`(rule_id, version)`, and `Rule.scope` matching is unchanged.

The Context Compiler does not construct an alternative RuleRegistry truth store.
Publication fails if a pinned RuleRef cannot resolve through the published rule-set
revision, or if R1's parent is not R0.

## Exact source input

Compiler input starts from an exact trusted ContextSourceRef or a future equivalent
immutable connector reference.

The compiler cannot accept an arbitrary local path and silently treat it as
canonical engineering material.

A source revision change is a new input. It cannot rewrite historical interpretation
of an older source revision.

## Compiler workspace

Candidates and ReviewRecords live in a compiler workspace: an append-only,
non-canonical store owned by the Context Compiler.

- It is not a ContextSourceRef kind and is never registered in a run's
  CanonicalContextRegistry. Consumers cannot resolve it.
- Candidate versions and ReviewRecords are immutable; the review log is append-only.
- Each compile session builds and freezes its own CanonicalContextRegistry instance
  over the exact attested sources it reads (engineering sources, the `PROCESS_GRAPH`
  source, the input rule-set revision, any alias dictionary). It never reuses or mutates a
  run's READY registry.
- Only publication produces canonical material: the EngineeringContextRevision
  artifact and, when Rule items are approved, a new rule-set revision, both committed
  to this repository.

The storage medium is a P1.2A implementation choice, provided it keeps these
properties.

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

This is a minimal MVP extraction vocabulary, not a complete industrial ontology and
not a permanently closed enum. Future namespaced kinds may be added under a versioned
extraction policy.

Unknown material may remain unsupported/unclassified. It must not be forced into an
incorrect known type.

The v0 routing policy is deterministic and versioned:
- ENTITY_MENTION / SIGNAL_MENTION -> CandidateBinding;
- OPERATING_LIMIT / CONTROL_RELATION -> existing inert literature-candidate Rule path;
- PROCEDURE_GUIDANCE / TROUBLESHOOTING_GUIDANCE / MAINTENANCE_OBSERVATION ->
  non-rule CandidateKnowledge / KnowledgeRef path unless a later versioned routing
  policy explicitly proves a different canonical form.

The routing decision itself is recorded in candidate provenance.

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
scope_refs[]
extraction method/version
grounding method/version
confidence?
supporting_fragment_refs[]
conflicting_fragment_refs[]
validation_findings[]
review_status (derived from ReviewRecords; never independently mutable)
~~~

The proposed target must be resolved against the exact selected plant-context
revision. Until P1.3 delivers PlantContextRevision, that revision is the exact
AGENT-visible `PROCESS_GRAPH` ContextSourceRef selected for the compilation
(ADR-004 Decision 4). A target is `(that source ref, ProcessGraph id)`, using the
ProcessGraph id of the matching kind; a SIGNAL target is the ProcessGraph semantic
entity id that P1.1 uses as `signal_id`. P1.3 preserves these identities, so
published bindings remain valid.

A candidate must not silently invent a new plant entity when no target exists.
Unresolved mentions stay unresolved until a later explicit entity-creation contract.

Subsystem applicability is expressed through domain-owned grouping/scope refs rather
than inventing a universal SUBSYSTEM entity kind. Plant-wide applicability must be
explicitly represented by the selected plant-context scope policy; an empty scope is
not silently interpreted as plant-wide.

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

Rule routing is decided by the versioned extraction-routing policy above, not by a
free-form "naturally representable" judgment.

Rule-routed items use the existing `literature_candidate(...)` / `ExtractionMetadata`
carrier rather than duplicating claim/conditions/units fields in a parallel truth
schema.

For non-rule CandidateKnowledge, the canonical KnowledgeRef content always points to
the exact SourceFragmentRef/original source material. A model-normalized claim is
derived annotation with generator/method/version provenance; it is never the
canonical content body.

APPROVE of a non-rule item may publish a KnowledgeRef with
`validation_status=VERIFIED` only when the effective ReviewRecord is retained as
validation evidence. Here VERIFIED means only that the scope binding and extraction
fidelity were verified against the cited fragment. It does not assert engineering
truth, does not correspond to a Rule `validation` maturity level and grants no
authority; the same APPROVE leaves a Rule-routed item at `validation=NONE` for the
same reason. Non-rule reviewed knowledge may then be composed in P1.3.

## Candidate identity and immutability

Candidate versions are immutable and have two explicit identity layers:

~~~text
content_key
  = exact fragment refs + typed extracted content + proposed target/scope

candidate_version
  = content_key + extraction/grounding/model/policy provenance versions
~~~

The content key permits deterministic comparison across compiler versions without
implicitly reusing trust decisions.

A source revision change, parser/extractor change, prompt/model retry, grounding
policy change, or provenance change creates a distinct candidate version whenever
the versioned derivation context changes. Historical candidates are not overwritten.

ReviewRecord always binds the exact candidate version, never only the content key.
Any review carry-forward across candidate versions requires an explicit versioned
policy and creates a new ReviewRecord; it is never implicit.

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

An approved binding resolves a mention inside its own fragment only. Approved
bindings never accumulate into a global alias table. A reusable alias or tag
dictionary is a separate, versioned, reviewed source, cited as a grounding input like
any other source. Nothing in the compiler edits ProcessGraph `name` or `tag`;
ProcessGraph owns plant naming.

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

A ReviewRecord decision may be authored only by:
- a human reviewer; or
- an explicit deterministic acceptance policy identified by `policy_id@version`.

Review and publication are Application API operations (ADR-002). They are never
registered as Agent tools, never appear in an Agent ToolSpec or ContextProjection,
and no Agent run can invoke them. v0 is local and single-user: the human principal is
the trusted application-session principal that the Application API records on each
ReviewRecord.

A model/Agent cannot be the review decision principal. A deterministic acceptance
policy cannot use model confidence, free-form model judgment, or model-generated
explanation as the condition that establishes acceptance.

The v0 decision vocabulary is:

~~~text
APPROVE
REJECT
REMAP
KEEP_CANDIDATE
~~~

Every item entering an EngineeringContextRevision must have one effective APPROVE or
REMAP ReviewRecord produced by an allowed decision principal.

APPROVE accepts the candidate under exact source, plant-context, compiler, candidate,
validation and review-policy versions. It does not grant runtime authority and does
not by itself change Rule validation/authority axes.

REJECT preserves the auditable candidate but excludes it from trusted publication.

REMAP selects a different already-existing canonical target while preserving the
original proposal and reviewer rationale. REMAP is a conditional approval of the
resolved mapping only after deterministic validation is rerun against the remapped
target, including kind/scope/unit/conflict checks. The effective published target is
the ReviewRecord target, never the rejected candidate target.

REMAP may change only binding target/scope refs. A correction to extracted content,
numeric value, units, or claim text requires REJECT plus a new provenance-bearing
candidate version; review never edits candidate content in place.

A REMAP ReviewRecord stores the full selected binding: `selected_target_ref` and
`selected_scope_refs[]`. An APPROVE accepts the reviewed candidate version's proposed
target and scope refs. Publication takes the target and scope of every approved item
from its effective ReviewRecord (or, for APPROVE, that candidate version), never from
publisher input.

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
selected_scope_refs[]?
decision_principal
  HUMAN(reviewer_id)
  | DETERMINISTIC_POLICY(policy_id@version)
reason
reviewed_at
review_policy_version
source / plant-context refs
compiler / validation policy refs
supersedes_review_refs[]
review_log_position
~~~

A superseding decision creates a new record rather than mutating historical review
evidence.

ReviewRecords form one append-only review log in the compiler workspace. Each record
receives the next log position when it is appended. `supersedes_review_refs[]` may
name only records for the same candidate version at earlier log positions, so
supersession cannot form a cycle.

The effective decision for one candidate version is computed over **every**
ReviewRecord for that candidate version in the log prefix up to the
`review_log_position` pinned by the publication. The publisher does not choose the
set.

- A terminal record is one that no other record in that prefix supersedes.
- Exactly one terminal record: it is the effective decision.
- No record: the candidate is unreviewed.
- More than one terminal record, even when they agree: the candidate is unresolved
  (equivalent to KEEP_CANDIDATE for publication eligibility) until a superseding
  record leaves exactly one terminal record.

The publication transaction pins `review_log_position` to the review-log tail and
cannot choose an earlier position. In v0 one workspace lock serializes review appends
and publication: it is held from reading the tail until the publication commit
completes, so no review is appended in between. Records appended after a publication
affect only later publications. Recomputing effective decisions at an older position
is allowed for audit replay only and never produces a new revision.

Candidate `review_status` is a derived projection of this log prefix, not a second
mutable source of truth.

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

For v0, every item that enters a published revision requires an effective ReviewRecord
from an allowed decision principal.

Automatic deterministic acceptance may be introduced only through an explicit,
versioned, tested and auditable policy. Its acceptance predicate must be deterministic
and must not depend on model confidence or free-form model judgment.

## Publication

Review does not mutate "the current context" in place.

Publication creates an immutable EngineeringContextRevision.

Conceptually:

~~~text
revision_id
lineage_id
parent_revision_ref?
selected plant-context revision (the PROCESS_GRAPH ContextSourceRef until P1.3)
source inventory refs[]
compiler version
parser/extraction versions
grounding policy version
validation policy version
review policy version
approved binding refs[]
approved knowledge refs[]
input rule-set revision ref (R0)
published rule-set revision ref (R0, or R1 by path + checksum)
pinned RuleRefs[]
rule scope bindings[]: (RuleRef, scope_refs[] from the effective ReviewRecord,
  effective ReviewRecord ref, validation result ref)
review_log_position + checksum of the committed review-log prefix
review refs[]
conflict/exclusion summary
publication provenance
~~~

The revision references existing canonical owners rather than copying their full
bodies.

Publication commits, with the revision artifact, the review-log prefix up to the
pinned position for every candidate version the revision considered, so every
effective decision can be recomputed from repository content alone.

Artifacts created by one publication commit (the revision, its review-log prefix and
any R1) reference each other by repository path plus content checksum, never by the
commit that creates them, `HEAD` or "latest". Once committed, each is addressed by a
ContextSourceRef of commit revision, path and content checksum, and prepare-time
registration verifies the checksum.

`Rule.scope` stays an exact registry string and registry matching is unchanged. The
plant scope a reviewer approved for a Rule-routed item is published as a rule scope
binding in the revision: the versioned binding provenance that the telemetry contract
requires for mapping plant scope refs to exact rule scopes. P1.3 scope resolution
validates these bindings against the PlantContextRevision.

Each revision represents the **complete approved set** for that revision. A parent ref
records lineage only; publication does not implicitly inherit undeclared parent items.

Publication eligibility is checked against one exact compatibility tuple:

~~~text
candidate version
source refs/revisions
selected plant-context revision
compiler/extraction/grounding versions
validation policy version
review policy version
effective ReviewRecord at the pinned review_log_position
effective remapped target where applicable
input rule-set revision (R0) and knowledge source revisions
~~~

A review performed against an incompatible source/plant/policy tuple is stale for the
new publication and requires a new ReviewRecord or an explicit versioned carry-forward
policy that itself creates a new ReviewRecord.

P1.3 composes this reviewed Engineering Context with canonical plant context
(ADR-004 Decision 4).

### Publication artifact and run registration

EngineeringContextRevision is materialized as an immutable repository-backed artifact
with exact content checksum and repository revision. It is not a mutable compiler DB.

A later run may consume it only when that exact artifact is registered during
`RunManager.prepare()` as a ContextSourceRef (for example kind
`ENGINEERING_CONTEXT_REVISION`) before the run reaches READY.

The compiler never mutates a READY CanonicalContextRegistry, and consumers never
resolve an implicit "latest Engineering Context" revision.

### Publication atomicity and failure semantics

Publication has two levels of validation:

- candidate-local failure: exclude that candidate and record the reason in the
  revision's exclusion summary;
- revision-level dependency/ref-integrity failure: publish nothing.

Before the revision becomes observable as published context, every exact source,
fragment, effective review, RuleRef/rule-set revision, KnowledgeRef/content ref,
binding target and policy ref in the approved set must resolve and validate.

Pending publication dependencies are not consumer-visible as trusted context until
the EngineeringContextRevision commit succeeds. On failure, the previous published
revision remains the usable revision and no partial new selection is exposed.

Rejected, unresolved, stale-review, or invalid candidates never enter the approved
set.

Publishing against a parent revision uses compare-and-set semantics on a per-lineage
head record committed in this repository. Publication is one repository commit that
adds the revision artifact (and any new rule-set revision) and advances the head
record of its `lineage_id`; it succeeds only if the head still names the intended
parent. If another publication advanced the head, the attempted publish fails and
must be rebuilt and reviewed against the new parent. The head record is publisher
bookkeeping: it is not a ContextSourceRef, and consumers pin exact revisions and
never resolve a head or "latest". P1.2 v0 does not silently create divergent
canonical branches.

## Visibility and derivation taint

Visibility is determined by **all inputs that influenced a derived output**, not only
the refs chosen as supporting evidence.

Influencing inputs include:

~~~text
source fragments
conflicting fragments
aliases / tag dictionaries
known mappings
grounding alternatives/evidence
validation inputs
model context
review metadata / rationale
generated summaries or conflict explanations
~~~

A derived candidate, review projection, summary, or published revision takes the most
restrictive visibility of every influencing input.

An Agent-visible compilation/publication must be produced only from Agent-visible
inputs. A result derived using evaluator-only material cannot later be "downgraded"
to Agent visibility by removing hidden refs; a separate allowed-input compilation is
required.

Agent projections of source/review/conflict/exclusion inventories must be
visibility-filtered, including counts and summary fields. Revision ids/checksums must
not be designed so that hidden-input membership becomes an observable side channel.

Evaluator-only sources cannot create Agent-visible published knowledge.

## Benchmark use

Visibility taint excludes evaluator-only inputs. It does not detect answer-bearing
content in Agent-visible sources: a troubleshooting SOP for a subsystem can map
symptoms to the causes a benchmark case family scores as its answer. Therefore:

- published Engineering Context enters a blind benchmark run only as evaluation-v0
  capability C6, under a versioned leakage-audit extension (ADR-004 Decision 6);
- the P1.2 fixture corpus records its authoring provenance (author, date, inputs
  consulted) and is authored without access to evaluator truth, candidate sets or
  scorer mappings;
- P1.2 makes no benchmark or capability claim.

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

DocumentRef identifies the immutable source document/material revision.
SourceFragmentRef identifies the exact bounded location inside that source.
KnowledgeRef for non-rule reviewed knowledge points back to that exact fragment/source
content; it does not replace DocumentRef or store a model rewrite as canonical text.

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

Any model-generated reviewer-facing explanation is explicitly labeled as derived
model output with provider/model/method version. It is not source evidence and cannot
serve as the review acceptance predicate.

The deterministic application shell owns exact source selection/versioning, schema
validation, candidate identity, review state, publication, visibility, target
validation, and provenance retention.

A compiler run is not an Agent run. Its model calls do not go through the runtime
Coordinator, receive no tools and cannot author ReviewRecords. P1.2C defines the
provider interface and records each call's exact inputs, outputs and
provider/model/method version; those records feed candidate provenance and visibility
taint.

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
15. no telemetry, runtime, ProcessGraph, RuleRegistry or RcaState owner is duplicated;
16. more than one terminal review for a candidate version blocks its publication
    until superseded;
17. REMAP targets are revalidated before publication;
18. all influencing inputs participate in visibility/derivation taint;
19. every pinned RuleRef resolves through the published rule-set revision, and a new
    rule-set revision's parent is the input rule-set revision;
20. EngineeringContextRevision is an immutable repository-backed artifact and can
    enter a run only through a future prepare-time ContextSourceRef registration;
21. publication validates the full dependency closure and exposes no partial new
    revision on failure;
22. effective decisions are computed over the complete review log up to its tail at
    publication time; an earlier REJECT or superseding record cannot be skipped;
23. approved Rule-routed items are published through a new rule-set revision and
    rule scope bindings, with `Rule.scope` matching unchanged;
24. no Agent tool or Agent run can review or publish;
25. published targets and scopes come from effective ReviewRecords or the reviewed
    candidate version, never from publisher input.

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
source fragment + candidate + review/publication contracts, compiler workspace,
and the rule-set source revision (Knowledge Rule Registry v0)

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
- treating a vector index as canonical storage;
- registering review or publication as an Agent tool;
- a global alias registry or any edit to ProcessGraph naming.

Do not invent a local bypass.
