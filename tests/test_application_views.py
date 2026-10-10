"""Application API v1 boundary tests (docs/specs/application-api-v1.md).

Real E0/P0 projections; telemetry reads are pinned to one exact snapshot.
"""

from dataclasses import FrozenInstanceError, replace
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from industrial_agent_runtime import to_jsonable

from tep_agent_lab import e0_demo
from tep_agent_lab.application_models import (CanonicalPoint, EntityType, TelemetrySnapshot,
                                              snapshot_from_json)
from tep_agent_lab.application_views import (
    AmbiguousSignal, ApplicationViewService, MAX_HISTORY_POINTS, UnknownEntity, UnknownSignal,
)
from tep_agent_lab.canonical_context import ProjectionScope
from tep_agent_lab.playground_views import ViewUnavailable, VisibilityViolation
from tep_agent_lab.telemetry import SnapshotUnavailable, TelemetryError
from tep_agent_lab.tep_world import leakage_findings
from test_e0_observatory import e0
from test_playground import lab_revision, request
from test_tool_surface import NOW

TEMPERATURE = "reactor.temperature_measurement"
PRESSURE = "reactor.pressure_measurement"


def plain(value):
    return json.loads(json.dumps(to_jsonable(value)))


class ApplicationViewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.demo = e0.run_demo(Path(directory.name) / "app", lab_revision=lab_revision(),
                               clock=lambda: NOW)
        cls.run_id = cls.demo.run_id
        cls.queries = cls.demo.manager.queries(cls.run_id)
        cls.service = ApplicationViewService(cls.demo.manager.queries)
        cls.snapshot = cls.service.open_telemetry_snapshot(cls.run_id)

    def changed_queries(self):
        queries = mock.Mock(wraps=self.queries)
        queries.scope = ProjectionScope.AGENT
        return queries, ApplicationViewService(lambda run_id: queries)

    def graph(self):
        return to_jsonable(self.queries.process_graph())

    def body(self, snapshot=None):
        return plain(self.queries.telemetry_snapshot(
            event_horizon_ticks=(snapshot or self.snapshot).event_horizon_ticks,
            ingest_sequence=(snapshot or self.snapshot).ingest_sequence))["snapshot"]

    def test_open_snapshot_discloses_resolved_t_and_k(self):
        snapshot = self.snapshot
        view = self.body()
        self.assertIsInstance(snapshot, TelemetrySnapshot)
        self.assertEqual((snapshot.snapshot_ref, snapshot.ingest_sequence,
                          snapshot.event_horizon_ticks, snapshot.context_ref),
                         (view["snapshot_ref"], view["ingest_sequence"],
                          view["event_horizon"]["ticks"], view["context_ref"]))
        self.assertEqual(("SIMULATION", "1/1"), (snapshot.event_clock_kind,
                                                 snapshot.event_resolution))
        self.assertEqual(snapshot, snapshot_from_json(plain(snapshot)))
        k0 = self.service.open_telemetry_snapshot(self.run_id, ingest_sequence=0)
        self.assertEqual((0, 0), (k0.ingest_sequence, k0.event_horizon_ticks))
        for bad in ({"ingest_sequence": -1}, {"ingest_sequence": True},
                    {"event_horizon_ticks": 1.5}, {"event_horizon_ticks": "1"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.service.open_telemetry_snapshot(self.run_id, **bad)
        with self.assertRaises(SnapshotUnavailable):
            self.service.open_telemetry_snapshot(
                self.run_id, ingest_sequence=snapshot.ingest_sequence + 1)

    def test_run_public_projections(self):
        summary = self.queries.run_summary()
        plain_run = self.service.get_run(self.run_id)
        self.assertEqual((summary["run_status"], summary["runtime_task_status"]["status"],
                          summary["manifest"]["checksum"], None, None),
                         (plain_run.run_status, plain_run.task_status,
                          plain_run.manifest_checksum, plain_run.snapshot_ref,
                          plain_run.telemetry_event_ticks))
        pinned = self.service.get_run(self.run_id, snapshot=self.snapshot)
        last = self.queries.telemetry_last_event(snapshot=self.body())
        self.assertEqual((self.snapshot.snapshot_ref, last["event_ticks"]),
                         (pinned.snapshot_ref, pinned.telemetry_event_ticks))
        self.assertEqual(round((e0_demo.DEMO_PRE_INCIDENT_HOURS
                                + e0_demo.DEMO_POST_INCIDENT_HOURS) * 3600),
                         pinned.telemetry_event_ticks)

    def test_created_run_reads_status_without_snapshot(self):
        self.demo.manager.create("app-created", request())
        result = self.service.get_run("app-created")
        self.assertEqual("CREATED", result.run_status)
        self.assertIsNone(result.task_status)
        self.assertIsNone(result.telemetry_event_ticks)
        self.assertIsNone(result.manifest_checksum)
        with self.assertRaises(ViewUnavailable):
            self.service.open_telemetry_snapshot("app-created")

    def test_entity_semantics_and_topology(self):
        graph = self.graph()
        for key, identity, kind in (("nodes", "node_id", EntityType.NODE),
                                    ("edges", "edge_id", EntityType.EDGE)):
            for original in graph[key]:
                result = self.service.get_entity(self.run_id, original[identity])
                self.assertEqual((original["name"], original["kind"], kind),
                                 (result.name, result.kind, result.entity_type))
                self.assertEqual([(b["semantic_entity_id"], b["runtime_variable_id"])
                                  for b in original["bindings"]],
                                 [(b.signal_id, b.source_locator) for b in result.bindings])
                if kind == EntityType.EDGE:
                    self.assertEqual((original["source_node"],), result.upstream)
                    self.assertEqual((original["target_node"],), result.downstream)
                else:
                    self.assertEqual(tuple(sorted({edge["source_node"] for edge in graph["edges"]
                                                   if edge["target_node"] == result.entity_id})),
                                     result.upstream)

    def test_v1_reads_are_snapshot_bound(self):
        body = self.body()
        signal = self.service.get_signal(self.run_id, TEMPERATURE, snapshot=self.snapshot)
        current = plain(self.queries.telemetry_current(TEMPERATURE, snapshot=body))
        sample = current["sample"]
        self.assertEqual((signal.run_id, signal.signal_id, signal.source_id, signal.source_locator),
                         (self.run_id, TEMPERATURE, current["source_id"], "XMEAS(9)"))
        self.assertEqual((signal.status, signal.eligible_count, signal.snapshot_ref),
                         (current["status"], current["eligible_count"], self.snapshot.snapshot_ref))
        self.assertEqual(CanonicalPoint(sample["event_ticks"], sample["sequence"], sample["value"],
                                        sample["quality"]), signal.current)
        self.assertEqual((sample["source_id"], sample["signal_id"]),
                         (signal.source_id, signal.signal_id))
        history = self.service.get_signal_history(self.run_id, PRESSURE, snapshot=self.snapshot,
                                                  max_points=1000)
        view = plain(self.queries.telemetry_history(PRESSURE, snapshot=body, max_points=1000))
        self.assertEqual([(s["source_id"], s["signal_id"], s["sequence"], s["event_ticks"],
                           s["value"]) for s in view["samples"]],
                         [(history.source_id, history.signal_id, p.sequence, p.event_ticks,
                           p.value) for p in history.points])
        self.assertEqual((view["eligible_count"], view["returned_count"], view["reduced"],
                          view["event_start_ticks"], view["event_end_ticks"]),
                         (history.eligible_count, history.returned_count, history.reduced,
                          history.event_start_ticks, history.event_end_ticks))
        with self.assertRaises(TypeError):
            self.service.get_signal(self.run_id, TEMPERATURE)          # no implicit latest
        with self.assertRaises(ValueError):
            self.service.get_signal(self.run_id, TEMPERATURE, snapshot=plain(self.snapshot))
        with self.assertRaises(SnapshotUnavailable):
            self.service.get_signal(self.run_id, TEMPERATURE,
                                    snapshot=replace(self.snapshot, run_id="other-run"))
        with self.assertRaises(SnapshotUnavailable):
            self.service.get_signal(self.run_id, TEMPERATURE,
                                    snapshot=replace(self.snapshot, snapshot_ref="forged"))

    def test_history_window_defaults_and_reduction(self):
        full = self.service.get_signal_history(self.run_id, PRESSURE, snapshot=self.snapshot,
                                               max_points=1000)
        self.assertEqual((0, self.snapshot.event_horizon_ticks),
                         (full.event_start_ticks, full.event_end_ticks))
        for size in (1, 2, 5, 1000):
            result = self.service.get_signal_history(self.run_id, PRESSURE,
                                                     snapshot=self.snapshot, max_points=size)
            n = len(full.points)
            expected = (full.points if n <= size else (full.points[-1],) if size == 1
                        else tuple(full.points[i * (n - 1) // (size - 1)] for i in range(size)))
            self.assertEqual(expected, result.points)
        empty = self.service.get_signal_history(self.run_id, PRESSURE, snapshot=self.snapshot,
                                                event_start_ticks=1, event_end_ticks=2)
        self.assertEqual(((), "NO_DATA"), (empty.points, empty.status))
        with self.assertRaises(TelemetryError):
            self.service.get_signal_history(self.run_id, PRESSURE, snapshot=self.snapshot,
                                            event_end_ticks=self.snapshot.event_horizon_ticks + 1)

    def test_invalid_history_bounds_before_telemetry(self):
        for kwargs in ({"event_start_ticks": -1}, {"event_end_ticks": -1},
                       {"event_start_ticks": 20, "event_end_ticks": 10}, {"max_points": 0},
                       {"max_points": -1}, {"max_points": MAX_HISTORY_POINTS + 1},
                       {"max_points": True}, {"max_points": 1.5},
                       {"event_start_ticks": 1.5}, {"event_end_ticks": True}):
            queries, service = self.changed_queries()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                service.get_signal_history(self.run_id, PRESSURE, snapshot=self.snapshot,
                                           **kwargs)
            queries.telemetry_history.assert_not_called()

    def test_binding_movement_and_metadata_are_dynamic(self):
        graph = self.graph()
        reactor = next(node for node in graph["nodes"] if node["node_id"] == "reactor")
        target = next(node for node in graph["nodes"] if node["node_id"] != "reactor")
        binding = next(b for b in reactor["bindings"] if b["semantic_entity_id"] == TEMPERATURE)
        reactor["bindings"].remove(binding)
        binding.update(attached_to=target["node_id"], quantity="test quantity", unit="test unit")
        target["bindings"].append(binding)
        queries, service = self.changed_queries()
        queries.process_graph.return_value = graph
        result = service.get_signal(self.run_id, TEMPERATURE, snapshot=self.snapshot)
        self.assertEqual((target["node_id"],), result.bound_entity_ids)
        self.assertEqual(("test quantity", "test unit", binding["relation"]),
                         (result.quantity, result.unit, result.relation))
        history = service.get_signal_history(self.run_id, TEMPERATURE, snapshot=self.snapshot)
        self.assertEqual((result.quantity, result.unit), (history.quantity, history.unit))

    def test_unknown_entity_and_signal_fail_closed(self):
        with self.assertRaises(UnknownEntity):
            self.service.get_entity(self.run_id, "unknown")
        for signal in ("unknown", "IDV(4)", "XMEAS(9)"):     # runtime ids are not keys
            for method in (self.service.get_signal, self.service.get_signal_history):
                with self.subTest(signal=signal, method=method.__name__), \
                        self.assertRaises(UnknownSignal):
                    method(self.run_id, signal, snapshot=self.snapshot)

    def test_inconsistent_bindings_fail_closed(self):
        for field in ("quantity", "relation", "unit"):
            graph = self.graph()
            binding = next(b for node in graph["nodes"] for b in node["bindings"]
                           if b["semantic_entity_id"] == TEMPERATURE)
            graph["nodes"][0]["bindings"].append({**binding, field: "different"})
            queries, service = self.changed_queries()
            queries.process_graph.return_value = graph
            for method in (service.get_signal, service.get_signal_history):
                with self.subTest(field=field), self.assertRaises(AmbiguousSignal):
                    method(self.run_id, TEMPERATURE, snapshot=self.snapshot)

    def test_matching_multiple_bindings_are_supported(self):
        graph = self.graph()
        binding = next(b for node in graph["nodes"] for b in node["bindings"]
                       if b["semantic_entity_id"] == TEMPERATURE)
        graph["nodes"][0]["bindings"].append(binding.copy())
        queries, service = self.changed_queries()
        queries.process_graph.return_value = graph
        self.assertEqual(tuple(sorted((graph["nodes"][0]["node_id"], "reactor"))),
                         service.get_signal(self.run_id, TEMPERATURE,
                                            snapshot=self.snapshot).bound_entity_ids)

    def test_hidden_projections_fail_closed(self):
        for method in ("run_summary", "telemetry_current", "telemetry_history",
                       "telemetry_last_event", "telemetry_snapshot"):
            queries, service = self.changed_queries()
            original = getattr(self.queries, method)

            def poisoned(*args, _original=original, **kwargs):
                return {**to_jsonable(_original(*args, **kwargs)), "hidden_fault": "IDV(4)"}

            getattr(queries, method).side_effect = poisoned
            calls = {"run_summary": lambda: service.get_run(self.run_id),
                     "telemetry_current": lambda: service.get_signal(
                         self.run_id, TEMPERATURE, snapshot=self.snapshot),
                     "telemetry_history": lambda: service.get_signal_history(
                         self.run_id, TEMPERATURE, snapshot=self.snapshot),
                     "telemetry_last_event": lambda: service.get_run(
                         self.run_id, snapshot=self.snapshot),
                     "telemetry_snapshot": lambda: service.open_telemetry_snapshot(self.run_id)}
            with self.subTest(method=method), self.assertRaises(VisibilityViolation):
                calls[method]()

    def test_agent_scope_and_hidden_projection_rejected(self):
        service = ApplicationViewService(lambda run_id: self.demo.manager.queries(
            run_id, ProjectionScope.EVALUATOR))
        for method, args, kwargs in (
                (service.open_telemetry_snapshot, (), {}), (service.get_run, (), {}),
                (service.get_entity, ("reactor",), {}),
                (service.get_signal, (TEMPERATURE,), {"snapshot": self.snapshot}),
                (service.get_signal_history, (TEMPERATURE,), {"snapshot": self.snapshot})):
            with self.assertRaises(VisibilityViolation):
                method(self.run_id, *args, **kwargs)
        for patch in ({"scope": "EVALUATOR"}, {"active_disturbances": ["IDV(4)"]},
                      {"run_id": "other-run"}):
            queries, service = self.changed_queries()
            queries.process_graph.return_value = {**self.graph(), **patch}
            with self.assertRaises(VisibilityViolation):
                service.get_entity(self.run_id, "reactor")

    def test_results_immutable_serializable_and_safe(self):
        results = (self.snapshot, self.service.get_run(self.run_id, snapshot=self.snapshot),
                   self.service.get_entity(self.run_id, "reactor"),
                   self.service.get_signal(self.run_id, TEMPERATURE, snapshot=self.snapshot),
                   self.service.get_signal_history(self.run_id, PRESSURE, snapshot=self.snapshot))
        for result in results:
            with self.assertRaises(FrozenInstanceError):
                result.run_id = "changed"
            serialized = json.dumps(to_jsonable(result))
            self.assertEqual([], leakage_findings(serialized))
            self.assertNotIn(str(self.demo.manager.root), serialized)
            self.assertNotIn("path", serialized)
            self.assertNotIn("prepare-", serialized)
        with self.assertRaises(FrozenInstanceError):
            results[-1].points[0].value = 0

    def test_application_reads_no_longer_use_telemetry_view(self):
        import tep_agent_lab.application_views as module
        self.assertNotIn(".telemetry(", inspect.getsource(module))
        queries, service = self.changed_queries()
        service.get_run(self.run_id, snapshot=self.snapshot)
        service.get_signal(self.run_id, TEMPERATURE, snapshot=self.snapshot)
        service.get_signal_history(self.run_id, TEMPERATURE, snapshot=self.snapshot)
        queries.telemetry.assert_not_called()

    def test_no_private_or_transport_dependencies(self):
        import tep_agent_lab.application_views as module
        source = inspect.getsource(module)
        for forbidden in ("TEPEnvironment", "ReferenceWorld", "._runs", "._run",
                          "session", "Path(", "read_bytes", "get_artifact", "fastapi", "flask",
                          "run_telemetry", "tep_telemetry"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
