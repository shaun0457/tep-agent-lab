"""LOS-B-S3: clean source, test-only faults, no evaluator material."""

from dataclasses import replace
import threading
import unittest
from unittest.mock import PropertyMock, patch

from industrial_agent_runtime import to_jsonable

import test_live_plant_session as helpers
from tep_agent_lab.live_plant import (LivePlantError, LivePlantPolicyV2, LivePlantSession,
                                     LivePlantState, MAX_RETRY_WINDOW_SECONDS)
from tep_agent_lab.persistence import RunLog, canonical_json
from tep_agent_lab.playground import LifecycleError, RunManager, RunStatus
from tep_agent_lab.run_telemetry import IngestBackpressure
from tep_agent_lab.telemetry import IdentityConflict, SnapshotUnavailable
from tep_agent_lab.application_views import ApplicationViewService
from tep_agent_lab.application_transport import ApplicationRequest, ApplicationTransport, PROTOCOL_VERSION


class LiveLimitsTests(unittest.TestCase):
    setUp = helpers.LivePlantTests.setUp
    prepare = helpers.LivePlantTests.prepare

    def baseline(self, policy=None, *, wall=None):
        wall = wall or helpers.FakeWall()
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
        self.assertEqual(diagnostic["category"], "RuntimeError")
        self.assertEqual(previous["category"], "RuntimeError")
        self.assertEqual(diagnostic["reason"], "resource_close_unavailable")
        self.assertEqual(previous["reason"], "source_unavailable")
        for record in (diagnostic, previous):
            self.assertNotIn("detail", record)
            self.assertNotIn("detail_truncated", record)
            self.assertNotIn("PRIVATE", str(record))
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
        for fault in ("simulator", "binding", "field", "grid", "nonfinite", "clock", "identity"):
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
                with patch.object(live, "_advance", side_effect=bad_advance), \
                        patch.object(live.telemetry.source, "translate_sanitized", side_effect=bad_translate):
                    live.start()
                    live.wait()
                self.assertEqual(live.state, LivePlantState.FAILED)
                self.assertEqual(live.last_step, 0)
                self.assertEqual(live.telemetry.store.current_ingest_sequence, 1)
                self.assertEqual(live.public_reason, "live_delivery_failed")
                self.assert_prefix(data)
                history = RunLog(live.history.directory)
                diagnostic = history.read_artifact(history.events()[-1]["payload"]["diagnostic_ref"])
                self.assertNotIn("detail", diagnostic)
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

    def test_paused_owner_cannot_be_replaced_and_host_close_joins(self):
        self.assertFalse(hasattr(LivePlantSession, "_check_owner"),
                         "in-process ownership must not pretend to detect lease loss")
        wall = helpers.HeldWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        _, live = self.prepare(LivePlantPolicyV2(step_count=1,
                              pacing=helpers.LivePacingPolicy(mode=helpers.LivePacingMode.REAL_TIME)))
        owner = live.start()
        try:
            self.assertTrue(wall.entered.wait(10))
            live.pause(owner=owner)
            with self.assertRaises(LifecycleError):
                self.manager.start_live("live-test")
            with self.assertRaises(LifecycleError):
                self.manager.resume_live("live-test", owner="different-owner")
            self.assertEqual(live.owner, owner)
            self.manager.close_live("live-test")
            self.assertEqual(live.state, LivePlantState.STOPPED)
            self.assertIsNone(live.owner)
            self.assertEqual(live.last_step, 0)
        finally:
            live.close_prepared()

    def test_verifier_nul_exception_at_400_byte_cap_always_fails(self):
        data = self.baseline(LivePlantPolicyV2(step_count=4, event_byte_cap=400))
        live = data[1]
        with patch.object(live, "_advance", side_effect=RuntimeError("\x00" * 48)):
            live.start()
            live.wait()
        self.assertEqual(live.state, LivePlantState.FAILED)
        self.assertEqual(live.public_status(), (LivePlantState.FAILED, "live_delivery_failed"))
        event = RunLog(live.history.directory).events()[-1]
        self.assertEqual(event["type"], "FAILED")
        self.assertEqual(event["payload"]["reason"], "source_unavailable")
        diagnostic = live.history.read_artifact(event["payload"]["diagnostic_ref"])
        self.assertNotIn("detail", diagnostic)
        self.assert_prefix(data)

    def test_preparation_reserves_longest_failure_record(self):
        from tep_agent_lab.playground import PrepareError
        # Old resource_close_unavailable frame fit exactly; pending-stop needs more.
        with patch("tep_agent_lab.playground.TEPEnvironment") as environment, \
                self.assertRaises(PrepareError):
            self.prepare(LivePlantPolicyV2(step_count=4, event_byte_cap=318))
        environment.assert_not_called()

    def test_producer_diagnostic_io_failure_keeps_failed_history(self):
        data = self.baseline()
        live = data[1]
        with patch.object(live, "_advance", side_effect=RuntimeError("PRIVATE")), \
                patch.object(live.history, "put_artifact", side_effect=OSError("PRIVATE disk")):
            live.start()
            live.wait()
        self.assertEqual(live.state, LivePlantState.FAILED)
        self.assertIsNone(live.owner)
        event = live.history.events()[-1]
        self.assertEqual(event["type"], "FAILED")
        self.assertEqual(event["payload"]["reason"], "source_unavailable")
        self.assertEqual(event["payload"]["diagnostic_status"], "diagnostic_unavailable")
        self.assertIsNone(event["payload"]["diagnostic_ref"])
        self.manager.dispose_live("live-test")

    def test_close_failure_is_terminal_and_disposable_for_all_diagnostics(self):
        class UnprintableError(RuntimeError):
            def __str__(self):
                raise AssertionError("exception text must never be formatted")

        faults = (UnprintableError(), RuntimeError("\x00" * 48),
                  RuntimeError("PRIVATE" * 1000), RuntimeError("\ud800"))
        for index, fault in enumerate(faults):
            for io_failure in (False, True):
                with self.subTest(index=index, io_failure=io_failure):
                    if index or io_failure:
                        self.manager = RunManager(
                            str(self.temp.name) + f"/close-{index}-{io_failure}",
                            revisions=self.manager.revisions, dependency_pins=self.manager.dependency_pins,
                            clock=self.manager.clock)
                        self.manager.create("live-test", self.request)
                    _, live = self.prepare(LivePlantPolicyV2(step_count=1, event_byte_cap=400))
                    close = live._close_physics
                    def bad_close():
                        close()
                        raise fault
                    writer = live.history.put_artifact
                    with patch.object(live, "_close_physics", side_effect=bad_close), \
                            patch.object(live.history, "put_artifact",
                                         side_effect=OSError("disk unavailable") if io_failure else writer):
                        live.start()
                        live.wait(timeout=1)
                    self.assertEqual(live.state, LivePlantState.FAILED)
                    self.assertIsNone(live.owner)
                    event = live.history.events()[-1]
                    self.assertEqual(event["payload"]["reason"], "resource_close_unavailable")
                    if io_failure:
                        self.assertEqual(event["payload"]["diagnostic_status"], "diagnostic_unavailable")
                    else:
                        diagnostic = live.history.read_artifact(event["payload"]["diagnostic_ref"])
                        self.assertNotIn("detail", diagnostic)
                    self.manager.close_live("live-test")
                    self.manager.dispose_live("live-test")
                    self.assertEqual(live.telemetry.retained_samples, 0)
                    self.manager.create("replacement", self.request)
                    self.manager.prepare("replacement", provider=helpers.NoModel(), context_sources=self.sources,
                                         live_policy=LivePlantPolicyV2(step_count=1))
                    self.addCleanup(self.manager.live_session("replacement").close_prepared)

    def test_terminal_history_io_failure_still_releases_owner_and_notifies(self):
        _, live = self.prepare(LivePlantPolicyV2(step_count=1))
        append = live.history.append
        def fail_terminal(kind, payload):
            if kind in ("FAILED", "STOPPED"):
                raise OSError("disk unavailable")
            return append(kind, payload)
        with patch.object(live.history, "append", side_effect=fail_terminal):
            live.start()
            live.wait(timeout=1)
        self.assertEqual(live.state, LivePlantState.FAILED)
        self.assertIsNone(live.owner)
        self.assertEqual(live.events[-1]["reason"], "terminal_history_unavailable")
        self.manager.dispose_live("live-test")

    def test_close_diagnostic_io_failure_preserves_prior_exact_ref(self):
        _, live = self.prepare(LivePlantPolicyV2(step_count=1, event_byte_cap=400))
        close, writer = live._close_physics, live.history.put_artifact
        refs = []
        def write_once(value):
            if refs:
                raise OSError("second diagnostic unavailable")
            ref = writer(value)
            refs.append(ref)
            return ref
        def bad_close():
            close()
            raise RuntimeError("PRIVATE close")
        with patch.object(live, "_advance", side_effect=RuntimeError("PRIVATE source")), \
                patch.object(live, "_close_physics", side_effect=bad_close), \
                patch.object(live.history, "put_artifact", side_effect=write_once):
            live.start()
            live.wait()
        event = live.history.events()[-1]["payload"]
        self.assertEqual(live.state, LivePlantState.FAILED)
        self.assertEqual(event["reason"], "resource_close_unavailable")
        self.assertEqual(event["diagnostic_status"], "diagnostic_unavailable")
        self.assertEqual(event["diagnostic_ref"], refs[0])
        self.assertEqual(live.history.read_artifact(refs[0])["reason"], "source_unavailable")
        self.manager.dispose_live("live-test")

    def test_host_close_serializes_prepared_disposal_against_start(self):
        _, live = self.prepare(LivePlantPolicyV2(step_count=1))
        disposing, start_attempted, release = (threading.Event() for _ in range(3))
        dispose = live.dispose
        outcomes = {}
        def blocked_dispose():
            disposing.set()
            self.assertTrue(release.wait(2))
            dispose()
        def close():
            try:
                self.manager.close_live("live-test")
                outcomes["close"] = "closed"
            except BaseException as exc:
                outcomes["close"] = exc
        def start():
            start_attempted.set()
            try:
                outcomes["start"] = self.manager.start_live("live-test")
            except LifecycleError:
                outcomes["start"] = "rejected"
        with patch.object(live, "dispose", side_effect=blocked_dispose):
            closer = threading.Thread(target=close)
            starter = threading.Thread(target=start)
            closer.start()
            try:
                self.assertTrue(disposing.wait(2))
                starter.start()
                self.assertTrue(start_attempted.wait(2))
                # Close owns the decision lock through disposal. A nonblocking
                # probe deterministically detects the former split critical sections.
                acquired = live._condition.acquire(blocking=False)
                if acquired:
                    live._condition.release()
                self.assertFalse(acquired)
            finally:
                release.set()
                closer.join(2)
                if starter.ident is not None:
                    starter.join(2)
        self.assertFalse(closer.is_alive())
        self.assertFalse(starter.is_alive())
        self.assertEqual(outcomes, {"close": "closed", "start": "rejected"})
        self.assertEqual(live.telemetry.retained_samples, 0)

    def test_retry_policy_total_wait_is_at_most_one_second(self):
        self.assertEqual(MAX_RETRY_WINDOW_SECONDS, 1.0)
        for count, wait in ((100, 60), (3, 0.34), (1, 1.01)):
            with self.subTest(count=count, wait=wait), self.assertRaises(ValueError):
                LivePlantPolicyV2(retry_count=count, retry_wait_seconds=wait)
        self.assertEqual(LivePlantPolicyV2(retry_count=4, retry_wait_seconds=0.25).retry_count, 4)

    def interrupted_retry(self, *, host_close):
        wall = helpers.HeldWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        data = self.baseline(wall=wall)
        live = data[1]
        finished = threading.Event()
        errors = []
        with patch.object(live.telemetry, "commit_batch", side_effect=IngestBackpressure("PRIVATE")) as commit, \
                patch.object(live, "_advance", wraps=live._advance) as advance, \
                patch.object(live.telemetry.source, "translate_sanitized",
                             wraps=live.telemetry.source.translate_sanitized) as translate:
            owner = self.manager.start_live("live-test")
            def stop():
                try:
                    if host_close:
                        self.manager.close_live("live-test")
                    else:
                        self.manager.stop_live("live-test", owner=owner)
                except LifecycleError as exc:
                    errors.append(exc)
                finally:
                    finished.set()
            stopper = threading.Thread(target=stop)
            try:
                self.assertTrue(wall.entered.wait(2))
                stopper.start()
                # Safety timeout, not a correctness sleep: the sleeper must be
                # interrupted rather than waiting for fake monotonic time to pass.
                self.assertTrue(finished.wait(MAX_RETRY_WINDOW_SECONDS))
            finally:
                live.close_prepared()
                if stopper.ident is not None:
                    stopper.join(2)
            self.assertEqual(commit.call_count, 1)
            self.assertEqual(advance.call_count, 1)
            self.assertEqual(translate.call_count, 1)
        self.assertEqual(len(errors), 0 if host_close else 1)
        self.assertEqual(live.state, LivePlantState.FAILED)
        self.assertIsNone(live.owner)
        self.assertEqual(wall.elapsed, 0)
        self.assertEqual(live.last_step, 0)
        self.assertEqual(live.telemetry.retained_samples, 53)
        reason = "host_close_pending_batch" if host_close else "operator_stop_pending_batch"
        self.assertEqual(live.history.events()[-1]["payload"]["reason"], reason)
        self.assertIsNotNone(live.history.events()[-1]["payload"]["pending_batch_checksum"])
        self.assert_prefix(data)
        self.manager.dispose_live("live-test")

    def test_host_close_interrupts_unresolved_retry(self):
        self.interrupted_retry(host_close=True)

    def test_operator_stop_interrupts_unresolved_retry(self):
        self.interrupted_retry(host_close=False)

    def test_v2_reservation_accepts_caps_above_v0_defaults(self):
        _, live = self.prepare(LivePlantPolicyV2(
            step_count=600, commit_cap=601, commit_metadata_byte_cap=601 * 1024,
            retained_sample_cap=40_000, index_record_cap=40_000, retained_byte_cap=32 * 1024 * 1024))
        self.assertEqual(live.state, LivePlantState.PREPARED)
        self.assertEqual(self.manager.queries("live-test").telemetry_snapshot()["snapshot"]["ingest_sequence"], 1)

    def test_run_summary_uses_one_atomic_state_reason_accessor(self):
        data = self.baseline()
        live = data[1]
        with patch.object(LivePlantSession, "state", new_callable=PropertyMock,
                          side_effect=AssertionError("split state read")), \
                patch.object(LivePlantSession, "public_reason", new_callable=PropertyMock,
                             side_effect=AssertionError("split reason read")), \
                patch.object(live, "public_status", wraps=live.public_status) as status:
            encoded = canonical_json(to_jsonable(data[2].run_summary())).decode()
        status.assert_called_once_with()
        self.assertIn('"state":"PREPARED"', encoded)
        self.assertIn('"reason":null', encoded)

    def test_close_live_translates_session_error(self):
        _, live = self.prepare(LivePlantPolicyV2(step_count=1))
        with patch.object(live, "close_prepared", side_effect=LivePlantError("trusted fixed error")):
            with self.assertRaises(LifecycleError) as caught:
                self.manager.close_live("live-test")
        self.assertIsInstance(caught.exception.__cause__, LivePlantError)

    def test_capacity_stop_precedes_real_time_pacing(self):
        wall = helpers.FakeWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        _, live = self.prepare(LivePlantPolicyV2(step_count=1, commit_cap=1,
            pacing=helpers.LivePacingPolicy(mode=helpers.LivePacingMode.REAL_TIME,
                                           real_time_seconds_per_sim_second=3600)))
        with patch.object(live, "_advance", wraps=live._advance) as advance:
            live.start()
            live.wait()
        self.assertEqual(wall.delays, [])
        advance.assert_not_called()
        self.assertEqual(live.events[-1]["reason"], "capacity_commits")

    def test_commit_serializes_each_sample_once_and_reuses_accounting(self):
        _, live = self.prepare(LivePlantPolicyV2(step_count=1))
        batch = live.telemetry.source.translate_sanitized(live._advance())
        telemetry = live.telemetry
        with patch("tep_agent_lab.run_telemetry.canonical_json", wraps=canonical_json) as encode:
            before = telemetry.retained_bytes
            result = telemetry.commit_batch(batch)
        self.assertEqual(encode.call_count, len(batch))
        exact = sum(len(canonical_json(to_jsonable(record.sample))) for record in result.records)
        self.assertEqual(telemetry.retained_bytes - before, exact)
        stats = telemetry.retained_samples, telemetry.retained_bytes
        duplicate_batch = (batch[0], *batch)  # Same identity twice and all already accepted.
        telemetry.commit_batch(duplicate_batch)
        self.assertEqual((telemetry.retained_samples, telemetry.retained_bytes), stats)
