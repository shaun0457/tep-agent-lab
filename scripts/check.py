"""Verify the exact local upstream pins, then run offline lab checks.

Usage: py -3.13 scripts/check.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim

Both dependencies must be clean checkouts at the exact revisions recorded in
``dependency-pins.json``; a floating branch head is never accepted. The numerical
stack is attested too: the NumPy imported by the test interpreter must equal the
recorded ``numpy`` pin. Exact Git revisions are owned by ``dependency-pins.json``,
this script, and CI; ``pyproject.toml`` declares package-level dependencies only, and
each pinned checkout's package version must equal that exact ``==`` declaration.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

# tep-sim imports the vendored upstream ``tep`` package from its pinned submodule.
TEP_UPSTREAM_SOURCE = Path("vendor/tep-sim-upstream/src")


def attest(name: str, checkout: Path, pin: str) -> bool:
    # Git for Windows compares safe.directory using slash-normalized paths.
    git_path = checkout.as_posix()
    git = ["git", "-c", f"safe.directory={git_path}", "-C", git_path]
    actual = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
    if actual != pin:
        print(f"Dependency pin mismatch for {name}: expected {pin}, got {actual}", flush=True)
        return False
    # Submodule commits and contents are part of the superproject status.
    dirty = subprocess.check_output(
        git + ["status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none"],
        text=True)
    if dirty.strip():
        print(f"{name} checkout is dirty; cannot attest the pinned revision", flush=True)
        return False
    print(f"Verified {name} pin: {actual}", flush=True)
    return True


def attest_version(name: str, checkout: Path, declared: list[str]) -> bool:
    """The pinned checkout's package version equals the lab's exact ``name==`` pin."""
    expected = [item.split("==", 1)[1].strip() for item in declared
                if item.replace(" ", "").startswith(f"{name}==")]
    if len(expected) != 1:
        print(f"Lab must declare exactly one {name}==<version> pin, found {expected}",
              flush=True)
        return False
    try:
        with open(checkout / "pyproject.toml", "rb") as file:
            actual = tomllib.load(file)["project"]["version"]
    except (OSError, KeyError) as error:
        print(f"Cannot read {name} package version: {error!r}", flush=True)
        return False
    if expected != [actual]:
        print(f"Package version mismatch for {name}: lab declares {expected}, "
              f"checkout is {actual}", flush=True)
        return False
    print(f"Verified {name} package version: {actual}", flush=True)
    return True


def attest_numpy(pin: str) -> bool:
    """The interpreter that runs the tests imports exactly the pinned NumPy."""
    actual = subprocess.check_output(
        [sys.executable, "-c", "import numpy; print(numpy.__version__)"], text=True).strip()
    if actual != pin:
        print(f"NumPy pin mismatch: expected {pin}, got {actual}", flush=True)
        return False
    print(f"Verified numpy pin: {actual}", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--tep-sim", required=True, type=Path, dest="tep_sim")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runtime, tep_sim = args.runtime.resolve(), args.tep_sim.resolve()
    pins = json.loads((root / "dependency-pins.json").read_text())
    with open(root / "pyproject.toml", "rb") as file:
        declared = tomllib.load(file)["project"]["dependencies"]
    if not (attest("industrial-agent-runtime", runtime, pins["industrial-agent-runtime"])
            and attest("tep-sim", tep_sim, pins["tep-sim"])
            and attest_version("industrial-agent-runtime", runtime, declared)
            and attest_version("tep-sim", tep_sim, declared)
            and attest_numpy(pins["numpy"])):
        return 1
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((
        str(root / "src"), str(runtime / "src"), str(tep_sim / "src"),
        str(tep_sim / TEP_UPSTREAM_SOURCE)))
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                            cwd=root, env=env)
    if result.returncode:
        return result.returncode
    return subprocess.run([sys.executable, "-m", "compileall", "-q", "src", "tests"], cwd=root).returncode


if __name__ == "__main__":
    raise SystemExit(main())
