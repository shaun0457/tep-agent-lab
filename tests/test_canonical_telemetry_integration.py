"""P1.1C acceptance: canonical telemetry through the live P0 wiring.

docs/p1-1c-canonical-telemetry-integration.md. Every test prepares a real P0 run
(``RunManager`` + TEP + ``FakeProvider``) and reads through the public P0 boundary
(``RunQueries``) and, where a read exists there, the v1 application boundary
(``ApplicationViewService``). Late, out-of-order, duplicate, conflict and same-tick
deliveries enter through the run-scoped trusted seam (``RunSession.telemetry``
``translate``/``deliver``): ``ReferenceWorld.history()`` cannot produce them in P0 v0.
"""

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest import mock

from industrial_agent_runtime import FakeProvider, to_jsonable
from tep_sim import ControlMode, MVIntervention

from tep_agent_lab import playground, playground_views, run_telemetry
from tep_agent_lab.application_transport import (ApplicationTransport, ErrorCode,
                                                 PROTOCOL_VERSION)
from tep_agent_lab.application_views import ApplicationViewService
from tep_agent_lab.canonical_context import ContextSourceRef, ProjectionScope
from tep_agent_lab.playground import PrepareError, RunManager, RunStatus, WorldSpec
from tep_agent_lab.playground_views import ViewUnavailable
from tep_agent_lab.run_telemetry import TelemetryReadPolicy, context_ref_for
from tep_agent_lab.telemetry import (IdentityConflict, ResourceLimitExceeded,
                                     SnapshotUnavailable, TelemetryError, UnknownBinding)
from tep_agent_lab.tep_telemetry import TEPSourceError
from tep_agent_lab.tep_world import leakage_findings
from test_playground import (PINS, REVISIONS, PlaygroundCase, harness_case, request, turn)

SRC = Path(playground.__file__).parent
TEMPERATURE = "reactor.temperature_measurement"
PRESSURE = "reactor.pressure_measurement"
COOLING = "reactor_cooling.flow_actuator"
OFFSET = 1000.0


def plain(view):
    return json.loads(json.dumps(to_jsonable(view)))


class LiveTelemetryCase(PlaygroundCase):
    """One fresh prepared run per test: several tests mutate the run store."""

    run_id = "run-1"

    def setUp(self):
        super().setUp()
        self.prepare_run()

    def prepare_run(self, run_id=None, case_setup=harness_case, **changes):
        run_id = run_id or self.run_id
        self.manager.create(run_id, request(**changes))
        self.manager.prepare(run_id, provider=FakeProvider([turn()]),
                             context_sources=self.sources(), case_setup=case_setup)
        return run_id

    def session(self, run_id=None):
        return self.manager._runs[run_id or self.run_id].session

    def telemetry(self, run_id=None):
        return self.session(run_id).telemetry

    def queries(self, run_id=None, scope=ProjectionScope.AGENT):
        return self.manager.queries(run_id or self.run_id, scope)

    def history(self, run_id=None):
        return self.session(run_id).world.history()

    def snapshot(self, run_id=None, **options):
        return plain(self.queries(run_id).telemetry_snapshot(**options))["snapshot"]

    def ticks(self, record):
        return round(record["simulation_time_hours"] * 3600)

    def runtime_id(self, signal_id):
        return self.telemetry().source.binding(signal_id).runtime_variable_id

    def value(self, record, signal_id):
        runtime = self.runtime_id(signal_id)
        group = "measurements" if runtime in record["measurements"] else "manipulated_variables"
        return record[group][runtime]

    def shifted(self, record, *, ticks=None, offset=OFFSET):
        """Sanitized-shape record on the run clock with distinguishable values."""
        changed = json.loads(json.dumps(to_jsonable(record)))
        if ticks is not None:
            changed["simulation_time_hours"] = ticks / 3600
        for group in ("measurements", "manipulated_variables"):
            changed[group] = {key: value + offset for key, value in changed[group].items()}
        return changed

    def deliver(self, record):
        telemetry = self.telemetry()
        batch = telemetry.translate(record)
        return batch, telemetry.deliver(batch)

    # Reads through both public boundaries: P0 RunQueries and the v1 application service.
    def app(self):
        return ApplicationViewService(self.manager.queries)

    def app_snapshot(self, snapshot):
        model = self.app().open_telemetry_snapshot(
            self.run_id, event_horizon_ticks=snapshot["event_horizon"]["ticks"],
            ingest_sequence=snapshot["ingest_sequence"])
        self.assertEqual(snapshot["snapshot_ref"], model.snapshot_ref)
        return model

    def current(self, snapshot, signal_id=TEMPERATURE):
        view = plain(self.queries().telemetry_current(signal_id, snapshot=snapshot))
        signal = plain(self.app().get_signal(self.run_id, signal_id,
                                             snapshot=self.app_snapshot(snapshot)))
        sample = view["sample"]
        self.assertEqual((signal["source_id"], signal["signal_id"], signal["status"],
                          signal["eligible_count"]),
                         (view["source_id"], view["signal_id"], view["status"],
                          view["eligible_count"]))
        self.assertEqual(signal["current"], None if sample is None else {
            "event_ticks": sample["event_ticks"], "sequence": sample["sequence"],
            "value": sample["value"], "quality": sample["quality"]})
        return {**view, "application": signal}

    def window(self, snapshot, signal_id=TEMPERATURE, start=None, end=None, max_points=100):
        view = plain(self.queries().telemetry_history(
            signal_id, snapshot=snapshot, event_start_ticks=start, event_end_ticks=end,
            max_points=max_points))
        history = plain(self.app().get_signal_history(
            self.run_id, signal_id, snapshot=self.app_snapshot(snapshot),
            event_start_ticks=start, event_end_ticks=end, max_points=max_points))
        self.assertEqual([(s["source_id"], s["signal_id"], s["sequence"], s["event_ticks"],
                           s["value"], s["quality"]) for s in view["samples"]],
                         [(history["source_id"], history["signal_id"], p["sequence"],
                           p["event_ticks"], p["value"], p["quality"])
                          for p in history["points"]])
        for key in ("event_start_ticks", "event_end_ticks", "eligible_count", "returned_count",
                    "reduced", "reduction_policy", "coverage"):
            self.assertEqual(view[key], history[key], key)
        return {**view, "application": history}

    def last_event(self, snapshot):
        view = plain(self.queries().telemetry_last_event(snapshot=snapshot))
        run = plain(self.app().get_run(self.run_id, snapshot=self.app_snapshot(snapshot)))
        self.assertEqual((view["event_ticks"], view["snapshot"]["snapshot_ref"]),
                         (run["telemetry_event_ticks"], run["snapshot_ref"]))
        return {**view, "application": run}


class IngestionTests(LiveTelemetryCase):
    def test_p0_prepare_ingests_reference_history_through_tep_source(self):
        history, telemetry = self.history(), self.telemetry()
        self.assertGreater(len(history), 5)
        self.assertEqual(len(history), telemetry.store.current_ingest_sequence)
        final = self.snapshot()
        self.assertEqual((final["ingest_sequence"], final["event_horizon"]["ticks"]),
                         (len(history), self.ticks(history[-1])))
        samples = self.window(final, max_points=1000)["samples"]
        self.assertEqual(list(range(len(history))), [s["sequence"] for s in samples])
        self.assertEqual([self.ticks(r) for r in history], [s["event_ticks"] for s in samples])
        for k, record in enumerate(history, start=1):
            snapshot = self.snapshot(ingest_sequence=k)
            for signal in (TEMPERATURE, COOLING):
                with self.subTest(k=k, signal=signal):
                    current = self.current(snapshot, signal)
                    self.assertEqual((current["sample"]["sequence"], current["sample"]["value"]),
                                     (k - 1, self.value(record, signal)))
        self.assertEqual(53, len(telemetry.source.signal_ids))
        for signal in telemetry.source.signal_ids:
            with self.subTest(signal=signal):
                self.assertEqual(self.current(final, signal)["sample"]["value"],
                                 self.value(history[-1], signal))

    def test_store_context_ref_is_the_attested_process_graph_source(self):
        manifest = self.manager.manifest(self.run_id)
        graph = [ContextSourceRef(**{**source, "provenance": dict(source["provenance"]),
                                     "governance": dict(source["governance"])})
                 for source in to_jsonable(manifest.canonical_context_sources)
                 if source["kind"] == "PROCESS_GRAPH"]
        self.assertEqual(1, len(graph))
        expected = context_ref_for(graph[0])
        self.assertEqual(expected, self.snapshot()["context_ref"])
        self.assertEqual(expected, self.telemetry().store.context_ref)
        binding = self.telemetry().source.binding(TEMPERATURE)
        self.assertEqual(graph[0].provenance["fixture_content_sha256"],
                         binding.graph_content_sha256)

    def test_prepare_fails_closed_when_ingestion_fails(self):
        with mock.patch.object(run_telemetry.RunTelemetry, "sync_reference_history",
                               side_effect=TEPSourceError("bad record")):
            self.manager.create("run-2", request())
            with self.assertRaises(PrepareError):
                self.manager.prepare("run-2", provider=FakeProvider([turn()]),
                                     context_sources=self.sources(), case_setup=harness_case)
        self.assertEqual(RunStatus.CREATED, self.manager.get("run-2").status)
        self.assertEqual([], self.manifests_on_disk("run-2"))

    def test_public_telemetry_ids_hide_prepare_attempts(self):
        def broken(world):
            raise RuntimeError("hidden setup failure")
        self.manager.create("run-2", request())
        with self.assertRaises(PrepareError):
            self.manager.prepare("run-2", provider=FakeProvider([turn()]),
                                 context_sources=self.sources(), case_setup=broken)
        self.manager.prepare("run-2", provider=FakeProvider([turn()]),
                             context_sources=self.sources(), case_setup=harness_case)
        self.assertEqual("prepare-0002", self.session("run-2").directory.name)
        for run_id in ("run-1", "run-2"):
            snapshot = self.snapshot(run_id)
            views = [self.queries(run_id).telemetry_snapshot(),
                     self.queries(run_id).telemetry_current(TEMPERATURE, snapshot=snapshot),
                     self.queries(run_id).telemetry_history(TEMPERATURE, snapshot=snapshot,
                                                            max_points=5),
                     self.queries(run_id).telemetry_last_event(snapshot=snapshot)]
            text = json.dumps(plain(views))
            token = self.telemetry(run_id)._token
            with self.subTest(run_id=run_id):
                self.assertRegex(token, r"\A[0-9a-f]{32}\Z")
                self.assertNotIn("prepare", text.lower())
                self.assertEqual(snapshot["store"], {
                    "store_id": "p0-reference-telemetry", "incarnation": f"inc-{token}",
                    "namespace": f"p0-run:{run_id}:{token}"})
                self.assertEqual(snapshot["event_clock"]["clock_id"], f"tep-sim:{run_id}:{token}")
                self.assertEqual(snapshot["event_clock"]["origin"], f"tep-reset:{run_id}:{token}")
                source_ids = {s["source_id"] for s in plain(views[2])["samples"]}
                self.assertEqual({f"tep-reference:{run_id}:{token}"}, source_ids)
                self.assertEqual(len(self.history(run_id)), snapshot["ingest_sequence"])
                self.assertEqual(0, plain(views[2])["samples"][0]["sequence"])

    def test_snapshot_survives_run_and_is_unavailable_after_reopen(self):
        snapshot = self.snapshot()

        def telemetry_reads():   # run status may change; pinned telemetry may not
            last = self.last_event(snapshot)
            return (self.current(snapshot), self.window(snapshot),
                    {k: v for k, v in last.items() if k != "application"},
                    last["application"]["telemetry_event_ticks"])

        before = telemetry_reads()
        outcome = self.manager.start(self.run_id)
        self.assertEqual(RunStatus.COMPLETED, outcome.terminal_status)
        self.assertEqual(before, telemetry_reads())
        self.assertEqual(snapshot, self.snapshot())   # nothing is ingested while RUNNING
        reopened_manager = self.make_manager()
        reopened = reopened_manager.queries(self.run_id)
        transport = ApplicationTransport(ApplicationViewService(reopened_manager.queries))
        response = transport.dispatch({
            "protocol_version": PROTOCOL_VERSION, "request_id": "req",
            "method": "open_telemetry_snapshot", "params": {"run_id": self.run_id}})
        self.assertEqual(ErrorCode.VIEW_UNAVAILABLE, response.error.code)
        with self.assertRaises(ViewUnavailable):
            reopened.telemetry_snapshot()
        with self.assertRaises(ViewUnavailable):
            reopened.telemetry_current(TEMPERATURE, snapshot=snapshot)


class SnapshotContractTests(LiveTelemetryCase):
    def test_reads_require_an_exact_snapshot(self):
        snapshot = self.snapshot()
        queries = self.queries()
        with self.assertRaises(TypeError):
            queries.telemetry_current(TEMPERATURE)          # no implicit latest
        with self.assertRaises(SnapshotUnavailable):
            queries.telemetry_current(TEMPERATURE, snapshot={**snapshot, "snapshot_ref": "x"})
        future = dict(snapshot, ingest_sequence=snapshot["ingest_sequence"] + 1)
        future["snapshot_ref"] = self._ref(future)
        with self.assertRaises(SnapshotUnavailable):
            queries.telemetry_current(TEMPERATURE, snapshot=future)
        for field, value in (("context_ref", "context-source:other:sha256:0"),
                             ("visibility_policy_ref", "other-policy")):
            forged = dict(snapshot, **{field: value})
            forged["snapshot_ref"] = self._ref(forged)
            with self.subTest(field=field), self.assertRaises(SnapshotUnavailable):
                queries.telemetry_current(TEMPERATURE, snapshot=forged)
        self.prepare_run("run-2")
        with self.assertRaises(SnapshotUnavailable):   # another run's incarnation
            queries.telemetry_current(TEMPERATURE, snapshot=self.snapshot("run-2"))
        for bad in ({**snapshot, "extra": 1}, {k: v for k, v in snapshot.items()
                                               if k != "context_ref"}, "latest", None):
            with self.subTest(bad=bad), self.assertRaises(TelemetryError):
                queries.telemetry_current(TEMPERATURE, snapshot=bad)
        with self.assertRaises(UnknownBinding):
            queries.telemetry_current("XMEAS(9)", snapshot=snapshot)   # runtime ids are not keys
        with self.assertRaises(SnapshotUnavailable):
            queries.telemetry_snapshot(ingest_sequence=snapshot["ingest_sequence"] + 1)
        for bad in (-1, True, 1.5):
            with self.subTest(bad=bad), self.assertRaises(TelemetryError):
                queries.telemetry_snapshot(ingest_sequence=bad)

    @staticmethod
    def _ref(body):
        """The core snapshot_ref rule over the snapshot body (everything but the ref)."""
        clean = {key: value for key, value in body.items() if key != "snapshot_ref"}
        return "telemetry-snapshot:sha256:" + hashlib.sha256(json.dumps(
            clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    def test_k0_snapshot_reads_no_data(self):
        snapshot = self.snapshot(ingest_sequence=0)
        self.assertEqual((0, 0), (snapshot["ingest_sequence"], snapshot["event_horizon"]["ticks"]))
        current = self.current(snapshot)
        self.assertEqual(("NO_DATA", None, 0),
                         (current["status"], current["sample"], current["eligible_count"]))
        history = self.window(snapshot)
        self.assertEqual(("NO_DATA", [], 0, 0, 0),
                         (history["status"], history["samples"], history["eligible_count"],
                          history["event_start_ticks"], history["event_end_ticks"]))
        self.assertEqual(("NO_DATA", None), tuple(self.last_event(snapshot)[key]
                                                  for key in ("status", "event_ticks")))

    def test_history_defaults_are_resolved_and_disclosed(self):
        history = self.history()
        snapshot = self.snapshot()
        result = self.window(snapshot, max_points=1000)
        self.assertEqual((0, snapshot["event_horizon"]["ticks"]),
                         (result["event_start_ticks"], result["event_end_ticks"]))
        self.assertEqual(len(history), result["eligible_count"])
        with self.assertRaises(TelemetryError):
            self.window(snapshot, end=snapshot["event_horizon"]["ticks"] + 1)
        with self.assertRaises(TelemetryError):
            self.window(snapshot, start=10, end=5)


class DeliveryTests(LiveTelemetryCase):
    def test_live_temporal_leakage_matrix(self):
        history = self.history()
        middle = len(history) // 2
        horizon = self.ticks(history[middle])
        snapshot = self.snapshot(event_horizon_ticks=horizon)     # set A: commit <= K, event > T
        k = snapshot["ingest_sequence"]
        self.assertGreater(len(history) - middle - 1, 0)
        reads = self._reads(snapshot)
        # Set B: commit > K, event <= T (same ticks as earlier records, and exactly T).
        for index in (0, middle // 2, middle):
            self.deliver(self.shifted(history[index]))
        self.assertEqual(k + 3, self.telemetry().store.current_ingest_sequence)
        after = self._reads(snapshot)
        self.assertEqual(reads, after)
        self.assertEqual(repr(reads), repr(after))
        eligible = history[:middle + 1]
        for name, view in reads.items():
            text = json.dumps(view)
            self.assertNotIn(f"{OFFSET + self.value(history[0], TEMPERATURE)}", text)
            if name == "current":
                self.assertEqual((view["sample"]["sequence"], view["eligible_count"]),
                                 (middle, len(eligible)))
            elif name == "last":
                self.assertEqual(view["event_ticks"], horizon)
            else:
                m = int(name[-1])
                expected = [middle] if m == 1 else [0, middle]
                self.assertEqual([s["sequence"] for s in view["samples"]], expected)
                self.assertEqual((view["eligible_count"], view["returned_count"], view["reduced"]),
                                 (len(eligible), m, True))
                self.assertEqual([s["value"] for s in view["samples"]],
                                 [self.value(history[i], TEMPERATURE) for i in expected])

    def _reads(self, snapshot):
        return {"current": self.current(snapshot), "last": self.last_event(snapshot),
                "history-m1": self.window(snapshot, start=0, max_points=1),
                "history-m2": self.window(snapshot, start=0, max_points=2)}

    def test_live_post_k_data_cannot_change_old_snapshot_outcomes(self):
        self.manager = self.make_manager_with_policy(max_scan_records=3)
        run_id = self.prepare_run("run-2")
        history = self.history(run_id)
        snapshot = self.snapshot(run_id)
        a, b = self.ticks(history[1]), self.ticks(history[3])
        queries = self.queries(run_id)
        ok = plain(queries.telemetry_history(TEMPERATURE, snapshot=snapshot,
                                             event_start_ticks=a, event_end_ticks=b,
                                             max_points=10))
        failures = {}
        for name, call in self._failing(queries, snapshot, a, history).items():
            with self.assertRaises((ResourceLimitExceeded, TelemetryError)) as raised:
                call()
            failures[name] = (type(raised.exception), raised.exception.args)
        app_failures = self._app_failures(run_id, snapshot, a)
        telemetry = self.telemetry(run_id)
        for _ in range(6):                                  # post-K, inside the ok window
            telemetry.deliver(telemetry.translate(self.shifted(history[2])))
        self.assertEqual(ok, plain(queries.telemetry_history(
            TEMPERATURE, snapshot=snapshot, event_start_ticks=a, event_end_ticks=b,
            max_points=10)))
        for name, call in self._failing(queries, snapshot, a, history).items():
            with self.subTest(name=name):
                with self.assertRaises((ResourceLimitExceeded, TelemetryError)) as raised:
                    call()
                self.assertEqual(failures[name], (type(raised.exception), raised.exception.args))
        self.assertEqual(app_failures, self._app_failures(run_id, snapshot, a))
        self.assertEqual({"scan": ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                          "beyond-horizon": ErrorCode.INVALID_REQUEST},
                         {name: codes[2] for name, codes in app_failures.items()})
        self.assertIs(failures["scan"][0], ResourceLimitExceeded)
        with self.assertRaises(ResourceLimitExceeded):     # a new snapshot sees them
            queries.telemetry_history(TEMPERATURE, snapshot=self.snapshot(run_id),
                                      event_start_ticks=a, event_end_ticks=b, max_points=10)

    def _app_failures(self, run_id, snapshot, start):
        """(exception type, args, transport code) of the failing reads at the app boundary."""
        service = ApplicationViewService(self.manager.queries)
        model = service.open_telemetry_snapshot(
            run_id, event_horizon_ticks=snapshot["event_horizon"]["ticks"],
            ingest_sequence=snapshot["ingest_sequence"])
        transport = ApplicationTransport(service)
        end = snapshot["event_horizon"]["ticks"]
        found = {}
        for name, last in (("scan", end), ("beyond-horizon", end + 1)):
            with self.assertRaises((ResourceLimitExceeded, TelemetryError)) as raised:
                service.get_signal_history(run_id, TEMPERATURE, snapshot=model,
                                           event_start_ticks=start, event_end_ticks=last,
                                           max_points=10)
            response = transport.dispatch({
                "protocol_version": PROTOCOL_VERSION, "request_id": "req", "method":
                "get_signal_history", "params": {
                    "run_id": run_id, "signal_id": TEMPERATURE, "snapshot": plain(model),
                    "event_start_ticks": start, "event_end_ticks": last, "max_points": 10}})
            found[name] = (type(raised.exception), raised.exception.args, response.error.code)
        return found

    def make_manager_with_policy(self, **policy):
        return RunManager(self.root / "runs-policy", revisions=REVISIONS, dependency_pins=PINS,
                          materializers=self.manager.materializers,
                          clock=self.manager.clock,
                          telemetry_policy=TelemetryReadPolicy(**policy))

    @staticmethod
    def _failing(queries, snapshot, start, history):
        end = snapshot["event_horizon"]["ticks"]
        return {"scan": lambda: queries.telemetry_history(
                    TEMPERATURE, snapshot=snapshot, event_start_ticks=start,
                    event_end_ticks=end, max_points=10),
                "beyond-horizon": lambda: queries.telemetry_history(
                    TEMPERATURE, snapshot=snapshot, event_start_ticks=start,
                    event_end_ticks=end + 1, max_points=10)}

    def test_v1_history_replaces_recent_window(self):
        """Windows older than the former 1000-record TelemetryView projection are served."""
        history = self.history()
        frontier = self.ticks(history[-1])
        telemetry = self.telemetry()
        for step in range(1, 1101):
            telemetry.deliver(telemetry.translate(
                self.shifted(history[-1], ticks=frontier + step, offset=step)))
        snapshot = self.snapshot()
        total = len(history) + 1100
        result = self.window(snapshot, start=0, max_points=1000)
        self.assertEqual((total, 1000, True, 0), (result["eligible_count"],
                                                   result["returned_count"], result["reduced"],
                                                   result["samples"][0]["sequence"]))
        oldest = self.window(snapshot, start=0, end=self.ticks(history[1]))
        self.assertEqual([0, 1], [s["sequence"] for s in oldest["samples"]])
        self.assertEqual(1000, len(self.app().get_signal_history(
            self.run_id, TEMPERATURE, snapshot=self.app_snapshot(snapshot),
            max_points=1000).points))

    def test_live_late_and_out_of_order_delivery(self):
        history = self.history()
        frontier = self.ticks(history[-1])
        telemetry = self.telemetry()
        first = telemetry.translate(self.shifted(history[-1], ticks=frontier + 60, offset=1))
        second = telemetry.translate(self.shifted(history[-1], ticks=frontier + 120, offset=2))
        n = len(history)
        self.assertEqual((n, n + 1), (first[0].sequence, second[0].sequence))
        telemetry.deliver(second)                               # out of order
        early = self.snapshot()
        before = (self.current(early), self.window(early, start=frontier + 1))
        self.assertEqual([n + 1], [s["sequence"] for s in before[1]["samples"]])
        result = telemetry.deliver(first)                      # late: event <= T, commit K+1
        self.assertEqual(early["ingest_sequence"] + 1, result.ingest_sequence)
        self.assertEqual(before, (self.current(early), self.window(early, start=frontier + 1)))
        late = self.snapshot()
        self.assertEqual(early["event_horizon"], late["event_horizon"])
        samples = self.window(late, start=frontier + 1)["samples"]
        self.assertEqual([(frontier + 60, n), (frontier + 120, n + 1)],
                         [(s["event_ticks"], s["sequence"]) for s in samples])
        self.assertEqual(n + 1, self.current(late)["sample"]["sequence"])

    def test_live_duplicate_redelivery_is_idempotent(self):
        record = self.shifted(self.history()[-1], ticks=self.ticks(self.history()[-1]) + 60)
        batch, first = self.deliver(record)
        snapshot = self.snapshot()
        reads = (self.current(snapshot), self.window(snapshot))
        again = self.telemetry().deliver(batch)
        self.assertEqual((0, first.ingest_sequence), (again.new_count, again.ingest_sequence))
        self.assertEqual(first.records, again.records)
        self.assertEqual(snapshot, self.snapshot())
        self.assertEqual(reads, (self.current(snapshot), self.window(snapshot)))
        retranslated, result = self.deliver(record)       # no durable delivery identity
        self.assertEqual(batch[0].sequence + 1, retranslated[0].sequence)
        self.assertEqual(first.ingest_sequence + 1, result.ingest_sequence)

    def test_live_identity_conflict_rejects_batch(self):
        batch, first = self.deliver(self.shifted(self.history()[-1]))
        conflict = (replace(batch[0], value=batch[0].value + 1.0), *batch[1:])
        with self.assertRaises(IdentityConflict):
            self.telemetry().deliver(conflict)
        self.assertEqual(first.ingest_sequence, self.telemetry().store.current_ingest_sequence)

    def test_live_same_tick_observations_retained(self):
        captured = []

        def manual_setup(world):
            world.advance(0.1)
            before = world.observe()
            value = before["manipulated_variables"]["XMV(10)"]
            world.environment.apply(MVIntervention("XMV(10)", value + 1.0 if value < 99 else 1.0))
            captured.extend((before, world.observe()))     # observe() never appends history

        run_id = self.prepare_run("run-2", case_setup=manual_setup,
                                  world=WorldSpec(seed=11, control_mode=ControlMode.MANUAL))
        history, telemetry = self.history(run_id), self.telemetry(run_id)
        self.assertEqual(captured[0]["simulation_time_hours"], captured[1]["simulation_time_hours"])
        self.assertEqual(len(history), telemetry.store.current_ingest_sequence)
        for record in captured:
            telemetry.deliver(telemetry.translate(record))
        snapshot = self.snapshot(run_id)
        tick = self.ticks(captured[0])
        queries = self.queries(run_id)
        samples = plain(queries.telemetry_history(COOLING, snapshot=snapshot,
                                                  event_start_ticks=tick, event_end_ticks=tick,
                                                  max_points=10))["samples"]
        runtime = telemetry.source.binding(COOLING).runtime_variable_id
        self.assertEqual([len(history) - 1, len(history), len(history) + 1],
                         [s["sequence"] for s in samples])
        self.assertEqual([r["manipulated_variables"][runtime] for r in (history[-1], *captured)],
                         [s["value"] for s in samples])
        current = plain(queries.telemetry_current(COOLING, snapshot=snapshot))
        self.assertEqual((len(history) + 1, captured[1]["manipulated_variables"][runtime]),
                         (current["sample"]["sequence"], current["sample"]["value"]))


class BoundaryTests(LiveTelemetryCase):
    def test_canonical_views_hide_hidden_state(self):
        snapshot = self.snapshot()
        for scope in (ProjectionScope.AGENT, ProjectionScope.EVALUATOR):
            queries = self.queries(scope=scope)
            views = [queries.telemetry_snapshot(),
                     queries.telemetry_current(TEMPERATURE, snapshot=snapshot),
                     queries.telemetry_history(TEMPERATURE, snapshot=snapshot, max_points=50),
                     queries.telemetry_last_event(snapshot=snapshot)]
            # The EVALUATOR scope label itself is the only screen hit an EVALUATOR view may have.
            bodies = [{k: v for k, v in view.items() if k != "scope"} for view in plain(views)]
            text = json.dumps(bodies)
            with self.subTest(scope=scope):
                self.assertEqual([], leakage_findings(bodies))
                self.assertIsNone(re.search(r"IDV|XMEAS|XMV|prepare-|active_disturbances",
                                            text, re.IGNORECASE))
                self.assertNotIn(str(self.root), text)

    def test_application_and_transport_payloads_hide_hidden_state(self):
        service = self.app()
        snapshot = service.open_telemetry_snapshot(self.run_id)
        transport = ApplicationTransport(service)
        payloads = [plain(snapshot), plain(service.get_run(self.run_id, snapshot=snapshot)),
                    plain(service.get_entity(self.run_id, "reactor")),
                    plain(service.get_signal(self.run_id, TEMPERATURE, snapshot=snapshot)),
                    plain(service.get_signal_history(self.run_id, TEMPERATURE,
                                                     snapshot=snapshot))]
        payloads.append(plain(transport.dispatch({
            "protocol_version": PROTOCOL_VERSION, "request_id": "req", "method": "get_signal",
            "params": {"run_id": self.run_id, "signal_id": TEMPERATURE,
                       "snapshot": plain(snapshot)}})))
        text = json.dumps(payloads)
        self.assertEqual([], leakage_findings(payloads))
        self.assertIsNone(re.search(r"IDV|prepare-|active_disturbances", text, re.IGNORECASE))
        # Runtime ids appear only as opaque source_locator labels.
        locators = re.findall(r'"source_locator": "([^"]+)"', text)
        self.assertTrue(locators)
        self.assertEqual(len(re.findall(r"XMEAS|XMV", text)), len(locators))
        evaluator = ApplicationViewService(
            lambda run_id: self.manager.queries(run_id, ProjectionScope.EVALUATOR))
        with self.assertRaises(playground_views.VisibilityViolation):
            evaluator.get_signal(self.run_id, TEMPERATURE, snapshot=snapshot)

    def test_no_public_ingestion_path(self):
        for module in ("playground_views", "application_views", "application_transport",
                       "desktop_backend"):
            source = (SRC / f"{module}.py").read_text(encoding="utf-8")
            for forbidden in ("deliver(", "sync_reference_history", "translate(",
                              "translate_sanitized", "_ingestor", ".ingest("):
                with self.subTest(module=module, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)
        public = {name for name in dir(playground_views.RunQueries) if not name.startswith("_")}
        self.assertFalse({"deliver", "translate", "sync_reference_history", "ingest"} & public)


class AgentAndV0RegressionTests(LiveTelemetryCase):
    # Values at e5ccf19 (the P1.1C base): Agent tools are unchanged until P1.5.
    BASE_TOOL_SET_VERSION = "68431d8093581e9cf9d649e32ef5649fc4cf399ed08ec4806984c02d82d1debf"

    def test_agent_tool_surface_unchanged(self):
        from tep_agent_lab.tool_bridge import BRIDGE_VERSION
        from tep_agent_lab.tool_surface import TOOL_SURFACE_VERSION
        policy = self.manager.manifest(self.run_id).runtime_policy
        self.assertEqual((TOOL_SURFACE_VERSION, BRIDGE_VERSION, self.BASE_TOOL_SET_VERSION),
                         ("tep-agent-lab.tool-surface/v0", "tep-agent-lab.tool-bridge/v0",
                          policy["tool_set_version"]))
        # get_history still reads the unchanged sanitized reference history.
        surface = self.session().surface.surface
        self.assertIs(surface.world, self.session().world)
        self.assertEqual(self.history(), surface.world.history())

    def test_telemetry_view_v0_unchanged(self):
        view = plain(self.queries().telemetry(max_records=1000))
        history = plain(list(self.history()))
        self.assertEqual(("ReferenceWorld sanitized history", len(history), history,
                          history[-1]),
                         (view["source"], view["total_records"], view["records"],
                          view["current"]))


class WiringRegressionTests(unittest.TestCase):
    @staticmethod
    def imports(name):
        tree = ast.parse((SRC / f"{name}.py").read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add("." * node.level + (node.module or ""))
        return found

    def test_production_wiring_dependency_direction(self):
        self.assertIn(".run_telemetry", self.imports("playground"))
        self.assertIn(".tep_telemetry", self.imports("run_telemetry"))
        users = sorted(path.name for path in SRC.glob("*.py")
                       if ".tep_telemetry" in self.imports(path.stem))
        self.assertEqual(["run_telemetry.py"], users)
        self.assertFalse({name for name in self.imports("telemetry") if "tep" in name
                          or "run_telemetry" in name})
        for name in ("tool_surface", "tool_bridge", "benchmark", "tep_world"):
            with self.subTest(module=name):
                self.assertFalse({".telemetry", ".tep_telemetry", ".run_telemetry"}
                                 & self.imports(name))


if __name__ == "__main__":
    unittest.main()
