"""LOS-B-S1: authorized clean simulated data only; no evaluator fixtures."""

from dataclasses import replace
import ast
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from industrial_agent_runtime import Budget, checksum, to_jsonable

from tep_agent_lab.application_views import ApplicationViewService
from tep_agent_lab.canonical_context import ContextSourceError, inventory_checksum, ProjectionScope
from tep_agent_lab.live_plant import (LIVE_POLICY_V1, LivePacingMode, LivePacingPolicy,
                                    LivePlantError, LivePlantPolicy, LivePlantPolicyV1,
                                    LivePlantState, LivePlantSuperseded)
from tep_agent_lab.persistence import RunLog, canonical_json
from tep_agent_lab.playground_views import ViewUnavailable
from tep_agent_lab.playground import (LifecycleError, ModelSpec, PrepareError, RunManager,
                                      RunRequest, RunStatus, SourceRevisions, WorldSpec,
                                      load_dependency_pins, pinned_tep_sim_sources)
from tep_agent_lab.run_telemetry import TelemetryReadPolicy, TelemetrySealed
from tep_agent_lab.tep_world import leakage_findings


class NoModel:
    def generate(self, *args, **kwargs):
        raise AssertionError("live mode must not call a model")


class FakeWall:
    """Independent monotonic and UTC clocks; no real sleeps."""

    def __init__(self):
        self.elapsed = 0.0
        self.utc = "2026-10-12T00:00:00Z"
        self.delays = []

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds, wake):
        self.delays.append(seconds)
        if not wake.is_set():
            self.elapsed += seconds


class HeldWall(FakeWall):
    """Hold a paced batch until an interrupt; synchronize through events."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()

    def sleep(self, seconds, wake):
        self.delays.append(seconds)
        self.entered.set()
        if not wake.wait(10):
            raise AssertionError("pacing barrier timed out")


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

    def lifecycle_policy(self, **kwargs):
        return LivePlantPolicyV1(step_count=4, **kwargs)

    def test_v0_checksum_and_delivery_match_main_5e5bd64(self):
        # Computed from LivePlantPolicy() at main 5e5bd64 (PR #44).
        self.assertEqual(checksum(LivePlantPolicy()),
                         "683ed7f5ae0131504097a1bceaed927e03fee83344d7a2e375b38a43b998ce15")
        self.assertNotIn("pacing", to_jsonable(LivePlantPolicy()))
        _, live = self.prepare()
        self.assertEqual(live.provenance["delivery"], "synchronous-deterministic/v0")
        self.assertNotIn("pacing", live.provenance)
        self.assertNotIn("pacing_checksum", live.provenance)

    def test_policy_versions_bind_event_cap_and_pacing(self):
        for policy, changes in ((LivePlantPolicy, {"session_event_cap": 35}),
                                (LivePlantPolicy, {"version": LIVE_POLICY_V1}),
                                (LivePlantPolicyV1, {"session_event_cap": 3}),
                                (LivePlantPolicyV1, {"version": LivePlantPolicy().version})):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                policy(**changes)
        with self.assertRaises(TypeError):
            LivePlantPolicy(pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME))
        self.assertEqual(LivePlantPolicy().pacing.seconds_per_tick, 0)
        self.assertEqual(LivePlantPolicyV1().session_event_cap, 35)

    def test_invalid_lifecycle_timeouts_never_arm_a_request(self):
        wall = HeldWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        _, live = self.prepare(self.lifecycle_policy(
            pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME)))
        owner = live.start()
        try:
            self.assertTrue(wall.entered.wait(10))
            for timeout in (-1, float("nan"), float("inf"), threading.TIMEOUT_MAX * 2):
                for method in (live.pause, live.stop):
                    with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                        method(owner=owner, timeout=timeout)
                    self.assertIsNone(live._request)
                    self.assertFalse(live._wake.is_set())
                    self.assertEqual(live.state, LivePlantState.ACTIVE)
        finally:
            live.close_prepared()

    def test_wait_exceptions_withdraw_unacknowledged_requests(self):
        _, live = self.prepare(self.lifecycle_policy())
        entered, release = threading.Event(), threading.Event()
        advance = live._advance
        def blocked():
            entered.set()
            if not release.wait(10):
                raise AssertionError("advance barrier timed out")
            return advance()
        with patch.object(live, "_advance", side_effect=blocked):
            owner = live.start()
            try:
                self.assertTrue(entered.wait(10))
                for error in (KeyboardInterrupt, OverflowError):
                    for method in (live.pause, live.stop):
                        with patch.object(live._condition, "wait_for", side_effect=error), \
                                self.assertRaises(error):
                            method(owner=owner)
                        self.assertIsNone(live._request)
                        self.assertFalse(live._wake.is_set())
                        self.assertEqual(live.state, LivePlantState.ACTIVE)
            finally:
                release.set()
                live.close_prepared()

    def test_stop_supersedes_pending_pause_after_inflight_commit(self):
        _, live = self.prepare(self.lifecycle_policy())
        entered, release = threading.Event(), threading.Event()
        commit = live.telemetry.commit_batch
        def blocked(batch):
            entered.set()
            if not release.wait(10):
                raise AssertionError("commit barrier timed out")
            return commit(batch)
        pause_errors, stop_errors = [], []
        def invoke(method, errors):
            try:
                method(owner=owner)
            except BaseException as exc:
                errors.append(exc)
        with patch.object(live.telemetry, "commit_batch", side_effect=blocked):
            owner = live.start()
            pauser = threading.Thread(target=invoke, args=(live.pause, pause_errors))
            stopper = threading.Thread(target=invoke, args=(live.stop, stop_errors))
            try:
                self.assertTrue(entered.wait(10))
                pauser.start()
                with live._condition:
                    self.assertTrue(live._condition.wait_for(lambda: live._request == "pause", 10))
                with self.assertRaises(LivePlantError):
                    live.resume(owner=owner)
                stopper.start()
                with live._condition:
                    self.assertTrue(live._condition.wait_for(lambda: live._request == "stop", 10))
                pauser.join(10)
                self.assertFalse(pauser.is_alive())
                self.assertEqual(len(pause_errors), 1)
                self.assertIsInstance(pause_errors[0], LivePlantSuperseded)
                self.assertEqual(live.last_step, 0)
                release.set()
                stopper.join(10)
                self.assertFalse(stopper.is_alive())
                self.assertEqual(stop_errors, [])
                self.assertEqual(live.last_step, 1)
                self.assertEqual(live.state, LivePlantState.STOPPED)
            finally:
                release.set()
                live.close_prepared()
                for worker in (pauser, stopper):
                    if worker.ident is not None:
                        worker.join(10)

    def test_run_close_stops_active_and_paused_producer_retaining_reads(self):
        for pause in (False, True):
            with self.subTest(pause=pause):
                wall = HeldWall()
                self.manager = RunManager(
                    Path(self.temp.name) / str(pause), revisions=self.manager.revisions,
                    dependency_pins=self.manager.dependency_pins,
                    live_monotonic=wall.monotonic, live_sleeper=wall.sleep)
                self.manager.create("live-test", self.request)
                _, live = self.prepare(self.lifecycle_policy(
                    pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME)))
                owner = live.start()
                try:
                    self.assertTrue(wall.entered.wait(10))
                    if pause:
                        live.pause(owner=owner)
                    before = self.manager.queries("live-test").telemetry_snapshot()
                    self.manager._runs["live-test"].session.close()
                    self.assertEqual(live.state, LivePlantState.STOPPED)
                    self.assertEqual(live.events[-1]["reason"], "host_close")
                    self.assertFalse(live._thread.is_alive())
                    self.assertIsNone(live.owner)
                    self.assertTrue(live.physics_closed)
                    self.assertEqual(before, self.manager.queries("live-test").telemetry_snapshot())
                    self.manager._runs["live-test"].session.close()
                finally:
                    live.close_prepared()

    def test_pacing_bounds_reject_platform_overflow(self):
        from tep_agent_lab.live_plant import MAX_WALL_SECONDS_PER_TICK
        for value in (MAX_WALL_SECONDS_PER_TICK + 1, threading.TIMEOUT_MAX * 2):
            for mode in LivePacingMode:
                with self.subTest(value=value, mode=mode), self.assertRaises(ValueError):
                    LivePacingPolicy(mode=mode, real_time_seconds_per_sim_second=value)
        self.assertLessEqual(LivePacingPolicy(mode=LivePacingMode.REAL_TIME,
                                            real_time_seconds_per_sim_second=3600).seconds_per_tick,
                             threading.TIMEOUT_MAX)

    def test_sleeper_clock_faults_have_distinct_pacing_reason(self):
        for fault in ("clock", "nonfinite_clock", "sleeper"):
            self.manager = RunManager(
                Path(self.temp.name) / fault, revisions=self.manager.revisions,
                dependency_pins=self.manager.dependency_pins)
            self.manager.create("live-test", self.request)
            _, live = self.prepare(self.lifecycle_policy(
                pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME)))
            clock = (Mock(side_effect=[0, RuntimeError("clock fault")]) if fault == "clock"
                     else Mock(side_effect=[0, float("nan")]) if fault == "nonfinite_clock"
                     else lambda: 0)
            with patch.object(live, "_monotonic", clock), \
                    patch.object(live, "_sleeper", side_effect=RuntimeError("sleeper fault")):
                live.start()
                live.wait()
            self.assertEqual(live.state, LivePlantState.FAILED)
            self.assertEqual(live.events[-1]["reason"], "pacing_error")
            self.assertEqual(live.last_step, 0)

    def test_start_clock_failure_closes_without_leaving_owner_armed(self):
        _, live = self.prepare(self.lifecycle_policy())
        with patch.object(live, "_monotonic", side_effect=RuntimeError("clock fault")), \
                self.assertRaises(RuntimeError):
            live.start()
        self.assertEqual(live.events[-1]["reason"], "pacing_error")
        self.assertEqual(live.state, LivePlantState.FAILED)
        self.assertIsNone(live.owner)
        self.assertTrue(live.physics_closed)

    def test_withdrawn_pause_bursts_never_advance_before_deadline(self):
        wall = FakeWall()
        entered = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        def sleep(seconds, wake):
            index = len(wall.delays)
            wall.delays.append(seconds)
            entered[index].set()
            if not release[index].wait(10):
                raise AssertionError("fake sleeper barrier timed out")
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, sleep
        _, live = self.prepare(LivePlantPolicyV1(step_count=1,
            pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME)))
        advance = Mock(wraps=live._advance)
        delivered = []
        commit = live.telemetry.commit_batch
        def capture(batch):
            delivered.append(wall.elapsed)
            return commit(batch)
        with patch.object(live, "_advance", advance), \
                patch.object(live.telemetry, "commit_batch", side_effect=capture):
            owner = live.start()
            try:
                self.assertTrue(entered[0].wait(10))
                for _ in range(20):
                    with self.assertRaisesRegex(LivePlantError, "boundary has not resolved"):
                        live.pause(owner=owner, timeout=0)
                release[0].set()
                self.assertTrue(entered[1].wait(10))
                advance.assert_not_called()
                self.assertIsNone(live._pending)
                self.assertEqual(live.telemetry.source.next_sequence, 1)
                self.assertEqual(live.last_step, 0)
                self.assertEqual(wall.delays, [1, 1])
                wall.elapsed = 1
                release[1].set()
                live.wait()
                self.assertEqual(delivered, [1])
                self.assertEqual([c.source_sequence for c in live.commits], [0, 1])
            finally:
                for barrier in release:
                    barrier.set()
                live.close_prepared()

    def test_pacing_has_no_derived_step_anchor(self):
        _, live = self.prepare(self.lifecycle_policy())
        self.assertFalse(hasattr(live, "_anchor_step"))

    def test_pause_during_pacing_acknowledges_without_advancing(self):
        wall = HeldWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        _, live = self.prepare(self.lifecycle_policy(
            pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME)))
        owner = live.start()
        try:
            self.assertTrue(wall.entered.wait(10))
            live.pause(owner=owner)
            self.assertEqual(live.last_step, 0)
            self.assertEqual(live.telemetry.source.next_sequence, 1)
            self.assertIsNone(live._pending)
            wall.elapsed += 100
            wall.entered.clear()
            live.resume(owner=owner)
            self.assertTrue(wall.entered.wait(10))
            self.assertEqual(wall.delays, [1, 1])
            self.assertEqual(live.last_step, 0)
        finally:
            live.close_prepared()

    def test_demo_real_time_milestone_timeout_covers_gap(self):
        from tep_agent_lab.live_plant_demo import commit_timeout
        pacing = LivePacingPolicy(mode=LivePacingMode.REAL_TIME)
        self.assertEqual(commit_timeout(pacing, 100), 130)
        self.assertEqual(commit_timeout(LivePacingPolicy(), 100), 30)

    def test_demo_rejects_deterministic_lifecycle_before_preparation(self):
        from tep_agent_lab import live_plant_demo
        with patch.object(sys, "argv", ["live-demo", "lifecycle", "--pacing", "DETERMINISTIC"]), \
                patch.object(live_plant_demo, "RunManager") as manager, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
            live_plant_demo.main()
        self.assertEqual(exit.exception.code, 2)
        manager.assert_not_called()

    def test_demo_cleanup_always_joins_preserves_original_error_and_is_idempotent(self):
        from tep_agent_lab.live_plant_demo import cleanup_live
        live = Mock()
        live.close_prepared.side_effect = LivePlantError("already ended")
        cleanup_live(live, suppress_errors=True)
        live.wait.assert_called_once_with()
        live.close_prepared.side_effect = None
        cleanup_live(live, suppress_errors=False)
        cleanup_live(live, suppress_errors=False)
        self.assertEqual(live.wait.call_count, 3)
        live.close_prepared.side_effect = LivePlantError("close failed")
        live.wait.side_effect = LivePlantError("join failed")
        cleanup_live(live, suppress_errors=True)
        self.assertEqual(live.wait.call_count, 4)

    def test_pacing_replays_identical_prepared_inputs_and_order(self):
        trajectories, delays = [], []
        for mode in LivePacingMode:
            wall = FakeWall()
            self.manager = RunManager(
                Path(self.temp.name) / mode.value, revisions=self.manager.revisions,
                dependency_pins=self.manager.dependency_pins, clock=lambda: wall.utc,
                live_monotonic=wall.monotonic, live_sleeper=wall.sleep)
            self.manager.create("live-test", self.request)
            # Fix incarnation before preparation; ids really agree across modes.
            with patch("secrets.token_hex", return_value="c" * 32):
                _, live = self.prepare(self.lifecycle_policy(pacing=LivePacingPolicy(mode=mode)))
            batches = []
            commit = live.telemetry.commit_batch
            def capture(batch):
                batches.append(canonical_json(to_jsonable(batch)))
                return commit(batch)
            with patch.object(live.telemetry, "commit_batch", side_effect=capture):
                self.manager.start_live("live-test")
                live.wait()
            trajectories.append((batches, live.commits))
            delays.append(sum(wall.delays))
            self.assertEqual(live.provenance["pacing"],
                             to_jsonable(live.policy.pacing))
        self.assertEqual(trajectories[0], trajectories[1])
        self.assertEqual(trajectories[0], trajectories[2])
        self.assertEqual(delays, [0, 0.2, 4.0])

    def test_pause_boundary_resume_and_operator_stop_keep_exact_reads(self):
        wall = FakeWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        self.manager.clock = lambda: wall.utc
        manifest, live = self.prepare(self.lifecycle_policy(
            pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME)))
        identities = (live.session_id, live.telemetry.source_id,
                      live.telemetry.source.event_clock, live.telemetry.store.ref)
        app = ApplicationViewService(self.manager.queries)
        old = app.open_telemetry_snapshot("live-test")
        signal = live.telemetry.source.signal_ids[0]
        def old_reads():
            return canonical_json(to_jsonable((
                app.get_signal("live-test", signal, snapshot=old),
                app.get_signal_history("live-test", signal, snapshot=old))))
        before = old_reads()
        entered, release = threading.Event(), threading.Event()
        commit = live.telemetry.commit_batch
        def blocked(batch):
            entered.set()
            if not release.wait(10):
                raise AssertionError("commit barrier timed out")
            return commit(batch)
        errors = []
        def pause():
            try:
                self.manager.pause_live("live-test", owner=owner)
            except BaseException as exc:
                errors.append(exc)
        with patch.object(live.telemetry, "commit_batch", side_effect=blocked):
            owner = self.manager.start_live("live-test")
            self.assertTrue(entered.wait(10))
            worker = threading.Thread(target=pause)
            worker.start()
            try:
                with live._condition:
                    self.assertTrue(live._condition.wait_for(lambda: live._request == "pause", 10))
                self.assertEqual(live.state, LivePlantState.ACTIVE)
                self.assertEqual(live.last_step, 0)
                self.assertEqual(before, old_reads())
                with self.assertRaises(LifecycleError):
                    self.manager.resume_live("live-test", owner=owner)
            finally:
                release.set()
                worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(live.state, LivePlantState.PAUSED)
            frozen = (live.commits, live.telemetry.source.next_sequence,
                      app.open_telemetry_snapshot("live-test"))
            wall.elapsed += 100
            wall.utc = "2026-10-11T00:00:00Z"  # UTC reverses, monotonic does not.
            self.assertEqual(frozen, (live.commits, live.telemetry.source.next_sequence,
                                     app.open_telemetry_snapshot("live-test")))
            self.assertEqual(before, old_reads())
            # Hold the next in-flight batch so stop is also proved at a boundary.
            entered.clear()
            release.clear()
            self.manager.resume_live("live-test", owner=owner)
            self.assertTrue(entered.wait(10))
            self.assertEqual(before, old_reads())
            stop_errors = []
            def stop():
                try:
                    self.manager.stop_live("live-test", owner=owner)
                except BaseException as exc:
                    stop_errors.append(exc)
            stopper = threading.Thread(target=stop)
            stopper.start()
            with live._condition:
                self.assertTrue(live._condition.wait_for(lambda: live._request == "stop", 10))
            self.assertEqual(live.state, LivePlantState.ACTIVE)
            release.set()
            stopper.join(10)
            self.assertFalse(stopper.is_alive())
            self.assertEqual(stop_errors, [])
        self.assertEqual([c.step for c in live.commits], [0, 1, 2])
        self.assertEqual([c.source_sequence for c in live.commits], [0, 1, 2])
        self.assertEqual(wall.delays, [1.0, 1.0])
        self.assertEqual(identities, (live.session_id, live.telemetry.source_id,
                                     live.telemetry.source.event_clock, live.telemetry.store.ref))
        self.assertEqual(live.state, LivePlantState.STOPPED)
        self.assertTrue(live.physics_closed)
        self.assertIsNone(live.owner)
        self.assertEqual(before, old_reads())
        self.assertEqual(manifest.checksum(), self.manager.manifest("live-test").checksum())
        self.assertEqual(manifest.context_inventory_checksum, inventory_checksum(
            self.manager.queries("live-test").registry().inventory(ProjectionScope.EVALUATOR)))
        self.assertEqual([e["type"] for e in RunLog(live.history.directory).events()],
                         ["PREPARED", "ACTIVE", "PAUSED", "ACTIVE", "STOPPED"])
        self.assertEqual([(e["last_step"], e["ingest_sequence"]) for e in live.events],
                         [(0, 1), (0, 1), (1, 2), (1, 2), (2, 3)])
        self.assertEqual(live.events[-1]["reason"], "operator_stop")
        self.assertEqual(self.manager.get("live-test").status, RunStatus.READY)
        self.assertIsNone(self.manager.get("live-test").outcome)

    def test_late_delivery_reanchors_without_catchup_or_skips(self):
        wall = FakeWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        _, live = self.prepare(self.lifecycle_policy(
            pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME)))
        advance, delivered = live._advance, []
        def slow_first_step():
            if live.last_step == 0:
                wall.elapsed += 5
            return advance()
        commit = live.telemetry.commit_batch
        def capture(batch):
            delivered.append(wall.elapsed)
            return commit(batch)
        with patch.object(live, "_advance", side_effect=slow_first_step), \
                patch.object(live.telemetry, "commit_batch", side_effect=capture):
            self.manager.start_live("live-test")
            live.wait()
        self.assertEqual(delivered, [6, 7, 8, 9])
        self.assertEqual([c.event_ticks for c in live.commits], [0, 1, 2, 3, 4])
        self.assertEqual([c.source_sequence for c in live.commits], [0, 1, 2, 3, 4])

    def test_owner_token_illegal_transitions_and_stop_from_paused(self):
        wall = HeldWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        _, live = self.prepare(self.lifecycle_policy(
            pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME)))
        for method in (live.pause, live.resume, live.stop):
            with self.assertRaises(LivePlantError):
                method(owner="not-the-owner")
        owner = self.manager.start_live("live-test")
        self.assertTrue(wall.entered.wait(10))
        try:
            self.assertEqual(owner, live.owner)
            with self.assertRaises(LifecycleError):
                self.manager.start_live("live-test")
            with self.assertRaises(LivePlantError):
                live.start()
            for method in (live.pause, live.resume, live.stop):
                with self.assertRaises(LivePlantError):
                    method(owner="not-the-owner")
            with self.assertRaises(LifecycleError):
                self.manager.resume_live("live-test", owner=owner)
            self.manager.pause_live("live-test", owner=owner)
            cursor = live.commits
            for operation in (self.manager.start_live,
                              lambda run: self.manager.pause_live(run, owner=owner)):
                with self.assertRaises(LifecycleError):
                    operation("live-test")
            self.assertEqual(live.owner, owner)
            self.manager.stop_live("live-test", owner=owner)
            self.assertEqual(live.commits, cursor)
        finally:
            if live.owner is not None:
                live.stop(owner=owner)
        for method in (live.pause, live.resume, live.stop):
            with self.assertRaises(LivePlantError):
                method(owner=owner)
        with self.assertRaises(LifecycleError):
            self.manager.start_live("live-test")
        self.assertEqual(live.events[-1]["reason"], "operator_stop")
        self.assertTrue(live.physics_closed)

    def test_event_cap_reserves_resume_and_terminal_without_changing_v0(self):
        self.assertEqual(LivePlantPolicy().session_event_cap, 3)
        wall = HeldWall()
        self.manager.live_monotonic, self.manager.live_sleeper = wall.monotonic, wall.sleep
        policy = self.lifecycle_policy(
            pacing=LivePacingPolicy(mode=LivePacingMode.REAL_TIME))
        _, live = self.prepare(policy)
        owner = self.manager.start_live("live-test")
        try:
            for _ in range(16):
                self.assertTrue(wall.entered.wait(10))
                self.manager.pause_live("live-test", owner=owner)
                wall.entered.clear()
                self.manager.resume_live("live-test", owner=owner)
            self.assertTrue(wall.entered.wait(10))
            events = live.events
            with self.assertRaises(LifecycleError):
                self.manager.pause_live("live-test", owner=owner)
            self.assertEqual(live.events, events)
            self.assertEqual(live.state, LivePlantState.ACTIVE)
        finally:
            self.manager.stop_live("live-test", owner=owner)
        self.assertEqual(len(live.events), 35)
        self.assertEqual(len(RunLog(live.history.directory).events()), 35)
        self.assertEqual(live.events[-1]["reason"], "operator_stop")

    def test_unresolved_batch_never_acknowledges_pause(self):
        _, live = self.prepare(self.lifecycle_policy())
        entered, release = threading.Event(), threading.Event()
        commit = live.telemetry.commit_batch
        def blocked(batch):
            entered.set()
            if not release.wait(10):
                raise AssertionError("test barrier timed out")
            return commit(batch)
        with patch.object(live.telemetry, "commit_batch", side_effect=blocked):
            owner = self.manager.start_live("live-test")
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaisesRegex(LifecycleError, "boundary has not resolved"):
                    self.manager.pause_live("live-test", owner=owner, timeout=0)
                self.assertEqual(live.state, LivePlantState.ACTIVE)
                self.assertEqual(live.last_step, 0)
                self.assertNotIn("PAUSED", [e["state"] for e in live.events])
                self.assertIsNotNone(live._pending)
            finally:
                release.set()
                live.wait()

    def test_lifecycle_stays_outside_reads_transport_and_task_status(self):
        from industrial_agent_runtime import TaskStatus
        from tep_agent_lab.playground_views import RunQueries
        _, live = self.prepare()
        self.assertEqual({s.value for s in RunStatus},
                         {"CREATED", "READY", "RUNNING", "COMPLETED", "FAILED"})
        self.assertNotIn("PAUSED", {s.value for s in TaskStatus})
        self.assertNotIn("STOPPED", {s.value for s in TaskStatus})
        for surface in (RunQueries, ApplicationViewService):
            for operation in ("start_live", "pause_live", "resume_live", "stop_live",
                              "live_session", "step"):
                self.assertFalse(hasattr(surface, operation))
        self.assertEqual(live.state, LivePlantState.PREPARED)

    def test_invalid_pacing_parameters_reject_before_physics(self):
        for changes in ({"mode": "REAL_TIME"}, {"version": "future"},
                        {"acceleration_factor": 0.5}, {"acceleration_factor": True},
                        {"real_time_seconds_per_sim_second": float("nan")},
                        {"real_time_seconds_per_sim_second": float("inf")},
                        {"real_time_seconds_per_sim_second": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                LivePacingPolicy(**changes)

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

    def test_live_suite_has_no_git_process_dependency(self):
        # The D0 diff is a verifier gate, never a shallow-checkout unit-test dependency.
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names]
        self.assertNotIn("subprocess", imports)

    def test_lifecycle_history_survives_session_and_manager_loss(self):
        _, live = self.prepare()
        path = live.history.directory
        prepared = RunLog(path).events()
        self.manager.start_live("live-test")
        live.wait()
        history = RunLog(path)
        events = history.events()
        self.assertEqual(events[:1], prepared)
        self.assertEqual([e["type"] for e in events], ["PREPARED", "ACTIVE", "STOPPED"])
        terminal = events[-1]["payload"]
        self.assertEqual((terminal["last_step"], terminal["ingest_sequence"]), (4, 5))
        self.assertEqual(terminal["reason"], "configured_steps_reached")
        self.assertIsNone(terminal["pending_batch_checksum"])
        self.assertIsNone(terminal["diagnostic_ref"])
        public = self.manager.queries("live-test", ProjectionScope.EVALUATOR).events()
        self.assertNotIn("configured_steps_reached", str(public))
        self.manager._runs.clear()
        self.assertEqual(RunLog(path).events(), events)

    def test_failed_history_keeps_exact_diagnostic_and_committed_prefix(self):
        for failure in ("source_unavailable", "cadence_mismatch", "ingest_translation_error",
                        "resource_close_unavailable"):
            with self.subTest(failure=failure):
                run_id = "failure-" + failure.replace("_", "-")
                self.manager.create(run_id, self.request)
                self.manager.prepare(run_id, provider=NoModel(), context_sources=self.sources,
                                     live_policy=LivePlantPolicy(step_count=2))
                live = self.manager.live_session(run_id)
                self.addCleanup(live.wait)
                self.addCleanup(live.close_prepared)
                patches = []
                if failure == "source_unavailable":
                    patches.append(patch.object(live, "_advance", side_effect=RuntimeError("SECRET")))
                elif failure == "cadence_mismatch":
                    translate = live.telemetry.source.translate_sanitized
                    def off_grid(record):
                        return tuple(replace(item, event_time=replace(item.event_time, ticks=99))
                                     for item in translate(record))
                    patches.append(patch.object(live.telemetry.source, "translate_sanitized",
                                                side_effect=off_grid))
                elif failure == "ingest_translation_error":
                    patches.append(patch.object(live.telemetry, "commit_batch",
                                                side_effect=ValueError("SECRET")))
                else:
                    close = live._close_physics
                    def bad_close():
                        close()
                        raise RuntimeError("SECRET")
                    patches.append(patch.object(live, "_close_physics", side_effect=bad_close))
                with patches[0]:
                    self.manager.start_live(run_id)
                    live.wait()
                history = RunLog(live.history.directory)
                terminal = history.events()[-1]["payload"]
                self.assertEqual(live.state, LivePlantState.FAILED)
                self.assertEqual(terminal["reason"], failure)
                last_step = 2 if failure == "resource_close_unavailable" else 0
                self.assertEqual((terminal["last_step"], terminal["ingest_sequence"]),
                                 (last_step, last_step + 1))
                diagnostic = history.read_artifact(terminal["diagnostic_ref"])
                self.assertEqual(diagnostic["reason"], failure)
                self.assertEqual(diagnostic["pending_batch_checksum"],
                                 terminal["pending_batch_checksum"])
                self.assertEqual(diagnostic["ingest_sequence"], last_step + 1)
                self.assertNotIn("SECRET", str(history.events()) + str(diagnostic))
                if failure in ("cadence_mismatch", "ingest_translation_error"):
                    self.assertEqual(terminal["pending_batch_checksum"], checksum(to_jsonable(live._pending)))
                # Each manager reserves one retained session; use another manager for the next case.
                self.manager = RunManager(Path(self.temp.name) / failure,
                                          revisions=self.manager.revisions,
                                          dependency_pins=self.manager.dependency_pins,
                                          clock=self.manager.clock)

    def test_provenance_is_bound_to_ready_and_verified_after_reload(self):
        _, live = self.prepare()
        run = self.manager._runs["live-test"]
        ready = run.lifecycle.events()[-1]["payload"]
        self.assertEqual(ready["live_provenance_checksum"], checksum(to_jsonable(live.provenance)))
        path = run.session_dir / "live-session-provenance.json"
        value = json.loads(path.read_bytes())
        value["policy_checksum"] = "f" * 64
        path.write_bytes(canonical_json(value))
        with self.assertRaises(LifecycleError):
            self.manager.manifest("live-test")
        with self.assertRaises(LifecycleError):
            self.manager.live_session("live-test")
        self.manager._runs.clear()
        with self.assertRaises(LifecycleError):
            self.manager.manifest("live-test")

    def test_full_trajectory_must_fit_every_host_reader_bound(self):
        for policy in (TelemetryReadPolicy(max_scan_records=5),
                       TelemetryReadPolicy(max_points_limit=4),
                       TelemetryReadPolicy(max_interval_ticks=3)):
            with self.subTest(policy=policy):
                self.manager = RunManager(Path(self.temp.name) / str(policy.max_scan_records)
                                          / str(policy.max_points_limit) / str(policy.max_interval_ticks),
                                          revisions=self.manager.revisions,
                                          dependency_pins=self.manager.dependency_pins,
                                          clock=self.manager.clock, telemetry_policy=policy)
                self.manager.create("live-test", self.request)
                with self.assertRaises(PrepareError):
                    self.prepare(LivePlantPolicy(step_count=4))
                self.assertEqual(self.manager.get("live-test").status, RunStatus.CREATED)

    def test_live_summary_reports_attachment_and_legacy_telemetry_is_unavailable(self):
        _, live = self.prepare()
        queries = self.manager.queries("live-test")
        self.assertEqual(queries.run_summary()["resources"]["session"], "LIVE")
        entered, release = threading.Event(), threading.Event()
        advance = live._advance
        def blocked():
            entered.set()
            if not release.wait(10):
                raise AssertionError("test barrier timed out")
            return advance()
        with patch.object(live, "_advance", side_effect=blocked):
            self.manager.start_live("live-test")
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(queries.run_summary()["resources"]["session"], "LIVE")
                with self.assertRaises(ViewUnavailable):
                    queries.telemetry()
            finally:
                release.set()
                live.wait()
        self.assertEqual(queries.run_summary()["resources"]["session"], "RELEASED")
        with self.assertRaises(ViewUnavailable):
            queries.telemetry()

    def test_demo_uses_shared_resource_and_revision_discovery(self):
        from tep_agent_lab import live_plant_demo
        pins_path = Path(__file__).parents[1] / "dependency-pins.json"
        with patch.object(sys, "argv", ["live-demo", "live"]), \
                patch.object(live_plant_demo, "dependency_pins_path", return_value=pins_path) as pins, \
                patch.object(live_plant_demo, "packaged_lab_revision", return_value="b" * 40) as packaged, \
                patch.object(live_plant_demo, "git_revision", side_effect=AssertionError("no Git in package")), \
                patch.object(live_plant_demo, "RunManager", side_effect=RuntimeError("discovery complete")) as manager:
            with self.assertRaisesRegex(RuntimeError, "discovery complete"):
                live_plant_demo.main()
        pins.assert_called_once_with()
        packaged.assert_called_once_with()
        self.assertEqual(manager.call_args.kwargs["revisions"].tep_agent_lab, "b" * 40)

    def test_reserve_and_batch_serialization_happen_once(self):
        import tep_agent_lab.live_plant as module
        reserve = LivePlantPolicy.reserve
        calls = []
        def counted(policy, telemetry):
            calls.append(telemetry)
            return reserve(policy, telemetry)
        with patch.object(LivePlantPolicy, "reserve", counted):
            _, live = self.prepare()
        self.assertEqual(len(calls), 1)
        batches = []
        encode = module.to_jsonable
        def counted_encode(value):
            if isinstance(value, tuple) and value and hasattr(value[0], "signal_id"):
                batches.append(value)
            return encode(value)
        with patch.object(module, "to_jsonable", side_effect=counted_encode):
            self.manager.start_live("live-test")
            live.wait()
        self.assertEqual(len(batches), 4)
        self.assertEqual([c.batch_checksum for c in live.commits[1:]],
                         [checksum(to_jsonable(batch)) for batch in batches])

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
        paths = list(Path(self.temp.name).glob("*/lifecycle/live-*"))
        self.assertEqual(len(paths), 1)
        history = RunLog(paths[0])
        event = history.events()[-1]
        self.assertEqual(event["type"], "FAILED")
        self.assertEqual(event["payload"]["reason"], "ingest_translation_error")
        self.assertEqual((event["payload"]["last_step"], event["payload"]["ingest_sequence"]),
                         (None, 0))
        diagnostic = history.read_artifact(event["payload"]["diagnostic_ref"])
        self.assertNotIn("synthetic ingestion error", str(diagnostic))

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
