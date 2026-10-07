"""Canonical bytes, embedded provenance, and no runtime Git fallback in frozen mode."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from tep_agent_lab import e0_demo, sidecar_resources as resources

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sidecar_builder", ROOT / "desktop/sidecar/build_sidecar.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
spec = importlib.util.spec_from_file_location("bundle_verifier", ROOT / "desktop/sidecar/verify_bundle.py")
verifier = importlib.util.module_from_spec(spec)
with mock.patch.object(sys, "path", [str(ROOT / "desktop/sidecar"), *sys.path]):
    spec.loader.exec_module(verifier)


class SidecarResourcesTests(unittest.TestCase):
    def test_installed_host_allows_only_tauris_single_nsis_marker_patch(self):
        raw = b"host-prefix__TAURI_BUNDLE_TYPE_VAR_UNKhost-suffix__TAURI_BUNDLE_TYPE_VAR_UNK"
        with TemporaryDirectory() as directory:
            source, installed = Path(directory) / "source.exe", Path(directory) / "installed.exe"
            source.write_bytes(raw)
            patched = raw.replace(b"__TAURI_BUNDLE_TYPE_VAR_UNK", b"__TAURI_BUNDLE_TYPE_VAR_NSS", 1)
            installed.write_bytes(patched)
            verifier.verify_nsis_host(installed, source)
            for invalid in (raw, patched + b"tampered", patched.replace(b"prefix", b"changed")):
                installed.write_bytes(invalid)
                with self.assertRaises(AssertionError):
                    verifier.verify_nsis_host(installed, source)

    def test_source_mode_uses_canonical_file_and_no_manifest(self):
        with mock.patch.object(resources.sys, "frozen", False, create=True):
            self.assertEqual(ROOT / "dependency-pins.json", resources.dependency_pins_path())
            self.assertIsNone(resources.packaged_lab_revision())

    def test_generated_copy_and_checksum_are_exact_and_frozen_uses_manifest(self):
        revision = "a" * 40
        with TemporaryDirectory() as directory, \
                mock.patch.object(builder.importlib.metadata, "version", return_value="6.22.3"):
            path = Path(directory)
            manifest = builder.generate_resources(ROOT, path, revision)
            canonical = (ROOT / "dependency-pins.json").read_bytes()
            self.assertEqual(canonical, (path / "dependency-pins.json").read_bytes())
            self.assertEqual(hashlib.sha256(canonical).hexdigest(),
                             manifest["dependency_pins_sha256"])
            self.assertEqual(json.loads(canonical), manifest["dependency_pins"])
            with mock.patch.object(resources.sys, "frozen", True, create=True), \
                    mock.patch.object(resources, "resource_directory", return_value=path), \
                    mock.patch.object(e0_demo, "git_revision", side_effect=AssertionError("runtime git")):
                self.assertEqual(revision, resources.packaged_lab_revision())
                with mock.patch.object(e0_demo, "RunManager", side_effect=RuntimeError("after provenance")) as manager:
                    with self.assertRaisesRegex(RuntimeError, "after provenance"):
                        e0_demo.run_demo(path, lab_revision="b" * 40)
                    self.assertEqual(revision, manager.call_args.kwargs["revisions"].tep_agent_lab)
                (path / "dependency-pins.json").write_bytes(canonical + b" ")
                with self.assertRaisesRegex(ValueError, "provenance"):
                    resources.packaged_lab_revision()
                (path / "build-manifest.json").unlink()
                with self.assertRaises(FileNotFoundError):
                    resources.packaged_lab_revision()


if __name__ == "__main__":
    unittest.main()
