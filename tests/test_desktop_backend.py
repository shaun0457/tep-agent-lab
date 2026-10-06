"""Bounded stdio framing, host ownership, and real shared E0/P0 compatibility."""

import ast
from contextlib import redirect_stdout
import inspect
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from industrial_agent_runtime.serialization import canonical_json

from tep_agent_lab import desktop_backend as backend, e0_demo
from tep_agent_lab.application_transport import ApplicationTransport, MAX_REQUEST_BYTES, PROTOCOL_VERSION
from tep_agent_lab.application_views import ApplicationViewService
from tep_agent_lab.playground import RunStatus
from tep_agent_lab.tep_world import leakage_findings
from test_e0_observatory import e0


def request(method="get_run", **params):
    return canonical_json({"protocol_version": PROTOCOL_VERSION, "request_id": method,
                           "method": method, "params": {"run_id": "e0-observatory", **params}}).encode("utf-8")


def four_requests():
    return [request(), request("get_entity", entity_id="reactor"),
            request("get_signal", signal_id="XMEAS(9)"),
            request("get_signal_history", signal_id="XMEAS(7)")]


class BoundedSource(io.BytesIO):
    def readline(self, size=-1):
        if not 0 < size <= MAX_REQUEST_BYTES + 2:
            raise AssertionError("unbounded input read")
        return super().readline(size)


class Sink(io.BytesIO):
    flushes = 0

    def flush(self):
        self.flushes += 1


class FramingTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.Mock(spec=ApplicationViewService)
        self.service.get_run.return_value = {"run_id": "e0-observatory"}
        self.transport = ApplicationTransport(self.service)

    def exchange(self, data):
        sink = Sink()
        backend.serve(self.transport, BoundedSource(data), sink)
        lines = sink.getvalue().splitlines()
        self.assertEqual(len(lines), sink.flushes)
        return [json.loads(line) for line in lines]

    def test_one_response_per_line_and_same_service(self):
        replies = self.exchange(request() + b"\n" + request() + b"\r\n")
        self.assertEqual(2, len(replies))
        self.assertTrue(all(row["ok"] for row in replies))
        self.assertEqual([mock.call(run_id="e0-observatory")] * 2,
                         self.service.get_run.call_args_list)

    def test_malformed_utf8_and_blank_frames_continue(self):
        replies = self.exchange(b'{bad\n\xff\n\n' + request() + b"\n")
        self.assertEqual(["INVALID_REQUEST"] * 3,
                         [row["error"]["code"] for row in replies[:-1]])
        self.assertTrue(replies[-1]["ok"])
        self.service.get_run.assert_called_once()

    def test_oversize_drained_in_bounded_chunks_before_next_request(self):
        replies = self.exchange(b"x" * (MAX_REQUEST_BYTES * 5) + b"\n" + request() + b"\n")
        self.assertEqual(2, len(replies))
        self.assertEqual("INVALID_REQUEST", replies[0]["error"]["code"])
        self.assertIsNone(replies[0]["request_id"])
        self.assertTrue(replies[1]["ok"])
        self.service.get_run.assert_called_once()

    def test_exact_byte_limit_lf_crlf_and_eof(self):
        padded = request() + b" " * (MAX_REQUEST_BYTES - len(request()))
        for ending in (b"\n", b"\r\n", b""):
            with self.subTest(ending=ending):
                self.assertTrue(self.exchange(padded + ending)[0]["ok"])
        for ending in (b"\n", b"\r\n", b""):
            with self.subTest(oversize_ending=ending):
                replies = self.exchange(padded + b" " + ending)
                self.assertEqual(1, len(replies))
                self.assertEqual("INVALID_REQUEST", replies[0]["error"]["code"])
        self.assertEqual([], self.exchange(b""))

    def test_client_loop_has_no_domain_or_filesystem_access(self):
        tree = ast.parse(inspect.getsource(backend.serve))
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertEqual({"readline", "endswith", "dispatch_json", "write", "encode", "flush"}, calls)
        imports = inspect.getsource(backend)
        for forbidden in ("RunQueries", "TEPEnvironment", "ReferenceWorld", "ProcessGraph",
                          "TelemetryView", "get_artifact", "_runs"):
            self.assertNotIn(forbidden, imports)


class HostTests(unittest.TestCase):
    def test_one_bootstrap_one_service_one_transport_and_diagnostics_stderr(self):
        source = mock.Mock(buffer=io.BytesIO(request() + b"\n" + request()))
        wire = io.BytesIO()
        stdout, stderr = io.TextIOWrapper(wire, encoding="utf-8"), io.StringIO()
        manager = mock.Mock()
        demo = mock.Mock(manager=manager)
        demo.outcome.terminal_status = RunStatus.COMPLETED

        def bootstrap(*args):
            print("trusted bootstrap diagnostic")
            return demo

        service = mock.Mock(spec=ApplicationViewService)
        service.get_run.side_effect = lambda **kwargs: (print("trusted read diagnostic") or kwargs)
        with mock.patch.object(backend, "run_demo", side_effect=bootstrap) as boot, \
                mock.patch.object(backend, "ApplicationViewService", return_value=service) as views, \
                mock.patch.object(backend, "ApplicationTransport", wraps=ApplicationTransport) as transport, \
                mock.patch("sys.stdin", source), mock.patch("sys.stdout", stdout), \
                mock.patch("sys.stderr", stderr):
            self.assertEqual(0, backend.main([]))
        boot.assert_called_once()
        views.assert_called_once_with(manager.queries)
        transport.assert_called_once_with(service)
        self.assertEqual(2, len(wire.getvalue().splitlines()))
        self.assertTrue(all(json.loads(line)["ok"] for line in wire.getvalue().splitlines()))
        self.assertIn("trusted bootstrap diagnostic", stderr.getvalue())
        self.assertIn("trusted read diagnostic", stderr.getvalue())

    def test_startup_exception_and_failed_demo_emit_no_protocol(self):
        for failure in (RuntimeError("trusted startup error"), None):
            wire = io.BytesIO()
            stdout, stderr = io.TextIOWrapper(wire, encoding="utf-8"), io.StringIO()
            demo = mock.Mock()
            demo.outcome.terminal_status = RunStatus.FAILED
            with mock.patch.object(backend, "run_demo", side_effect=failure, return_value=demo), \
                    mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                self.assertEqual(1, backend.main([]))
            self.assertEqual(b"", wire.getvalue())
            self.assertIn("desktop backend failed:", stderr.getvalue())

    def test_help_is_diagnostic_only(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as result:
                backend.main(["--help"])
        self.assertEqual(0, result.exception.code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("--output-root", stderr.getvalue())


class DemoCompatibilityTests(unittest.TestCase):
    def test_static_cli_uses_shared_bootstrap_and_preserves_report(self):
        self.assertIs(e0.run_demo, e0_demo.run_demo)
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            self.assertEqual(0, e0.main(["--output-root", directory]))
            html = e0.report_path(Path(directory), e0_demo.DEFAULT_RUN_ID).read_text(encoding="utf-8")
            self.assertIn('id="agent-payload"', html)
            self.assertIn('id="developer-setup"', html)

    def test_subprocess_multiple_reads_same_real_session_and_clean_eof(self):
        with TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "tep_agent_lab.desktop_backend", "--output-root", directory],
                input=b"\n".join(four_requests()) + b"\n", capture_output=True, timeout=90)
            self.assertEqual(0, result.returncode, result.stderr.decode("utf-8"))
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(4, len(replies))
            self.assertTrue(all(row["ok"] for row in replies), replies)
            self.assertEqual("COMPLETED", replies[0]["result"]["run_status"])
            self.assertTrue(replies[-1]["result"]["points"])
            self.assertEqual([], leakage_findings(replies))
            for hidden in ("IDV", "active_disturbances", "evaluator", "known_injected_cause",
                           "developer_setup", "candidate_cause", "lab-artifacts", directory):
                self.assertNotIn(hidden.lower(), result.stdout.decode("utf-8").lower())
            # One creation/start, not a new session per request; no report is produced.
            events = (Path(directory) / "p0-runs" / e0_demo.DEFAULT_RUN_ID /
                      "lifecycle" / "events.jsonl").read_text(encoding="utf-8")
            self.assertEqual(1, events.count('"type":"RUN_CREATED"'))
            self.assertEqual(1, events.count('"type":"RUN_STARTED"'))
            self.assertFalse((Path(directory) / "observatory").exists())


if __name__ == "__main__":
    unittest.main()
