"""Binding coordinator review regressions; each ran before the corresponding fix."""

from dataclasses import replace
from fractions import Fraction
import unittest
from unittest import mock

from industrial_agent_runtime import to_jsonable
from tep_agent_lab import telemetry
from tep_agent_lab.application_models import snapshot_to_view
from tep_agent_lab.application_transport import ApplicationTransport, ErrorCode, PROTOCOL_VERSION
from tep_agent_lab.run_telemetry import TelemetryReadPolicy
from tep_agent_lab.telemetry import ResourceLimitExceeded, TelemetryError
import test_application_views as app_tests
from test_application_views import TEMPERATURE, plain
from test_application_transport import SNAPSHOT, envelope
from test_telemetry_core import Fixture, at, make_store, obs


class ReviewScanTests(Fixture):
    def setUp(self):
        super().setUp()
        with mock.patch.object(telemetry, "_INDEX_BLOCK", 2):
            self.store = make_store()
        self.ingestor = telemetry.TelemetryIngestor(self.store)
        self.reader = telemetry.TimeSeriesReader(self.store, max_interval_ticks=10000,
                                                 max_scan_records=3)
        self.ingest(*(obs(i, i) for i in range(20)))
        self.snapshot = self.store.snapshot(at(18))  # T < frontier

    def test_f2_current_stops_at_scan_limit(self):
        with self.assertRaises(ResourceLimitExceeded):
            self.reader.current(source_id="src-1", signal_id="sig-a", snapshot=self.snapshot)

    def test_f2_last_event_stops_at_scan_limit(self):
        with self.assertRaises(ResourceLimitExceeded):
            self.reader.last_event_time(self.snapshot)

    def test_f2_history_count_stops_early(self):
        with mock.patch.object(telemetry, "bisect_right", wraps=telemetry.bisect_right) as probe:
            with self.assertRaises(ResourceLimitExceeded):
                self.reader.history(source_id="src-1", signal_id="sig-a", event_start=at(0),
                                    event_end=at(18), max_points=2, snapshot=self.snapshot)
        # One K-prefix lookup, then only the first two full blocks cross limit 3.
        self.assertLessEqual(probe.call_count, 3)

    def test_f2_sparse_index_scans_are_bounded(self):
        self.ingest(*(obs(20 + i, 20 + i) for i in range(300)))
        snapshot = self.store.snapshot(at(0))
        calls = (
            lambda: self.reader.current(source_id="src-1", signal_id="sig-a", snapshot=snapshot),
            lambda: self.reader.last_event_time(snapshot),
            lambda: self.reader.history(source_id="src-1", signal_id="sig-a", event_start=at(0),
                                        event_end=at(0), max_points=1, snapshot=snapshot),
        )
        for call in calls:
            with self.subTest(call=call), self.assertRaises(ResourceLimitExceeded):
                call()

    def test_f8_store_has_no_duplicate_event_index(self):
        self.assertFalse(hasattr(self.store, "_all"))

    def test_f8_history_bisects_each_block_once(self):
        reader = telemetry.TimeSeriesReader(self.store, max_interval_ticks=10000,
                                             max_scan_records=100)
        with mock.patch.object(telemetry, "bisect_left", wraps=telemetry.bisect_left) as probe:
            result = reader.history(source_id="src-1", signal_id="sig-a", event_start=at(0),
                                    event_end=at(18), max_points=30, snapshot=self.snapshot)
        self.assertEqual(19, result.eligible_count)
        self.assertEqual(10, probe.call_count)


class ReviewApplicationTests(unittest.TestCase):
    setUpClass = classmethod(app_tests.ApplicationViewsTests.setUpClass.__func__)
    changed_queries = app_tests.ApplicationViewsTests.changed_queries
    graph = app_tests.ApplicationViewsTests.graph

    def dispatch(self, service, method="get_signal", **params):
        return ApplicationTransport(service).dispatch({
            "protocol_version": PROTOCOL_VERSION, "request_id": "review",
            "method": method, "params": {"run_id": self.run_id,
                "signal_id": TEMPERATURE, "snapshot": plain(self.snapshot), **params}})

    def test_f3_different_locators_use_first_canonical_binding(self):
        graph = self.graph()
        reactor = next(node for node in graph["nodes"] if node["node_id"] == "reactor")
        binding = next(b for b in reactor["bindings"] if b["semantic_entity_id"] == TEMPERATURE)
        target = next(node for node in graph["nodes"] if node["node_id"] < "reactor")
        target["bindings"].append({**binding, "runtime_variable_id": "alternate-locator"})
        queries, service = self.changed_queries()
        for nodes in (graph["nodes"], list(reversed(graph["nodes"]))):
            queries.process_graph.return_value = {**graph, "nodes": nodes}
            result = service.get_signal(self.run_id, TEMPERATURE, snapshot=self.snapshot)
            self.assertEqual("alternate-locator", result.source_locator)
            self.assertEqual(tuple(sorted((target["node_id"], "reactor"))), result.bound_entity_ids)

    def test_f4_snapshot_construction_fault_is_internal_error(self):
        with mock.patch("tep_agent_lab.run_telemetry.StoreRef", side_effect=TelemetryError("fault")):
            result = self.dispatch(self.service)
        self.assertEqual(ErrorCode.INTERNAL_ERROR, result.error.code)

    def test_f4_malformed_server_snapshot_is_internal_error(self):
        queries, service = self.changed_queries()
        view = plain(self.queries.telemetry_snapshot())
        view["snapshot"]["event_clock"]["resolution"] = "invalid"
        queries.telemetry_snapshot.return_value = view
        result = ApplicationTransport(service).dispatch({
            "protocol_version": PROTOCOL_VERSION, "request_id": "review",
            "method": "open_telemetry_snapshot", "params": {"run_id": self.run_id}})
        self.assertEqual(ErrorCode.INTERNAL_ERROR, result.error.code)

    def test_f4_binding_fault_is_internal_error(self):
        store = self.demo.manager._runs[self.run_id].session.telemetry.store
        with mock.patch.object(store, "require_binding", side_effect=TelemetryError("fault")):
            result = self.dispatch(self.service)
        self.assertEqual(ErrorCode.INTERNAL_ERROR, result.error.code)

    def test_f4_bad_interval_is_invalid_request(self):
        result = self.dispatch(self.service, "get_signal_history",
                               event_end_ticks=self.snapshot.event_horizon_ticks + 1)
        self.assertEqual(ErrorCode.INVALID_REQUEST, result.error.code)

    def test_f9_p0_resolution_digit_cap_matches_application(self):
        for resolution in ("1/" + "9" * 19, "9" * 19 + "/1"):
            body = snapshot_to_view(self.snapshot)
            body["event_clock"]["resolution"] = resolution
            with self.subTest(resolution=resolution), self.assertRaises(TelemetryError):
                self.queries.telemetry_current(TEMPERATURE, snapshot=body)

    def test_f9_both_python_boundaries_accept_same_resolution_grammar(self):
        from tep_agent_lab.application_models import snapshot_from_json
        for resolution in ("1/1", "1/100000000000000000", "999999999999999999/1",
                           "01/1", "1/01", "2/4", "0/1", "1/0", "1/1\n"):
            body = snapshot_to_view(self.snapshot)
            body["event_clock"]["resolution"] = resolution
            valid = resolution in ("1/1", "1/100000000000000000", "999999999999999999/1")
            with self.subTest(resolution=resolution):
                if valid:
                    snapshot_from_json({**plain(self.snapshot), "event_resolution": resolution})
                    from tep_agent_lab.telemetry import SnapshotUnavailable
                    if resolution == "1/1":
                        self.queries.telemetry_current(TEMPERATURE, snapshot=body)
                    else:
                        with self.assertRaises(SnapshotUnavailable):
                            self.queries.telemetry_current(TEMPERATURE, snapshot=body)
                else:
                    with self.assertRaises(ValueError):
                        snapshot_from_json({**plain(self.snapshot), "event_resolution": resolution})
                    with self.assertRaises(TelemetryError):
                        self.queries.telemetry_current(TEMPERATURE, snapshot=body)


class ReviewPolicyAndTransportTests(unittest.TestCase):
    def test_f5_policy_rejects_invalid_values_immediately(self):
        for field, values in (("max_points_limit", (0, -1, True, 1.5, "2")),
                              ("max_scan_records", (0, -1, True, 1.5, "2")),
                              ("max_interval_ticks", (-1, True, 1.5, "2"))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(TelemetryError):
                    TelemetryReadPolicy(**{field: value})
        self.assertEqual(0, TelemetryReadPolicy(max_interval_ticks=0).max_interval_ticks)

    def test_f7_null_run_snapshot_means_no_telemetry(self):
        service = mock.Mock()
        service.get_run.return_value = {}
        result = ApplicationTransport(service).dispatch(envelope("get_run", snapshot=None))
        self.assertTrue(result.ok)
        service.get_run.assert_called_once_with(run_id="run-1", snapshot=None)
        for method in ("get_signal", "get_signal_history"):
            result = ApplicationTransport(service).dispatch(
                envelope(method, signal_id=TEMPERATURE, snapshot=None))
            self.assertEqual(ErrorCode.INVALID_REQUEST, result.error.code)

    def test_f10_integer_params_are_safe_json_integers(self):
        service = mock.Mock()
        service.open_telemetry_snapshot.return_value = {}
        service.get_signal_history.return_value = {}
        transport = ApplicationTransport(service)
        for field in ("event_horizon_ticks", "ingest_sequence", "event_start_ticks",
                      "event_end_ticks", "max_points"):
            method = ("open_telemetry_snapshot" if field in ("ingest_sequence", "event_horizon_ticks")
                      else "get_signal_history")
            params = {} if method == "open_telemetry_snapshot" else dict(signal_id=TEMPERATURE,
                                                                         snapshot=SNAPSHOT)
            for value in (2**53, -(2**53)):
                service.reset_mock()
                with self.subTest(field=field, value=value):
                    result = transport.dispatch(envelope(method, **{**params, field: value}))
                    self.assertEqual(ErrorCode.INVALID_REQUEST, result.error.code)
                    self.assertEqual([], service.mock_calls)
            for value in (2**53 - 1, -(2**53 - 1)):
                self.assertTrue(transport.dispatch(envelope(method, **{**params, field: value})).ok)
        for field in ("ingest_sequence", "event_horizon_ticks"):
            result = transport.dispatch(envelope("get_run", snapshot={**SNAPSHOT, field: 2**53}))
            self.assertEqual(ErrorCode.INVALID_REQUEST, result.error.code)

    def test_f10_no_redundant_serialization_of_validated_params(self):
        service = mock.Mock()
        service.get_run.return_value = {}
        with mock.patch("tep_agent_lab.application_transport.to_jsonable", wraps=to_jsonable) as encode:
            self.assertTrue(ApplicationTransport(service).dispatch(envelope()).ok)
        self.assertEqual(1, encode.call_count)
