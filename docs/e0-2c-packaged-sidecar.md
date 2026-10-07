# E0.2C2B — Packaged Python backend

ADR-002 remains authoritative. Architecture = platform-neutral.
C2B validation matrix = Windows x86_64 only (`x86_64-pc-windows-msvc`).
macOS/Linux packaging is deferred; additional platform binaries can use the same
application boundary. This milestone completes E0.2C and E0.2, not E1.

```text
[React] -- application_request / Tauri invoke --> [Rust BackendProcess]
[Rust BackendProcess] -- host-owned launch / NDJSON --> [bundled external sidecar]
[sidecar: Python desktop_backend] -- v0 JSON --> [ApplicationTransport]
[ApplicationTransport] -- bounded reads --> [ApplicationViewService]
[ApplicationViewService] -- public AGENT projections --> [P0]
```

The packaged app needs no installed Python, source checkout, PYTHONPATH,
TEP_AGENT_PYTHON, tep-sim source tree or runtime Git. Python remains the industrial
backend; Rust owns process supervision and packaging only. No UI expansion,
network listener, HTTP/WebSocket backend, signing or updater is added.

## Build inputs and commands

Use a clean committed lab checkout, Python 3.13 x64, Node 22.18+, Rust/MSVC and
Tauri Windows prerequisites (including WebView2). Dependency checkouts must be
clean at the exact revisions from canonical `dependency-pins.json`, with tep-sim's
submodule initialized. Build in an isolated Python virtual environment:

```powershell
py -3.13 -m venv .venv
$python = (Resolve-Path .venv/Scripts/python.exe).Path
& $python -m pip install -r desktop/sidecar/requirements-build.txt
$numpy = (Get-Content dependency-pins.json | ConvertFrom-Json).numpy
& $python -m pip install "numpy==$numpy"
& $python scripts/check.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim
& $python desktop/sidecar/build_sidecar.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim
& $python desktop/sidecar/smoke_sidecar.py desktop/src-tauri/binaries/tep-agent-backend-x86_64-pc-windows-msvc.exe
Set-Location desktop
npm ci
npm run build
npm test
$env:TEP_FROZEN_SIDECAR = (Resolve-Path src-tauri/binaries/tep-agent-backend-x86_64-pc-windows-msvc.exe).Path
Set-Location src-tauri
cargo test --locked --test packaged -- --ignored
Set-Location ..
npm run tauri build -- --bundles nsis --target x86_64-pc-windows-msvc
Set-Location ..
& $python desktop/sidecar/verify_bundle.py # installs/uninstalls unsigned bundle in a fresh temp directory
```

Development real-Python tests additionally need the same TEP_AGENT_PYTHON and
PYTHONPATH setup documented in `e0-2c-tauri-shell.md`. The builder owns naming:
logical externalBin `binaries/tep-agent-backend`, generated file
`desktop/src-tauri/binaries/tep-agent-backend-x86_64-pc-windows-msvc.exe`.
Tauri removes the build target suffix when installing `tep-agent-backend.exe`
beside `tep-agent-lab-desktop.exe`. Generated binaries are ignored by Git.

The unsigned NSIS installer is generated under
`desktop/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/`.
Windows CI uploads it together with its build manifest as
`tep-agent-observatory-windows-x86_64-<commit SHA>`. Source dependency checkouts are
not distribution artifacts. This is a development/research distribution, with
no code signing, production certificates or release publishing.

## Resources and provenance

The builder attests pinned dependency revisions and NumPy before freezing. It
copies `dependency-pins.json` byte-for-byte into generated packaging resources;
there is no second source-controlled pins file. The generated build manifest
records lab commit, exact runtime/tep-sim revisions, NumPy pin, sidecar build
version, Python/PyInstaller versions, target triple and hashes of Cargo.lock,
package-lock.json and packaging requirements. PyInstaller is pinned to **6.22.3**;
its build dependencies are pinned in requirements-build.txt. Tauri Rust versions
are locked in Cargo.lock; Tauri CLI/API are 2.12.1 in npm's lockfile.

The narrow `sidecar_resources` locator selects source checkout or frozen package
resources explicitly. Frozen mode verifies pins bytes against the manifest
checksum and takes lab provenance from the embedded manifest; it never invokes
Git or guesses a repository root. Missing/inconsistent provenance fails startup.
Source mode continues to attest the lab via `git_revision(ROOT)`. The frontend
cannot supply provenance. These inputs are traceable/reconstructable; bit-for-bit
binary reproducibility is not claimed.

The spec follows the E0 import graph for tep_agent_lab, industrial_agent_runtime,
tep_sim and vendored tep. NumPy uses PyInstaller's official collection hooks.
tep-sim fixtures and upstream_hashes.json are explicit data. tep-sim's upstream
attestation reads and hashes a fixed set of upstream `.py` files, so those exact
files are preserved as data as well as needed frozen modules. Dashboard/CLI source
bytes are attestation data only; dashboard/UI stacks are excluded. No environment
wide collect-all or fork of simulator/runtime semantics is used.

## Launch and lifecycle

Debug builds preserve the existing system/developer Python path and
`$env:TEP_AGENT_PYTHON = ...; npm run tauri dev` workflow. The npm CLI wrapper
passes a dev-only externalBin override, and build.rs also clears externalBin for
plain debug cargo checks/tests. Developers need not freeze Python for frontend
iteration. Release builds keep the externalBin configuration and use
`app.shell().sidecar("tep-agent-backend")`.

The shell plugin's documented `From<Command> for std::process::Command` adapter
feeds the existing Tokio process transport. Both launch paths use the same
BackendProcess, OS pipes and framing implementation; no second bridge or event
framing loop exists. This preserves one child, one in-flight request, monotonic
IDs/correlation, 64 KiB request payloads, 1 MiB bounded response frames, stderr
isolation, timeout, poisoned-session/no-restart behavior, graceful stdin EOF,
kill/reap fallback and kill_on_drop. Rust's fixed run ID and arguments remain
host-owned. `desktop_backend` retains precisely its existing CLI/protocol.

PyInstaller one-file has a bootloader and an internal Python worker. Windows
launch creates the managed child suspended, assigns a kill-on-close Job Object,
then resumes its primary thread. Workers inherit that job before any user code
runs. Closing the job on host drop, poisoned session or fallback shutdown kills
the whole tree; a packaged test forcibly kills the bootloader and verifies worker
termination. There is still one host process channel and one P0 session.

Packaged output is a fresh `session-*` directory under Tauri's app local data
directory `/sessions` for every launch. P0 data is never written beside the
executable and no old ephemeral session is reopened. Paths stay host-private.

## Validation and architecture gate

The frozen EXE smoke copies only the executable to an isolated directory, removes
Python-related environment variables and empties PATH. It sends all four real
reads (`get_run`, reactor, XMEAS(9), XMEAS(7) history <=30 points), checks protocol
correlation, success, bounded telemetry, hidden-truth/path isolation, exactly one
P0 creation/start, embedded source provenance and clean EOF exit. It never launches
the Python module as the backend. Unit tests verify canonical copied bytes and
checksum mismatch rejection.

An explicit packaged Rust/Tauri IPC test launches the frozen binary through
ShellExt, retains one PID for the four reads, shuts down/reaps and proves shell
execute/spawn/stdin_write/kill/open are denied to the frontend. Windows CI runs
the existing development bridge suite, actually builds release externalBin + NSIS,
installs the bundle, compares the host's entire bytes with exactly Tauri's NSIS
bundle-type marker patch (the bundler restores the original build output), checks
the installed sidecar's unchanged SHA-256, smokes the installed
backend and uninstalls. The Python 3.11/3.13 matrix remains intact.

Architecture gate: React reaches only `application_request`; Rust reaches only
the supervised backend; desktop_backend reaches ApplicationTransport and
ApplicationViewService/P0. No frontend shell/process/filesystem permissions are
added. React has no shell dependency, executable selector, run-id selector,
output-root selector or direct sidecar stdin. Rust reads no telemetry files and
implements no ProcessGraph/domain semantics. Installed mode needs no checkout,
system Python or runtime Git. No HTTP/WebSocket application transport exists.

```text
[Static E0 HTML] -- application projections --\
                                              [Application / P0 architecture]
[Tauri Desktop] -- application requests ------/
```

Neither client is the architecture. Agent Runtime, P0, ProcessGraph,
ApplicationViewService and ApplicationTransport stay independent of UI redesign.
E0.2 COMPLETE = MVP UI is no longer an architectural dependency.
E1 remains the richer Industrial Observatory UI.

SPEC_CONFLICT: none
