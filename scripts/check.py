"""Verify the exact local upstream pin, then run offline lab checks.

Usage: py -3.13 scripts/check.py --runtime ../industrial-agent-runtime
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runtime = args.runtime.resolve()
    pin = json.loads((root / "dependency-pins.json").read_text())["industrial-agent-runtime"]
    git = ["git", "-c", f"safe.directory={runtime}", "-C", str(runtime)]
    actual = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
    if actual != pin:
        print(f"Dependency pin mismatch: expected {pin}, got {actual}", flush=True)
        return 1
    dirty = subprocess.check_output(
        git + ["status", "--porcelain", "--untracked-files=normal"], text=True)
    if dirty.strip():
        print("Runtime checkout is dirty; cannot attest the pinned revision", flush=True)
        return 1
    print(f"Verified industrial-agent-runtime pin: {actual}", flush=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(root / "src"), str(runtime / "src")))
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                            cwd=root, env=env)
    if result.returncode:
        return result.returncode
    return subprocess.run([sys.executable, "-m", "compileall", "-q", "src", "tests"], cwd=root).returncode


if __name__ == "__main__":
    raise SystemExit(main())
