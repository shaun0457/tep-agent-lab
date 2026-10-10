# P1.2A — Context Compiler Contracts, Workspace and Rule-Set Revision

Status: implemented (P1.2A). Normative contracts:
[Context Compiler v0](specs/context-compiler-v0.md),
[Knowledge Rule Registry v0 §Rule-set source revision](specs/knowledge-rule-registry-v0.md),
[ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md) Decisions 4 and 5.

> Candidate Context and canonical context are different trust domains. P1.2A builds
> the contracts between them. It has no parser, no model call and no extraction.

## What is implemented

`src/tep_agent_lab/rule_sets.py` (rule-set source revision):

```text
RuleRegistry  -> RuleSetRevision(registry, visibility, parent?)
              -> serialize(): canonical JSON, kind ENGINEERING_RULE_SET
              -> load_rule_set(bytes, checksum) / load_rule_set_source(registry, id, scope)
              -> derive(parent_ref, added_rules): complete child set R1, parent = R0
```

- `rule_record` / `rule_from_record` cover every `Rule` field, including behavior,
  scope, source and validation refs and `ExtractionMetadata`.
  `conflict_record` / `conflict_from_record` cover every `RuleConflict`. Records are
  strict: no missing, extra or mistyped field.
- A round trip returns an equal `RuleRegistry`, so `find_rule_conflicts` returns the
  same records in the same order. Conflict RuleRefs must resolve within the set.
- Identity is a `ContextSourceRef` (repository revision, path, canonical-JSON
  checksum). The loader verifies the checksum and rejects non-canonical order, and the
  same ref always loads an equal immutable registry. `RuleRegistry` gained read-only
  `rules()` / `conflicts()` accessors for this.
- `derive` keeps every parent rule and conflict unchanged (`check_extends`), refuses
  to re-publish an existing `(rule_id, version)` and records the parent ref for
  lineage only. A derived set never widens its parent's visibility.
- `canonical_context.most_restrictive(...)` is the shared derivation-taint helper.

`src/tep_agent_lab/context_compiler.py` (compiler contracts, generic, no `tep_sim`
import):

```text
ContextSourceRefs -> CompileSession.prepare        own frozen CanonicalContextRegistry
                  -> SourceFragmentRef             BYTE_RANGE / JSON_POINTER + checksum
                  -> CandidateBinding | CandidateKnowledge   immutable versions
                  -> CompilerWorkspace.add_candidate         append-only + validation
                  -> CompilerWorkspace.append_review         append-only review log
                  -> CompilerWorkspace.publish(session, request, target)
                       EngineeringContextRevision + review-log prefix (+ R1)
                       -> PublicationTarget.commit           per-lineage head CAS
                  -> verify_publication(files, revision)      recompute from content
```

- **CompileSession**: registers and freezes its own `CanonicalContextRegistry` over
  the exact attested inputs. It requires exactly one AGENT-visible `PROCESS_GRAPH`
  source (the plant context until P1.3) and exactly one `ENGINEERING_RULE_SET` input
  (R0). An AGENT session rejects any non-AGENT input. `session_id` hashes the
  inventory, plant context, R0, compiler, validation and review policy versions, and
  visibility: this is the compatibility tuple. `PlantContextIndex` holds ProcessGraph
  node, binding-semantic and edge ids (ENTITY, SIGNAL, RELATIONSHIP). The caller
  supplies the ProcessGraph loader.
- **SourceFragmentRef**: exact source ref, location, SHA-256 of the extracted bytes,
  fragment kind and `fragmentation_policy@version`. `fragment_id` is derived
  deterministically. `citation()` is the `SourceVersion` a Rule-routed item's
  `ExtractionMetadata` must cite.
- **Candidates**: the versioned routing (`ROUTING_POLICY`) maps ENTITY/SIGNAL mentions
  to `CandidateBinding`. OPERATING_LIMIT and CONTROL_RELATION go to `CandidateKnowledge`
  carrying a `literature_candidate` Rule that must stay LITERATURE/NONE/REFERENCE.
  Procedure, troubleshooting and maintenance kinds go to non-rule `CandidateKnowledge`.
  Unknown kinds raise and stay unclassified. `content_key` hashes the fragments, typed
  content, target and scope. `candidate_version` adds the session and every provenance
  version (extraction, grounding, model, routing, influencing inputs, confidence). A
  model retry therefore keeps the key and changes the version. `Confidence` (score and
  method) is routing metadata only, and the model-normalized claim is a
  `DerivedAnnotation`.
- **Validation** (`validate_candidate`, `VALIDATION_POLICY`): it checks that sources
  resolve exactly, fragment checksum and location, influencing sources, the plant
  context, target existence and kind compatibility, unresolved targets, empty or
  unknown scope (an empty scope is never plant-wide), visibility widening (candidate
  plus review taint), that a Rule extraction cites a fragment, and that an existing
  `(rule_id, version)` is untouched. `AMBIGUOUS_TARGET` and `CONFLICT_DECLARED` are
  non-blocking findings. A REMAP is revalidated against its selected target and scope.
- **Review**: `ReviewRecord` holds candidate version, decision
  (APPROVE/REJECT/REMAP/KEEP_CANDIDATE), principal (`HUMAN` or a
  `DETERMINISTIC_POLICY` registered in `CompilerPolicies.acceptance_policies`; none
  exist in v0, and no model/Agent kind exists), reason, session/policy/plant refs,
  `supersedes` (same candidate, earlier records only), the selected binding for REMAP
  only, the visibility of the review metadata and the 1-based `review_log_position`
  that the workspace assigns. `effective_review` computes over every record of the
  candidate version up to a position. Exactly one terminal record decides; none means
  UNREVIEWED; more than one means UNRESOLVED. `review_status` is a derived projection.
- **Publication** (`CompilerWorkspace.publish`): the workspace lock is held from
  reading the review-log tail to the end of `target.commit`. The position is always
  the tail; no API accepts a position. Publication re-attests every session source and
  R0. A candidate-local failure becomes an `Exclusion` (stale session or review,
  unreviewed, unresolved, rejected, kept, validation, invalid REMAP). A revision-level
  failure raises `PublicationError` and writes nothing: an unknown candidate, a source
  changed on disk, an invalid R1 or an unresolvable RuleRef. A selected candidate, or
  any of its review records, that is more restrictive than the publication also fails
  the whole publication. Excluding it instead would put its exclusion record and
  review-log prefix into a less restrictive revision and expose hidden membership.
  Targets and scopes come from the effective review (REMAP) or the reviewed candidate
  version (APPROVE); `PublicationRequest` only selects candidate versions. Approved
  rules produce R1 = R0 plus those rules; R1 is committed alongside the revision and
  its RuleRefs are pinned with rule scope bindings. Non-rule items become KnowledgeRefs
  with `validation_status=VERIFIED` (scope and extraction fidelity only), with the
  fragment as content and the effective review as evidence.
- **Artifacts** (canonical JSON; same-commit refs are `ArtifactPathRef` path plus
  checksum):

  ```text
  context/engineering-context/<lineage>/revisions/<revision_id>.json
  context/engineering-context/<lineage>/review-logs/<checksum>.json   prefix for considered candidates
  context/rule-sets/<checksum>.json                                   R1, when rules were approved
  context/engineering-context/<lineage>/HEAD.json                     publisher bookkeeping, not a source
  ```

  `DirectoryPublicationTarget.commit` creates each artifact once and moves the head by
  compare-and-set on `(path, checksum)`; a lost CAS raises `StaleHead`. On failure it
  removes the files it created. Once committed, a revision enters a run only as an
  `ENGINEERING_CONTEXT_REVISION` ContextSourceRef registered at prepare time.
- `verify_publication` recomputes every effective decision from the committed review
  log and checks REMAP targets and scopes, R1's parent (must equal R0), pinned RuleRef
  resolution and that the published rules keep their literature axes.

## Ownership

| Concern | Owner reused |
|---|---|
| source inventory, attestation | `CanonicalContextRegistry` / `ContextSourceRef` (a fresh instance per session) |
| plant identities before P1.3 | the selected `PROCESS_GRAPH` source; ProcessGraph ids are never renamed |
| rules | `Rule`, `RuleRegistry`, `literature_candidate`, `ExtractionMetadata` |
| visibility | runtime `Visibility` |

The workspace is not a ContextSourceRef kind and is never registered. Review and
publication are Application API methods. No ToolSpec, gate policy, ContextProjection,
tool surface or playground module imports or exposes them (tested).

## Acceptance criteria (context-compiler-v0 §Acceptance criteria)

`tests/test_context_compiler.py` and `tests/test_rule_sets.py` name the criteria they
cover.

| Covered in P1.2A (contract level) | Deferred |
|---|---|
| 4, 5, 6, 7, 8, 9, 10, 12, 13, 15, 16, 17, 18, 19, 21, 22, 23, 24, 25 | — |
| 1, 2 for hand-built fragments over registered fixture sources | parser/fixture corpus path: P1.2B |
| 3 for immutable candidate contracts | typed model-assisted extraction producing them: P1.2C |
| 11, 14 at the contract level | end-to-end demo traceability: P1.2D |
| 20: repository artifact; registration-compatible ContextSourceRef verified | `RunManager.prepare` consumer registration: a later milestone (P1.3/P1.5); git commit creation: P1.2D |

The reproducible vertical-slice demo (spec §Initial P1.2 vertical slice) is P1.2D.

## Known limitations

- The workspace is in memory; review logs persist only through publication. A durable
  workspace store is a later implementation choice under the same contract.
- `DirectoryPublicationTarget` writes into a working tree. Creating the repository
  commit is the operator's step until P1.2D. The CAS covers one process; there is no
  cross-process lock (v0 is local and single-user).
- Plant-wide scope is not representable yet: no plant-context scope policy exists, so
  an empty scope fails validation.
- No deterministic acceptance policy is registered; every approved item needs a human
  review.
- The location kinds are BYTE_RANGE and JSON_POINTER. Semantic locators (section,
  table rows, procedure steps) arrive with the P1.2B parser policy.

## Next step

P1.2B: a deterministic parser and fixture path for one small engineering corpus that
produces `SourceFragmentRef`s from registered sources, with the corpus authoring
provenance that context-compiler-v0 §Benchmark use requires.
