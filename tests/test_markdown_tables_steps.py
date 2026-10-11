"""S3 bounded table/list grammar, exact refs and policy-v1 compatibility."""

from dataclasses import replace
import hashlib
import inspect
import os
from pathlib import Path
import subprocess
import sys
import unittest

from industrial_agent_runtime.serialization import freeze_json

from tep_agent_lab import engineering_corpus, markdown_fragments as md
from tep_agent_lab.canonical_context import CANONICAL_JSON_SHA256, ProjectionScope
from tep_agent_lab.context_compiler import (
    CompilerError, FragmentLocation, _fragment_from_record,
)
from tep_agent_lab.persistence import canonical_json, content_checksum, strict_json_loads
from test_markdown_fragments import source


def parse(data: bytes, **changes):
    return md.fragment_markdown(source(data, **changes), data, policy=md.TABLES_STEPS_POLICY)


class MarkdownTablesStepsTests(unittest.TestCase):
    def corpus(self):
        registry = engineering_corpus.register_corpus(
            repository="example/lab", git_revision="c" * 40)
        return tuple(registry.resolve(ref.source_id, ProjectionScope.AGENT)
                     for ref in registry.inventory(ProjectionScope.AGENT)
                     if ref.path_or_ref.endswith(("/tags-and-limits.md", "/operating-procedure.md")))

    def test_strict_identity_location_checksum_golden_and_determinism(self):
        raw = Path(__file__).with_name("markdown_tables_steps_golden.json").read_bytes()
        golden = strict_json_loads(raw)
        self.assertEqual({"policy", "sources"}, set(golden))
        self.assertEqual(md.TABLES_STEPS_POLICY, golden["policy"])
        actual = {}
        for item in self.corpus():
            first = md.fragment_markdown(item.ref, item.content, policy=md.TABLES_STEPS_POLICY)
            second = md.fragment_markdown(item.ref, item.content, policy=md.TABLES_STEPS_POLICY)
            self.assertEqual(canonical_json([f.record() for f in first]),
                             canonical_json([f.record() for f in second]))
            # Exact source refs are independently checked by the round-trip test;
            # avoid duplicating inherited author/provenance text in the golden.
            actual[item.ref.source_id] = [{key: value for key, value in f.record().items()
                                          if key != "source_ref"} for f in first]
        self.assertEqual(golden["sources"], actual)
        self.assertEqual([11, 13], [len(rows) for rows in actual.values()])

    def test_every_row_step_reextracts_and_roundtrips(self):
        counts = {"TABLE_ROW": 0, "PROCEDURE_STEP": 0}
        for item in self.corpus():
            for f in md.fragment_markdown(item.ref, item.content, policy=md.TABLES_STEPS_POLICY):
                exact = item.content[f.location.start:f.location.end]
                self.assertEqual(exact, f.location.extract(item.content))
                self.assertEqual(hashlib.sha256(exact).hexdigest(), f.content_checksum)
                self.assertTrue(f.verify(item.content))
                record = strict_json_loads(canonical_json(f.record()))
                self.assertEqual(f, _fragment_from_record(record))
                self.assertEqual(f, _fragment_from_record(freeze_json(record)))
                identity = {key: value for key, value in record.items() if key != "fragment_id"}
                self.assertEqual("frag-" + content_checksum(identity), f.fragment_id)
                if f.fragment_kind in counts:
                    counts[f.fragment_kind] += 1
        self.assertEqual({"TABLE_ROW": 4, "PROCEDURE_STEP": 5}, counts)

    def test_row_edit_changes_only_row_table_and_ancestor_diff_keys(self):
        old = b"# Root\n\n## Data\n\n| A | B |\n| --- | --- |\n| x | y |\n| z | w |\n\nAfter.\n\n## End\n\nSame.\n"
        new = old.replace(b"| x | y |", b"| longer cell | y |")
        before, after = parse(old), parse(new, git_revision="d" * 40)
        self.assertEqual([f.location.semantic_locator for f in before],
                         [f.location.semantic_locator for f in after])
        changed = {a.location.semantic_locator for a, b in zip(before, after, strict=True)
                   if md.diff_key(a) != md.diff_key(b)}
        self.assertEqual({"root", "root/data", "root/data/table-1", "root/data/table-1/row-1"}, changed)
        self.assertTrue(all(a.fragment_id != b.fragment_id
                            for a, b in zip(before, after, strict=True)))
        self.assertTrue(all(f.verify(new) for f in after))
        self.assertNotEqual(before[-1].location.start, after[-1].location.start)

    def test_continuations_are_included_and_step_ordinal_is_not_marker_number(self):
        data = "# Café\n\n3. First\n   continuation é.\n1. Second\n1. Third without LF".encode()
        steps = parse(data)[1:]
        self.assertEqual(["PROCEDURE_STEP"] * 3, [f.fragment_kind for f in steps])
        self.assertEqual(["café/list-1/step-1", "café/list-1/step-2", "café/list-1/step-3"],
                         [f.location.semantic_locator for f in steps])
        self.assertEqual("3. First\n   continuation é.\n".encode(), steps[0].location.extract(data))
        self.assertEqual(b"1. Third without LF", steps[-1].location.extract(data))

    def test_indices_are_per_innermost_section_and_block(self):
        data = (b"# A\n\n1) One\n2) Two\n\nText.\n\n1. Three\n\n"
                b"| H |\n| :---: |\n| a |\n\n| H |\n| --- |\n| b |\n\n## B\n\n1. Four\n")
        self.assertEqual(["a", "a/list-1/step-1", "a/list-1/step-2", "a/paragraph-1",
                          "a/list-2/step-1", "a/table-1", "a/table-1/row-1",
                          "a/table-2", "a/table-2/row-1", "a/b", "a/b/list-1/step-1"],
                         [f.location.semantic_locator for f in parse(data)])

    def test_top_level_indented_items_remain_supported_across_blank_blocks(self):
        data = b"# A\n\n  1. First\n     continuation\n  2. Second\n\n  1. Third\n"
        self.assertEqual(["a", "a/list-1/step-1", "a/list-1/step-2", "a/list-2/step-1"],
                         [f.location.semantic_locator for f in parse(data)])

    def test_cells_are_derived_from_verified_header_and_exact_row(self):
        data = b"# A\n\nName | Value\n:--- | ---:\na\\|b | c\n"
        row = parse(data)[-1]
        self.assertEqual("TABLE_ROW", row.fragment_kind)
        self.assertEqual({"Name": "a|b", "Value": "c"}, md.table_row_cells(row, data))
        self.assertEqual(b"a\\|b | c\n", row.location.extract(data))
        self.assertNotIn("cells", row.record())
        with self.assertRaises(CompilerError):
            md.table_row_cells(row, data.replace(b"Name", b"Other"))
        with self.assertRaises(CompilerError):
            md.table_row_cells(parse(data)[0], data)
        with self.assertRaises(CompilerError):
            md.table_row_cells(replace(row, content_checksum="0" * 64), data)

    def assert_unsupported(self, block):
        data = b"# A\n\nFirst.\n\n" + block + b"\nLast.\n"
        fragments = parse(data)
        self.assertEqual(["SECTION", "PARAGRAPH", "PARAGRAPH"], [f.fragment_kind for f in fragments])
        self.assertEqual([b"First.\n", b"Last.\n"],
                         [f.location.extract(data) for f in fragments[1:]])

    def test_unordered_lists_stay_unclassified(self):
        for block in (b"- Item\n  continuation\n", b"+ Item\n", b"* Item\n",
                      b"1. Step\n   - Nested item\n", b"Prose\n- Item\n",
                      b"- Item\n\n  1. Nested ordered\n"):
            with self.subTest(block=block):
                self.assert_unsupported(block)

    def test_fenced_code_stays_unclassified(self):
        for block in (b"```\n| H |\n| --- |\n| v |\n1. Step\n# Hidden\n```\n",
                      b"~~~\n1. Step\n~~~\n", b"1. Step\n   ```\n   code\n   ```\n"):
            with self.subTest(block=block):
                self.assert_unsupported(block)
        self.assertEqual(["a"], [f.location.semantic_locator for f in
                                parse(b"# A\n\n```\n1. Step\n# Hidden\n")])

    def test_html_stays_unclassified(self):
        for block in (b"<div>\n\n1. Step\n| H |\n| --- |\n| v |\n# Hidden\n</div>\n",
                      b"<!--\n1. Step\n-->\n", b"<div>inline</div>\n"):
            with self.subTest(block=block):
                self.assert_unsupported(block)

    def test_malformed_tables_stay_wholly_unclassified(self):
        for block in (b"| A | B |\n| --- | --- |\n| one |\n",
                      b"| A | B |\n| --- | --- |\n| one | two |\n| extra | cells | bad |\n",
                      b"| A | B |\n| -- | --- |\n| x | y |\n",
                      b"| A | A |\n| --- | --- |\n| x | y |\n",
                      b"| | B |\n| --- | --- |\n| x | y |\n",
                      b"Prose\n| A |\n| --- |\n| x |\n"):
            with self.subTest(block=block):
                self.assert_unsupported(block)

    def test_other_unsupported_blocks_and_preamble(self):
        for block in (b"    code\n", b"> Quote\n", b"---\n", b"Title\n=====\n",
                      b"Prose\n1. Step\n", b"1. Step\nplain continuation\n",
                      b"1. Step\n   1. Nested ordered\n"):
            with self.subTest(block=block):
                self.assert_unsupported(block)
        self.assertEqual((), parse(b"1. Step\n\n| H |\n| --- |\n| v |\n"))
        nested = parse(b"# A\n\n1. Top\n\n   1. Nested\n")
        self.assertEqual(["SECTION", "PROCEDURE_STEP"], [f.fragment_kind for f in nested])

    def test_new_location_keys_are_strict_and_legacy_shapes_unchanged(self):
        data = b"# A\n\n| H |\n| --- |\n| v |\n\n1. Step\n"
        for fragment in parse(data):
            record = fragment.location.record()
            base = {"kind", "start", "end", "pointer", "section_path", "paragraph_index"}
            extra = ({"table_index", "row_index"} if fragment.fragment_kind in ("TABLE", "TABLE_ROW")
                     else {"list_index", "step_index"} if fragment.fragment_kind == "PROCEDURE_STEP"
                     else set())
            self.assertEqual(base | extra, set(record))
            for key in extra:
                missing = dict(record)
                missing.pop(key)
                with self.assertRaises(CompilerError):
                    FragmentLocation.from_record(missing)
            for extra_key in ("extra", "list_index" if "table_index" in extra else "table_index"):
                bad = dict(record, **{extra_key: None})
                with self.assertRaises(CompilerError):
                    FragmentLocation.from_record(bad)

    def test_invalid_new_locator_fields_and_policy_kind_pairing(self):
        data = b"# A\n\n| H |\n| --- |\n| v |\n\n1. Step\n"
        row, step = parse(data)[2:]
        for fragment, fields in ((row, {"table_index": True}), (row, {"row_index": 0}),
                                 (row, {"table_index": None}), (row, {"list_index": 1}),
                                 (row, {"paragraph_index": 1}), (step, {"step_index": False}),
                                 (step, {"list_index": 0}), (step, {"step_index": None})):
            with self.subTest(fields=fields), self.assertRaises(CompilerError):
                replace(fragment.location, **fields)
        for fragment in (row, step):
            for fields in ({"fragmentation_policy": md.PARSER_POLICY}, {"fragment_kind": "PARAGRAPH"},
                           {"source_ref": replace(fragment.source_ref, schema_version="text/plain")},
                           {"source_ref": replace(fragment.source_ref, checksum_method=CANONICAL_JSON_SHA256)}):
                with self.subTest(fields=fields), self.assertRaises(CompilerError):
                    replace(fragment, **fields)
        for loc in (FragmentLocation.byte_range(0, 2), FragmentLocation.json_pointer("/a")):
            with self.assertRaises(CompilerError):
                replace(loc, table_index=1)

    def test_forged_semantics_or_byte_anchor_never_verify(self):
        data = b"# A\n\n| H |\n| --- |\n| same |\n| same |\n\n1. Same\n2. Same\n"
        row, step = parse(data)[2], parse(data)[4]
        for fragment, changes in ((row, {"row_index": 2}), (row, {"table_index": 2}),
                                  (step, {"list_index": 2}), (step, {"step_index": 2}),
                                  (row, {"start": row.location.start + 1}),
                                  (row, {"end": len(data) + 1}), (row, {"section_path": ("wrong",)})):
            loc = replace(fragment.location, **changes)
            self.assertFalse(replace(fragment, location=loc).verify(data))
            with self.assertRaises(CompilerError):
                loc.extract(data)

    def test_unknown_policy_bad_bytes_rejected(self):
        for data in (b"# A\r\n", b"# A\n\xff"):
            with self.assertRaises(CompilerError):
                parse(data)
        with self.assertRaises(CompilerError):
            md.fragment_markdown(source(b"# A\n"), b"# A\n", policy="unknown@1")

    def test_v1_output_is_available_and_unchanged(self):
        for item in self.corpus():
            self.assertEqual(md.fragment_markdown(item.ref, item.content),
                             md.fragment_markdown(item.ref, item.content, policy=md.PARSER_POLICY))
            self.assertFalse(any(f.fragment_kind in ("TABLE", "TABLE_ROW", "PROCEDURE_STEP")
                                 for f in md.fragment_markdown(item.ref, item.content)))

    def test_generic_module_has_no_domain_identifiers(self):
        for literal in ("XMEAS", "XMV", "IDV"):
            self.assertNotIn(literal, inspect.getsource(md))

    def test_rows_demo_and_policy_selection(self):
        item = next(i for i in self.corpus() if i.ref.path_or_ref.endswith("/tags-and-limits.md"))
        env = os.environ.copy()

        def demo(*args):
            return subprocess.run([sys.executable, "-m", "tep_agent_lab.engineering_corpus", *args,
                                   "--repository", "example/lab", "--git-revision", "c" * 40],
                                  capture_output=True, text=True, check=True, env=env)

        result = demo("rows", item.ref.source_id)
        fragments = md.fragment_markdown(item.ref, item.content, policy=md.TABLES_STEPS_POLICY)
        rows = [f for f in fragments if f.fragment_kind == "TABLE_ROW"]
        expected = "\n".join(f"TABLE_ROW {f.location.semantic_locator} "
                             f"[{f.location.start}, {f.location.end}) "
                             f"sha256:{f.content_checksum[:12]} cells:"
                             f"{canonical_json(md.table_row_cells(f, item.content)).decode()}" for f in rows) + "\n"
        self.assertEqual(expected, result.stdout)
        self.assertEqual(4, len(result.stdout.splitlines()))
        self.assertEqual("", result.stderr)
        self.assertEqual("\n".join(md.format_fragment_tree(fragments)) + "\n",
                         demo("fragments", item.ref.source_id).stdout)
        legacy = md.fragment_markdown(item.ref, item.content)
        self.assertEqual("\n".join(md.format_fragment_tree(legacy)) + "\n",
                         demo("fragments", item.ref.source_id, "--policy", md.PARSER_POLICY).stdout)


if __name__ == "__main__":
    unittest.main()
