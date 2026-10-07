"""Attest exact source inputs and build Tauri's Windows target-specific externalBin."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
TARGET = "x86_64-pc-windows-msvc"
FILENAME = f"tep-agent-backend-{TARGET}.exe"
BUILD_VERSION = "e0.2c2b/v1"


def generate_resources(root: Path, destination: Path, revision: str) -> dict:
    """Copy canonical pins byte-for-byte; duplicated values exist only in generated data."""
    raw = (root / "dependency-pins.json").read_bytes()
    manifest = {
        "schema_version": "tep-agent-lab.sidecar-build/v1",
        "sidecar_build_version": BUILD_VERSION,
        "target_triple": TARGET,
        "lab_revision": revision,
        "dependency_pins": json.loads(raw),
        "dependency_pins_sha256": hashlib.sha256(raw).hexdigest(),
        "python_version": platform.python_version(),
        "pyinstaller_version": importlib.metadata.version("PyInstaller"),
        "build_tools": {
            line.split("==")[0]: importlib.metadata.version(line.split("==")[0])
            for line in (root / "desktop/sidecar/requirements-build.txt").read_text().splitlines()
            if "==" in line and not line.startswith("#")},
        "lockfile_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("desktop/src-tauri/Cargo.lock", "desktop/package-lock.json",
                         "desktop/sidecar/requirements-build.txt")},
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "dependency-pins.json").write_bytes(raw)
    (destination / "build-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--tep-sim", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        parser.error("C2B packaging supports Windows x86_64 only")
    if sys.version_info[:2] != (3, 13):
        parser.error("C2B builds require Python 3.13")
    for line in Path(__file__).with_name("requirements-build.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==")
        if importlib.metadata.version(name) != expected:
            parser.error(f"install the exact requirements-build.txt first: {name}")
    runtime, sim = args.runtime.resolve(), args.tep_sim.resolve()
    spec = importlib.util.spec_from_file_location("lab_checks", ROOT / "scripts/check.py")
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    pins = json.loads((ROOT / "dependency-pins.json").read_bytes())
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    if not (checks.attest("industrial-agent-runtime", runtime, pins["industrial-agent-runtime"])
            and checks.attest("tep-sim", sim, pins["tep-sim"])
            and checks.attest_version("industrial-agent-runtime", runtime, declared)
            and checks.attest_version("tep-sim", sim, declared)
            and checks.attest_numpy(pins["numpy"])):
        return 1
    # Reject modified tracked input: the embedded SHA must identify the built lab code.
    git = ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT)]
    if subprocess.check_output(git + ["status", "--porcelain", "--untracked-files=normal"],
                               text=True).strip():
        parser.error("build from a clean committed lab checkout for exact provenance")
    revision = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
    build = ROOT / "build/sidecar"
    resources = build / "resources"
    generate_resources(ROOT, resources, revision)
    paths = [str(ROOT / "src"), str(runtime / "src"), str(sim / "src"),
             str(sim / "vendor/tep-sim-upstream/src")]
    config = build / "config.json"
    config.write_text(json.dumps({"paths": paths, "resources": str(resources),
                                  "tep_sim": str(sim)}), encoding="utf-8")
    env = os.environ.copy()
    env["TEP_SIDECAR_BUILD_CONFIG"] = str(config)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(build / "dist"), "--workpath", str(build / "work"),
                    str(Path(__file__).with_name("tep-agent-backend.spec"))],
                   env=env, check=True)
    binary = build / "dist" / FILENAME
    if not binary.is_file():
        raise RuntimeError(f"missing target-specific sidecar: {FILENAME}")
    destination = ROOT / "desktop/src-tauri/binaries" / FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(binary, destination)
    print(f"Sidecar: {destination}\nManifest: {resources / 'build-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
