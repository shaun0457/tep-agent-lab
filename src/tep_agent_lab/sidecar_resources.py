"""Packaging-owned resources; source mode keeps the canonical checkout pins."""

import hashlib
import json
from pathlib import Path
import re
import sys


def resource_directory() -> Path:
    """PyInstaller places the declared data beside this package, never beside cwd."""
    return Path(__file__).with_name("_sidecar")


def dependency_pins_path() -> Path:
    if getattr(sys, "frozen", False):
        return resource_directory() / "dependency-pins.json"
    return Path(__file__).resolve().parents[2] / "dependency-pins.json"


def packaged_lab_revision() -> str | None:
    """Fail closed on absent/mismatched packaged provenance; never fall back to Git."""
    if not getattr(sys, "frozen", False):
        return None
    raw = dependency_pins_path().read_bytes()
    pins = json.loads(raw)
    manifest = json.loads((resource_directory() / "build-manifest.json").read_bytes())
    revision = manifest["lab_revision"]
    if (manifest["schema_version"] != "tep-agent-lab.sidecar-build/v1"
            or manifest["dependency_pins"] != pins
            or manifest["dependency_pins_sha256"] != hashlib.sha256(raw).hexdigest()
            or not isinstance(revision, str)
            or re.fullmatch(r"[0-9a-f]{40}", revision) is None):
        raise ValueError("invalid sidecar build provenance")
    return revision
