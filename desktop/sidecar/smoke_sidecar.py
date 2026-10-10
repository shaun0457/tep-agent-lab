"""Real frozen EXE smoke from an isolated directory with no Python/Git on PATH."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

PROTOCOL = "tep-agent-lab.application-transport/v1"
TEMPERATURE = "reactor.temperature_measurement"
PRESSURE = "reactor.pressure_measurement"
FILENAME = "tep-agent-backend-x86_64-pc-windows-msvc.exe"


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
        process = subprocess.Popen([str(isolated), "--run-id", "frozen-smoke",
                                    "--output-root", str(output)], cwd=root, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        lines: list[bytes] = []

        def exchange(index: int, method: str, params: dict) -> dict:
            frame = json.dumps({"protocol_version": PROTOCOL, "request_id": f"smoke-{index}",
                                "method": method,
                                "params": {"run_id": "frozen-smoke", **params}})
            process.stdin.write((frame + "\n").encode())
            process.stdin.flush()
            line = process.stdout.readline()
            lines.append(line)
            return json.loads(line)

        try:
            replies = [exchange(1, "open_telemetry_snapshot", {})]
            assert replies[0]["ok"], replies
            snapshot = replies[0]["result"]
            reads = [("get_run", {"snapshot": snapshot}), ("get_entity", {"entity_id": "reactor"}),
                     ("get_signal", {"signal_id": TEMPERATURE, "snapshot": snapshot}),
                     ("get_signal_history", {"signal_id": PRESSURE, "snapshot": snapshot,
                                             "max_points": 30})]
            replies += [exchange(i, method, params)
                        for i, (method, params) in enumerate(reads, 2)]
            process.stdin.close()
            returncode = process.wait(timeout=120)
            stderr = process.stderr.read()
        finally:
            if process.poll() is None:
                process.kill()
            process.stdout.close()
            process.stderr.close()
        assert returncode == 0, stderr.decode(errors="replace")
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
