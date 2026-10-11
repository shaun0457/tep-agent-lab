# P1.2B S1–S2 — Engineering corpus and deterministic fragments

Status: S1 registration and S2 section/paragraph fragmentation implemented. Owning semantics:
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

Owning contracts: context-compiler-v0 sections SourceFragmentRef, Exact source input
and Incremental compilation. `markdown_fragments.fragment_markdown(ref, bytes)`
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

For MARKDOWN only, `fragment_id` hashes the source namespace (repository, source id,
path, kind, schema, checksum method and visibility), structured semantic locator,
fragment kind, content checksum, policy and optional label. Whole-source checksum,
git revision, source provenance and byte offsets are excluded from this content
identity. The full immutable `source_ref` and exact offsets remain in every record
and are verified independently; an id is never authority to substitute a source.

Identical source bytes and policy yield identical ids and order. Replacing one
paragraph, without changing headings or block structure, changes only that paragraph
id and the containing section ids, including ancestors. Later fragments keep ids
even when their offsets move, and new refs retain the new revision/checksum. Heading
renames, paragraph insertion/deletion, or classification changes can change locators
and therefore additional ids. New parser semantics require a new policy version.

The corpus golden is strict JSON at `tests/markdown_fragments_golden.json`, with
all 32 ids, kinds, displayed locators and byte ranges (6 procedure, 9 subsystem,
8 tag-table, 9 troubleshooting fragments). `tests/test_markdown_fragments.py` was
written first and failed initially because the module did not exist. It covers
the full golden, repeated runs, length-changing paragraph edits across exact source
revisions, exact re-extraction/checksums, empty sections, Unicode byte offsets,
duplicate headings, unsupported blocks, LF rejection, strict persistence, legacy
compatibility, generic code literals and subprocess demo output.

S3 owns table-row and procedure-step fragments; S4 owns typed extraction. Those
blocks remain exact content inside their SECTION fragments but get no specialized
fragments here. S2 adds no candidates, model calls, Agent tools, reviews or publication.

## Remaining slices

- S3: table-row and numbered-procedure-step fragments; unsupported blocks stay unclassified.
- S4: deterministic typed table extraction with fragment provenance and existing inert Rules.
- S5: end-to-end test-principal review and reproducible non-canonical staged revision.

The repository publication transaction and head CAS remain P1.2D.

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
