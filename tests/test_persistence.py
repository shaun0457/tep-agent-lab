import tempfile
import unittest
from pathlib import Path

from tep_agent_lab.persistence import CorruptRunLog, RunLog, canonical_json


class RunLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.manifest = {"run_id": "run-1", "lab_revision": "fixture"}
        self.log = RunLog(self.path, self.manifest)

    def test_restart_preserves_accepted_rejected_and_failed_history(self):
        for kind in ("STATE_ACCEPTED", "STATE_REJECTED", "EXPERIMENT_FAILED"):
            self.log.append(kind, {"revision": 1, "detail": "失敗仍保留"})
        reopened = RunLog(self.path)
        self.assertEqual(self.log.events(), reopened.events())
        self.assertEqual([0, 1, 2], [e["sequence"] for e in reopened.events()])

    def test_artifacts_are_immutable_content_addressed_and_detached(self):
        original = {"measurements": [1, 2]}
        checksum = self.log.put_artifact(original)
        original["measurements"].append(3)
        self.assertEqual({"measurements": [1, 2]}, self.log.read_artifact(checksum))
        self.assertEqual(checksum, self.log.put_artifact({"measurements": [1, 2]}))
        changed = self.log.put_artifact(original)
        self.assertNotEqual(checksum, changed)
        self.assertEqual(2, len(list((self.path / "artifacts").iterdir())))

    def test_returned_events_cannot_rewrite_history(self):
        event = self.log.append("OBSERVATION", {"x": [1]})
        event["payload"]["x"].append(2)
        self.assertEqual([1], self.log.events()[0]["payload"]["x"])

    def test_manifest_change_rejected(self):
        with self.assertRaises(ValueError):
            RunLog(self.path, {"run_id": "different"})
        self.assertEqual(self.manifest, self.log.manifest())

    def test_corrupt_event_and_partial_write_fail_closed(self):
        self.log.append("STATE_ACCEPTED", {"revision": 1})
        event_file = self.path / "events.jsonl"
        original = event_file.read_bytes()
        event_file.write_bytes(original.replace(b'"revision":1', b'"revision":2'))
        with self.assertRaises(CorruptRunLog):
            RunLog(self.path)
        event_file.write_bytes(original[:-1])
        with self.assertRaises(CorruptRunLog):
            self.log.append("MORE", {})

    def test_tampered_artifact_and_path_traversal_rejected(self):
        checksum = self.log.put_artifact({"x": 1})
        (self.path / "artifacts" / f"{checksum}.json").write_text("{}")
        with self.assertRaises(CorruptRunLog):
            self.log.read_artifact(checksum)
        with self.assertRaises(ValueError):
            self.log.read_artifact("../manifest")

    def test_invalid_serialization_never_appends(self):
        for bad in ({1: "not string key"}, {"x": float("nan")}, {"x": object()}):
            with self.assertRaises((ValueError, TypeError)):
                self.log.append("INVALID", bad)
        self.assertEqual((), self.log.events())

    def test_mapping_order_is_irrelevant(self):
        self.assertEqual(canonical_json({"a": 1, "b": 2}),
                         canonical_json({"b": 2, "a": 1}))


if __name__ == "__main__":
    unittest.main()
