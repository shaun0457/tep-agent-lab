# P1.2B S1–S5 — Engineering corpus to staged revision

Status: S1–S5 implemented: registration, fragmentation, deterministic extraction,
recorded human demo review and verified non-canonical staging. Owning semantics:
[Context Compiler v0](specs/context-compiler-v0.md) sections Exact source input,
SourceFragmentRef, Benchmark use, Model/provider boundary and Initial P1.2 vertical
slice; existing contracts are described in [P1.2A](p1-2a-compiler-contracts.md).

## Purpose and provenance

The four short Markdown documents cover only the TEP reactor and its cooling-water
loop. They are original teaching sources based on the public Downs and Vogel (1993)
paper, [public PDF](https://users.abo.fi/~khaggblo/RS/Downs.pdf),
[DOI](https://doi.org/10.1016/0098-1354(93)80018-I). Tags, units and numerical values
use Tables 1, 3, 4 and 6; the procedure and troubleshooting guidance are original
desk-review reasoning, not a published plant SOP. No company-specific material is used.

Package data lives at `src/tep_agent_lab/engineering_corpus/tep_reactor_cooling/`.
The strict JSON `manifest.json` records corpus id/version, subsystem, author, date,
public inputs, leakage statement, each document's path/SHA-256/purpose and the
intentional conflict. Its author is `Codex executor (coordinated by Claude Code)`
and its authoring date is `2026-10-11`.

## Leakage rule

The corpus author did not open, read or search repository benchmark fixtures,
scorer/evaluator code or documentation, candidate sets or evaluator truth. The
specified compiler contract and source-registration test pattern were consulted
for implementation only; their fixture statements were not engineering inputs.
Only the public paper and general engineering reasoning informed source content.
The mandatory whole-repository verification executes existing tests; that is
validation, not access to their source or use in corpus authoring.

The loader bounds every document to the manifest's package corpus directory and
requires public HTTPS provenance references without benchmark/scorer/evaluator
paths. Tests independently check this separation. These guards check recorded
provenance; they do not prove the truth of an author's declaration. Engineering
context may enter a blind study only through C6 and a versioned leakage audit.
S1 makes no benchmark or Agent capability claim.

## S1 behavior and trust boundary

```text
[package manifest + exact Markdown bytes]
    -- strict JSON/schema + SHA-256 validation --> [validated source inventory]
[validated source inventory + caller-attested repository revision]
    -- ContextSourceRef registration --> [fresh frozen CanonicalContextRegistry]
[fresh frozen CanonicalContextRegistry]
    -- AGENT-scope exact-byte resolution --> [read-only list demo]
```

`engineering_corpus.load_corpus` uses `persistence.strict_json_loads`, checks exact
keys at every object level, validates types/date/identities/paths/conflict refs,
checks document UTF-8 and exact bytes, and returns immutable manifest data.
`register_corpus(repository=..., git_revision=...)` uses the existing registry and
materializer pattern. Each source is an AGENT-visible `ENGINEERING_DOCUMENT` with
`text/markdown` schema and `sha256:bytes`, plus authoring and exact manifest
provenance. It creates and freezes its own inventory; it does not edit READY state.
Source ids combine corpus id and document stem. Version/revision/checksum are
retained separately; changes require new exact source refs.

Registration trusts the caller's repository attestation as the existing registry
does. It verifies bytes, not git membership. The checkout demo uses local HEAD as
that attestation label; while these sources are uncommitted, this is a working-tree
registration demo, not proof that they exist in HEAD. Production compilation must
supply the revision containing the committed corpus. No source is reviewed
canonical knowledge merely because it was registered.

The S1 registration module has no domain tag literals, physics, extraction, Agent
tools, model/provider calls, review decisions or publication. Domain names occur
only in the default corpus path data. Setuptools includes Markdown and manifest
package data. `.gitattributes` fixes corpus files to LF so checkout conversion does
not invalidate exact-byte checksums; other files keep the repository convention.

## Seeds for subsequent vertical-slice demonstrations

| Required item | Source seed | Later handling |
| --- | --- | --- |
| Unambiguous signal | `subsystem.md`: XMEAS(21), reactor cooling-water outlet temperature | Bind against the selected plant revision |
| Ambiguous binding | `operating-procedure.md`, step 4: reactor-side temperature, medium/tag omitted | Explicit review chooses between process and coolant outlet temperature |
| Operating/control/troubleshooting claim | `subsystem.md`: cooling-flow manipulation increases heat removal, with conditions | Typed claim with exact fragment provenance |
| Source conflict | `tags-and-limits.md`: normal upper process temperature 150 degC; `troubleshooting.md`: deliberately 145 degC | Preserve both citations; manifest marks intentional disagreement |
| RuleRegistry-backed claim | `tags-and-limits.md`: normal temperature upper limit 150 degC, distinct from shutdown 175 degC | Later OPERATING_LIMIT extraction uses the existing inert Rule path |
| Rejected/unresolved candidate | `troubleshooting.md`: unverified RCW-IN-TT inlet transmitter proposal | No canonical entity invention; KEEP_CANDIDATE or REJECT excludes publication |

S1 seeds source material only. It does not claim to have produced bindings, Rules,
fragments, review records or a staged revision.

## S2 — Section and paragraph fragmenter, parser policy v1

Owning contracts: context-compiler-v0 sections SourceFragmentRef, Exact source input,
Candidate identity and immutability, and Incremental compilation.
`markdown_fragments.fragment_markdown(ref, bytes)`
accepts the exact `ContextSourceRef` and bytes resolved by a trusted application
from its frozen registry. It requires `text/markdown`, `sha256:bytes` and a matching
whole-source checksum. It accepts no document path and does not register sources.
The caller's registry still owns git-revision attestation and visibility filtering.

```text
[frozen registry] -- exact ref + attested bytes --> [Markdown parser policy v1]
[Markdown parser policy v1] -- semantic path + byte anchor --> [SourceFragmentRef]
[SourceFragmentRef] -- reparse anchor + source/fragment checksums --> [verified exact bytes]
```

Every fragment records `fragmentation_policy = markdown.sections-paragraphs@1`.
This is a bounded deterministic grammar, not arbitrary Markdown support:

- Input is UTF-8 with LF only. CRLF and bare CR are rejected before parsing; no
  line endings or source text are normalized. Corpus LF is fixed by `.gitattributes`.
- ATX headings have one to six `#` characters, zero to three leading spaces and
  whitespace or end-of-line after the markers. Optional closing hashes separated
  by whitespace are removed from the heading label used to derive the slug only.
  A section begins at its heading's first byte and ends immediately before the
  next heading of the same or higher level, or at EOF. Ancestor sections therefore
  include child sections. Skipped heading levels use the actual heading stack.
- Slugs use NFC, case folding, alphanumeric characters and collapsed hyphens;
  an empty slug becomes `section`. Repeated sibling slugs get `~2`, `~3`, etc.
  The `~` character cannot occur in a base slug, avoiding numbered-title collisions.
- Paragraphs are nonempty blank-line-separated blocks owned by the innermost
  section. Headings also terminate blocks, even without a blank separator. Blank
  lines contain only spaces/tabs. A paragraph excludes the separators but retains
  its last line's LF if present. Eligible paragraphs get 1-based indices per section;
  unsupported blocks do not consume an index. Output is section-first source order.
- Headings and table-like blocks (conservatively, any block containing `|`), ordered
  or unordered lists and their indented continuations, fenced/indented code,
  quotations, HTML, thematic breaks and Setext headings are not paragraph fragments.
  A mixed prose/unsupported block remains wholly unsupported. Fences suppress
  headings until a matching same-character fence of at least the opening length;
  an unclosed fence suppresses through EOF. HTML containers conservatively suppress
  through their closing tag, including blank lines; unclosed containers suppress
  through EOF. Preamble before the first ATX heading stays unsupported.

### Locator model and strict persistence

P1.2A `FragmentLocation` adds `LocationKind.MARKDOWN`. Its exact record keys are
`kind`, `start`, `end`, `pointer`, `section_path`, `paragraph_index`. `pointer` must
be null; `start` and `end` must be non-boolean integers with `0 <= start < end`.
`section_path` is a nonempty immutable tuple of slugs (a JSON array in records),
and `paragraph_index` is null for a section or a positive non-boolean integer for
a paragraph. `fragment_kind` must agree (`SECTION` or `PARAGRAPH`), and the policy
must be the supported v1. The demo displays the path and `/paragraph-N` suffix;
the structured path/index fields, rather than this display string, are the locator.

`extract` recomputes v1 locations from the supplied bytes and requires both the
semantic path/index and the recorded `[start, end)` to match before returning that
exact byte slice. A checksum-valid slice with a fabricated semantic path or changed
anchor is rejected. `SourceFragmentRef.verify` additionally checks the exact source
checksum and fragment SHA-256. Existing compiler candidate validation and publication
verification use this same path; they still require exact session source membership.
There is no search against newer revisions or offset fallback.

`_location_fits` restricts MARKDOWN to `sha256:bytes`. Strict reconstruction rejects
missing or extra location/fragment keys. The existing BYTE_RANGE and JSON_POINTER
record keys, extraction behavior and identity hashes stay unchanged; semantic fields
are forbidden on these legacy kinds.

### Identity and edit isolation

`fragment_id` uses the same exact identity for every location kind: the full
`source_ref` record (including git revision, whole-source checksum and provenance),
the full location record (including byte start/end and structured section path and
paragraph index), fragment kind, content checksum, policy and optional label.
Identical exact refs, locations and policy yield identical ids and order. A new
source revision changes every fragment id even for unchanged paragraph bytes;
candidate content keys and versions therefore change through the existing P1.2A
candidate API. Record reconstruction rejects a substituted source under the old id.

`markdown_fragments.diff_key(fragment)` is a separate **non-identity** comparison
key: structured semantic locator (section path and paragraph index), fragment kind,
policy and fragment content checksum. It excludes the source ref and byte offsets
and is compared only within corresponding source namespaces. It is not persisted
and is never used in candidate hashing, review binding or publication. Equal diff
keys permit edit comparison only; they cannot reuse trust decisions or exact refs.

Replacing one paragraph, without changing headings or block structure, changes
exactly that paragraph's diff key and those of its enclosing sections, including
ancestors: their exact byte slices contain the edited paragraph by design. Every
other diff key stays unchanged even when later offsets move. Heading renames,
paragraph insertion/deletion, or classification changes can change locators and
therefore additional diff keys. New parser semantics require a new policy version.

The corpus golden is strict JSON at `tests/markdown_fragments_golden.json`, with
all 32 ids, kinds, displayed locators and byte ranges (6 procedure, 9 subsystem,
8 tag-table, 9 troubleshooting fragments). `tests/test_markdown_fragments.py` was
written first and failed initially because the module did not exist. It covers
the full golden, repeated runs, exact fragment/candidate identity changes across
source revisions, substituted-source reconstruction rejection, exact diff-key edit
isolation including ancestor sections, exact re-extraction/checksums, empty sections,
Unicode byte offsets, duplicate headings, unsupported blocks, LF rejection, strict
persistence, legacy compatibility, generic code literals and subprocess demo output.

S3 owns table-row and procedure-step fragments; S4 owns typed extraction. Those
blocks remain exact content inside their SECTION fragments but get no specialized
fragments here. S2 adds no candidates, model calls, Agent tools, reviews or publication.

## S3 — Table rows and procedure steps

Owning contracts remain context-compiler-v0 sections SourceFragmentRef, Exact source
input, Candidate identity and immutability, and Incremental compilation.

```text
[exact registered ref + source bytes] -- explicit parser policy --> [sections / paragraphs / tables / rows / steps]
[row SourceFragmentRef + exact source bytes] -- reparse + checksum verification --> [derived header-keyed cells]
[exact fragments + derived cells] -- future S4 extraction policy --> [typed candidates]
```

### Versioned grammar

`markdown.sections-paragraphs-tables-steps@1` adds `TABLE`, `TABLE_ROW` and
`PROCEDURE_STEP`. Call `fragment_markdown(ref, bytes, policy=TABLES_STEPS_POLICY)`
to select it. The Python API's default remains `markdown.sections-paragraphs@1`;
that policy's records, ids, output and `tests/markdown_fragments_golden.json` are
unchanged. Both policies preserve LF UTF-8 bytes, heading rules, innermost section
ownership, preamble exclusion and conservative blank-line block boundaries from S2.

- A supported pipe table is one contiguous nonblank block: a header, a delimiter
  line and zero or more data rows. Outer pipes are optional. Every line must contain
  an unescaped pipe, and every row must have the header's cell count. Delimiter cells
  have at least three hyphens with optional leading/trailing alignment colons.
  Header names must be nonempty and distinct after trimming spaces/tabs.
- Cell parsing trims spaces/tabs and unescapes `\|` and `\\` for the derived view
  only. Code-span/backtick table syntax is conservatively unsupported. It does not
  render Markdown or infer types, units or meaning.
- `TABLE` spans the header through the last data row (or delimiter for an empty
  table). Each `TABLE_ROW` spans exactly its data line, including the final LF when
  present. Header/delimiter lines are never row fragments. Output is TABLE then its
  rows, in source order.
- A supported ordered list is one contiguous nonblank block with one-to-nine-digit
  markers ending in `.` or `)`, zero to three initial spaces, and whitespace or EOL
  after the marker. Items share the initial indentation and marker delimiter.
  Each item becomes a `PROCEDURE_STEP`, including adjacent plain continuation lines
  indented at least to that item's content column. The marker and all whitespace
  remain in the exact bytes. Tabs in continuation indentation, nested lists and
  structural continuation blocks are unsupported. Blank lines end the block/list;
  detached continuation blocks stay unsupported, while subsequent numbered blocks
  receive a new list index. There is no LIST fragment.
- Unsupported blocks remain wholly unclassified: unordered lists, fenced/indented
  code, HTML containers/comments, quotations, thematic breaks, Setext headings,
  mixed prose/table/list blocks, malformed tables (including inconsistent cell
  counts), and unsupported list continuations. No partial rows/steps or PARAGRAPH
  fragments are salvaged from such a block. Enclosing SECTION bytes still retain it.
  Fences and HTML keep the S2 suppression of apparent headings and content.

### Exact locators and derived cells

MARKDOWN keeps the six S2 keys: `kind`, `start`, `end`, `pointer`, `section_path`,
`paragraph_index`. SECTION/PARAGRAPH add no keys. TABLE/TABLE_ROW additionally require
exactly `table_index` and `row_index`; PROCEDURE_STEP additionally requires exactly
`list_index` and `step_index`. For the new kinds `paragraph_index` is null. All indices
are positive non-boolean integers, except TABLE's null `row_index`. A row cannot exist
without its table index, and a step requires both list and step indices. Mixed,
missing or extra fields are rejected; semantic fields remain forbidden on BYTE_RANGE
and JSON_POINTER, whose shapes and hashes are unchanged.

Table/list indices count supported blocks per innermost section, starting at one.
Row indices count data rows, and step indices count items in appearance order;
the printed marker number is retained in source bytes rather than used as an index.
Repeated `1.` markers therefore have distinct locators. Display examples are
`section/path/table-1/row-2` and `section/path/list-1/step-3`; structured fields remain
authoritative, since heading slugs can coincide with display suffixes.

`SourceFragmentRef` checks policy/kind agreement. Verification reparses using its
exact policy, matches the entire structured locator and byte range, and checks both
whole-source and fragment checksums. Direct location extraction infers the new policy
for table/step locators; shared section/paragraph locators default to S2, or accept an
explicit `fragmentation_policy`. `_location_fits` keeps the same `sha256:bytes` plus
`text/markdown` restriction for every MARKDOWN kind. Full exact source/location records
still determine fragment identity; there is no offset fallback or newer-source lookup.

`table_row_cells(fragment, exact_source_bytes)` first verifies the row's exact source
and checksum, reparses its enclosing table to obtain header bytes, and parses the exact
row bytes. It returns a fresh header-keyed string mapping. Cells are not persisted in
fragment records and do not affect identity. S4 may consume these views with the exact
row refs and parser policy as provenance; typed extraction, candidates, review and
staging are outside S3.

`diff_key` retains the S2 structured fields and adds only the applicable table/row or
list/step indices. It still excludes source revision/checksum/provenance and offsets,
and is non-identity. A length-changing row edit, preserving block structure, changes
only that row's key plus its enclosing TABLE and SECTION keys (including ancestor
sections). Every exact fragment id changes under a new exact source ref, even where
later byte offsets move but content/diff keys remain unchanged. Table/list insertion,
header edits or classification changes can affect more keys.

### Evidence and demo

`tests/test_markdown_tables_steps.py` was written and executed before implementation;
it initially failed on the missing new policy/API. The independent strict identity,
location and checksum golden at `tests/markdown_tables_steps_golden.json` anchors the two corpus documents:
13 tag-table fragments (including one TABLE and four TABLE_ROWs), and 11 procedure
fragments (including five PROCEDURE_STEPs). Tests verify determinism, round trips,
exact extraction/checksums, row-edit isolation, continuation bytes, repeated markers,
strict indices/records, forged locators, derived cells, unsupported blocks, unchanged
S2 output, generic-code literals and subprocess demos.

```console
python -m tep_agent_lab.engineering_corpus rows tep-reactor-cooling.tags-and-limits
python -m tep_agent_lab.engineering_corpus fragments tep-reactor-cooling.operating-procedure
python -m tep_agent_lab.engineering_corpus fragments tep-reactor-cooling.tags-and-limits --policy markdown.sections-paragraphs@1
```

The fragments demo defaults to the new policy, while `--policy` preserves access to
S2 output. The rows demo requires the new policy and prints each data row's locator,
exact byte range, SHA-256 prefix and canonical-JSON cells. Registration and revision
attestation retain the S1 limitations described above.

## S4 — Deterministic table extraction to typed candidates

Owning contracts: context-compiler-v0 sections Typed extraction, Candidate identity
and immutability, Confidence semantics, Benchmark use and Initial P1.2 vertical slice.
The generic `deterministic_extraction` engine uses the existing P1.2A candidate API.
Domain tags, selected plant ids and source locators live in the corpus's packaged
`extraction-rules.json`, not in engine code. The four registered Markdown documents
and their manifest/checksums remain unchanged.

```text
[frozen CompileSession] -- exact source refs + re-attested bytes --> [S3 fragments / cells]
[versioned extraction rule data] -- table patterns / exact phrases --> [deterministic engine]
[S3 fragments / cells] -- exact citations --> [deterministic engine]
[deterministic engine] -- P1.2A routing + provenance --> [unreviewed Binding / inert Rule candidates]
[deterministic engine] -- unmatched rows, cells and prose --> [unsupported report]
```

Deterministic rules precede P1.2C to establish inspectable source grounding,
unsupported behavior, identity and conflict evidence before model extraction shares
the same candidate contracts. They provide a small reproducible example, not an
arbitrary-document parser, a diagnosis or a capability result. Confidence is omitted;
any future confidence remains review-routing metadata only.

### Strict rule-set schema

The top-level record has exactly `schema_version`, `rule_set_id`, `version`,
`authored_at`, `units` and `rules`. This corpus selects
`deterministic-extraction-rules/v1` and
`tep-reactor-cooling.deterministic-extraction@1`. The date is fixed authoring metadata,
never extraction wall-clock time. `units` is the explicit supported-unit vocabulary.
Unknown keys, duplicate JSON keys, NaN/Infinity, wrong types, invalid dates/versions,
duplicate rule ids, duplicate targets/scopes and invalid regex syntax fail loading.
The validated rule data is deeply immutable. Semantic rule changes require a new
rule-set version; changing even formatting changes the exact derived-input checksum.

Every rule has exactly `id`, `form`, `source_id`, `locator`, `item`, plus the field
specific to its form:

| Form | Selection and extraction |
| --- | --- |
| `table` | `locator` names a TABLE under the S3 policy. `matches` maps exact header names to regex patterns applied with `fullmatch` to each TABLE_ROW's verified cells. A binding consumes `mention_column`; a limit consumes `number_column` and `unit_column`, with exactly the named capture `value` on the number pattern. Each rule emits one typed item per matching row. |
| `phrase` | `locator` names one PARAGRAPH or PROCEDURE_STEP. The exact UTF-8 `phrase`, including line breaks, must occur inside that fragment. One matching rule emits one item. A missing phrase, missing source/locator or wrong anchor kind aborts the whole batch without returning partial candidates. |

`item` has an explicit nonempty `scope` array of exact `{kind, id}` plant targets.
Its remaining keys are discriminated by kind and form:

- ENTITY_MENTION / SIGNAL_MENTION: `target_kind`, `targets`, plus `mention_column`
  for table rules. Exactly one existing target becomes `proposed_target`; multiple
  existing targets become alternatives with no chosen proposal; none stays unresolved.
  The engine never invents a canonical entity or fallback alias.
- OPERATING_LIMIT: `variable`, `constraint`, `conditions`, `uncertainties`, plus the
  two table column names or phrase `value_text`. The latter must occur in `phrase`.
  Decimal number syntax and the declared unit are parsed through P1.2A `TypedValue`;
  unsupported units, non-finite values and inexact oversized integers are refused.
  The resulting value/unit and constraint remain in the existing Rule carrier's
  descriptive extraction claim and units; there is no second numeric Rule schema.
- CONTROL_RELATION: phrase rules only, with `variables`, `conditions`,
  `uncertainties` and `units`. The exact phrase becomes the Rule claim, without
  paraphrasing. Claim variables and scopes must exist in the selected plant index.

Bindings use P1.2A `ROUTING_POLICY`; limits and control relations use
`CandidateKnowledge` carrying `literature_candidate(...)` and `ExtractionMetadata`.
Every Rule remains `LITERATURE / NONE / REFERENCE`, status `candidate`, behavior
`ANNOTATE`. Extraction creates no review decision or execution permission.

### Provenance, identities and conflict handling

Each candidate cites its exact S3 SourceFragmentRef, selected PROCESS_GRAPH and
every source involved in a conflict. Its `extraction_method` is the rule-set
`id@version`, `grounding_method` is `exact-selected-plant-ids@1`, and routing is the
existing P1.2A version. `DerivedInput` records the rule-set method ref and SHA-256 of
the exact JSON bytes. A future publication caller must resolve those same bytes
through the existing derived-input resolver; S4 does not stage them.

The candidate id hashes the rule id and exact fragment id. The existing P1.2A API
computes `content_key` and `candidate_version`; no alternate candidate identity or
model is introduced. With the same exact corpus/session and rule-set bytes, records
and ids are byte-identical. Changing only the rule-set version preserves ids and
content keys while changing candidate versions. A source revision still changes
fragment identities and therefore candidate identities, as specified in S2/S3.

Limits with the same variable, constraint, unit, conditions and scope but different
values receive bidirectional `conflicting_candidate_ids` and exact
`conflicting_fragments`. This is the existing P1.2A conflict mechanism. Normal upper
and shutdown high have distinct constraint/condition keys, so 150 and 175 degC are
not treated as a conflict. Neither conflicting value silently replaces the other.

### Corpus items and demo

The eight table rules produce four SIGNAL_MENTION bindings and four limits:

| Source row | Binding | Normal upper | Shutdown high |
| --- | --- | --- | --- |
| XMEAS(7) | `reactor.pressure_measurement` | 2895 kPa gauge | 3000 kPa gauge |
| XMEAS(9) | `reactor.temperature_measurement` | 150 degC | 175 degC |
| XMEAS(21) | `reactor_cooling.outlet_temperature` | unsupported | unsupported |
| XMV(10) | `reactor_cooling.flow_actuator` | unsupported | unsupported |

The four phrase rules produce the remaining items:

| Exact source locator (below its document heading) | Output |
| --- | --- |
| operating-procedure: `review-steps/list-1/step-4` | `reactor-side temperature`: unresolved proposal, two existing alternatives, ambiguous and needs review |
| subsystem: `control-relation/paragraph-1` | CONTROL_RELATION preserving the exact cooling-flow / heat-removal claim and stated utility/thermal conditions |
| troubleshooting: `intentional-competing-limit/paragraph-1` | 145 degC normal upper limit, linked to the table's competing 150 degC candidate |
| troubleshooting: `unverified-instrumentation-proposal/paragraph-1` | RCW-IN-TT mention, no existing registered signal, unresolved and visibly `no-matching-signal` |

```console
python -m tep_agent_lab.engineering_corpus candidates
```

Each candidate line prints kind, mention/target or claim/value, derived status flags,
all cited source locators and fragment id prefixes. The 12 candidates are all
`unreviewed`; ambiguity and missing signals also produce blocking existing validator
findings. These flags are display metadata, never mutable review state or acceptance.
The demo's compile session uses the pinned packaged ProcessGraph and a trusted,
in-memory empty input RuleSetRevision (R0), labelled demo-only; it writes no R0 or
publication artifact. Lab revision attestation retains S1's working-tree limitation.
Outside a checkout, also supply `--plant-git-revision <trusted-pinned-revision>`.
Production extraction accepts the application's own exact frozen CompileSession.

The report has 20 unsupported entries: six unmatched cells from table rows 3/4 and
14 unselected paragraphs/steps. Unknown rows are additionally reported as
`unmatched-row`, with their unmatched cells retained independently. Unselected
prose is `no-phrase-rule`. Container sections/tables are not extraction items.
Matched patterns consume whole cells; base values recognized inside a limit pattern
are not separately emitted. A selected phrase produces just its declared item,
not claims from surrounding prose. No base value, unspecified alarm, command range,
unsupported unit, unknown row or unselected procedure guidance is inferred.

### Evidence and limits

`tests/test_deterministic_extraction.py` was written and executed first; it initially
failed on the absent extraction module. Its 17 tests cover the complete strict
golden, candidate record reconstruction, source-byte re-verification, provenance and
route, unambiguous and ambiguous bindings, missing signal refusal, linked competing
limits, separate shutdown limit, CONTROL_RELATION, ENTITY_MENTION routing, unknown
rows/cells, invalid numbers/units, missing phrase/locator failure, exact-byte tampering,
version identity layers, repeated-run determinism, immutable records, strict schema,
generic engine literal guards and the CLI demo. The canonical JSON golden at
`tests/deterministic_extraction_golden.json` pins all 12 full candidate records plus
all 20 unsupported records under a fixed test repository revision.
`.gitattributes` fixes this golden to LF so checkout conversion preserves the exact
canonical JSON bytes checked by the test, including the final LF.

This engine intentionally supports only the two declared forms and bounded typed
vocabulary above. It has no model/provider calls, Agent tools, confidence-based
acceptance, review records, staging or publication. Reviewed Rule and rejection
publication evidence belong to S5; the repository transaction remains P1.2D. No
benchmark/scorer/evaluator sources or existing test fixtures were consulted.

## S5 — End-to-end staged revision

Owning contracts: context-compiler-v0 sections Human review, Candidate identity and
immutability, Publication, Hard trust invariant, Confidence semantics, Visibility
and derivation taint, Benchmark use and Initial P1.2 vertical slice. S5 uses the
existing P1.2A API; there is no second review/publication model.

```text
[registered exact corpus + frozen CompileSession]
  -- S3 parser / S4 extraction DATA --> [immutable typed candidates]
[candidates + recorded HUMAN demo decisions]
  -- exact-version coverage + append_review --> [CompilerWorkspace review log]
[workspace + PublicationRequest]
  -- stage_publication (one internal verify_publication) --> [StagedPublication (canonical=False)]
[successfully verified staged items + recorded decisions + exclusions]
  -- format_stage_summary --> [PASS + source-traced plant knowledge summary]
```

`staged_revision_demo.stage_revision(session, rules, review_raw)` returns a
`StagedPublication` and immutable `StageSummary`. It composes S3/S4 and the P1.2A
workspace, review, request, staging and verification contracts, entirely in memory.
It accepts exact review bytes or the validated immutable mapping returned by
`load_recorded_review`; the CLI loads the DATA once and passes that mapping.
The generic module contains no domain tag literals or simulator imports. Its
summary retains the session visibility, including counts and exclusion reasons.
The exact extraction-rule bytes resolve by method/role and are checksum-validated
by staging and its internal verification. The summary reports that single
`verify_publication` call inside P1.2A `stage_publication`; the demo does not run a
second verification with the same session objects. Verification failure returns
no summary. Separate artifact-tampering tests still exercise the verifier directly.

### Recorded human demo review DATA

`engineering_corpus/tep_reactor_cooling/demo-review.json` is strict, versioned
`recorded-demo-review/v1` DATA. The named principal is
`demo-reviewer (human test principal)`, with role `HUMAN`. These are recorded
human demo decisions requested for the test scenario, **not an automated acceptance
policy**, model/Agent decisions or authenticated real-world human sign-off.
`compiler-demo.recorded-human@1` identifies the review replay protocol; it registers
no deterministic acceptance policy and computes no acceptance predicate.

The DATA pins visibility (which must match the session), source repository revisions,
full compile session id, review policy, principal, fixed UTC review/staging
timestamps, workspace/lineage and one
exact `CandidateRef` per decision. The demo pins the corpus-containing base
`2b35d4604fea6054fc318cd97585ee89ae69b0dd` and the existing simulator pin. It does
not silently substitute a newer HEAD. A changed source/parser/extractor/plant/policy
requires newly recorded reviews; CLI revision overrides that disagree fail closed.
The stage CLI accepts only the corpus repository `shaun0457/tep-agent-lab`; it
rejects the plant repository or any other repository before preparing a session.
The exact session id checks the frozen source inventory without flattening it to
one revision per repository.
The input R0 remains the explicitly marked in-memory empty S4 demo rule set.

All 12 candidates are accounted for:

- APPROVE the four unambiguous table signal bindings, including XMEAS(21) cooling
  water outlet temperature.
- REMAP the ambiguous `reactor-side temperature` to the existing
  `reactor.temperature_measurement`, with its full reactor scope. P1.2A reruns
  deterministic validation and retains the original unresolved proposal, both
  alternatives and the reviewer rationale. This recorded choice applies only to
  the cited mention; it establishes no global alias or proof of source intent.
- APPROVE four table limits: pressure normal upper 2895 and shutdown high 3000
  kPa gauge; process temperature normal upper 150 and shutdown high 175 degC.
- APPROVE the qualitative CONTROL_RELATION as reference knowledge. All five
  Rule-routed items keep `LITERATURE / NONE / REFERENCE`; APPROVE accepts extraction
  fidelity and does not promote engineering truth or execution authority.
- REJECT the competing 145 degC claim because the table gives 150 degC and the
  troubleshooting note declares an intentional discrepancy. REJECT RCW-IN-TT
  because the pinned plant context has no matching signal.
- No candidate is deferred in the recorded baseline. Explicit `deferred` entries
  require exactly `candidate_ref`, `review_id` and `reason`, and replay as
  `KEEP_CANDIDATE` ReviewRecords, preserving the human rationale and explicit id in
  the same immutable log and revision identity. All ids must be unique across
  decisions and deferrals. The loader sorts deferrals by full CandidateRef, so
  reordering otherwise identical deferrals produces byte-identical staged files.
  They never enter the approved set.

The loader rejects duplicate JSON keys, unknown schema keys/versions, unsupported
principals, duplicate decisions/review ids and malformed target refs. Before creating the
workspace, replay requires exact equality between extracted and recorded candidate
versions: unknown, stale, missing, or decided-plus-deferred entries fail closed.
REJECT/KEEP_CANDIDATE and invalid selections remain auditable exclusions, never
approved bindings or Rules. Audit artifacts retain the full candidate/review log;
the 150 degC item's conflict citation intentionally retains the rejected 145 degC
fragment. Removing that history would violate P1.2A provenance requirements.

The summary has four groups: APPROVED for items accepted into the staged content;
REJECTED for recorded REJECT decisions; EXCLUDED for other decisions denied staging
(including REMAP_INVALID, VALIDATION_FAILED, STALE_REVIEW and explicit
KEEP_CANDIDATE); DEFERRED only for explicit entries in the DATA's `deferred` array.
Each excluded row retains its recorded decision and the contract exclusion reason.
Approved REMAP rows show the staged validation's selected target, or selected scope
for knowledge. Excluded REMAP rows have no binding arrow: any requested target/scope
appears only next to the exclusion reason, labelled as requested.

### S5 evidence and visible demo

`tests/test_staged_revision_demo.py` was written first and initially failed because
the pipeline module did not exist. It covers corpus-to-verifier PASS, preserved
Rule axes, rejection and explicit deferral, original/remapped targets, exact source
fragments and ReviewRecords, byte-identical repeated staging, tampering with every
artifact, stale/unknown/missing/duplicate reviews, strict DATA, changed review
rationale changing revision identity, generic literals and the stage CLI.
The review-fix regressions cover EXCLUDED versus explicit DEFERRED, validated
binding/knowledge REMAP output, mandatory unique deferred ids, deferred order
independence, one internal verification and failure propagation, one CLI DATA load,
repository rejection, and specific CLI REJECT/REMAP rows and verification output.
`tests/staged_revision_demo_golden.json` pins the strict summary and all four
staged-file SHA-256 checksums; `.gitattributes` fixes its bytes to LF.

```console
python -m tep_agent_lab.engineering_corpus stage
```

The output lists each accepted plant binding/limit/relation, reviewer decision and
reason, source semantic locator and fragment id prefix; it also lists rejections,
exclusions, explicit deferrals, unsupported extraction count and the staged revision identity:

```text
APPROVED: 10
REJECTED: 2
EXCLUDED: 0
DEFERRED: 0
UNSUPPORTED extraction entries: 20
revision_id=ecr-32140d7b6870d7cc6f35726e87e5ab8eb0dd5fab1b556aeaf09d1b5fa93e538d
checksum=f2649802cea37c0e56c6752fe88ac886abc8ddf95466ab33a4bcc0fcbadd42ce
staged files=4; canonical=False
verify_publication: PASS (stage_publication internal verification)
```

No staged file or log is written by the demo. No head advances, no Agent tool is
registered and no model is called. The corpus documents and manifest are unchanged.

## P1.2B completion and follow-ups

P1.2B delivers the complete deterministic source-only path: registered corpus ->
exact S3 fragments -> immutable S4 candidates -> recorded human demo reviews ->
reproducible verified staging. S1–S4 landed in PRs #30–#33; S5 completes the path
in this change. This is the first visible "what the plant is" output, with exact
source and review provenance, not a consumer-visible canonical publication or a
benchmark/capability claim.

Limitations and follow-ups:

- The corpus is deliberately small; the bounded Markdown grammar and declarative
  extraction rules leave 20 extraction entries unsupported. Those entries are
  unextracted source content, distinct from reviewed candidate deferrals.
- S3 follow-up: numbered items whose content starts with a nested list, quote or
  fence still become `PROCEDURE_STEP`. Tighten conservative classification in a
  follow-up parser policy with dedicated regression evidence; S5 does not change
  the frozen S3 policy.
- Reviews are recorded demo DATA, not an interactive reviewer UI or authentication
  system. Workspace and demo R0 are in memory; repository membership/ancestor
  commit attestation limitations remain as described in P1.2A.
- P1.2C is next: bounded model-assisted extraction/grounding and exact call records
  behind the same candidate/trust contracts. S5 makes no model calls.
- P1.2D owns the real review workflow and repository transaction: one git commit,
  immutable repository-backed artifacts, trusted repository anchors and head CAS.
  Staged results remain non-canonical until that transaction succeeds. P1.3 then
  composes reviewed engineering context with canonical plant semantics.

## Demo and verification

```console
python -m tep_agent_lab.engineering_corpus list
python -m tep_agent_lab.engineering_corpus fragments tep-reactor-cooling.subsystem
```

Each tab-separated line prints source id, repository-relative path, byte size and
SHA-256, in the registry's deterministic source-id order. In an installed package
outside a checkout, pass `--git-revision <trusted-40-hex-revision>` (and
`--repository <owner/repo>` when needed). No arbitrary local document is accepted.
The fragments command prints the tree with kind, semantic locator, half-open exact
byte range and SHA-256 prefix; it resolves only the supplied registered source id.

`tests/test_engineering_corpus.py` was written first and initially failed because
the module did not exist. It covers exact registry resolution, freeze, changed
bytes before/after registration, exact keys, strict JSON, invalid schema fields,
bounded paths, duplicate identities, conflict references, immutable provenance,
leakage guards, generic module literals and subprocess demo output.

Required verification is `scripts/check.py` with the pinned runtime and simulator,
the owner-specified unchanged-fixture D0 diff gate and `git diff --check`. No
milestone status, roadmap or agent instructions are changed in S1.
