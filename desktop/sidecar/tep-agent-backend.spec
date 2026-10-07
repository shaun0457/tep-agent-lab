"""Only E0's import graph, NumPy's official hooks, and explicit required data."""
import json
import os
from pathlib import Path

config = json.loads(Path(os.environ["TEP_SIDECAR_BUILD_CONFIG"]).read_text())
sim = Path(config["tep_sim"]) / "src" / "tep_sim"
upstream = Path(config["tep_sim"]) / "vendor/tep-sim-upstream/src/tep"
# tep-sim verifies these exact source bytes at runtime, even when modules are frozen.
# cli/dashboard source is attestation data only; no dashboard stack is imported.
hashes = json.loads((sim / "upstream_hashes.json").read_text())
datas = [(str(sim / "upstream_hashes.json"), "tep_sim")]
datas += [(str(p), "tep_sim/fixtures") for p in (sim / "fixtures").glob("*.json")]
datas += [(str(upstream / name), str(Path("tep") / Path(name).parent)) for name in hashes]
datas += [(str(Path(config["resources"]) / name), "tep_agent_lab/_sidecar")
          for name in ("dependency-pins.json", "build-manifest.json")]
a = Analysis(
    [str(Path(SPECPATH) / "entrypoint.py")],
    pathex=config["paths"], datas=datas, binaries=[], hiddenimports=[],
    excludes=["tep.dashboard_dash", "tep.cli", "dash", "plotly", "matplotlib", "tkinter"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="tep-agent-backend-x86_64-pc-windows-msvc", console=True,
          debug=False, strip=False, upx=False)
