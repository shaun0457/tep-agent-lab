# P1.2A — Context Compiler Contracts, Workspace and Rule-Set Revision

Status: implemented (P1.2A). Normative contracts:
[Context Compiler v0](specs/context-compiler-v0.md),
[Knowledge Rule Registry v0 §Rule-set source revision](specs/knowledge-rule-registry-v0.md),
[ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md) Decisions 4 and 5.

> Candidate Context and canonical context are different trust domains. P1.2A builds
> the contracts between them and stops at a validated, **non-canonical** staged
> publication. It has no parser, no model call, no extraction and no repository write.

## Publication split: staging in P1.2A, the repository transaction in P1.2D

context-compiler-v0 §Publication atomicity requires that a publication is one
repository commit that adds its artifacts and advances the per-lineage head by
compare-and-set. P1.2A therefore does not write anything:

- `CompilerWorkspace.stage_publication` builds and fully validates the complete
  bundle and returns a `StagedPublication`. The bundle holds the revision, the
  review-log prefix, the candidate records, R1, the expected head and the new head
  record. `StagedPublication.canonical` is `False`. Staging advances no head, and
  nothing in it is consumer-visible trusted context.
- No production API returns a "published" result, writes a file or writes a head
  (a test checks the module source for file writes).
- **P1.2D** adds the repository transaction: one git commit, per-root serialized
  writers, real ref/head compare-and-set and crash safety. Requirements for that
  adapter, carried over from the P1.2A review (the removed working-tree prototype
  had these defects):
  - one cross-process writer lock per repository root, not a per-instance lock;
  - unique temporary names (no fixed `.staged` file), fsync of files and
    directories, and Windows-safe replace that handles `PermissionError`;
  - a head reader that raises the module's own errors, never a raw `JSONDecodeError`;
  - no partially written content-addressed files on failure: write to a temporary
    location first, so an interrupted commit leaves no artifact behind;
  - CAS on the git ref and the `HEAD.json` record together, so `StaleHead` means
    nothing was committed;
  - attestation of ancestor rule sets (and parent revisions) by commit and path, not
    only by content checksum (see Known limitations).

## What is implemented

`src/tep_agent_lab/rule_sets.py` (rule-set source revision):

```text
RuleRegistry  -> RuleSetRevision(registry, visibility, parent?)
              -> serialize(): canonical JSON, kind ENGINEERING_RULE_SET (cached)
              -> load_rule_set(bytes, checksum) / load_rule_set_source(registry, id, scope)
              -> derive(parent_ref, added_rules): complete child set R1, parent = R0
```

- `rule_record` / `rule_from_record` cover every `Rule` field, including behavior,
  scope, source and validation refs and `ExtractionMetadata`. The `RuleConflict`
  record functions do the same. Records are strict, and every malformed input raises
  `RuleSetError`.
- Conflicts are serialized in canonical order, so the checksum does not depend on
  registration order. A round trip returns the revision's registry unchanged, so
  `find_rule_conflicts` returns the same records. Conflict RuleRefs must resolve
  within the set.
- The loader parses strictly before it computes the checksum: duplicate keys at any
  depth and NaN/Infinity are rejected (`persistence.strict_json_loads`). It also
  rejects non-canonical order. Identity is a `ContextSourceRef`, and the same ref
  always loads an equal immutable registry.
- `derive` and `check_extends` keep every parent rule and conflict unchanged and
  refuse to re-publish an existing `(rule_id, version)`. The constructor and
  `check_extends` both refuse a child less restricted than its parent.
- A parent ref is data inside the child, so its declared visibility proves nothing.
  `load_rule_set_source(..., resolve_parent=...)` (`verify_rule_set_lineage`) loads
  every ancestor by content checksum and checks each link, visibility included. A
  parent that cannot be resolved fails closed. `CompileSession.prepare` resolves
  ancestors through the session's materializers, so it rejects an input rule set that
  widens its parent.
- `RuleRegistry` gained read-only `rules()` / `conflicts()` accessors.
  `canonical_context.most_restrictive(...)` is the shared derivation-taint helper.

`src/tep_agent_lab/context_compiler.py` (generic; never imports `tep_sim`):

```text
ContextSourceRefs -> CompileSession.prepare        own frozen CanonicalContextRegistry
                  -> SourceFragmentRef             BYTE_RANGE (exact bytes) / JSON_POINTER
                  -> CandidateBinding | CandidateKnowledge   immutable versions
                  -> CompilerWorkspace(id, visibility).add_candidate   append-only
                  -> CompilerWorkspace.append_review                    append-only log
                  -> CompilerWorkspace.stage_publication(session, request,
                                                         derived_input_resolver=...)
                       -> StagedPublication (non-canonical bundle)
                  -> verify_publication(files, revision, input_rule_set)
```

- **CompileSession**: `__post_init__` checks every invariant, so direct construction
  cannot skip them. The registry must be frozen. The session needs exactly one
  AGENT-visible `PROCESS_GRAPH` (the plant context until P1.3) and exactly one R0, and
  no input may be more restrictive than the session. The plant index is rebuilt from
  the source with the supplied ProcessGraph loader; any loader failure becomes a
  `CompilerError`. `session_id` hashes the
  compatibility tuple (inventory, plant context, R0, policies including
  `acceptance_policies`, visibility). The verifier recomputes it from the revision.
- **SourceFragmentRef**: an exact source ref, a location, the SHA-256 of the
  extracted bytes and `fragmentation_policy@version`. `fragment_id` is
  deterministic. `BYTE_RANGE` is allowed only on `BYTES_SHA256` sources: a
  canonical-JSON checksum ignores line endings, so a CRLF checkout would shift the
  offsets silently. `citation()` is the `SourceVersion` that a Rule-routed item must
  cite.
- **Candidates**: the versioned routing (`ROUTING_POLICY`) sends ENTITY/SIGNAL
  mentions to `CandidateBinding`. OPERATING_LIMIT and CONTROL_RELATION become
  `CandidateKnowledge` carrying a `literature_candidate` Rule that stays
  LITERATURE/NONE/REFERENCE. The other kinds become non-rule `CandidateKnowledge`,
  and unknown kinds stay unclassified. `content_key` and `candidate_version` hash
  NFC-normalized canonical JSON. A model retry keeps the key and changes the version.
  `record()` is the complete immutable candidate and derivation record (content,
  alternatives, provenance with every influencing input and exact derived-input
  checksums, fragments); its hashes can be recomputed. Numbers are normalized to
  float, and integers beyond 2^53, booleans and non-finite values are rejected.
- **Validation** (`validate_candidate`): sources must match the frozen inventory by
  their complete identity; a same-id ref with another revision, path or checksum
  fails. The validator also checks fragment checksum/location, influencing sources,
  plant context, target existence and kind, unresolved targets, empty or unknown
  scope, visibility widening, rule citation and immutability, and, at staging,
  `CONFLICT_UNRESOLVED` for conflicting candidate ids missing from the workspace.
  A REMAP is fully revalidated against its selected target and scope. Units and
  values are immutable candidate content that a REMAP cannot change.
- **Review**: a `ReviewRecord` is made by `HUMAN` or by a registered
  `DETERMINISTIC_POLICY` principal (none exist in v0). `reviewed_at` and
  `published_at` must be strict UTC ISO-8601 ending in `Z`. `effective_review` takes
  the single terminal record; no record means UNREVIEWED and several mean
  UNRESOLVED.
- **Workspace visibility**: a workspace is bound to one visibility. Sessions,
  candidates and reviews more restrictive than the workspace are refused, so hidden
  activity can never move the log tail or change the checksum of a less restrictive
  revision. Use a separate workspace per visibility.
- **Staging** (`stage_publication`): a plain `threading.Lock` is held from reading
  the tail to the end of self-verification. The position is always the tail. Staging
  re-attests every session source and R0. Every considered candidate's
  `DerivedInput`s must resolve through `derived_input_resolver` to their exact bytes;
  an input with no resolver fails. Candidate-local failures become `Exclusion`s.
  Revision-level failures raise `PublicationError` and produce no bundle: an unknown
  candidate, a changed source, an unresolved derived input, a duplicate RuleRef
  across candidates, an invalid R1 or a hidden-tainted selection. A failure also
  occurs when the parent revision cannot be resolved through `parent_resolver`, does
  not hash to its ref, belongs to another lineage, or is more restricted than the
  staging (its ref or its content). Targets and scopes
  come from the effective review (REMAP) or the reviewed candidate version (APPROVE).
  Every list is sorted by `CandidateRef`, so the selection order never changes the
  bytes. `StagedPublication` is self-consistent by construction. It checks paths
  under the publication roots and that every referenced artifact is present with a
  matching checksum. It parses the revision strictly; lineage, review-log ref and
  position, candidates ref, rule-set ref, expected head (from `parent_revision`) and
  exclusions must agree with it, and `document` is derived from those bytes. The head
  record is built from the revision ref and kept out of the files. Dependency
  verification (the shared closure plus item re-validation) is `verify_publication`.
- **Bundle layout** (canonical JSON; same-commit refs are `ArtifactPathRef`):

  ```text
  context/engineering-context/<lineage>/revisions/<revision_id>.json
  context/engineering-context/<lineage>/review-logs/<sha256>.json   prefix for considered candidates
  context/engineering-context/<lineage>/candidates/<sha256>.json    considered + dependency records
  context/rule-sets/<sha256>.json                                   R1, when rules were approved
  context/engineering-context/<lineage>/HEAD.json                   head record (P1.2D writes it)
  ```

- **One dependency closure.** `_dependency_closure` is the single check that both
  `stage_publication` and `verify_publication` call. It covers:
  - re-attesting every session source;
  - re-loading R0 and its whole lineage from exact bytes (equal to the frozen R0 and
    visible at the revision's visibility);
  - resolving the parent revision;
  - checking candidate and review visibility for every stored candidate;
  - resolving every stored candidate's derived inputs through a required resolver;
  - conflict dependencies: candidates that a selected candidate names in
    `conflicting_candidate_ids` are stored in the candidates artifact as exact
    dependency records (not approved), and the known ids used for conflict validation
    come from stored records only.

  The verifier only adds checks on top of it.
- **`verify_publication(files, revision, session, derived_input_resolver=...,
  parent_resolver=...)`** recomputes everything from bundle content plus the exact
  dependencies. `session` is a compile session over the revision's own inventory (the
  pinned PROCESS_GRAPH and R0), and its id must equal the revision's. Beyond the
  shared closure it checks:
  - strict revision, review-log and candidate schemas, with duplicate keys rejected;
  - the revision id and the `compile_session_id` tuple;
  - review supersession, the compatibility tuple and principals against the recorded
    `acceptance_policies`;
  - candidate hashes and visibility;
  - effective review ids and decisions against each item;
  - every approved item: the candidate is rebuilt from its stored record (it must
    re-serialize identically) and re-validated with `validate_candidate`. Fragments
    are re-attested against the sources and targets against the pinned PROCESS_GRAPH
    index, so a REMAP to a nonexistent target fails. Validation must pass, and the
    item must equal the record rebuilt from candidate, review and validation:
    fragments, `KnowledgeRef.content`, extraction kind, target, scope, provenance and
    validation record;
  - the `conflicts` summary, re-derived from the approved candidates;
  - exclusion reasons against the recomputed status;
  - R1: visibility no more restricted than the revision, parent = R0, every R0 rule and
    conflict unchanged, no rules beyond the pinned ones, no duplicate pinned RuleRef,
    and each pinned rule equal to its reviewed candidate rule with literature axes.

## Ownership

| Concern | Owner reused |
|---|---|
| source inventory, attestation | `CanonicalContextRegistry` / `ContextSourceRef` (fresh instance per session) |
| plant identities before P1.3 | the selected `PROCESS_GRAPH` source; ProcessGraph ids are never renamed |
| rules | `Rule`, `RuleRegistry`, `literature_candidate`, `ExtractionMetadata` |
| visibility | runtime `Visibility` |
| strict JSON parsing | `persistence.strict_json_loads` (a pure addition next to `canonical_json`) |

The workspace is not a ContextSourceRef kind and is never registered. Review and
staging are Application API methods. No ToolSpec, gate policy, tool surface or
playground module imports or exposes them (tested).

## Acceptance criteria (context-compiler-v0 §Acceptance criteria)

`tests/test_context_compiler.py` and `tests/test_rule_sets.py` name the criteria and
review findings they cover.

| Criterion | P1.2A state |
|---|---|
| 4, 5, 6, 7, 8, 9, 12, 15, 19, 22, 23, 24, 25 | covered at the contract level |
| 11 | covered: complete records are staged; the verifier rebuilds and re-validates every approved item (H5, round-2 item 1) |
| 13, 18 | covered: workspace visibility (H4), parent-revision visibility and rule-set lineage taint (round-2 items 2 and 3) |
| 16 | covered, including a staging test where two terminal reviews block publication |
| 17 | covered: REMAP target, scope and conflict revalidation at staging and in the verifier, against stored candidate records and stored conflict dependencies; units/values are immutable content |
| 1, 2 | partial: hand-built fragments over registered fixture sources; parser corpus in P1.2B |
| 3 | partial: immutable candidate contracts; model-assisted extraction in P1.2C |
| 14 | partial: contract level; end-to-end demo in P1.2D |
| 10 | partial: immutable, fully validated staged bundle; the atomic commit and head CAS are P1.2D |
| 20 | partial: a written staged revision registers through a ContextSourceRef (tested); the repository commit is P1.2D and `RunManager.prepare` consumption comes later |
| 21 | partial: the shared closure fails closed in staging and in verification. Tested cases: a changed source, changed R0 bytes, an unresolvable or mismatched derived input, an unresolvable or foreign parent, and a missing conflict dependency. Ancestor rule sets are attested by content checksum only, and commit atomicity and crash safety are P1.2D |

The reproducible vertical-slice demo (spec §Initial P1.2 vertical slice) is P1.2D.

## Known limitations

- The workspace is in memory. Candidate records and the review-log prefix persist
  only through a committed publication, which P1.2D adds.
- Ancestor rule sets are identified by content checksum only. The default resolver
  reads the ancestor's path from the session's materialization and checks its
  checksum. The git revision recorded in the parent ref is not attested, so a parent
  ref naming a nonexistent ancestor commit is accepted when the content matches.
  Commit and path attestation of ancestors is a P1.2D repository-adapter requirement.
- Plant-wide scope is not representable yet: no plant-context scope policy exists, so
  an empty scope fails validation.
- No deterministic acceptance policy is registered, so every approved item needs a
  human review.
- The location kinds are BYTE_RANGE and JSON_POINTER. Semantic locators (section,
  table rows, procedure steps) arrive with the P1.2B parser policy.
- Derived-input resolvers are caller-supplied until P1.2C defines model-call records.

## Next step

P1.2B: a deterministic parser and fixture path for one small engineering corpus that
produces `SourceFragmentRef`s from registered sources, with the corpus authoring
provenance that context-compiler-v0 §Benchmark use requires.
