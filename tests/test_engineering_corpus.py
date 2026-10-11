"""S1: exact package sources, strict authoring provenance and a read-only demo."""

import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from industrial_agent_runtime import Visibility

from tep_agent_lab import engineering_corpus
from tep_agent_lab.canonical_context import (
    BYTES_SHA256, ContextSourceError, PackageSourceMaterializer, ProjectionScope,
)


class EngineeringCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.materializer = PackageSourceMaterializer("tep_agent_lab")
        self.path = engineering_corpus.DEFAULT_MANIFEST
        self.raw = self.materializer.read_bytes(self.path)
        self.manifest = json.loads(self.raw)

    def load(self, raw: bytes):
        outer = self

        class EditedManifest:
            def read_bytes(self, path: str) -> bytes:
                return raw if path == outer.path else outer.materializer.read_bytes(path)

        return engineering_corpus.load_corpus(materializer=EditedManifest())

    def reject(self, manifest) -> None:
        with self.assertRaises(engineering_corpus.CorpusError):
            self.load(json.dumps(manifest).encode())

    def test_every_document_resolves_to_exact_package_bytes(self) -> None:
        registry = engineering_corpus.register_corpus(
            repository="example/lab", git_revision="c" * 40)
        self.assertTrue(registry.frozen)
        refs = registry.inventory(ProjectionScope.AGENT)
        self.assertEqual(4, len(refs))
        for ref in refs:
            resolved = registry.resolve(ref.source_id, ProjectionScope.AGENT)
            data = self.materializer.read_bytes(ref.path_or_ref)
            self.assertEqual(data, resolved.content)
            self.assertEqual(hashlib.sha256(data).hexdigest(), ref.content_checksum)
            self.assertEqual(BYTES_SHA256, ref.checksum_method)
            self.assertEqual(Visibility.AGENT, ref.visibility)
            self.assertEqual("ENGINEERING_DOCUMENT", ref.kind)
            self.assertEqual("text/markdown", ref.schema_version)
            self.assertEqual(self.manifest["author"], ref.provenance["author"])
        with self.assertRaises(ContextSourceError):
            registry.register(refs[0])

    def test_tampered_byte_fails_before_registration(self) -> None:
        outer = self
        target = self.manifest["documents"][0]["path"]

        class TamperedPackage:
            def read_bytes(self, path: str) -> bytes:
                data = outer.materializer.read_bytes(path)
                return data + b"!" if path == target else data

        with self.assertRaisesRegex(engineering_corpus.CorpusError, "checksum"):
            engineering_corpus.register_corpus(repository="example/lab",
                                               git_revision="c" * 40,
                                               materializer=TamperedPackage())

    def test_changed_source_after_freeze_fails_resolution(self) -> None:
        outer = self

        class MutablePackage:
            tamper = False

            def read_bytes(self, path: str) -> bytes:
                data = outer.materializer.read_bytes(path)
                return data + b"!" if self.tamper else data

        materializer = MutablePackage()
        registry = engineering_corpus.register_corpus(repository="example/lab",
                                                      git_revision="c" * 40,
                                                      materializer=materializer)
        ref = registry.inventory(ProjectionScope.AGENT)[0]
        materializer.tamper = True
        with self.assertRaises(ContextSourceError):
            registry.resolve(ref.source_id, ProjectionScope.AGENT)

    def test_manifest_exact_keys_at_each_object(self) -> None:
        for location in ((), ("documents", 0), ("intentional_conflicts", 0)):
            for action in ("extra", "missing"):
                with self.subTest(location=location, action=action):
                    manifest = json.loads(self.raw)
                    item = manifest
                    for key in location:
                        item = item[key]
                    if action == "extra":
                        item["unexpected"] = "value"
                    else:
                        del item[next(iter(item))]
                    self.reject(manifest)

    def test_manifest_strict_json(self) -> None:
        duplicate = self.raw.replace(b'"author":', b'"author": "forged", "author":', 1)
        nested = self.raw.replace(b'"purpose":', b'"purpose": "forged", "purpose":', 1)
        for data in (duplicate, nested, b'{"value": NaN}', b'{"value": Infinity}',
                     b'{"value": 1e400}', b'{', b'[]', b'\xff'):
            with self.subTest(data=data[:40]):
                with self.assertRaises(engineering_corpus.CorpusError):
                    self.load(data)

    def test_manifest_field_types_and_formats(self) -> None:
        for field, values in {
                "corpus_id": (None, "../bad", "", 1),
                "version": (None, "", True),
                "subsystem": (None, "", []),
                "author": (None, "", False),
                "authored_at": ("2026-02-30", "2026-1-1", 20261011),
                "inputs_consulted": ([], "reference", [None], [""]),
                "leakage_statement": (None, "", {}),
                "documents": ([], None, ["bad"]),
                "intentional_conflicts": (None, "bad", ["bad"]),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.reject({**self.manifest, field: value})

    def test_document_paths_hashes_and_duplicate_identity(self) -> None:
        for field, value in (("path", "../outside.md"), ("path", self.path),
                             ("path", "src/tep_agent_lab/other/document.md"),
                             ("path", "src/tep_agent_lab/fixtures/benchmarks/hidden.md"),
                             ("sha256", "0" * 64), ("sha256", "A" * 64),
                             ("purpose", "")):
            with self.subTest(field=field, value=value):
                manifest = json.loads(self.raw)
                manifest["documents"][0][field] = value
                self.reject(manifest)
        manifest = json.loads(self.raw)
        manifest["documents"].append(manifest["documents"][0])
        self.reject(manifest)

    def test_conflicts_reference_distinct_registered_documents(self) -> None:
        for field, value in (("documents", []), ("documents", ["missing.md", "other.md"]),
                             ("documents", [self.manifest["documents"][0]["path"]] * 2),
                             ("description", ""), ("conflict_id", "../bad")):
            with self.subTest(field=field):
                manifest = json.loads(self.raw)
                manifest["intentional_conflicts"][0][field] = value
                self.reject(manifest)

    def test_leakage_guard_and_public_provenance(self) -> None:
        corpus = engineering_corpus.load_corpus()
        self.assertEqual("2026-10-11", corpus.manifest["authored_at"])
        self.assertEqual("Codex executor (coordinated by Claude Code)",
                         corpus.manifest["author"])
        for document in corpus.manifest["documents"]:
            self.assertNotIn("fixtures/benchmarks", document["path"].lower())
            self.assertIn("/engineering_corpus/", document["path"])
            self.assertLess(len(self.materializer.read_bytes(document["path"]).splitlines()), 120)
        for reference in corpus.manifest["inputs_consulted"]:
            self.assertTrue(reference.startswith("https://"))
            for forbidden in ("benchmark", "scorer", "evaluator", "fixtures"):
                self.assertNotIn(forbidden, reference.lower())

    def test_disallowed_authoring_input_is_rejected(self) -> None:
        for reference in ("src/fixtures/benchmarks/case.json", "docs/scorer-guide.md",
                          "https://example.org/evaluator/truth", "file:///local/source"):
            with self.subTest(reference=reference):
                self.reject({**self.manifest, "inputs_consulted": [reference]})

    def test_manifest_is_immutable(self) -> None:
        corpus = engineering_corpus.load_corpus()
        with self.assertRaises(TypeError):
            corpus.manifest["author"] = "changed"
        with self.assertRaises(TypeError):
            corpus.manifest["documents"][0]["sha256"] = "0" * 64

    def test_generic_module_has_no_domain_identifiers(self) -> None:
        source = inspect.getsource(engineering_corpus)
        for literal in ("XMEAS", "XMV", "IDV"):
            self.assertNotIn(literal, source)

    def test_demo_command_lists_exact_registry_inventory(self) -> None:
        result = subprocess.run([sys.executable, "-m", "tep_agent_lab.engineering_corpus",
                                 "list"], capture_output=True, text=True, check=True,
                                cwd=Path(__file__).resolve().parents[1], env=os.environ.copy())
        lines = result.stdout.strip().splitlines()
        self.assertEqual(4, len(lines))
        registry = engineering_corpus.register_corpus(repository="example/lab",
                                                      git_revision="c" * 40)
        for line, ref in zip(lines, registry.inventory(ProjectionScope.AGENT), strict=True):
            data = self.materializer.read_bytes(ref.path_or_ref)
            self.assertEqual(f"{ref.source_id}\t{ref.path_or_ref}\t{len(data)}\t"
                             f"{ref.content_checksum}", line)
        self.assertEqual("", result.stderr)


if __name__ == "__main__":
    unittest.main()
