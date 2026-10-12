"""LOS-B-S3: clean source, test-only faults, no evaluator material."""

from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from industrial_agent_runtime import to_jsonable

import test_live_plant_session as helpers
from tep_agent_lab.live_plant import LivePlantPolicyV2, LivePlantState
from tep_agent_lab.persistence import RunLog, canonical_json
from tep_agent_lab.playground import LifecycleError, RunManager, RunStatus
from tep_agent_lab.run_telemetry import IngestBackpressure
from tep_agent_lab.telemetry import IdentityConflict, SnapshotUnavailable
from tep_agent_lab.application_views import ApplicationViewService
from tep_agent_lab.application_transport import ApplicationRequest, ApplicationTransport, PROTOCOL_VERSION


class LiveLimitsTests(unittest.TestCase):
    setUp = helpers.LivePlantTests.setUp
    prepare = helpers.LivePlantTests.prepare

    def baseline(self, policy=None):
        wall = helpers.FakeWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        manifest, live = self.prepare(policy or LivePlantPolicyV2(step_count=4))
        query = self.manager.queries("live-test")
        snapshot = query.telemetry_snapshot()["snapshot"]
        signal = live.telemetry.source.signal_ids[0]
        def read():
            return canonical_json(to_jsonable((
                query.telemetry_current(signal, snapshot=snapshot),
                query.telemetry_history(signal, snapshot=snapshot, max_points=10))))
        return manifest, live, query, snapshot, read, read(), wall

    def assert_prefix(self, data):
        manifest, live, query, snapshot, read, before, wall = data
        self.assertEqual(read(), before)
        self.assertEqual(self.manager.manifest("live-test").checksum(), manifest.checksum())
        self.assertEqual(self.manager.get("live-test").status, RunStatus.READY)
        self.assertIsNone(self.manager.get("live-test").outcome)
        self.assertNotIn("diagnostic", str(query.run_summary()))
        self.assertEqual(live.provenance["inventory_checksum"], manifest.context_inventory_checksum)
        inventory = query.registry().inventory(query.scope)
        self.assertEqual(len(inventory), 1)
        # Real application/transport serialization, not just a direct store read.
        transport = ApplicationTransport(ApplicationViewService(self.manager.queries))
        app_snapshot = transport.dispatch(ApplicationRequest(PROTOCOL_VERSION, "snapshot",
            "open_telemetry_snapshot", {"run_id": "live-test", "ingest_sequence": 1}))
        self.assertTrue(app_snapshot.ok)
        response = transport.dispatch(ApplicationRequest(PROTOCOL_VERSION, "read", "get_signal",
            {"run_id": "live-test", "signal_id": live.telemetry.source.signal_ids[0],
             "snapshot": app_snapshot.result}))
        self.assertTrue(response.ok)
        encoded = canonical_json(to_jsonable(response)).decode()
        for hidden in ("PRIVATE", "diagnostic", "injection", "ingest_time", "environment_config"):
            self.assertNotIn(hidden, encoded)

    def capacity(self, cap, value, reason):
        data = self.baseline(LivePlantPolicyV2(step_count=4, **{cap: value}))
        live = data[1]
        with patch.object(live, "_advance", wraps=live._advance) as advance:
            live.start()
            live.wait()
        self.assertEqual(live.state, LivePlantState.STOPPED)
        self.assertEqual(live.events[-1]["reason"], reason)
        self.assertEqual(advance.call_count, 1)
        self.assertEqual(live.telemetry.store.current_ingest_sequence, 2)
        self.assert_prefix(data)

    def test_sample_cap(self):
        self.capacity("retained_sample_cap", 106, "capacity_samples")

    def test_byte_cap(self):
        # Initial actual bytes + one worst-case batch fit; two do not.
        self.capacity("retained_byte_cap", 80_000, "capacity_bytes")

    def test_commit_cap(self):
        self.capacity("commit_cap", 2, "capacity_commits")

    def test_index_cap(self):
        self.capacity("index_record_cap", 106, "capacity_index")

    def test_commit_metadata_cap(self):
        self.capacity("commit_metadata_byte_cap", 2048, "capacity_commit_metadata")

    def test_session_event_cap_preserves_terminal_and_disposal(self):
        data = self.baseline()
        live = data[1]
        # Test-only log saturation; genuine pause/resume admission is covered by S2.
        for _ in range(32):
            live._event(LivePlantState.PREPARED, "test_only_reserved_event")
        with patch.object(live, "_advance", wraps=live._advance) as advance:
            live.start()
            live.wait()
        self.assertEqual(advance.call_count, 0)
        self.assertEqual(live.events[-1]["reason"], "capacity_events")
        self.assertEqual(len(live.events), 35)
        self.assert_prefix(data)
        self.manager.dispose_live("live-test")
        self.manager.dispose_live("live-test")
        self.assertEqual(len(RunLog(live.history.directory).events()), 36)

    def test_zero_simulator_history_cap(self):
        with patch("tep_agent_lab.playground.ReferenceWorld", side_effect=AssertionError("no world history")):
            _, live = self.prepare(LivePlantPolicyV2(step_count=1))
            live.start()
            live.wait()
        self.assertEqual(live.policy.simulator_history_cap, 0)
        self.assertFalse((self.manager._runs["live-test"].session_dir / "world").exists())

    def test_zero_rollout_artifact_cap(self):
        with patch("tep_agent_lab.playground.ArtifactStore", side_effect=AssertionError("no rollout artifacts")), \
                patch("tep_agent_lab.playground.RcaStateStore", side_effect=AssertionError("no RCA assembly")):
            _, live = self.prepare(LivePlantPolicyV2(step_count=1))
            live.start()
            live.wait()
        self.assertEqual(live.policy.rollout_artifact_cap, 0)
        self.assertFalse((self.manager._runs["live-test"].session_dir / "lab-artifacts").exists())

    def test_frame_caps_reject_before_environment_allocation(self):
        from tep_agent_lab.playground import PrepareError
        for field in ("sample_byte_cap", "batch_byte_cap", "association_byte_cap", "event_byte_cap"):
            with self.subTest(field=field), \
                    patch("tep_agent_lab.playground.TEPEnvironment") as environment, \
                    self.assertRaises(PrepareError):
                self.prepare(LivePlantPolicyV2(step_count=1, **{field: 1}))
            environment.assert_not_called()

    def test_two_diagnostics_bounded_and_trusted_only(self):
        data = self.baseline()
        live = data[1]
        close = live._close_physics
        def bad_close():
            close()
            raise RuntimeError("PRIVATE close detail")
        with patch.object(live, "_advance", side_effect=RuntimeError("PRIVATE advance detail")), \
                patch.object(live, "_close_physics", side_effect=bad_close):
            live.start()
            live.wait()
        history = RunLog(live.history.directory)
        diagnostic = history.read_artifact(history.events()[-1]["payload"]["diagnostic_ref"])
        previous = history.read_artifact(diagnostic["prior_diagnostic_ref"])
        self.assertEqual(diagnostic["detail"], "PRIVATE close detail")
        self.assertEqual(previous["detail"], "PRIVATE advance detail")
        self.assertEqual(len(list((history.directory / "artifacts").iterdir())), 2)
        self.assert_prefix(data)
        self.manager.dispose_live("live-test")

    def test_retry_is_same_tuple_no_retranslation_no_step_while_pending(self):
        data = self.baseline()
        live, wall = data[1], data[-1]
        entered, release = threading.Event(), threading.Event()
        batches = []
        commit = live.telemetry.commit_batch
        def refuse(batch):
            batches.append(batch)
            if len(batches) == 1:
                entered.set()
                self.assertTrue(release.wait(10))
                raise IngestBackpressure("test-only refusal")
            return commit(batch)
        with patch.object(live.telemetry, "commit_batch", side_effect=refuse), \
                patch.object(live.telemetry.source, "translate_sanitized",
                             wraps=live.telemetry.source.translate_sanitized) as translate, \
                patch.object(live, "_advance", wraps=live._advance) as advance:
            live.start()
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(advance.call_count, 1)
                self.assertEqual(translate.call_count, 1)
                self.assertEqual(live.telemetry.store.current_ingest_sequence, 1)
                self.assert_prefix(data)
            finally:
                release.set()
                live.wait()
            self.assertEqual(translate.call_count, 4)
            self.assertEqual(advance.call_count, 4)
        self.assertIs(batches[0], batches[1])
        self.assertEqual([c.ingest_sequence for c in live.commits], [1, 2, 3, 4, 5])
        self.assertEqual(wall.delays, [0.1])
        # Accepted batch retries do not consume K or retention again.
        stats = live.telemetry.retained_samples, live.telemetry.retained_bytes
        self.assertEqual(commit(batches[0]).ingest_sequence, 5)
        self.assertEqual(stats, (live.telemetry.retained_samples, live.telemetry.retained_bytes))
        changed = (replace(batches[0][0], value=123.0), *batches[0][1:])
        with self.assertRaises(IdentityConflict):
            commit(changed)
        self.assertEqual(live.telemetry.store.current_ingest_sequence, 5)
        self.assert_prefix(data)

    def test_retry_exhaustion(self):
        data = self.baseline()
        live = data[1]
        with patch.object(live.telemetry, "commit_batch", side_effect=IngestBackpressure("PRIVATE")) as commit:
            live.start()
            live.wait()
        self.assertEqual(commit.call_count, 4)
        self.assertEqual(len(data[-1].delays), 3)
        for delay in data[-1].delays:
            self.assertAlmostEqual(delay, 0.1)
        self.assertEqual(live.state, LivePlantState.FAILED)
        self.assertEqual(live.public_reason, "live_delivery_failed")
        self.assertEqual(live.last_step, 0)
        self.assert_prefix(data)

    def test_fail_closed_source_and_translation(self):
        for fault in ("simulator", "binding", "field", "grid", "nonfinite", "clock", "identity", "owner"):
            with self.subTest(fault=fault):
                data = self.baseline()
                live = data[1]
                advance = live._advance
                translate = live.telemetry.source.translate_sanitized
                def bad_advance():
                    if fault == "simulator":
                        raise RuntimeError("PRIVATE test-only detail")
                    record = dict(advance())
                    if fault == "binding":
                        record["measurements"] = {}
                    if fault == "field":
                        record["PRIVATE"] = "injection metadata"
                    if fault == "grid":
                        record["simulation_time_hours"] = 0.5 / 3600
                    if fault == "nonfinite":
                        record["measurements"] = {k: float("nan") for k in record["measurements"]}
                    return record
                def bad_translate(record):
                    batch = translate(record)
                    if fault == "clock":
                        batch = (replace(batch[0], event_time=replace(batch[0].event_time, clock_id="wrong")), *batch[1:])
                    if fault == "identity":
                        batch = tuple(replace(x, sequence=0) for x in batch)
                    return batch
                def lose_owner():
                    with live._condition:
                        live._owner = None
                    return live._boundary()
                seam = (patch.object(live, "_pace", side_effect=lose_owner) if fault == "owner"
                        else patch.object(live, "_advance", side_effect=bad_advance))
                with seam, patch.object(live.telemetry.source, "translate_sanitized", side_effect=bad_translate):
                    live.start()
                    live.wait()
                self.assertEqual(live.state, LivePlantState.FAILED)
                self.assertEqual(live.last_step, 0)
                self.assertEqual(live.telemetry.store.current_ingest_sequence, 1)
                self.assertEqual(live.public_reason, "live_delivery_failed")
                self.assert_prefix(data)
                history = RunLog(live.history.directory)
                diagnostic = history.read_artifact(history.events()[-1]["payload"]["diagnostic_ref"])
                self.assertIn("detail", diagnostic)
                self.assertNotIn("PRIVATE", str(data[2].run_summary()))
                self.manager.dispose_live("live-test")
                # New run, never re-prepare READY; independent test root.
                self.manager = RunManager(str(self.temp.name) + "/" + fault,
                                          revisions=self.manager.revisions,
                                          dependency_pins=self.manager.dependency_pins,
                                          clock=self.manager.clock)
                self.manager.create("live-test", self.request)

    def test_disposal_restart_and_process_loss(self):
        data = self.baseline()
        live, query, snapshot = data[1:4]
        live.start()
        live.wait()
        self.manager.create("live-new", self.request)
        with self.assertRaises(LifecycleError):
            self.manager.prepare("live-new", provider=helpers.NoModel(), context_sources=self.sources,
                                 live_policy=LivePlantPolicyV2(step_count=1))
        reloaded = RunManager(self.temp.name, revisions=self.manager.revisions,
                              dependency_pins=self.manager.dependency_pins)
        with self.assertRaises(SnapshotUnavailable):
            reloaded.queries("live-test").telemetry_current(live.telemetry.source.signal_ids[0], snapshot=snapshot)
        self.assertEqual(reloaded.manifest("live-test").checksum(), data[0].checksum())
        self.manager.dispose_live("live-test")
        with self.assertRaises(SnapshotUnavailable):
            data[4]()
        with self.assertRaises(SnapshotUnavailable):
            live.telemetry.store.resolve(live.telemetry.resolve_snapshot(snapshot))
        self.assertEqual(live.telemetry.retained_samples, 0)
        self.manager.prepare("live-new", provider=helpers.NoModel(), context_sources=self.sources,
                             live_policy=LivePlantPolicyV2(step_count=1))
        new = self.manager.live_session("live-new")
        self.addCleanup(new.close_prepared)
        self.assertNotEqual(live.telemetry.source_id, new.telemetry.source_id)
        self.assertNotEqual(live.telemetry.source.event_clock, new.telemetry.source.event_clock)
        self.assertNotEqual(live.telemetry.store.ref, new.telemetry.store.ref)
        with self.assertRaises(SnapshotUnavailable):
            new.telemetry.resolve_snapshot(snapshot)
        self.assertEqual(RunLog(live.history.directory).events()[-1]["type"], "DISPOSED")

    def test_prepared_close_releases_environment_and_slot(self):
        _, live = self.prepare(LivePlantPolicyV2(step_count=1))
        self.manager.close_live("live-test")
        self.assertTrue(live.physics_closed)
        self.assertEqual(live.state, LivePlantState.STOPPED)
        self.assertEqual(live.telemetry.retained_samples, 0)
        self.manager.create("live-new", self.request)
        self.manager.prepare("live-new", provider=helpers.NoModel(), context_sources=self.sources,
                             live_policy=LivePlantPolicyV2(step_count=1))
        self.addCleanup(self.manager.live_session("live-new").close_prepared)

    def test_failed_slot_is_held_until_disposal_then_new_prepare_succeeds(self):
        data = self.baseline()
        live = data[1]
        with patch.object(live, "_advance", side_effect=RuntimeError("PRIVATE")):
            live.start()
            live.wait()
        self.manager.create("live-new", self.request)
        with self.assertRaises(LifecycleError):
            self.manager.prepare("live-new", provider=helpers.NoModel(), context_sources=self.sources,
                                 live_policy=LivePlantPolicyV2(step_count=1))
        self.manager.dispose_live("live-test")
        transport = ApplicationTransport(ApplicationViewService(self.manager.queries))
        response = transport.dispatch(ApplicationRequest(PROTOCOL_VERSION, "expired", "open_telemetry_snapshot",
                                                       {"run_id": "live-test"}))
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "SNAPSHOT_UNAVAILABLE")
        self.assertEqual(response.error.message, "snapshot unavailable")
        self.manager.prepare("live-new", provider=helpers.NoModel(), context_sources=self.sources,
                             live_policy=LivePlantPolicyV2(step_count=1))
        self.addCleanup(self.manager.live_session("live-new").close_prepared)

    def test_active_disposal_rejected_without_changing_prefix(self):
        wall = helpers.HeldWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        _, live = self.prepare(LivePlantPolicyV2(step_count=1,
                              pacing=helpers.LivePacingPolicy(mode=helpers.LivePacingMode.REAL_TIME)))
        live.start()
        try:
            self.assertTrue(wall.entered.wait(10))
            with self.assertRaises(LifecycleError):
                self.manager.dispose_live("live-test")
            self.assertEqual(live.telemetry.store.current_ingest_sequence, 1)
        finally:
            live.close_prepared()

    def test_lost_ownership_while_paused_fails_without_another_step(self):
        wall = helpers.HeldWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        _, live = self.prepare(LivePlantPolicyV2(step_count=1,
                              pacing=helpers.LivePacingPolicy(mode=helpers.LivePacingMode.REAL_TIME)))
        owner = live.start()
        try:
            self.assertTrue(wall.entered.wait(10))
            live.pause(owner=owner)
            with live._condition:
                live._owner = None  # Test-only ownership loss, never a public operation.
                live._condition.notify_all()
            live.wait(timeout=1)
            self.assertEqual(live.state, LivePlantState.FAILED)
            self.assertEqual(live.public_reason, "live_delivery_failed")
            self.assertEqual(live.last_step, 0)
        finally:
            live.close_prepared()
