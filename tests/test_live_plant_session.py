"""LOS-B-S1: authorized clean simulated data only; no evaluator fixtures."""

from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from industrial_agent_runtime import Budget, checksum, to_jsonable

from tep_agent_lab.application_views import ApplicationViewService
from tep_agent_lab.canonical_context import ContextSourceError, inventory_checksum, ProjectionScope
from tep_agent_lab.live_plant import LivePlantPolicy, LivePlantState
from tep_agent_lab.persistence import canonical_json
from tep_agent_lab.playground import (LifecycleError, ModelSpec, PrepareError, RunManager,
                                      RunRequest, RunStatus, SourceRevisions, WorldSpec,
                                      load_dependency_pins, pinned_tep_sim_sources)
from tep_agent_lab.run_telemetry import TelemetryReadPolicy, TelemetrySealed
from tep_agent_lab.tep_world import leakage_findings


class NoModel:
    def generate(self, *args, **kwargs):
        raise AssertionError("live mode must not call a model")


class LivePlantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        pins = load_dependency_pins(Path(__file__).parents[1] / "dependency-pins.json")
        self.manager = RunManager(
            self.temp.name,
            revisions=SourceRevisions(pins["tep-sim"], pins["industrial-agent-runtime"], "a" * 40),
            dependency_pins=pins, clock=lambda: "2026-10-12T00:00:00Z")
        # Select only the authorized PROCESS_GRAPH ref; do not resolve hidden material.
        self.sources = tuple(s for s in pinned_tep_sim_sources(pins["tep-sim"])
                             if s.kind == "PROCESS_GRAPH")
        self.request = RunRequest("live-test", "clean stream", WorldSpec(17, record_interval=1),
                                  ModelSpec("unused", "unused", "v0", "v0"), Budget(0, 0, 0, 0, 0),
                                  allowed_tools=(), require_conclusion=False)
        self.manager.create("live-test", self.request)

    def prepare(self, policy=None):
        manifest = self.manager.prepare("live-test", provider=NoModel(),
                                        context_sources=self.sources,
                                        live_policy=policy or LivePlantPolicy(step_count=4))
        live = self.manager.live_session("live-test")
        self.addCleanup(live.wait)
        self.addCleanup(live.close_prepared)
        return manifest, live

    def test_multiple_batches_without_reads_and_frozen_inventory(self):
        manifest, live = self.prepare()
        before = canonical_json(manifest.record())
        inventory = inventory_checksum(self.manager.queries("live-test").registry().inventory(
            ProjectionScope.EVALUATOR))
        with patch.object(self.manager.queries("live-test").registry(), "register",
                          side_effect=AssertionError("sample registered a source")):
            self.manager.start_live("live-test")
            live.wait()
        self.assertEqual(live.state, LivePlantState.STOPPED)
        self.assertIsNone(live.owner)
        self.assertTrue(live.physics_closed)
        self.assertEqual(live.telemetry.store.current_ingest_sequence, 5)
        self.assertEqual([c.step for c in live.commits], list(range(5)))
        self.assertEqual([c.ingest_sequence for c in live.commits], list(range(1, 6)))
        self.assertEqual(before, canonical_json(self.manager.manifest("live-test").record()))
        self.assertEqual(inventory, inventory_checksum(self.manager.queries("live-test").registry()
                                                      .inventory(ProjectionScope.EVALUATOR)))
        self.assertEqual(self.manager.get("live-test").status, RunStatus.READY)
        self.assertIsNone(self.manager.get("live-test").outcome)
        with self.assertRaises(LifecycleError):
            self.manager.start("live-test")
        self.assertEqual(live.provenance["manifest_checksum"], manifest.checksum())
        self.assertEqual(live.provenance["engineering_selection"], ())
        self.assertEqual(live.provenance["policy_checksum"], checksum(to_jsonable(live.policy)))
        with self.assertRaises(ContextSourceError):
            self.manager.queries("live-test").registry().register(self.sources[0])

    def test_mid_delivery_whole_batch_old_snapshots_and_reads_never_step(self):
        _, live = self.prepare()
        queries = self.manager.queries("live-test")
        app = ApplicationViewService(self.manager.queries)
        signal_ids = live.telemetry.source.signal_ids
        old = app.open_telemetry_snapshot("live-test")
        old_current = canonical_json(to_jsonable(app.get_signal("live-test", signal_ids[0],
                                                               snapshot=old)))
        old_history = canonical_json(to_jsonable(app.get_signal_history(
            "live-test", signal_ids[0], snapshot=old)))
        # A source batch has validated its first sample but has not committed yet.
        entered = {n: threading.Event() for n in (1, 2)}
        release = {n: threading.Event() for n in (1, 2)}
        accept = live.telemetry.store._accept

        def blocked(observation, ingest_time):
            value = accept(observation, ingest_time)
            if observation.sequence in entered and observation.signal_id == signal_ids[0]:
                entered[observation.sequence].set()
                if not release[observation.sequence].wait(10):
                    raise AssertionError("test delivery barrier timed out")
            return value

        with patch.object(live.telemetry.store, "_accept", side_effect=blocked):
            self.manager.start_live("live-test")
            try:
                self.assertTrue(entered[1].wait(10))
                with self.assertRaises(LifecycleError):
                    self.manager.start_live("live-test")
                snapshot = queries.telemetry_snapshot()["snapshot"]
                self.assertEqual(snapshot["ingest_sequence"], 1)
                for signal in signal_ids:
                    current = queries.telemetry_current(signal, snapshot=snapshot)
                    self.assertEqual(current["eligible_count"], 1)
                    self.assertEqual(current["sample"]["sequence"], 0)
                self.assertEqual(live.last_step, 0)
                release[1].set()
                self.assertTrue(entered[2].wait(10))
                growing = app.open_telemetry_snapshot("live-test")
                self.assertEqual((growing.event_horizon_ticks, growing.ingest_sequence), (1, 2))
                self.assertEqual(old_current, canonical_json(to_jsonable(app.get_signal(
                    "live-test", signal_ids[0], snapshot=old))))
                self.assertEqual(old_history, canonical_json(to_jsonable(app.get_signal_history(
                    "live-test", signal_ids[0], snapshot=old))))
            finally:
                for event in release.values():
                    event.set()
            live.wait()
        # Read explicit committed prefixes through BOTH public boundaries.
        values = []
        for k in range(1, 6):
            snapshot = app.open_telemetry_snapshot("live-test", ingest_sequence=k)
            self.assertEqual(snapshot.event_horizon_ticks, k - 1)
            values.append(tuple(app.get_signal("live-test", s, snapshot=snapshot).current.value
                                for s in signal_ids))
            public = queries.telemetry_snapshot(ingest_sequence=k)["snapshot"]
            for signal in signal_ids:
                result = queries.telemetry_current(signal, snapshot=public)
                self.assertEqual(result["eligible_count"], k)
                self.assertEqual(result["sample"]["sequence"], k - 1)
                self.assertEqual(leakage_findings(result), [])
                self.assertNotIn("ingest_time", str(result))
                self.assertNotIn("environment_config", str(result))
        self.assertNotEqual(values[0], values[-1])
        self.assertEqual(old_current, canonical_json(to_jsonable(app.get_signal(
            "live-test", signal_ids[0], snapshot=old))))
        self.assertEqual(old_history, canonical_json(to_jsonable(app.get_signal_history(
            "live-test", signal_ids[0], snapshot=old))))
        self.assertEqual(live.last_step, 4)

    def test_default_policy_retains_full_readable_demo_prefix(self):
        manifest, live = self.prepare(LivePlantPolicy())
        self.manager.start_live("live-test")
        live.wait()
        self.assertEqual(live.state, LivePlantState.STOPPED)
        self.assertEqual(len(live.commits), 301)
        self.assertEqual(len(live.events), 3)
        queries = self.manager.queries("live-test")
        pinned = queries.telemetry_snapshot()["snapshot"]
        self.assertEqual((pinned["event_horizon"]["ticks"], pinned["ingest_sequence"]), (300, 301))
        count = 0
        for signal in live.telemetry.source.signal_ids:
            history = queries.telemetry_history(signal, snapshot=pinned, max_points=301)
            self.assertFalse(history["reduced"])
            self.assertEqual(history["returned_count"], 301)
            count += history["returned_count"]
        self.assertEqual(count, 15_953)
        self.assertLessEqual(count, live.policy.retained_sample_cap)
        self.assertEqual(manifest.checksum(), self.manager.manifest("live-test").checksum())

    def test_d0_paths_are_unchanged_without_reading_fixture_contents(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(
            ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "diff",
             "--exit-code", "origin/main", "--", "src/tep_agent_lab/fixtures",
             "tests/test_benchmark.py", "tests/test_benchmark_family.py",
             "tests/test_benchmark_healthy.py", "tests/test_tool_surface.py",
             "tests/test_tool_bridge.py"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_prepared_reads_do_not_start_or_step_and_p0_store_stays_sealed(self):
        _, live = self.prepare()
        for _ in range(3):
            self.manager.queries("live-test").telemetry_snapshot()
        self.assertEqual(live.state, LivePlantState.PREPARED)
        self.assertEqual(live.telemetry.source.next_sequence, 1)
        p0 = self.manager._runs["live-test"].session._telemetry
        self.assertIsNot(p0.store, live.telemetry.store)
        self.assertTrue(p0.sealed)
        with self.assertRaises(TelemetrySealed):
            p0.sync_reference_history(())

    def test_initial_live_ingestion_error_publishes_no_ready(self):
        with patch("tep_agent_lab.run_telemetry.LiveTelemetry.commit_batch",
                   side_effect=ValueError("synthetic ingestion error")):
            with self.assertRaises(PrepareError):
                self.manager.prepare("live-test", provider=NoModel(), context_sources=self.sources,
                                     live_policy=LivePlantPolicy(step_count=4))
        self.assertEqual(self.manager.get("live-test").status, RunStatus.CREATED)
        self.assertIsNone(self.manager.get("live-test").manifest_checksum)
        self.assertEqual(list(Path(self.temp.name).glob("*/prepare-*/manifest.json")), [])

    def test_policy_reserves_full_session_and_reader_prefix(self):
        self.manager.telemetry_policy = TelemetryReadPolicy(max_scan_records=4)
        with self.assertRaises(PrepareError):
            self.prepare(LivePlantPolicy(step_count=4))
        for policy in (replace(LivePlantPolicy(), retained_sample_cap=1),
                       replace(LivePlantPolicy(), retained_byte_cap=1)):
            with self.assertRaises(PrepareError):
                self.prepare(policy)
        with self.assertRaises(ValueError):
            LivePlantPolicy(step_count=True)

    def test_lifecycle_has_no_domain_ids_or_private_delivery_seam(self):
        path = Path(__file__).parents[1] / "src/tep_agent_lab/live_plant.py"
        code = path.read_text(encoding="utf-8")
        for forbidden in ("XMEAS", "XMV", "IDV", "_harness_translate", "_harness_deliver",
                          "rollout(", "history(", "._sim", "snapshot("):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
