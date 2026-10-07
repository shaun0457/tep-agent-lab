"""Real frozen EXE smoke from an isolated directory with no Python/Git on PATH."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

PROTOCOL = "tep-agent-lab.application-transport/v0"
FILENAME = "tep-agent-backend-x86_64-pc-windows-msvc.exe"


def smoke(binary: Path, build_manifest: dict) -> None:
    with TemporaryDirectory(prefix="frozen-smoke-") as directory:
        root = Path(directory)
        isolated = root / "installed-backend.exe"
        shutil.copyfile(binary, isolated)
        output = root / "sessions" / "fresh"
        reads = [("get_run", {}), ("get_entity", {"entity_id": "reactor"}),
                 ("get_signal", {"signal_id": "XMEAS(9)"}),
                 ("get_signal_history", {"signal_id": "XMEAS(7)", "max_points": 30})]
        wire = b"".join((json.dumps({"protocol_version": PROTOCOL,
                                     "request_id": f"smoke-{i}", "method": method,
                                     "params": {"run_id": "frozen-smoke", **params}})
                          + "\n").encode() for i, (method, params) in enumerate(reads, 1))
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("PYTHON", "TEP_AGENT", "TEP_SIDECAR"))}
        env["PATH"] = ""  # Neither Python nor Git nor the dependency sources can be found.
        result = subprocess.run([str(isolated), "--run-id", "frozen-smoke",
                                 "--output-root", str(output)], input=wire, cwd=root,
                                env=env, capture_output=True, timeout=120)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(replies) == 4, replies
        for i, reply in enumerate(replies, 1):
            assert reply["ok"] and reply["request_id"] == f"smoke-{i}", reply
            assert reply["protocol_version"] == PROTOCOL
            assert reply["result"]["run_id"] == "frozen-smoke"
        assert replies[0]["result"]["run_status"] == "COMPLETED"
        assert replies[1]["result"]["entity_id"] == "reactor"
        assert replies[2]["result"]["signal_id"] == "XMEAS(9)"
        assert replies[2]["result"]["telemetry_available"] is True
        history = replies[3]["result"]
        assert history["signal_id"] == "XMEAS(7)"
        assert 0 < len(history["points"]) <= 30
        text = result.stdout.decode().lower()
        for hidden in ("idv", "evaluator", "known_injected_cause", "developer_setup",
                       "active_disturbances", "candidate_cause", "lab-artifacts",
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
        print("Frozen EXE four-read smoke: PASS; one session; clean EOF; PATH empty")
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
