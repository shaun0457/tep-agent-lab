"""S2 parser policy v1: exact anchors, stable content identities and corpus golden."""

from dataclasses import replace
import hashlib
import inspect
import os
from pathlib import Path
import subprocess
import sys
import unittest

from industrial_agent_runtime import Visibility
from industrial_agent_runtime.serialization import freeze_json

from tep_agent_lab import engineering_corpus, markdown_fragments
from tep_agent_lab.canonical_context import (
    BYTES_SHA256, CANONICAL_JSON_SHA256, ContextSourceRef, ProjectionScope,
)
from tep_agent_lab.context_compiler import (
    CompilerError, FragmentLocation, LocationKind, SourceFragmentRef, _fragment_from_record,
)
from tep_agent_lab.persistence import canonical_json, strict_json_loads


def source(data: bytes, **changes) -> ContextSourceRef:
    return ContextSourceRef(**{
        "source_id": "manual", "repository": "example/lab", "git_revision": "c" * 40,
        "path_or_ref": "docs/manual.md", "content_checksum": hashlib.sha256(data).hexdigest(),
        "kind": "ENGINEERING_DOCUMENT", "schema_version": "text/markdown",
        "visibility": Visibility.AGENT, "checksum_method": BYTES_SHA256, **changes,
    })


def parse(data: bytes, **changes) -> tuple[SourceFragmentRef, ...]:
    return markdown_fragments.fragment_markdown(source(data, **changes), data)


def golden_row(fragment: SourceFragmentRef) -> dict:
    return {"id": fragment.fragment_id, "kind": fragment.fragment_kind,
            "locator": fragment.location.semantic_locator,
            "start": fragment.location.start, "end": fragment.location.end}


class MarkdownFragmentsTests(unittest.TestCase):
    def corpus(self):
        registry = engineering_corpus.register_corpus(
            repository="example/lab", git_revision="c" * 40)
        return tuple(registry.resolve(ref.source_id, ProjectionScope.AGENT)
                     for ref in registry.inventory(ProjectionScope.AGENT))

    def test_whole_corpus_golden_strict_json(self) -> None:
        raw = Path(__file__).with_name("markdown_fragments_golden.json").read_bytes()
        golden = strict_json_loads(raw)
        self.assertEqual({"policy", "sources"}, set(golden))
        self.assertEqual(markdown_fragments.PARSER_POLICY, golden["policy"])
        actual = {item.ref.source_id: [golden_row(fragment) for fragment in
                  markdown_fragments.fragment_markdown(item.ref, item.content)]
                  for item in self.corpus()}
        self.assertEqual(golden["sources"], actual)
        self.assertEqual(4, len(actual))
        self.assertEqual([6, 9, 8, 9], [len(rows) for rows in actual.values()])

    def test_two_runs_are_identical_including_records_and_order(self) -> None:
        for item in self.corpus():
            first = markdown_fragments.fragment_markdown(item.ref, item.content)
            second = markdown_fragments.fragment_markdown(item.ref, item.content)
            self.assertEqual(first, second)
            self.assertEqual(canonical_json([f.record() for f in first]),
                             canonical_json([f.record() for f in second]))
            self.assertTrue(all(f.fragmentation_policy == markdown_fragments.PARSER_POLICY
                                for f in first))

    def test_every_fragment_reextracts_exact_bytes_and_checksum(self) -> None:
        for item in self.corpus():
            for fragment in markdown_fragments.fragment_markdown(item.ref, item.content):
                with self.subTest(locator=fragment.location.semantic_locator):
                    loc = fragment.location
                    exact = item.content[loc.start:loc.end]
                    self.assertEqual(exact, loc.extract(item.content))
                    self.assertEqual(hashlib.sha256(exact).hexdigest(), fragment.content_checksum)
                    self.assertTrue(fragment.verify(item.content))
                    self.assertEqual(item.ref, fragment.source_ref)

    def test_length_changing_paragraph_edit_isolated_across_new_revision(self) -> None:
        old = b"# Root\n\nIntro.\n\n## First\n\nEdit me.\n\nUnchanged.\n\n## Last\n\nAlso unchanged.\n"
        new = old.replace(b"Edit me.", b"A much longer replacement paragraph.")
        before = parse(old)
        after = parse(new, git_revision="d" * 40, provenance={"revision": "new"})
        changed = {"root", "root/first", "root/first/paragraph-1"}
        self.assertEqual([f.location.semantic_locator for f in before],
                         [f.location.semantic_locator for f in after])
        for a, b in zip(before, after, strict=True):
            self.assertEqual(a.fragment_id != b.fragment_id,
                             a.location.semantic_locator in changed)
            self.assertNotEqual(a.source_ref, b.source_ref)
            self.assertTrue(b.verify(new))
        self.assertFalse(before[0].verify(new))
        self.assertFalse(after[0].verify(old))

    def test_corpus_one_paragraph_edit_isolation(self) -> None:
        for item in self.corpus():
            old = markdown_fragments.fragment_markdown(item.ref, item.content)
            target = next(f for f in old if f.fragment_kind == "PARAGRAPH")
            loc = target.location
            new = item.content[:loc.start] + b"Replacement paragraph.\n" + item.content[loc.end:]
            ref = replace(item.ref, content_checksum=hashlib.sha256(new).hexdigest(),
                          git_revision="d" * 40, provenance={})
            after = markdown_fragments.fragment_markdown(ref, new)
            for a, b in zip(old, after, strict=True):
                affected = (a == target or (a.fragment_kind == "SECTION"
                            and a.location.start <= loc.start < a.location.end))
                self.assertEqual(affected, a.fragment_id != b.fragment_id)

    def test_source_namespace_and_policy_are_part_of_identity(self) -> None:
        data = b"# Heading\n\nText.\n"
        first = parse(data)[1]
        for field, value in (("source_id", "other"), ("repository", "example/other"),
                             ("path_or_ref", "docs/other.md")):
            self.assertNotEqual(first.fragment_id, parse(data, **{field: value})[1].fragment_id)
        with self.assertRaises(CompilerError):
            replace(first, fragmentation_policy="markdown.sections-paragraphs@2")

    def test_section_nesting_empty_headings_and_exact_boundaries(self) -> None:
        data = b"# A\n## B\n### C\n\nText.\n\n## D\n# E"
        fragments = parse(data)
        self.assertEqual([
            ("SECTION", "a", 0, 28), ("SECTION", "a/b", 4, 23),
            ("SECTION", "a/b/c", 9, 23), ("PARAGRAPH", "a/b/c/paragraph-1", 16, 22),
            ("SECTION", "a/d", 23, 28), ("SECTION", "e", 28, 31),
        ], [(f.fragment_kind, f.location.semantic_locator, f.location.start, f.location.end)
            for f in fragments])

    def test_atx_syntax_duplicate_slugs_and_unicode_byte_offsets(self) -> None:
        data = "# Café ###\n\néén.\n\n## Same\n## Same\n## Same-2\n## !!!\n".encode()
        fragments = parse(data)
        self.assertEqual(["café", "café/paragraph-1", "café/same", "café/same~2",
                          "café/same-2", "café/section"],
                         [f.location.semantic_locator for f in fragments])
        paragraph = fragments[1]
        self.assertEqual("één.\n".encode(), paragraph.location.extract(data))
        self.assertEqual(data.index("één".encode()), paragraph.location.start)

    def test_skipped_heading_levels_follow_heading_stack(self) -> None:
        fragments = parse(b"# A\n### B\n## C\n#### D\n")
        self.assertEqual(["a", "a/b", "a/c", "a/c/d"],
                         [f.location.semantic_locator for f in fragments])

    def test_crlf_and_bare_cr_rejected_without_normalizing(self) -> None:
        for data in (b"# A\r\n\r\nText.\r\n", b"# A\rText."):
            with self.subTest(data=data), self.assertRaisesRegex(CompilerError, "LF"):
                parse(data)

    def test_invalid_utf8_rejected(self) -> None:
        with self.assertRaises(CompilerError):
            parse(b"# A\n\n\xff\n")

    def test_preamble_and_document_without_atx_headings_stay_unsupported(self) -> None:
        self.assertEqual((), parse(b"Preamble only.\n\nAnother paragraph.\n"))
        self.assertEqual((), parse(b""))
        self.assertEqual(["a", "a/paragraph-1"], [f.location.semantic_locator for f in
                         parse(b"Preamble.\n\n# A\n\nSupported.\n")])

    def test_unsupported_blocks_are_never_paragraphs(self) -> None:
        blocks = (
            b"| A | B |\n| --- | --- |\n| 1 | 2 |\n",
            b"A | B\n--- | ---\n1 | 2\n", b"1. Step\n   continuation\n\n   second block\n",
            b"- Item\n  continuation\n\n  another block\n", b"* Item\n", b"+ Item\n",
            b"1) Step\n", b"```text\n# Fake heading\n\nText\n```\n",
            b"~~~\n## Fake heading\n~~~\n", b"    indented code\n",
            b"> Quotation\n", b"<div>HTML</div>\n", b"---\n", b"Title\n=====\n",
            b"Text before list\n- Item\n", b"<div>\n\n# Not a heading\n\n</div>\n",
        )
        for block in blocks:
            with self.subTest(block=block):
                data = b"# A\n\nFirst.\n\n" + block + b"\nLast.\n"
                fragments = parse(data)
                self.assertEqual(["SECTION", "PARAGRAPH", "PARAGRAPH"],
                                 [f.fragment_kind for f in fragments])
                self.assertEqual([b"First.\n", b"Last.\n"],
                                 [f.location.extract(data) for f in fragments[1:]])

    def test_unclosed_and_long_fences_suppress_headings_and_paragraphs(self) -> None:
        for data in (b"# A\n\n```\n# Hidden\n\nText\n",
                     b"# A\n\n````\n```\n# Hidden\n\nText\n````\n"):
            self.assertEqual(["a"], [f.location.semantic_locator for f in parse(data)])

    def test_paragraph_range_excludes_blank_separators_keeps_final_lf(self) -> None:
        data = b"# A\n\n  First\ncontinues.\n \t\nSecond without LF"
        paragraphs = [f for f in parse(data) if f.fragment_kind == "PARAGRAPH"]
        self.assertEqual([b"  First\ncontinues.\n", b"Second without LF"],
                         [f.location.extract(data) for f in paragraphs])

    def test_exact_source_bytes_required(self) -> None:
        data = b"# A\n\nText.\n"
        for ref in (source(data, content_checksum="0" * 64),
                    source(data, checksum_method=CANONICAL_JSON_SHA256),
                    source(data, schema_version="text/plain")):
            with self.assertRaises(CompilerError):
                markdown_fragments.fragment_markdown(ref, data)
        with self.assertRaises(CompilerError):
            markdown_fragments.fragment_markdown("docs/manual.md", data)

    def test_markdown_location_strict_roundtrip(self) -> None:
        for fragment in parse(b"# A\n\nText.\n"):
            record = strict_json_loads(canonical_json(fragment.record()))
            self.assertEqual(fragment, _fragment_from_record(record))
            self.assertEqual(fragment, _fragment_from_record(freeze_json(record)))
            self.assertEqual({"kind", "start", "end", "pointer", "section_path",
                              "paragraph_index"}, set(record["location"]))
            for mutation in (lambda r: r["location"].update(extra=True),
                             lambda r: r["location"].pop("paragraph_index"),
                             lambda r: r.update(extra=True),
                             lambda r: r.update(fragment_id="frag-" + "0" * 64),
                             lambda r: r.update(fragmentation_policy="markdown.sections-paragraphs@2"),
                             lambda r: r["location"].update(paragraph_index=True)):
                bad = strict_json_loads(canonical_json(record))
                mutation(bad)
                with self.assertRaises(CompilerError):
                    _fragment_from_record(bad)

    def test_markdown_location_semantics_and_anchor_cannot_disagree(self) -> None:
        data = b"# A\n\nSame.\n\nSame.\n"
        first = parse(data)[1]
        for location in (replace(first.location, section_path=("wrong",)),
                         replace(first.location, paragraph_index=2),
                         replace(first.location, end=first.location.end - 1),
                         replace(first.location, end=len(data) + 1)):
            with self.subTest(location=location):
                self.assertFalse(replace(first, location=location).verify(data))
                with self.assertRaises(CompilerError):
                    location.extract(data)
        with self.assertRaises(CompilerError):
            replace(first, fragment_kind="SECTION")
        self.assertFalse(first.verify("not bytes"))
        for fields in ({"checksum_method": CANONICAL_JSON_SHA256},
                       {"schema_version": "text/plain"}):
            with self.assertRaises(CompilerError):
                replace(first, source_ref=replace(first.source_ref, **fields))

    def test_markdown_location_constructor_rejects_invalid_fields(self) -> None:
        loc = parse(b"# A\n")[0].location
        for fields in ({"start": True}, {"end": 0}, {"pointer": "/x"},
                       {"section_path": []}, {"section_path": ()},
                       {"section_path": ("a/b",)}, {"paragraph_index": 0},
                       {"paragraph_index": True}):
            with self.subTest(fields=fields), self.assertRaises(CompilerError):
                replace(loc, **fields)

    def test_legacy_location_records_and_identities_are_unchanged(self) -> None:
        for data, loc, method in (
            (b"abc", FragmentLocation.byte_range(0, 2), BYTES_SHA256),
            (b'{"a":1}', FragmentLocation.json_pointer("/a"), CANONICAL_JSON_SHA256),
        ):
            fragment = SourceFragmentRef.from_content(
                source(data, checksum_method=method), data, loc, fragment_kind="TEXT",
                fragmentation_policy="legacy@1")
            self.assertEqual({"kind", "start", "end", "pointer"}, set(loc.record()))
            from tep_agent_lab.persistence import content_checksum
            identity = {key: value for key, value in fragment.record().items()
                        if key != "fragment_id"}
            self.assertEqual("frag-" + content_checksum(identity), fragment.fragment_id)
            self.assertEqual(fragment, _fragment_from_record(fragment.record()))
            with self.assertRaises(CompilerError):
                replace(loc, section_path=("a",))
        with self.assertRaises(CompilerError):
            SourceFragmentRef.from_content(source(b"abc", checksum_method=CANONICAL_JSON_SHA256),
                                           b"abc", FragmentLocation.byte_range(0, 2),
                                           fragment_kind="TEXT", fragmentation_policy="legacy@1")

    def test_generic_module_has_no_domain_identifiers(self) -> None:
        for literal in ("XMEAS", "XMV", "IDV"):
            self.assertNotIn(literal, inspect.getsource(markdown_fragments))

    def test_demo_outputs_subsystem_fragment_tree(self) -> None:
        item = next(item for item in self.corpus() if item.ref.path_or_ref.endswith("/subsystem.md"))
        result = subprocess.run(
            [sys.executable, "-m", "tep_agent_lab.engineering_corpus", "fragments",
             item.ref.source_id], capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parents[1], env=os.environ.copy())
        fragments = markdown_fragments.fragment_markdown(item.ref, item.content)
        expected = "\n".join(markdown_fragments.format_fragment_tree(fragments)) + "\n"
        self.assertEqual(expected, result.stdout)
        self.assertEqual("", result.stderr)
        self.assertEqual(9, len(result.stdout.splitlines()))
        self.assertIn("SECTION reactor-and-cooling-water-loop [0, 968)", result.stdout)


if __name__ == "__main__":
    unittest.main()
