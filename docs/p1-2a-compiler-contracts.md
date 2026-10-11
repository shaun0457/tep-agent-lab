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
    only by content checksum (see Known limitations);
  - the trusted `WorkspaceAnchor` (workspace id, review-log tail position and hash,
    candidate-log count and tail hash) read from repository history, not from the
    bundle;
  - binding `published_by` / `published_at` to the commit author and time.

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
  and unknown kinds stay unclassified. `content_key`, `candidate_version`,
  `fragment_id` and review `record_hash` hash the exact canonical JSON bytes, with no
  Unicode normalization (the canonical_context checksum convention). An NFC and an NFD
  spelling are different records with different hashes. A model retry keeps the key
  and changes the version.
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
  UNRESOLVED. Each record carries `previous_hash`, so the review log is a hash chain.
  Its `effective_visibility` is the taint over every ref it carries (declared
  visibility, plant context, selected target, selected scope refs).
  `append_review` refuses a record whose effective visibility exceeds the workspace's,
  and a REMAP that does not change target or scope is refused at append and excluded
  as `REMAP_UNCHANGED` when publishing.
- **Workspace anchor**: candidate appends form a second hash chain (ordered
  candidate refs). `CompilerWorkspace.anchor()` returns the `WorkspaceAnchor` that
  pins both chains and the workspace id.
- **Workspace visibility**: a workspace is bound to one visibility. Sessions,
  candidates and reviews more restrictive than the workspace are refused, so hidden
  activity can never move the log tail or change the checksum of a less restrictive
  revision. Use a separate workspace per visibility.
- **Staging** (`stage_publication`): the workspace `threading.RLock` is held from
  reading the tail to the end of self-verification. Resolvers run under it and must
  not modify the workspace: the lock is re-entrant, so a misbehaving resolver cannot
  deadlock, and staging raises `PublicationError` if the review or candidate log
  moved. The position is always the tail. Staging
  re-attests every session source and R0. Every approved candidate's `DerivedInput`s
  must resolve through `derived_input_resolver` to their exact bytes, otherwise
  staging fails. A cited conflict partner whose inputs do not resolve excludes its
  citers as `DEPENDENCY_INVALID`, and an excluded candidate that nothing cites needs
  no resolvable inputs. Candidate-local failures become `Exclusion`s.
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
  context/engineering-context/<lineage>/review-logs/<sha256>.json   full hash-chained review log + candidate log
  context/engineering-context/<lineage>/candidates/<sha256>.json    considered + dependency records
  context/rule-sets/<sha256>.json                                   R1, when rules were approved
  context/engineering-context/<lineage>/HEAD.json                   head record (P1.2D writes it)
  ```

- **One staging function.** `_stage_bundle` is staging as a pure function of its
  inputs: the session, the request, the review records, the candidate log, and the
  considered and dependency candidates. `stage_publication` calls it with
  workspace state. Inside it runs:
  - `_dependency_closure`: re-attest every session source; re-load R0 and its lineage
    from exact bytes, visible at the revision's visibility; resolve the parent; check
    the effective visibility of every review record and every stored candidate;
    check the exact source integrity (sources, fragments, influencing sources, derived
    inputs) of every cited conflict partner, whether selected or a stored dependency
    and whatever its own decision; and limit the known ids to stored candidates;
  - `_decide` for every considered candidate. A candidate whose conflict partner fails
    integrity is excluded as `DEPENDENCY_INVALID`, a candidate-level fail-closed: the
    revision does not abort;
  - derived-input resolution for the approved candidates (an unresolved input still
    fails the whole publication). An excluded candidate that no considered candidate
    cites needs no resolvable inputs; a cited partner was already checked by the
    closure;
  - `_build_bundle`.

  A conflict dependency is, for each candidate id that a selected candidate names in
  `conflicting_candidate_ids` and that is not itself selected, the latest version of
  that id in the anchored candidate log. Older versions, for example from an earlier
  session, are not pulled in. Dependencies are stored as exact records but not
  approved.
- **`verify_publication(files, revision, session, *, anchor, derived_input_resolver,
  parent_resolver)`**:
  - Trusted inputs: the compile session over the revision's dependencies (its id must
    equal the revision's) and the `WorkspaceAnchor`. A missing anchor or resolver
    fails closed.
  - It checks that the bundle's review log is a contiguous hash chain and that the
    review log and candidate log both match the anchor.
  - It re-runs `PublicationRequest` on the revision's request metadata, rebuilds every
    candidate record exactly (duplicate rows are rejected), and calls the same
    `_stage_bundle`.
  - The bundle's file set must equal the re-staged file set exactly (an extra or a
    missing file is rejected), and every re-staged artifact (revision, review log,
    candidates, R1) must equal the bundle byte for byte. The verifier therefore
    performs every staging check, and only adds checks to it.

### Field audit

Classes: **A**: recomputed by the verifier from trusted inputs and compared
byte-exactly. **B**: checked against a trusted anchor supplied by the caller (the
session, the `WorkspaceAnchor`, or the parent resolver). **C**: not verified, with a
justification. `TamperEveryFieldTests` mutates every A/B field listed here, recomputes
every hash and expects rejection. It skips only the fields marked C.

| Artifact | Field | Class | How it is verified, or why C is safe |
|---|---|---|---|
| revision | `schema_version`, `kind`, `routing_policy` | A | constants of `_build_bundle` (implemented routing version) |
| revision | `visibility`, `compile_session_id`, `plant_context`, `source_inventory`, `compiler_version`, `validation_policy`, `review_policy`, `acceptance_policies`, `input_rule_set` | A | from the trusted session, whose id must equal `compile_session_id` |
| revision | `lineage_id` | C | publisher input. Constructor constraints are re-run, every artifact path and the head path derive from it, and the parent must be in the same lineage. Renaming it consistently re-stages a revision with identical approved content, so it grants no trust or visibility. Which lineage head it advances is the P1.2D head CAS |
| revision | `parent_revision` | B | resolved through the trusted parent resolver: exact checksum, strict schema, same lineage, visibility (the head CAS in P1.2D decides which parent is current) |
| revision | `considered_candidate_refs` | B (membership) / C (subset) | each ref must be in the anchored candidate log (B). Which anchored candidates to consider is the publisher's selection (spec: publication selects candidate versions); every subset re-stages to a fully validated revision, so it cannot add unreviewed, unvalidated or hidden content (C) |
| revision | `dependency_candidate_refs` | A | recomputed from the anchored candidate log (exact conflict partners) |
| revision | `extraction_methods`, `grounding_methods`, `approved_bindings`, `approved_knowledge`, `rule_scope_bindings`, `review_ids`, `conflicts`, `exclusions` | A | re-staged: `_decide` on anchored reviews and rebuilt candidates, `validate_candidate` against the exact dependencies |
| revision | `published_rule_set`, `pinned_rule_refs` | A | R1 re-derived from the re-loaded R0 and the approved rules |
| revision | `candidates` (path, checksum), `review_log.path`, `review_log.content_checksum` | A | re-staged artifact refs |
| revision | `review_log.review_log_position`, `.tail_hash`, `.candidate_count`, `.candidate_tail_hash` | B | equal to the anchor |
| revision | `provenance.workspace_id` | B | equal to the anchor's `workspace_id` |
| revision | `provenance.published_by`, `provenance.published_at` | C | publisher-asserted audit metadata. Constructor constraints are re-run (nonempty, strict UTC); no decision, trust or visibility depends on them; P1.2D binds them to the commit author and time |
| revision | `revision_id` | A | recomputed digest |
| review log | `schema_version` | A | constant |
| review log | `workspace_id`, `review_log_position`, `tail_hash`, `candidate_count`, `candidate_tail_hash`, `candidate_log` | B | the candidate-log hash chain and counts must equal the anchor |
| review log | `records[*]` (every field, including `previous_hash`) | B | contiguous hash chain whose tail hash equals the anchor; each record is then used as staging input |
| candidates | `schema_version` | A | constant |
| candidates | `candidates[*]` (every field) | A + B | rebuilt exactly (content key, version, fragment ids recomputed). Membership comes from the anchored candidate log; approved items are re-validated against the exact sources and the plant index; exclusions are re-decided. Fragments of an excluded candidate that no considered candidate cites are not attested, which is safe because it contributes nothing to the approved set and no decision depends on it. Exception: a cited partner, selected or not and whatever its own decision (REJECTED, STALE_SESSION and so on), always has its exact sources, fragments, influencing sources and derived inputs checked by `_partner_intact`; a failure excludes its citers as `DEPENDENCY_INVALID` |
| candidates | `dependencies[*]` (every field) | A | rebuilt exactly; the set is recomputed from the anchored candidate log (latest version per referenced, unselected id). Every cited partner, the selected version when selected and otherwise this dependency, has its sources, fragments, influencing sources and derived inputs attested regardless of its own decision. A failing partner excludes its citers (`DEPENDENCY_INVALID`), so a claim's outcome does not depend on whether its partner is selected |
| bundle | set of file paths | A | must equal the re-staged set exactly |
| all records | text fields used in identity hashes | A | hashed as exact canonical bytes; an NFC/NFD variant changes the hash and fails the anchor and the byte comparison |
| R1 | every field | A | re-derived from R0 and the approved rules, byte-exact; visibility no more restricted than the revision |
| head record | `schema_version`, `lineage_id`, `expected_parent`, `revision` | A | built by `StagedPublication` from the revision ref and the parent; P1.2D writes it |
| staged metadata | `lineage_id`, refs, `review_log_position`, `expected_head`, `exclusions`, `document` | A | `StagedPublication.__post_init__` parses the revision and requires agreement |

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
| 4, 5, 6, 7, 8, 9, 12, 15, 19, 23, 24, 25 | covered at the contract level |
| 11 | covered: complete records are staged; the verifier rebuilds and re-validates every approved item (H5, round-2 item 1) |
| 13, 18 | covered. Workspace visibility, review taint over every carried ref (including REMAP targets and scopes), parent-revision visibility, rule-set lineage taint, R1 visibility. Every one is re-checked when verifying |
| 22 | covered at the contract level: effective decisions come from the complete hash-chained review log up to its tail, pinned by a trusted `WorkspaceAnchor`; a dropped or reordered record fails. In P1.2A the anchor comes from the workspace; P1.2D reads it from repository history |
| 16 | covered, including a staging test where two terminal reviews block publication |
| 17 | covered: REMAP target, scope and conflict revalidation at staging and in the verifier, against stored candidate records and stored conflict dependencies; units/values are immutable content |
| 1, 2 | partial: hand-built fragments over registered fixture sources; parser corpus in P1.2B |
| 3 | partial: immutable candidate contracts; model-assisted extraction in P1.2C |
| 14 | partial: contract level; end-to-end demo in P1.2D |
| 10 | partial: immutable, fully validated staged bundle; the atomic commit and head CAS are P1.2D |
| 20 | partial: a written staged revision registers through a ContextSourceRef (tested); the repository commit is P1.2D and `RunManager.prepare` consumption comes later |
| 21 | partial. The same `_stage_bundle` fails closed in staging and in verification. Tested cases: a changed source, changed R0 bytes, an unresolvable or mismatched derived input on an approved candidate, an unresolvable, foreign or hidden parent, a fabricated conflict dependency, and extra or missing bundle files. A conflict partner that fails integrity (for example a fragment outside the inventory, an older session's sources, or an unresolvable derived input) excludes only the claims that cite it, as `DEPENDENCY_INVALID`. This holds whether or not the partner is selected and whatever its own decision (selection-invariance tests cover STALE_SESSION, REJECTED-but-intact and outside-inventory partners). Ancestor rule sets are attested by content checksum only, and commit atomicity and crash safety are P1.2D |

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
- Source content is re-read and re-attested on each validation call (`_dependency_closure`
  and `_decide` do not share a per-call cache). This is fine for the small P1.2 corpus;
  a shared per-call content cache is a follow-up if corpus size makes it matter.
- Small validation helpers (`_ID`, `_SHA256`, `_exact_keys`-style checks) are
  duplicated across `canonical_context`, `rule_sets` and `context_compiler`;
  consolidating them is a P1.2B follow-up.
- `strict_json_loads` (shared persistence helper) rejects duplicate keys and
  non-finite numbers, `1e400` included. Integers stay exact and are bounded by Python's
  integer string-conversion limit, which also raises `ValueError`.

## Next step

P1.2B: a deterministic parser and fixture path for one small engineering corpus that
produces `SourceFragmentRef`s from registered sources, with the corpus authoring
provenance that context-compiler-v0 §Benchmark use requires.
