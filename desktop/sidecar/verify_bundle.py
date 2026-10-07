"""Install unsigned NSIS in CI, verify final layout, smoke its backend, then uninstall."""

import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from smoke_sidecar import FILENAME, smoke

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_nsis_host(installed: Path, unbundled: Path) -> None:
    # Tauri patches the first bundle-type token for NSIS, then restores the
    # original build output. Accept exactly that change, never normalize arbitrary
    # installed bytes. See tauri-bundler/src/bundle.rs::patch_binary.
    raw = unbundled.read_bytes()
    token = b"__TAURI_BUNDLE_TYPE_VAR_UNK"
    assert token in raw, "missing Tauri bundle-type marker"
    expected = raw.replace(token, b"__TAURI_BUNDLE_TYPE_VAR_NSS", 1)
    assert installed.read_bytes() == expected, "installed host differs beyond NSIS marker"


def main() -> None:
    release = ROOT / "desktop/src-tauri/target/x86_64-pc-windows-msvc/release"
    installers = list((release / "bundle/nsis").glob("*.exe"))
    assert len(installers) == 1, installers
    manifest = json.loads((ROOT / "build/sidecar/resources/build-manifest.json").read_bytes())
    with TemporaryDirectory(prefix="tep-bundle-") as directory:
        installed = Path(directory) / "app"
        try:
            subprocess.run([str(installers[0]), "/S", f"/D={installed}"], check=True, timeout=180)
            backend = installed / "tep-agent-backend.exe"
            host = installed / "tep-agent-lab-desktop.exe"
            assert host.is_file() and backend.is_file(), list(installed.glob("*"))
            verify_nsis_host(host, release / host.name)
            assert digest(backend) == digest(ROOT / "desktop/src-tauri/binaries" / FILENAME)
            assert not (installed / "src").exists()
            assert not (installed / "dependency-pins.json").exists()
            smoke(backend, manifest)
            print("Tauri NSIS installed layout: PASS; app + exact bundled sidecar")
        finally:
            uninstaller = installed / "uninstall.exe"
            if uninstaller.is_file():
                # NSIS _?= disables self-copy so the waited process owns completion.
                subprocess.run([str(uninstaller), "/S", f"_?={installed}"],
                               check=True, timeout=120)
    print(f"Windows unsigned distributable: {installers[0]}")


if __name__ == "__main__":
    main()
