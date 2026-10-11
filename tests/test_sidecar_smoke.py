"""The frozen-sidecar smoke harness cannot hang or deadlock on a misbehaving child."""

import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("smoke_sidecar", ROOT / "desktop/sidecar/smoke_sidecar.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

CHILDREN = {
    # Reads requests but never replies.
    "silent": "import sys\nfor line in sys.stdin.buffer:\n    pass\n",
    # Floods stderr well past any pipe buffer before every reply, then answers.
    "chatty": ("import json, sys\n"
               "for line in sys.stdin.buffer:\n"
               "    sys.stderr.write('diagnostic ' * 200000)\n"
               "    sys.stderr.flush()\n"
               "    request = json.loads(line)\n"
               "    print(json.dumps({'request_id': request['request_id'], 'ok': True}),"
               " flush=True)\n"),
    # Exits with a diagnostic before replying.
    "crash": "import sys\nsys.stdin.buffer.readline()\nsys.stderr.write('boom: private failure')\n"
             "sys.exit(3)\n",
}


class FrameSessionTests(unittest.TestCase):
    def session(self, name, timeout):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        script = Path(directory.name) / f"{name}.py"
        script.write_text(CHILDREN[name], encoding="utf-8")
        return smoke.FrameSession([sys.executable, str(script)], cwd=Path(directory.name),
                                  env=dict(os.environ), timeout=timeout)

    def test_child_that_never_replies_fails_within_the_deadline_and_is_killed(self):
        started = time.monotonic()
        with self.session("silent", timeout=2) as session:
            with self.assertRaises(smoke.SmokeFailure) as raised:
                session.exchange({"request_id": "smoke-1"})
            process = session.process
        self.assertIn("deadline", str(raised.exception))
        self.assertLess(time.monotonic() - started, 30)
        self.assertIsNotNone(process.poll())              # killed and reaped

    def test_child_flooding_stderr_does_not_deadlock(self):
        with self.session("chatty", timeout=60) as session:
            replies = [session.exchange({"request_id": f"smoke-{i}"}) for i in range(3)]
            self.assertEqual(0, session.finish())
            self.assertIn("diagnostic", session.stderr_text())
        self.assertEqual(["smoke-0", "smoke-1", "smoke-2"], [r["request_id"] for r in replies])

    def test_eof_reports_the_child_stderr(self):
        with self.session("crash", timeout=30) as session:
            with self.assertRaises(smoke.SmokeFailure) as raised:
                session.exchange({"request_id": "smoke-1"})
        self.assertIn("boom: private failure", str(raised.exception))

    def test_default_deadline_matches_the_previous_smoke_timeout(self):
        self.assertEqual(120, smoke.SMOKE_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
