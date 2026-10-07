# E0.2C2A — Tauri live development shell

ADR-002 remains authoritative. This is the first native desktop client, not E1.

```text
[React / TypeScript] -- four typed reads --> [applicationClient.ts]
[applicationClient.ts] -- Tauri invoke --> [Rust application_request]
[Rust application bridge] -- serialized stdin/stdout NDJSON --> [persistent Python child]
[desktop_backend] -- existing v0 envelope --> [ApplicationTransport]
[ApplicationTransport] -- bounded application reads --> [ApplicationViewService]
[ApplicationViewService] -- public AGENT projections --> [P0]
```

## Development setup

Requires Node 22.18+, npm, stable Rust and the platform's Tauri v2 native build
prerequisites (Windows: MSVC build tools and WebView2). Use the exact Python
dependency revisions in `dependency-pins.json` and its exact NumPy version.
`scripts/check.py` remains the Python verification owner; it requires explicit
`--runtime` and `--tep-sim` checkout arguments.

Use a Python environment with the pinned runtime and tep-sim available, including
the vendored upstream `tep` source. Editable lab installation is optional: the
host prepends this checkout's `src` to inherited `PYTHONPATH`. Example from the lab
checkout, with dependencies in sibling directories:

```powershell
$env:TEP_AGENT_PYTHON = (py -3.13 -c "import sys; print(sys.executable)")
$runtime = (Resolve-Path ../industrial-agent-runtime).Path
$sim = (Resolve-Path ../tep-sim).Path
$env:PYTHONPATH = "$runtime/src;$sim/src;$sim/vendor/tep-sim-upstream/src"
& $env:TEP_AGENT_PYTHON scripts/check.py --runtime $runtime --tep-sim $sim
Set-Location desktop
npm ci
npm run tauri dev
```

`TEP_AGENT_PYTHON` is one host-owned executable path/name, not a shell command or
argument string; unset defaults to `python`. No frontend argument can change it.
Rust launches `-m tep_agent_lab.desktop_backend --run-id e0-observatory
--output-root <fresh-host-directory>` once during Tauri setup. The output root
is a unique `tep-desktop-*` directory under the OS temporary directory, retained
for run history/diagnostics. No prior run is reopened. Each new app session gets
a fresh output root; changing the environment requires restarting the app.

C2A's development mode uses a Python environment and this source checkout.
C2B adds a release-only packaged externalBin and unsigned Windows NSIS bundle;
see `e0-2c-packaged-sidecar.md`. Debug builds and `npm run tauri dev` still use
this development path without requiring a sidecar build. No updater is added.

## Boundary and lifecycle

Only `application_request(method, params)` is registered. Rust allows `get_run`,
`get_entity`, `get_signal`, `get_signal_history`; rejects client run IDs and
unknown parameter names; adds the fixed demo run ID and v0 protocol version.
Python retains schema/range/domain/visibility validation. IDs are host-generated
`desktop-1`, `desktop-2`, ... across the session. JSON is compact/sorted-key serde
serialization followed by LF; no JSON strings are manually assembled.

A single `BackendProcess` owns the child handle, stdin, buffered stdout, stderr
drain task, monotonic counter and health state. One async mutex covers ID
generation, write/flush/read and response validation. Only one request can be
in flight. No multiplexing or automatic restart exists.

Requests retain E0.2B's 64 KiB payload limit. The fixed response frame limit is
**1 MiB including LF**. Bounded `fill_buf`/`consume` prevents unbounded response
allocation, including newline-free output. EOF, invalid UTF-8/JSON, mismatched
IDs, wrong protocol, malformed envelopes or oversize frames fail closed. Rust
checks exactly one object result or fixed E0.2B error. Broken I/O/framing marks
the session unavailable and stops the child; later calls return the same stable
error, never a new P0 session. Requests time out after 30 seconds, including
initial Python bootstrap. Slow/missing development environments fail visibly.

Python stderr is drained in 4 KiB chunks to trusted Rust stderr. It is never an
application response. Native failures return only stable enum codes such as
`BACKEND_NOT_RUNNING`, `BACKEND_IO_ERROR`, `BACKEND_PROTOCOL_ERROR`,
`BACKEND_RESPONSE_TOO_LARGE`, `BACKEND_EXITED`, `BACKEND_TIMEOUT`.
Frontend validation narrows `unknown`, checks the exact v0 envelope and JSON
safety, and renders errors as React text with fixed public messages.

On app exit Rust cancels any in-flight exchange, closes child stdin, allows up
to two seconds for EOF exit, and kills/reaps only if necessary. Shutdown also
joins the stderr drain with a bound. `kill_on_drop` covers failed setup/panic.
Windows child console suppression keeps the host-owned child in the background.

The only capability is `allow-application-request` for the local `main` window,
with an explicit application command manifest. C2B registers the shell plugin
for Rust-side launch only, without any frontend shell permission; filesystem
and HTTP access are not exposed. No remote IPC capability is enabled. Production CSP
permits local Tauri IPC only. Vite's loopback dev server/HMR are frontend build
tooling; they do not carry application requests. There is no application HTTP,
WebSocket, CORS or network backend. All industrial reads go through Python;
Rust/TypeScript never read P0 files or implement ProcessGraph/simulator semantics.

## UI and smoke test

The shell automatically reads the Run Summary. Click the three probe buttons:
`reactor`, `XMEAS(9)`, and `XMEAS(7)` history with `max_points: 30`. Each response
shows its monotonic request ID. Run Summary is retained as transient display
state. The returned entity/signal semantics come entirely from Python.

Expected: COMPLETED run, successful entity and signal responses, nonempty bounded
history, one persistent Python PID throughout. Close the app and verify that PID
exits. There is no start-backend button or run ID input. E1 will add the P&ID,
charts, signal browser, branch tree, Agent trace and SOP/context UI.

Headless checks (set the same Python environment as above):

```powershell
npm run build # tsc --noEmit + Vite production assets
npm test      # node:test, no additional test framework
Set-Location src-tauri
cargo fmt --check
cargo check --locked
cargo test --locked
```

Rust tests exercise real child reuse, IDs, canonical LF framing, allowlist,
serialization under concurrent callers, malformed/mismatched/oversized output,
child exit, stderr separation and cancellation/reaping on shutdown. The real
backend smoke test performs all four reads against one actual Python process;
the Tauri mock-runtime integration test goes through the registered IPC command
without launching a window. No GUI is required by CI. The desktop job runs
Node/Rust checks and real Python integration on Windows; the existing Python
3.11/3.13 matrix is preserved.

On Windows MSVC, the build script also embeds a Common Controls v6 manifest into
Cargo's integration-test executables. Tauri's app manifest normally covers only
the app binary; mock-runtime tests still link native common-control APIs. This
avoids the Windows loader's `STATUS_ENTRYPOINT_NOT_FOUND` before tests can run.
It adds no runtime process API or frontend permission.

SPEC_CONFLICT: none
