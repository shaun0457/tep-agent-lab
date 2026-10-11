"""Real frozen EXE smoke from an isolated directory with no Python/Git on PATH."""

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import tempfile
from tempfile import TemporaryDirectory
import threading
import time

PROTOCOL = "tep-agent-lab.application-transport/v1"
TEMPERATURE = "reactor.temperature_measurement"
PRESSURE = "reactor.pressure_measurement"
FILENAME = "tep-agent-backend-x86_64-pc-windows-msvc.exe"
SMOKE_TIMEOUT_SECONDS = 120
CLEANUP_TIMEOUT_SECONDS = 2


class SmokeFailure(AssertionError):
    """The backend did not answer correctly within the smoke deadline."""


class _ProcessTree:
    """Own descendants even if the launcher exits while a worker holds stdout."""

    def __init__(self) -> None:
        self._job = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                        ("thread_id", wintypes.DWORD), ("process_id", wintypes.DWORD),
                        ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                        ("flags", wintypes.DWORD)]

        self._ctypes, self._ThreadEntry = ctypes, ThreadEntry
        api = self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(api, name)
            function.argtypes, function.restype = arguments, result
        self._job = api.CreateJobObjectW(None, None)
        if not self._job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def start(self, argv: list[str], **kwargs: object) -> subprocess.Popen:
        process = subprocess.Popen(argv, **kwargs,
                                   creationflags=0x4 if os.name == "nt" else 0,
                                   start_new_session=os.name != "nt")  # CREATE_SUSPENDED
        self.process = process
        if os.name != "nt":
            return process
        # Suspend before assigning the job: no worker can escape in the launch race.
        ctypes, api = self._ctypes, self._api
        try:
            if not api.AssignProcessToJobObject(self._job, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            threads = api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
            if threads == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                entry = self._ThreadEntry()
                entry.size = ctypes.sizeof(entry)
                present = api.Thread32First(threads, ctypes.byref(entry))
                while present:
                    if entry.process_id == process.pid:
                        thread = api.OpenThread(0x2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                        if not thread:
                            raise ctypes.WinError(ctypes.get_last_error())
                        try:
                            if api.ResumeThread(thread) == 0xFFFFFFFF:
                                raise ctypes.WinError(ctypes.get_last_error())
                        finally:
                            api.CloseHandle(thread)
                        return process
                    present = api.Thread32Next(threads, ctypes.byref(entry))
                raise OSError("no suspended launcher thread")
            finally:
                api.CloseHandle(threads)
        except BaseException:
            self.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
            for stream in (process.stdin, process.stdout):
                stream.close()
            raise

    def close(self) -> None:
        if self._job:
            self._api.CloseHandle(self._job)  # kills all descendants, including orphaned workers
            self._job = None
        elif os.name != "nt" and hasattr(self, "process"):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class FrameSession:
    """One child process, one NDJSON frame and one reply at a time, under a deadline.

    stdout is read by a daemon thread into a queue, so every exchange waits with a
    timeout; stderr goes to an unbounded temporary file, so a chatty child can never
    block on a full pipe. On exit the entire process tree is terminated; process
    reaping and reader joins are bounded, and stdout is closed only after the
    reader has stopped, avoiding its buffered-read lock.
    """

    def __init__(self, argv: list[str], *, cwd: Path, env: dict[str, str],
                 timeout: float = SMOKE_TIMEOUT_SECONDS) -> None:
        self.deadline = time.monotonic() + timeout
        self.lines: list[bytes] = []
        self._stderr = tempfile.TemporaryFile()
        self._replies: queue.Queue[bytes] = queue.Queue()
        try:
            self._tree = _ProcessTree()
            self.process = self._tree.start(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=self._stderr)
        except BaseException:
            if hasattr(self, "_tree"):
                self._tree.close()
            self._stderr.close()
            raise
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for line in iter(self.process.stdout.readline, b""):
            self._replies.put(line)
        self._replies.put(b"")                              # EOF marker

    def _remaining(self) -> float:
        return self.deadline - time.monotonic()

    def stderr_text(self) -> str:
        self._stderr.flush()
        self._stderr.seek(0)
        return self._stderr.read()[-8192:].decode(errors="replace")

    def exchange(self, frame: dict) -> dict:
        try:
            self.process.stdin.write((json.dumps(frame) + "\n").encode())
            self.process.stdin.flush()
        except OSError:
            raise SmokeFailure("backend stdin closed; stderr:\n" + self.stderr_text()) from None
        remaining = self._remaining()
        if remaining <= 0:
            raise SmokeFailure("smoke deadline passed before a reply")
        try:
            line = self._replies.get(timeout=remaining)
        except queue.Empty:
            raise SmokeFailure("no backend reply before the deadline") from None
        if not line:
            raise SmokeFailure("backend closed stdout; stderr:\n" + self.stderr_text())
        self.lines.append(line)
        return json.loads(line)

    def finish(self) -> int:
        """Close stdin and wait for a clean exit within the remaining deadline."""
        self.process.stdin.close()
        try:
            return self.process.wait(timeout=max(self._remaining(), 0.001))
        except subprocess.TimeoutExpired:
            raise SmokeFailure("backend did not exit before the deadline") from None

    def __enter__(self) -> "FrameSession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Terminate descendants even when their launcher has already exited.
        self._tree.close()
        try:
            if not self.process.stdin.closed:
                self.process.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        self._reader.join(timeout=CLEANUP_TIMEOUT_SECONDS)
        # A buffered close would wait forever if a reader still held its lock.
        if not self._reader.is_alive():
            self.process.stdout.close()
        self._stderr.close()


def smoke(binary: Path, build_manifest: dict) -> None:
    with TemporaryDirectory(prefix="frozen-smoke-") as directory:
        root = Path(directory)
        isolated = root / "installed-backend.exe"
        shutil.copyfile(binary, isolated)
        output = root / "sessions" / "fresh"
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("PYTHON", "TEP_AGENT", "TEP_SIDECAR"))}
        env["PATH"] = ""  # Neither Python nor Git nor the dependency sources can be found.
        # v1 reads need the snapshot from the first reply: one frame, one reply.
        with FrameSession([str(isolated), "--run-id", "frozen-smoke",
                           "--output-root", str(output)], cwd=root, env=env) as session:
            def exchange(index: int, method: str, params: dict) -> dict:
                return session.exchange({"protocol_version": PROTOCOL,
                                         "request_id": f"smoke-{index}", "method": method,
                                         "params": {"run_id": "frozen-smoke", **params}})

            replies = [exchange(1, "open_telemetry_snapshot", {})]
            assert replies[0]["ok"], replies
            snapshot = replies[0]["result"]
            reads = [("get_run", {"snapshot": snapshot}), ("get_entity", {"entity_id": "reactor"}),
                     ("get_signal", {"signal_id": TEMPERATURE, "snapshot": snapshot}),
                     ("get_signal_history", {"signal_id": PRESSURE, "snapshot": snapshot,
                                             "max_points": 30})]
            replies += [exchange(i, method, params)
                        for i, (method, params) in enumerate(reads, 2)]
            returncode = session.finish()
            stderr = session.stderr_text()
            lines = list(session.lines)
        assert returncode == 0, stderr
        assert len(replies) == 5, replies
        for i, reply in enumerate(replies, 1):
            assert reply["ok"] and reply["request_id"] == f"smoke-{i}", reply
            assert reply["protocol_version"] == PROTOCOL
        assert replies[1]["result"]["run_id"] == "frozen-smoke"
        assert replies[4]["result"]["run_id"] == "frozen-smoke"
        assert replies[1]["result"]["run_status"] == "COMPLETED"
        assert replies[1]["result"]["snapshot_ref"] == snapshot["snapshot_ref"]
        assert replies[2]["result"]["entity_id"] == "reactor"
        assert replies[3]["result"]["signal_id"] == TEMPERATURE
        assert replies[3]["result"]["source_locator"] == "XMEAS(9)"
        assert replies[3]["result"]["status"] == "DATA"
        history = replies[4]["result"]
        assert history["signal_id"] == PRESSURE
        assert 0 < len(history["points"]) <= 30
        text = b"".join(lines).decode().lower()
        for hidden in ("idv", "evaluator", "known_injected_cause", "developer_setup",
                       "active_disturbances", "candidate_cause", "lab-artifacts", "prepare-",
                       str(root).lower(), str(binary.parent).lower()):
            assert hidden not in text, hidden
        run = output / "p0-runs/frozen-smoke"
        events = (run / "lifecycle/events.jsonl").read_text()
        assert events.count('"type":"RUN_CREATED"') == 1
        assert events.count('"type":"RUN_STARTED"') == 1
        # The persisted P0 manifest must carry the embedded lab revision and pins.
        manifest = json.loads((run / "prepare-0001/manifest.json").read_text())
        pins = build_manifest["dependency_pins"]
        assert manifest["source_revisions"] == {
            "tep_agent_lab_git_revision": build_manifest["lab_revision"],
            "industrial_agent_runtime_git_revision": pins["industrial-agent-runtime"],
            "tep_sim_git_revision": pins["tep-sim"],
        }, manifest["source_revisions"]
        assert manifest["numerical_stack"]["numpy"] == pins["numpy"]
        print("Frozen EXE v1 snapshot + four-read smoke: PASS; one session; clean EOF; "
              "PATH empty")
        print("Run provenance:", json.dumps(manifest.get("source_revisions", {}), sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[2]
                        / "build/sidecar/resources/build-manifest.json")
    args = parser.parse_args()
    binary = args.binary.resolve()
    assert binary.name == FILENAME, f"incorrect Tauri target name: {binary.name}"
    assert binary.is_file(), binary
    smoke(binary, json.loads(args.manifest.read_bytes()))


if __name__ == "__main__":
    main()
