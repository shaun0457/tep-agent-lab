# E0.2C1 — Persistent Python desktop backend

ADR-002 remains authoritative. This host adds process/session lifetime and stdio
framing to the existing application transport; it adds no domain read layer.

```text
[Tauri TypeScript UI, later] -- application requests --> [Rust host, later]
[Rust host] -- NDJSON stdin/stdout --> [desktop_backend Python process]
[desktop_backend] -- raw JSON --> [ApplicationTransport]
[ApplicationTransport] -- four bounded reads --> [ApplicationViewService]
[ApplicationViewService] -- AGENT projections --> [P0 RunManager / live session]
```

## Startup and ownership

From a lab source checkout with the pinned Python dependencies and lab source
on PYTHONPATH (or installed in editable mode):

```powershell
python -m tep_agent_lab.desktop_backend --run-id e0-observatory --output-root runs/e0-desktop
```

The only CLI options besides help are `--run-id` and `--output-root`. Defaults are
`e0-observatory` and `runs/e0-desktop`. Run records are write-once: choose a fresh
run id or output root when restarting. Existing records are not reopened to
reconstruct ephemeral telemetry.

Python owns one deterministic E0 developer-demo P0 session for the entire process.
`e0_demo.py` is the shared bootstrap for both the static report and desktop host:
create → prepare with trusted harness → start through the normal Coordinator.
The backend constructs one service and one transport after successful completion;
requests reuse that manager and completed in-memory session. This is not a
production plant connection. Arbitrary simulation/cause configuration is absent.

`RunManager.start()` already calls `RunSession.close()` in its terminal finally
path to release simulator handles while retaining readable history/lineage.
At EOF the loop returns and the process releases its owned in-memory objects.
There is no extra public manager shutdown API and no private cleanup traversal.
A failed startup writes trusted diagnostics to stderr and exits non-zero without
an application response. A non-COMPLETED demo is also a startup failure.

## Wire contract

**stdout is protocol-only; stderr is trusted diagnostics**, including CLI help.
The host redirects Python stdout diagnostics during bootstrap and dispatch to
stderr, and writes responses explicitly to the captured binary stdout stream.
No banner or startup-success envelope is emitted.

Each UTF-8 JSON line produces exactly one UTF-8 response line, serialized with
runtime `canonical_json` and flushed immediately. The envelope/version/methods
remain E0.2B's: `get_run`, `get_entity`, `get_signal`, `get_signal_history`.
There is no `scope` option or lifecycle/mutation protocol method.

```json
{"protocol_version":"tep-agent-lab.application-transport/v0","request_id":"1","method":"get_run","params":{"run_id":"e0-observatory"}}
```

Framing reuses E0.2B's `application_transport.MAX_REQUEST_BYTES` (64 KiB) as its sole
request-size authority. Every input read is bounded to that limit plus two
framing bytes. LF and CRLF terminators are excluded from the JSON byte count;
a final non-terminated frame is processed before clean EOF exit. Oversized lines
are drained in bounded chunks through LF/EOF; their bounded oversize prefix goes
through `dispatch_json` to obtain the existing `INVALID_REQUEST` envelope once.
Future frames remain readable. Invalid UTF-8, malformed JSON and blank lines also
go through the transport codec, return failures, and leave the process alive.
No schema validation or response DTO is duplicated in the host.

## Visibility and architecture review

Only ApplicationViewService's AGENT projections enter protocol responses. The
shared harness's known injected cause and developer setup are never serialized;
no active disturbances, evaluator projection or benchmark truth is exposed.
Trusted stderr is separate from client response data.

The request loop calls only `ApplicationTransport.dispatch_json`, bounded stream
reads and response serialization/writes. It never calls RunQueries, simulator,
ReferenceWorld, ProcessGraph or artifact filesystem methods. Graph semantics,
history filtering, visibility and authorization remain with their existing owners.
Bootstrap exists in one place; HTML/SVG/JavaScript and report assembly remain in
`examples/e0_environment_observatory.py`. The offline reproducible static report
continues to work as a separate host over the same bootstrap.

Tests cover injected host ownership/diagnostics, exact framing limits, draining,
error recovery and EOF, the static CLI, and one real subprocess with four reads
of the same run/session. The existing E0 and transport suites remain in the full
check. No dependency was added.

E0.2C2A adds the Tauri/TypeScript/Rust live development shell and process
supervision (`e0-2c-tauri-shell.md`). E0.2C2B owns packaging Python as an external
sidecar (`e0-2c-packaged-sidecar.md`). E0.2C is complete; E1 remains future UI work.

SPEC_CONFLICT: none
