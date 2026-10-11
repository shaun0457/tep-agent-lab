"""Transport v1: schema isolation, error redaction, real in-process reads.

docs/specs/application-api-v1.md §Transport v1.
"""

from dataclasses import FrozenInstanceError
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from industrial_agent_runtime import to_jsonable
from industrial_agent_runtime.serialization import canonical_json

from tep_agent_lab.application_models import InvalidRequest, snapshot_from_json
from tep_agent_lab.application_transport import (
    ApplicationRequest, ApplicationTransport, ErrorCode, MAX_REQUEST_BYTES, PROTOCOL_VERSION,
    decode_request_json,
)
from tep_agent_lab.application_views import (
    AmbiguousSignal, ApplicationViewService, UnknownEntity, UnknownSignal,
)
from tep_agent_lab.canonical_context import ProjectionScope
from tep_agent_lab.playground_views import ViewUnavailable, VisibilityViolation
from tep_agent_lab.telemetry import (ClockMismatch, ResourceLimitExceeded, SnapshotUnavailable,
                                     TelemetryError, UnknownBinding)
from tep_agent_lab.tep_world import leakage_findings
from test_e0_observatory import e0
from test_playground import lab_revision
from test_tool_surface import NOW

TEMPERATURE = "reactor.temperature_measurement"
PRESSURE = "reactor.pressure_measurement"
SNAPSHOT = {"run_id": "run-1", "snapshot_ref": "telemetry-snapshot:sha256:" + "0" * 64,
            "contract": "tep-agent-lab.telemetry/v0", "store_id": "p0-reference-telemetry",
            "incarnation": "inc-" + "a" * 32, "namespace": "p0-run:run-1:" + "a" * 32,
            "ingest_sequence": 19, "event_horizon_ticks": 1080,
            "event_clock_id": "tep-sim:run-1:" + "a" * 32, "event_clock_kind": "SIMULATION",
            "event_resolution": "1/1", "event_clock_origin": "tep-reset:run-1:" + "a" * 32,
            "context_ref": "context-source:tep-sim.process-graph:sha256:" + "b" * 64,
            "visibility_policy_ref": "tep-agent-lab.playground-visibility/v0#AGENT"}


def envelope(method="get_run", **params):
    return {"protocol_version": PROTOCOL_VERSION, "request_id": "req-123",
            "method": method, "params": {"run_id": "run-1", **params}}


class TransportSchemaTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.Mock(spec=ApplicationViewService)
        self.transport = ApplicationTransport(self.service)

    def assert_failure(self, request, code, request_id="req-123"):
        result = to_jsonable(self.transport.dispatch(request))
        self.assertEqual({"protocol_version": PROTOCOL_VERSION, "request_id": request_id,
                          "ok": False, "error": {"code": code, "message": code.lower().replace("_", " ")}},
                         result)
        return result

    def test_protocol_is_v1(self):
        self.assertEqual("tep-agent-lab.application-transport/v1", PROTOCOL_VERSION)

    def test_invalid_shapes_never_call_service(self):
        valid = envelope()
        invalid = [None, [], "get_run", {},
                   {**valid, "extra": 1}, {**valid, "params": []},
                   {**valid, "params": {}}, {**valid, "params": {"run_id": 123}},
                   {**valid, "params": {"run_id": True}},
                   {**valid, "params": {"run_id": "run-1", "scope": "EVALUATOR"}},
                   {**valid, "method": None}, {**valid, "protocol_version": 0}]
        for key in valid:
            invalid.append({k: v for k, v in valid.items() if k != key})
        for request in invalid:
            request_id = request.get("request_id") if isinstance(request, dict) else None
            with self.subTest(request=request):
                self.assert_failure(request, ErrorCode.INVALID_REQUEST, request_id)
        for request_id in (None, True, 12, {}, []):
            self.assert_failure({**valid, "request_id": request_id}, ErrorCode.INVALID_REQUEST, None)
        self.assertEqual([], self.service.mock_calls)

    def test_method_and_protocol_allowlist(self):
        for method in ("__dict__", "start", "get_artifact", "get_signal; import os", "",
                       "deliver", "sync_reference_history", "telemetry_snapshot"):
            self.assert_failure(envelope(method), ErrorCode.UNSUPPORTED_METHOD)
        for version in ("tep-agent-lab.application-transport/v0", "v1"):
            self.assert_failure({**envelope(), "protocol_version": version},
                                ErrorCode.UNSUPPORTED_PROTOCOL_VERSION)
        self.assertEqual([], self.service.mock_calls)

    def test_method_parameter_schemas(self):
        for method, identity in (("get_entity", "entity_id"), ("get_signal", "signal_id"),
                                 ("get_signal_history", "signal_id")):
            extra = {} if method == "get_entity" else {"snapshot": SNAPSHOT}
            for params in ({**extra}, {identity: None, **extra}, {identity: 5, **extra},
                           {identity: [], **extra}, {identity: "value", "unknown": 1, **extra}):
                self.assert_failure(envelope(method, **params), ErrorCode.INVALID_REQUEST)
        for method in ("get_signal", "get_signal_history"):        # snapshot is required
            self.assert_failure(envelope(method, signal_id=TEMPERATURE), ErrorCode.INVALID_REQUEST)
        for snapshot in (None, "latest", [], {}, {**SNAPSHOT, "extra": 1},
                         {k: v for k, v in SNAPSHOT.items() if k != "context_ref"},
                         {**SNAPSHOT, "ingest_sequence": True},
                         {**SNAPSHOT, "ingest_sequence": 1.0},
                         {**SNAPSHOT, "event_horizon_ticks": "1080"},
                         {**SNAPSHOT, "snapshot_ref": 5}):
            with self.subTest(snapshot=snapshot):
                self.assert_failure(envelope("get_signal", signal_id=TEMPERATURE,
                                             snapshot=snapshot), ErrorCode.INVALID_REQUEST)
                self.assert_failure(envelope("get_run", snapshot=snapshot),
                                    ErrorCode.INVALID_REQUEST)
        for options in ({"event_start_ticks": True}, {"event_end_ticks": "1"},
                        {"event_start_ticks": 1.5}, {"max_points": True},
                        {"max_points": 2.0}, {"max_points": None}):
            self.assert_failure(envelope("get_signal_history", signal_id=TEMPERATURE,
                                         snapshot=SNAPSHOT, **options), ErrorCode.INVALID_REQUEST)
        for options in ({"ingest_sequence": True}, {"ingest_sequence": None},
                        {"event_horizon_ticks": 1.5}, {"snapshot": SNAPSHOT}):
            self.assert_failure(envelope("open_telemetry_snapshot", **options),
                                ErrorCode.INVALID_REQUEST)
        self.assert_failure(envelope("get_signal", signal_id=TEMPERATURE, snapshot=SNAPSHOT,
                                     max_points=5), ErrorCode.INVALID_REQUEST)
        self.assertEqual([], self.service.mock_calls)

    def test_request_id_is_a_bounded_ascii_token(self):
        for request_id in ("", "x" * 129, "req 1", "req/1", "req-中文", "\ud800", "a\nb"):
            with self.subTest(request_id=request_id):
                self.assert_failure({**envelope(), "request_id": request_id},
                                    ErrorCode.INVALID_REQUEST, None)
        for request_id in ("desktop-1", "smoke-2", "a.b:c_d-e", "x" * 128):
            self.service.get_run.return_value = {}
            self.assertTrue(self.transport.dispatch({**envelope(), "request_id": request_id}).ok)
        response = self.transport.dispatch_json('{"protocol_version":"%s","request_id":"\\ud800",'
                                                '"method":"get_run","params":{"run_id":"r"}}'
                                                % PROTOCOL_VERSION)
        self.assertEqual((None, ErrorCode.INVALID_REQUEST),
                         (response.request_id, response.error.code))

    def test_snapshot_object_is_validated_once_and_strictly(self):
        bad = ({**SNAPSHOT, "event_clock_origin": None}, {**SNAPSHOT, "ingest_sequence": -1},
               {**SNAPSHOT, "event_horizon_ticks": -5}, {**SNAPSHOT, "context_ref": "x" * 257},
               {**SNAPSHOT, "event_resolution": "2000/2000"}, {**SNAPSHOT, "event_resolution": "2/4"},
               {**SNAPSHOT, "event_resolution": "0/1"}, {**SNAPSHOT, "event_resolution": "1"},
               {**SNAPSHOT, "event_resolution": "01/1"}, {**SNAPSHOT, "run_id": ""})
        for snapshot in bad:
            with self.subTest(snapshot={k: v for k, v in snapshot.items() if SNAPSHOT.get(k) != v}):
                self.assert_failure(envelope("get_signal", signal_id=TEMPERATURE,
                                             snapshot=snapshot), ErrorCode.INVALID_REQUEST)
        self.assertEqual([], self.service.mock_calls)
        for resolution in ("1/1", "1/1000", "3/2"):
            snapshot_from_json({**SNAPSHOT, "event_resolution": resolution})
        self.service.get_signal.return_value = {}
        with mock.patch("tep_agent_lab.application_transport.snapshot_from_json",
                        wraps=snapshot_from_json) as parse:
            self.assertTrue(self.transport.dispatch(
                envelope("get_signal", signal_id=TEMPERATURE, snapshot=SNAPSHOT)).ok)
        self.assertEqual(1, parse.call_count)

    def test_valid_requests_reach_the_service_with_typed_snapshot(self):
        for method in ("open_telemetry_snapshot", "get_run", "get_entity", "get_signal",
                       "get_signal_history"):
            getattr(self.service, method).return_value = {}
        model = snapshot_from_json(SNAPSHOT)
        calls = (
            (envelope("open_telemetry_snapshot", ingest_sequence=0, event_horizon_ticks=5),
             self.service.open_telemetry_snapshot,
             mock.call(run_id="run-1", ingest_sequence=0, event_horizon_ticks=5)),
            (envelope("get_run"), self.service.get_run, mock.call(run_id="run-1")),
            (envelope("get_run", snapshot=SNAPSHOT), self.service.get_run,
             mock.call(run_id="run-1", snapshot=model)),
            (envelope("get_signal", signal_id=TEMPERATURE, snapshot=SNAPSHOT),
             self.service.get_signal,
             mock.call(run_id="run-1", signal_id=TEMPERATURE, snapshot=model)),
            (envelope("get_signal_history", signal_id=TEMPERATURE, snapshot=SNAPSHOT,
                      event_start_ticks=None, event_end_ticks=-1, max_points=0),
             self.service.get_signal_history,
             mock.call(run_id="run-1", signal_id=TEMPERATURE, snapshot=model,
                       event_start_ticks=None, event_end_ticks=-1, max_points=0)))
        for request, method, expected in calls:
            with self.subTest(request=request["method"]):
                self.assertTrue(self.transport.dispatch(request).ok)
                self.assertEqual(expected, method.call_args)

    def test_known_errors_and_unexpected_errors_are_redacted(self):
        errors = ((UnknownEntity, ErrorCode.UNKNOWN_ENTITY),
                  (UnknownSignal, ErrorCode.UNKNOWN_SIGNAL),
                  (UnknownBinding, ErrorCode.UNKNOWN_SIGNAL),
                  (AmbiguousSignal, ErrorCode.AMBIGUOUS_SIGNAL),
                  (ViewUnavailable, ErrorCode.VIEW_UNAVAILABLE),
                  (VisibilityViolation, ErrorCode.VISIBILITY_VIOLATION),
                  (SnapshotUnavailable, ErrorCode.SNAPSHOT_UNAVAILABLE),
                  (ClockMismatch, ErrorCode.SNAPSHOT_UNAVAILABLE),
                  (ResourceLimitExceeded, ErrorCode.RESOURCE_LIMIT_EXCEEDED),
                  (TelemetryError, ErrorCode.INTERNAL_ERROR),
                  (InvalidRequest, ErrorCode.INVALID_REQUEST),
                  (ValueError, ErrorCode.INTERNAL_ERROR),          # server-side, not client
                  (RuntimeError, ErrorCode.INTERNAL_ERROR),
                  (KeyError, ErrorCode.INTERNAL_ERROR),
                  (TypeError, ErrorCode.INTERNAL_ERROR))
        secret = "C:/private/session hidden_fault IDV(4) <PrivateState> 1234"
        for exception, code in errors:
            with self.subTest(exception=exception):
                self.service.get_signal.side_effect = exception(secret)
                result = self.assert_failure(
                    envelope("get_signal", signal_id=TEMPERATURE, snapshot=SNAPSHOT), code)
                self.assertNotIn(secret, json.dumps(result))
        self.service.get_entity.side_effect = ValueError(secret)
        self.assert_failure(envelope("get_entity", entity_id="reactor"), ErrorCode.INTERNAL_ERROR)

    def test_telemetry_errors_outside_telemetry_reads_are_server_faults(self):
        for method, params in (("get_entity", {"entity_id": "reactor"}), ("get_run", {}),
                               ("get_run", {"snapshot": SNAPSHOT}),
                               ("open_telemetry_snapshot", {}),
                               ("get_signal", {"signal_id": TEMPERATURE, "snapshot": SNAPSHOT}),
                               ("get_signal_history", {"signal_id": TEMPERATURE,
                                                       "snapshot": SNAPSHOT})):
            with self.subTest(method=method):
                getattr(self.service, method).side_effect = TelemetryError("private fault")
                self.assert_failure(envelope(method, **params), ErrorCode.INTERNAL_ERROR)

    def test_serialization_failure_is_internal_error(self):
        for result in (object(), {"value": float("nan")}, {"value": object()}):
            self.service.get_run.return_value = result
            self.assert_failure(envelope(), ErrorCode.INTERNAL_ERROR)

    def test_nested_request_and_response_are_immutable_snapshots(self):
        raw = envelope()
        request = ApplicationRequest(**raw)
        raw["params"]["run_id"] = "changed"
        self.assertEqual("run-1", request.params["run_id"])
        with self.assertRaises(FrozenInstanceError):
            request.method = "start"
        with self.assertRaises(TypeError):
            request.params["run_id"] = "changed"
        source = {"nested": [{"value": 1}]}
        self.service.get_run.return_value = source
        response = self.transport.dispatch(request)
        source["nested"][0]["value"] = 2
        self.assertEqual(1, response.result["nested"][0]["value"])
        with self.assertRaises(TypeError):
            response.result["nested"][0]["value"] = 2
        with self.assertRaises(FrozenInstanceError):
            response.ok = False
        self.service.get_run.assert_called_once_with(run_id="run-1")

    def test_only_application_service_is_below_transport(self):
        import tep_agent_lab.application_transport as module
        source = inspect.getsource(module)
        for forbidden in ("getattr(", "eval(", "exec(", "RunQueries", "ProcessGraph",
                          ".telemetry(", "TEPEnvironment", "ReferenceWorld", "Path(",
                          "read_bytes", "fastapi", "flask", "subprocess", "run_telemetry"):
            self.assertNotIn(forbidden, source)


class TransportCodecTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.Mock(spec=ApplicationViewService)
        self.service.get_run.return_value = {"run_id": "run-1"}
        self.transport = ApplicationTransport(self.service)

    def assert_invalid(self, payload):
        response = self.transport.dispatch_json(payload)
        self.assertEqual({"protocol_version": PROTOCOL_VERSION, "request_id": None,
                          "ok": False, "error": {"code": "INVALID_REQUEST",
                                                   "message": "invalid request"}},
                         to_jsonable(response))
        self.assertEqual([], self.service.mock_calls)

    def test_valid_utf8_bytes_and_string_dispatch(self):
        request = envelope(run_id="run-中文")
        text = json.dumps(request, ensure_ascii=False)
        for payload in (text, text.encode("utf-8")):
            with self.subTest(kind=type(payload)):
                self.service.reset_mock()
                self.assertEqual(request, decode_request_json(payload))
                response = self.transport.dispatch_json(payload)
                self.assertTrue(response.ok)
                self.assertEqual("req-123", response.request_id)
                self.assertEqual({"run_id": "run-1"}, to_jsonable(response.result))
                self.service.get_run.assert_called_once_with(run_id="run-中文")

    def test_malformed_json_is_redacted_and_never_calls_service(self):
        for text in ("", '{"request_id":"secret",', '{"path":"C:/private/hidden_fault",}',
                     json.dumps(envelope()) + " trailing", '{"value":NaN}',
                     '{"value":Infinity}', '{"value":-Infinity}'):
            for payload in (text, text.encode("utf-8")):
                with self.subTest(payload=payload):
                    self.assert_invalid(payload)

    def test_invalid_utf8_is_rejected(self):
        for payload in (b'\xff', b'{"request_id":"\xc3("}', "\ud800"):
            self.assert_invalid(payload)

    def test_non_object_json_is_rejected(self):
        for payload in ("[]", "null", "true", "123", '"request"'):
            self.assert_invalid(payload)

    def test_exact_and_under_raw_byte_limit_are_accepted(self):
        text = json.dumps(envelope())
        for size in (MAX_REQUEST_BYTES - 1, MAX_REQUEST_BYTES):
            padded = text + " " * (size - len(text.encode("utf-8")))
            for payload in (padded, padded.encode("utf-8")):
                with self.subTest(size=size, kind=type(payload)):
                    self.service.reset_mock()
                    self.assertTrue(self.transport.dispatch_json(payload).ok)
                    self.service.get_run.assert_called_once_with(run_id="run-1")

    def test_oversized_raw_input_is_rejected_before_parsing(self):
        text = json.dumps(envelope())
        padded = text + " " * (MAX_REQUEST_BYTES + 1 - len(text.encode("utf-8")))
        with mock.patch("tep_agent_lab.application_transport.json.loads") as parser:
            for payload in (padded, padded.encode("utf-8")):
                self.assert_invalid(payload)
            parser.assert_not_called()

    def test_multibyte_size_uses_encoded_bytes(self):
        request = envelope(run_id="中")
        text = json.dumps(request, ensure_ascii=False)
        boundary = text + " " * (MAX_REQUEST_BYTES - len(text.encode("utf-8")))
        self.assertLess(len(boundary), MAX_REQUEST_BYTES)
        self.assertTrue(self.transport.dispatch_json(boundary).ok)
        self.service.reset_mock()
        above = boundary + " "
        self.assertEqual(MAX_REQUEST_BYTES + 1, len(above.encode("utf-8")))
        with mock.patch("tep_agent_lab.application_transport.json.loads") as parser:
            self.assert_invalid(above)
            parser.assert_not_called()

    def test_snapshot_requests_fit_the_raw_limit(self):
        request = envelope("get_signal_history", signal_id=TEMPERATURE, snapshot=SNAPSHOT,
                           event_start_ticks=0, event_end_ticks=1080, max_points=1000)
        self.assertLess(len(canonical_json(request).encode("utf-8")), MAX_REQUEST_BYTES // 16)

    def test_raw_requests_reuse_existing_schema_validation(self):
        for request, code in (({**envelope(), "extra": 1}, ErrorCode.INVALID_REQUEST),
                              (envelope("start"), ErrorCode.UNSUPPORTED_METHOD),
                              ({**envelope(), "protocol_version": "v1"},
                               ErrorCode.UNSUPPORTED_PROTOCOL_VERSION)):
            self.assertEqual(to_jsonable(self.transport.dispatch(request)),
                             to_jsonable(self.transport.dispatch_json(json.dumps(request))))
            self.assertEqual(code, self.transport.dispatch_json(json.dumps(request)).error.code)
        self.assertEqual([], self.service.mock_calls)


class TransportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.demo = e0.run_demo(Path(directory.name) / "transport", lab_revision=lab_revision(),
                               clock=lambda: NOW)
        cls.service = ApplicationViewService(cls.demo.manager.queries)
        cls.transport = ApplicationTransport(cls.service)

    def test_all_five_reads_match_application_models(self):
        run_id = self.demo.run_id
        opened = self.transport.dispatch(envelope("open_telemetry_snapshot", run_id=run_id))
        self.assertTrue(opened.ok)
        snapshot_json = to_jsonable(opened.result)
        snapshot = self.service.open_telemetry_snapshot(run_id)
        self.assertEqual(to_jsonable(snapshot), snapshot_json)
        calls = (("get_run", {"run_id": run_id, "snapshot": snapshot_json},
                  self.service.get_run(run_id, snapshot=snapshot)),
                 ("get_entity", {"run_id": run_id, "entity_id": "reactor"},
                  self.service.get_entity(run_id, "reactor")),
                 ("get_signal", {"run_id": run_id, "signal_id": TEMPERATURE,
                                 "snapshot": snapshot_json},
                  self.service.get_signal(run_id, TEMPERATURE, snapshot=snapshot)),
                 ("get_signal_history", {"run_id": run_id, "signal_id": PRESSURE,
                                         "snapshot": snapshot_json, "event_start_ticks": 0,
                                         "event_end_ticks": 1080, "max_points": 3},
                  self.service.get_signal_history(run_id, PRESSURE, snapshot=snapshot,
                                                  event_start_ticks=0, event_end_ticks=1080,
                                                  max_points=3)))
        for method, params, expected in calls:
            with self.subTest(method=method):
                request = ApplicationRequest(PROTOCOL_VERSION, "req-1", method, params)
                response = self.transport.dispatch(request)
                self.assertTrue(response.ok, to_jsonable(response))
                payload = to_jsonable(response)
                self.assertEqual(to_jsonable(expected), payload["result"])
                self.assertEqual(payload, to_jsonable(self.transport.dispatch_json(
                    canonical_json(request).encode("utf-8"))))
                encoded = canonical_json(response)
                self.assertEqual(payload, json.loads(encoded))
                self.assertEqual([], leakage_findings(payload))
                self.assertNotIn(str(self.demo.manager.root), encoded)
                self.assertNotIn("prepare-", encoded)

    def test_real_domain_errors_and_visibility(self):
        run_id = self.demo.run_id
        snapshot = to_jsonable(self.service.open_telemetry_snapshot(run_id))
        forged = {**snapshot, "snapshot_ref": "telemetry-snapshot:sha256:" + "0" * 64}
        for method, params, code in (
            ("get_entity", {"entity_id": "missing"}, ErrorCode.UNKNOWN_ENTITY),
            ("get_signal", {"signal_id": "IDV(4)", "snapshot": snapshot}, ErrorCode.UNKNOWN_SIGNAL),
            ("get_signal", {"signal_id": "XMEAS(9)", "snapshot": snapshot},
             ErrorCode.UNKNOWN_SIGNAL),
            ("get_signal", {"signal_id": TEMPERATURE, "snapshot": forged},
             ErrorCode.SNAPSHOT_UNAVAILABLE),
            ("get_signal_history", {"signal_id": PRESSURE, "snapshot": snapshot, "max_points": 0},
             ErrorCode.INVALID_REQUEST),
            ("get_signal_history", {"signal_id": PRESSURE, "snapshot": snapshot,
                                    "event_end_ticks": 10**6}, ErrorCode.INVALID_REQUEST),
            ("open_telemetry_snapshot", {"ingest_sequence": 10**6},
             ErrorCode.SNAPSHOT_UNAVAILABLE),
        ):
            with self.subTest(method=method, params=params):
                response = self.transport.dispatch(envelope(method, run_id=run_id, **params))
                self.assertFalse(response.ok)
                self.assertEqual(code, response.error.code)
        evaluator = ApplicationTransport(ApplicationViewService(
            lambda run_id: self.demo.manager.queries(run_id, ProjectionScope.EVALUATOR)))
        response = evaluator.dispatch(envelope(run_id=run_id))
        self.assertEqual(ErrorCode.VISIBILITY_VIOLATION, response.error.code)


if __name__ == "__main__":
    unittest.main()
