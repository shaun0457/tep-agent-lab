# P1.2B S1 — Engineering corpus and registration

Status: S1 implemented in the working tree. Owning semantics:
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

The generic module has no domain tag literals, physics, parsing/extraction, Agent
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

## Remaining slices

- S2: versioned deterministic section/paragraph fragments and exact re-extraction.
- S3: table-row and numbered-procedure-step fragments; unsupported blocks stay unclassified.
- S4: deterministic typed table extraction with fragment provenance and existing inert Rules.
- S5: end-to-end test-principal review and reproducible non-canonical staged revision.

The repository publication transaction and head CAS remain P1.2D.

## Demo and verification

```console
python -m tep_agent_lab.engineering_corpus list
```

Each tab-separated line prints source id, repository-relative path, byte size and
SHA-256, in the registry's deterministic source-id order. In an installed package
outside a checkout, pass `--git-revision <trusted-40-hex-revision>` (and
`--repository <owner/repo>` when needed). No arbitrary local document is accepted.

`tests/test_engineering_corpus.py` was written first and initially failed because
the module did not exist. It covers exact registry resolution, freeze, changed
bytes before/after registration, exact keys, strict JSON, invalid schema fields,
bounded paths, duplicate identities, conflict references, immutable provenance,
leakage guards, generic module literals and subprocess demo output.

Required verification is `scripts/check.py` with the pinned runtime and simulator,
the owner-specified unchanged-fixture D0 diff gate and `git diff --check`. No
milestone status, roadmap or agent instructions are changed in S1.
