"""Schema isolation, error redaction, and real in-process application reads."""

from dataclasses import FrozenInstanceError
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from industrial_agent_runtime import to_jsonable
from industrial_agent_runtime.serialization import canonical_json

from tep_agent_lab.application_transport import (
    ApplicationRequest, ApplicationTransport, ErrorCode, MAX_REQUEST_BYTES, PROTOCOL_VERSION,
    decode_request_json,
)
from tep_agent_lab.application_views import (
    AmbiguousSignal, ApplicationViewService, UnknownEntity, UnknownSignal,
)
from tep_agent_lab.canonical_context import ProjectionScope
from tep_agent_lab.playground_views import ViewUnavailable, VisibilityViolation
from tep_agent_lab.tep_world import leakage_findings
from test_e0_observatory import e0
from test_playground import lab_revision
from test_tool_surface import NOW


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
        for method in ("__dict__", "start", "get_artifact", "get_signal; import os", ""):
            self.assert_failure(envelope(method), ErrorCode.UNSUPPORTED_METHOD)
        self.assert_failure({**envelope(), "protocol_version": "v1"},
                            ErrorCode.UNSUPPORTED_PROTOCOL_VERSION)
        self.assertEqual([], self.service.mock_calls)

    def test_method_parameter_schemas(self):
        for method, identity in (("get_entity", "entity_id"), ("get_signal", "signal_id"),
                                 ("get_signal_history", "signal_id")):
            for params in ({}, {identity: None}, {identity: 5}, {identity: []},
                           {identity: "value", "unknown": 1}):
                self.assert_failure(envelope(method, **params), ErrorCode.INVALID_REQUEST)
        for options in ({"start_hours": True}, {"end_hours": "1"}, {"max_points": True},
                        {"max_points": 2.0}, {"max_points": None},
                        {"start_hours": float("nan")}, {"end_hours": float("inf")}):
            self.assert_failure(envelope("get_signal_history", signal_id="XMEAS(9)", **options),
                                ErrorCode.INVALID_REQUEST)
        self.assert_failure(envelope("get_signal", signal_id="XMEAS(9)", max_points=5),
                            ErrorCode.INVALID_REQUEST)
        self.assertEqual([], self.service.mock_calls)

    def test_known_errors_and_unexpected_errors_are_redacted(self):
        errors = ((UnknownEntity, ErrorCode.UNKNOWN_ENTITY),
                  (UnknownSignal, ErrorCode.UNKNOWN_SIGNAL),
                  (AmbiguousSignal, ErrorCode.AMBIGUOUS_SIGNAL),
                  (ViewUnavailable, ErrorCode.VIEW_UNAVAILABLE),
                  (VisibilityViolation, ErrorCode.VISIBILITY_VIOLATION),
                  (RuntimeError, ErrorCode.INTERNAL_ERROR),
                  (KeyError, ErrorCode.INTERNAL_ERROR),
                  (TypeError, ErrorCode.INTERNAL_ERROR),
                  (ValueError, ErrorCode.INTERNAL_ERROR))
        secret = "C:/private/session hidden_fault IDV(4) <PrivateState>"
        for exception, code in errors:
            with self.subTest(exception=exception):
                self.service.get_run.side_effect = exception(secret)
                result = self.assert_failure(envelope(), code)
                self.assertNotIn(secret, json.dumps(result))
        self.service.get_signal_history.side_effect = ValueError(secret)
        self.assert_failure(envelope("get_signal_history", signal_id="XMEAS(9)"),
                            ErrorCode.INVALID_REQUEST)

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

    def test_numeric_semantics_remain_in_service(self):
        self.service.get_signal_history.return_value = {"points": []}
        options = {"start_hours": -1, "end_hours": None, "max_points": 0}
        self.assertTrue(self.transport.dispatch(
            envelope("get_signal_history", signal_id="XMEAS(9)", **options)).ok)
        self.service.get_signal_history.assert_called_once_with(
            run_id="run-1", signal_id="XMEAS(9)", **options)

    def test_only_application_service_is_below_transport(self):
        import tep_agent_lab.application_transport as module
        source = inspect.getsource(module)
        for forbidden in ("getattr(", "eval(", "exec(", "RunQueries", "ProcessGraph",
                          ".telemetry(", "TEPEnvironment", "ReferenceWorld", "Path(",
                          "read_bytes", "fastapi", "flask", "subprocess"):
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
        request = {**envelope(), "request_id": "req-中文"}
        text = json.dumps(request, ensure_ascii=False)
        for payload in (text, text.encode("utf-8")):
            with self.subTest(kind=type(payload)):
                self.service.reset_mock()
                self.assertEqual(request, decode_request_json(payload))
                response = self.transport.dispatch_json(payload)
                self.assertTrue(response.ok)
                self.assertEqual("req-中文", response.request_id)
                self.assertEqual({"run_id": "run-1"}, to_jsonable(response.result))
                self.service.get_run.assert_called_once_with(run_id="run-1")

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
        # Legal JSON whitespace leaves room for exactly one three-byte character.
        request = {**envelope(), "request_id": "中"}
        text = json.dumps(request, ensure_ascii=False)
        boundary = text + " " * (MAX_REQUEST_BYTES - len(text.encode("utf-8")))
        self.assertLess(len(boundary), MAX_REQUEST_BYTES)
        self.assertTrue(self.transport.dispatch_json(boundary).ok)
        self.service.reset_mock()
        above = boundary + " "
        self.assertLess(len(above), MAX_REQUEST_BYTES)
        self.assertEqual(MAX_REQUEST_BYTES + 1, len(above.encode("utf-8")))
        with mock.patch("tep_agent_lab.application_transport.json.loads") as parser:
            self.assert_invalid(above)
            parser.assert_not_called()

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

    def test_all_four_reads_match_application_models(self):
        run_id = self.demo.run_id
        calls = (("get_run", {"run_id": run_id}, self.service.get_run(run_id)),
                 ("get_entity", {"run_id": run_id, "entity_id": "reactor"},
                  self.service.get_entity(run_id, "reactor")),
                 ("get_signal", {"run_id": run_id, "signal_id": "XMEAS(9)"},
                  self.service.get_signal(run_id, "XMEAS(9)")),
                 ("get_signal_history", {"run_id": run_id, "signal_id": "XMEAS(7)",
                                         "start_hours": 0.0, "end_hours": 0.3, "max_points": 3},
                  self.service.get_signal_history(run_id, "XMEAS(7)", start_hours=0.0,
                                                  end_hours=0.3, max_points=3)))
        for method, params, expected in calls:
            with self.subTest(method=method):
                request = ApplicationRequest(PROTOCOL_VERSION, "req-中文", method, params)
                response = self.transport.dispatch(request)
                self.assertTrue(response.ok)
                payload = to_jsonable(response)
                self.assertEqual(to_jsonable(expected), payload["result"])
                self.assertEqual(payload, to_jsonable(self.transport.dispatch_json(
                    canonical_json(request).encode("utf-8"))))
                self.assertNotIn("error", payload)
                encoded = canonical_json(response)
                self.assertEqual(payload, json.loads(encoded))
                self.assertEqual(encoded, canonical_json(self.transport.dispatch(request)))
                self.assertEqual([], leakage_findings(payload))
                self.assertNotIn(str(self.demo.manager.root), encoded)

    def test_real_domain_errors_and_visibility(self):
        for method, params, code in (
            ("get_entity", {"entity_id": "missing"}, ErrorCode.UNKNOWN_ENTITY),
            ("get_signal", {"signal_id": "IDV(4)"}, ErrorCode.UNKNOWN_SIGNAL),
            ("get_signal_history", {"signal_id": "XMEAS(7)", "max_points": 0},
             ErrorCode.INVALID_REQUEST),
            ("get_signal_history", {"signal_id": "XMEAS(7)", "end_hours": 100},
             ErrorCode.VIEW_UNAVAILABLE),
        ):
            response = self.transport.dispatch(envelope(method, run_id=self.demo.run_id, **params))
            self.assertFalse(response.ok)
            self.assertEqual(code, response.error.code)
        evaluator = ApplicationTransport(ApplicationViewService(
            lambda run_id: self.demo.manager.queries(run_id, ProjectionScope.EVALUATOR)))
        response = evaluator.dispatch(envelope(run_id=self.demo.run_id))
        self.assertEqual(ErrorCode.VISIBILITY_VIOLATION, response.error.code)


if __name__ == "__main__":
    unittest.main()
