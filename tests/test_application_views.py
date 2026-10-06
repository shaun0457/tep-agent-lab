"""Application boundary tests using real P0 projections and changed graph inputs."""

from dataclasses import FrozenInstanceError
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from industrial_agent_runtime import to_jsonable

from tep_agent_lab.application_models import EntityType
from tep_agent_lab.application_views import (
    AmbiguousSignal, ApplicationViewService, MAX_HISTORY_POINTS, UnknownEntity, UnknownSignal,
)
from tep_agent_lab.canonical_context import ProjectionScope
from tep_agent_lab.playground_views import ViewUnavailable, VisibilityViolation
from tep_agent_lab.tep_world import leakage_findings
from test_e0_observatory import e0
from test_playground import lab_revision, request
from test_tool_surface import NOW


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

    def changed_queries(self):
        queries = mock.Mock(wraps=self.queries)
        queries.scope = ProjectionScope.AGENT
        return queries, ApplicationViewService(lambda run_id: queries)

    def graph(self):
        return to_jsonable(self.queries.process_graph())

    def test_run_public_projections(self):
        result = self.service.get_run(self.run_id)
        summary = self.queries.run_summary()
        self.assertEqual(summary["run_status"], result.run_status)
        self.assertEqual(summary["runtime_task_status"]["status"], result.task_status)
        self.assertEqual(summary["manifest"]["checksum"], result.manifest_checksum)
        self.assertEqual(self.queries.telemetry(max_records=1)["current"]["simulation_time_hours"],
                         result.simulation_time_hours)

    def test_unavailable_current_is_explicit(self):
        queries, service = self.changed_queries()
        queries.telemetry.side_effect = ViewUnavailable("no live projection")
        self.assertIsNone(service.get_run(self.run_id).simulation_time_hours)
        self.assertFalse(service.get_signal(self.run_id, "XMEAS(9)").telemetry_available)
        with self.assertRaises(ViewUnavailable):
            service.get_signal_history(self.run_id, "XMEAS(9)")

    def test_created_run_has_optional_unavailable_fields(self):
        self.demo.manager.create("app-created", request())
        result = self.service.get_run("app-created")
        self.assertEqual("CREATED", result.run_status)
        self.assertIsNone(result.task_status)
        self.assertIsNone(result.simulation_time_hours)
        self.assertIsNone(result.manifest_checksum)

    def test_entity_semantics_and_topology(self):
        graph = self.graph()
        for key, identity, kind in (("nodes", "node_id", EntityType.NODE),
                                    ("edges", "edge_id", EntityType.EDGE)):
            for original in graph[key]:
                result = self.service.get_entity(self.run_id, original[identity])
                self.assertEqual((original["name"], original["kind"], kind),
                                 (result.name, result.kind, result.entity_type))
                self.assertEqual([b["runtime_variable_id"] for b in original["bindings"]],
                                 [b.signal_id for b in result.bindings])
                if kind == EntityType.EDGE:
                    self.assertEqual((original["source_node"],), result.upstream)
                    self.assertEqual((original["target_node"],), result.downstream)
                else:
                    self.assertEqual(tuple(sorted({edge["source_node"] for edge in graph["edges"]
                                                   if edge["target_node"] == result.entity_id})),
                                     result.upstream)

    def test_binding_movement_and_metadata_are_dynamic(self):
        graph = self.graph()
        reactor = next(node for node in graph["nodes"] if node["node_id"] == "reactor")
        target = next(node for node in graph["nodes"] if node["node_id"] != "reactor")
        binding = next(b for b in reactor["bindings"] if b["runtime_variable_id"] == "XMEAS(9)")
        reactor["bindings"].remove(binding)
        binding.update(attached_to=target["node_id"], quantity="test quantity", unit="test unit")
        target["bindings"].append(binding)
        queries, service = self.changed_queries()
        queries.process_graph.return_value = graph
        result = service.get_signal(self.run_id, "XMEAS(9)")
        self.assertEqual((target["node_id"],), result.bound_entity_ids)
        self.assertEqual(("test quantity", "test unit", binding["relation"]),
                         (result.quantity, result.unit, result.relation))
        history = service.get_signal_history(self.run_id, "XMEAS(9)")
        self.assertEqual((result.quantity, result.unit), (history.quantity, history.unit))

    def test_current_values_are_telemetry_not_history_or_graph(self):
        queries, service = self.changed_queries()
        telemetry = to_jsonable(self.queries.telemetry(variables=("XMEAS(9)",), max_records=1))
        telemetry["current"]["measurements"]["XMEAS(9)"] = 123.45
        telemetry["current"]["simulation_time_hours"] = 2.0
        queries.telemetry.return_value = telemetry
        result = service.get_signal(self.run_id, "XMEAS(9)")
        self.assertEqual((123.45, 2.0, True),
                         (result.current_value, result.simulation_time_hours, result.telemetry_available))
        queries.telemetry.assert_called_once_with(variables=("XMEAS(9)",), max_records=1)

    def test_unknown_entity_and_signal_fail_closed(self):
        with self.assertRaises(UnknownEntity):
            self.service.get_entity(self.run_id, "unknown")
        for signal in ("unknown", "IDV(4)"):
            for method in (self.service.get_signal, self.service.get_signal_history):
                with self.subTest(signal=signal, method=method.__name__), self.assertRaises(UnknownSignal):
                    method(self.run_id, signal)

    def test_inconsistent_bindings_fail_closed(self):
        for field in ("quantity", "relation", "unit"):
            graph = self.graph()
            binding = next(b for node in graph["nodes"] for b in node["bindings"]
                           if b["runtime_variable_id"] == "XMEAS(9)")
            graph["nodes"][0]["bindings"].append({**binding, field: "different"})
            queries, service = self.changed_queries()
            queries.process_graph.return_value = graph
            for method in (service.get_signal, service.get_signal_history):
                with self.subTest(field=field), self.assertRaises(AmbiguousSignal):
                    method(self.run_id, "XMEAS(9)")

    def test_matching_multiple_bindings_are_supported(self):
        graph = self.graph()
        binding = next(b for node in graph["nodes"] for b in node["bindings"]
                       if b["runtime_variable_id"] == "XMEAS(9)")
        graph["nodes"][0]["bindings"].append(binding.copy())
        queries, service = self.changed_queries()
        queries.process_graph.return_value = graph
        self.assertEqual(tuple(sorted((graph["nodes"][0]["node_id"], "reactor"))),
                         service.get_signal(self.run_id, "XMEAS(9)").bound_entity_ids)

    def test_invalid_history_bounds_before_telemetry(self):
        for kwargs in ({"start_hours": -1}, {"end_hours": -1},
                       {"start_hours": 0.2, "end_hours": 0.1}, {"max_points": 0},
                       {"max_points": -1}, {"max_points": MAX_HISTORY_POINTS + 1},
                       {"max_points": True}, {"max_points": 1.5},
                       {"start_hours": float("nan")}, {"end_hours": float("inf")},
                       {"start_hours": True}):
            queries, service = self.changed_queries()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                service.get_signal_history(self.run_id, "XMEAS(9)", **kwargs)
            queries.telemetry.assert_not_called()

    def test_arbitrary_signal_history_uses_public_p0(self):
        # This signal is absent from both E0's selection and its issued history artifact.
        result = self.service.get_signal_history(self.run_id, "XMEAS(7)",
                                                 start_hours=0.0, end_hours=0.3)
        telemetry = self.queries.telemetry(variables=("XMEAS(7)",), max_records=1000)
        expected = [(r["simulation_time_hours"], r["measurements"]["XMEAS(7)"])
                    for r in telemetry["records"] if r["simulation_time_hours"] <= 0.3]
        self.assertGreater(len(expected), 1)
        self.assertEqual(expected, [(p.simulation_time_hours, p.value) for p in result.points])
        self.assertFalse(result.downsampled)
        self.assertEqual("P0 TelemetryView.records", result.source)

    def test_window_filter_and_deterministic_downsampling(self):
        full = self.service.get_signal_history(self.run_id, "XMEAS(7)",
                                               start_hours=0.1, end_hours=0.29)
        for size in (1, 2, 5, 1000):
            result = self.service.get_signal_history(self.run_id, "XMEAS(7)",
                                                     start_hours=0.1, end_hours=0.29,
                                                     max_points=size)
            expected = (full.points if len(full.points) <= size else (full.points[-1],)
                        if size == 1 else tuple(full.points[i * (len(full.points) - 1) // (size - 1)]
                                               for i in range(size)))
            self.assertEqual(expected, result.points)
            self.assertLessEqual(len(result.points), size)
        empty = self.service.get_signal_history(self.run_id, "XMEAS(7)",
                                                start_hours=0.001, end_hours=0.002)
        self.assertEqual((), empty.points)

    def test_truncated_history_never_silently_clips_explicit_window(self):
        queries, service = self.changed_queries()
        telemetry = to_jsonable(self.queries.telemetry(variables=("XMEAS(7)",), max_records=5))
        queries.telemetry.return_value = telemetry
        result = service.get_signal_history(self.run_id, "XMEAS(7)")
        self.assertTrue(result.earlier_records_omitted)
        self.assertEqual(telemetry["records"][0]["simulation_time_hours"], result.start_hours)
        with self.assertRaises(ViewUnavailable):
            service.get_signal_history(self.run_id, "XMEAS(7)", start_hours=0.0)
        with self.assertRaises(ViewUnavailable):
            service.get_signal_history(self.run_id, "XMEAS(7)", end_hours=2.0)

    def test_missing_signal_data_fails_closed(self):
        queries, service = self.changed_queries()
        queries.telemetry.return_value = self.queries.telemetry(variables=(), max_records=1000)
        self.assertFalse(service.get_signal(self.run_id, "XMEAS(7)").telemetry_available)
        with self.assertRaises(ViewUnavailable):
            service.get_signal_history(self.run_id, "XMEAS(7)")

    def test_hard_bound_and_order_for_large_projection(self):
        queries, service = self.changed_queries()
        telemetry = to_jsonable(self.queries.telemetry(variables=("XMEAS(7)",), max_records=1))
        telemetry["records"] = [
            {"simulation_time_hours": i / 60, "measurements": {"XMEAS(7)": float(i)},
             "manipulated_variables": {}} for i in range(1000)]
        telemetry.update(total_records=1001, returned_records=1000)
        queries.telemetry.return_value = telemetry
        result = service.get_signal_history(self.run_id, "XMEAS(7)", max_points=1000)
        self.assertEqual(1000, len(result.points))
        self.assertTrue(result.earlier_records_omitted)
        queries.telemetry.assert_called_once_with(variables=("XMEAS(7)",), max_records=1000)

    def test_hidden_telemetry_and_summary_fail_closed(self):
        for method in ("run_summary", "telemetry"):
            queries, service = self.changed_queries()
            original = getattr(self.queries, method)()
            getattr(queries, method).return_value = {**to_jsonable(original),
                                                   "hidden_fault": "IDV(4)"}
            with self.assertRaises(VisibilityViolation):
                service.get_run(self.run_id)
            if method == "telemetry":
                with self.assertRaises(VisibilityViolation):
                    service.get_signal_history(self.run_id, "XMEAS(7)")

    def test_agent_scope_and_hidden_projection_rejected(self):
        service = ApplicationViewService(lambda run_id: self.demo.manager.queries(
            run_id, ProjectionScope.EVALUATOR))
        for method, args in ((service.get_run, ()), (service.get_entity, ("reactor",)),
                             (service.get_signal, ("XMEAS(9)",)),
                             (service.get_signal_history, ("XMEAS(9)",))):
            with self.assertRaises(VisibilityViolation):
                method(self.run_id, *args)
        for patch in ({"scope": "EVALUATOR"}, {"active_disturbances": ["IDV(4)"]},
                      {"run_id": "other-run"}):
            queries, service = self.changed_queries()
            queries.process_graph.return_value = {**self.graph(), **patch}
            with self.assertRaises(VisibilityViolation):
                service.get_entity(self.run_id, "reactor")

    def test_results_immutable_serializable_and_safe(self):
        results = (self.service.get_run(self.run_id), self.service.get_entity(self.run_id, "reactor"),
                   self.service.get_signal(self.run_id, "XMEAS(9)"),
                   self.service.get_signal_history(self.run_id, "XMEAS(7)"))
        for result in results:
            with self.assertRaises(FrozenInstanceError):
                result.run_id = "changed"
            serialized = json.dumps(to_jsonable(result))
            self.assertEqual([], leakage_findings(serialized))
            self.assertNotIn(str(self.demo.manager.root), serialized)
            self.assertNotIn("path", serialized)
        with self.assertRaises(FrozenInstanceError):
            results[-1].points[0].value = 0

    def test_no_private_or_transport_dependencies(self):
        import tep_agent_lab.application_views as module
        source = inspect.getsource(module)
        for forbidden in ("TEPEnvironment", "ReferenceWorld", "._runs", "._run",
                          "session", "Path(", "read_bytes", "get_artifact", "fastapi", "flask"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
