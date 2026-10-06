# E0.2B — Thin Application Transport

ADR-002 remains authoritative. This milestone defines a versioned bounded JSON
boundary around an already-constructed `ApplicationViewService`.

```text
[future TypeScript UI] -- application request --> [future Tauri Rust adapter]
[Rust adapter] -- raw UTF-8 JSON --> [bounded JSON codec]
[bounded JSON codec] -- decoded object --> [ApplicationTransport]
[ApplicationTransport] -- four application reads --> [ApplicationViewService]
[ApplicationViewService] -- public AGENT projections --> [P0 RunQueries]
```

There is no HTTP server, Tauri implementation, stdio loop, standalone backend,
run-reopening mechanism, or process attachment. Live telemetry depends on the
existing in-process P0 session. E0.2C owns process lifecycle and IPC framing; an
adapter must not invent cross-process access or bypass P0 to retrieve telemetry.

## Use

```python
from industrial_agent_runtime import to_jsonable
from industrial_agent_runtime.serialization import canonical_json
from tep_agent_lab.application_transport import ApplicationTransport, PROTOCOL_VERSION
from tep_agent_lab.application_views import ApplicationViewService

transport = ApplicationTransport(ApplicationViewService(manager.queries))
response = transport.dispatch({
    "protocol_version": PROTOCOL_VERSION,
    "request_id": "req-123",
    "method": "get_signal",
    "params": {"run_id": run_id, "signal_id": "XMEAS(9)"},
})
payload = to_jsonable(response)
wire_json = canonical_json(response)  # deterministic JSON; UTF-8 when encoded
raw_response = transport.dispatch_json(canonical_json({
    "protocol_version": PROTOCOL_VERSION,
    "request_id": "req-124",
    "method": "get_run",
    "params": {"run_id": run_id},
}).encode("utf-8"))
```

`ApplicationRequest` optionally represents the decoded request as a frozen
dataclass. Dispatch validates both raw mappings and typed requests. Request params
and successful serialized results are defensively copied and recursively frozen
with runtime `freeze_json`. The transport does not define another domain DTO:
results come directly from the existing application dataclasses via `to_jsonable`.

## Raw JSON codec

`MAX_REQUEST_BYTES = 64 * 1024` (64 KiB) is the fixed local application transport
protocol limit, not an HTTP/body limit. E0.2C must respect the same bound when
wiring the future Tauri/Rust adapter to Python. No standalone backend process or
process loop exists in this milestone.

```text
[raw UTF-8 JSON] -- raw byte count --> [size bound: 64 KiB]
[size bound] -- UTF-8 parsing --> [JSON object decode]
[JSON object] -- dispatch --> [existing schema validation]
[validated request] -- method selection --> [allowlisted dispatch]
```

`decode_request_json(payload: bytes | str)` returns a decoded object or an
`ApplicationFailure`. It checks `len(payload)` for bytes and the encoded UTF-8
length for strings before JSON parsing. Exactly 64 KiB is accepted; anything
larger is rejected. Whitespace and multibyte characters count toward the raw
limit; canonical re-serialization is not used to measure requests.

Invalid UTF-8, malformed JSON (including non-JSON NaN/Infinity constants), parser
depth failures, oversized input, and non-object top-level JSON return the fixed
`INVALID_REQUEST` error with `request_id: null`. No parser exception text or raw
payload is returned. `ApplicationTransport.dispatch_json(payload)` uses this
codec and passes decoded objects to the existing `dispatch()` schema/allowlist
owner. Codec failures never call the application service. Decoded Mapping and
`ApplicationRequest` dispatch remain available for trusted internal callers;
external raw input must enter through the bounded codec.

## v0 contract

The protocol version is exactly `tep-agent-lab.application-transport/v0`.
Requests have exactly four fields: string `protocol_version`, string `request_id`,
string `method`, object `params`. IDs are opaque strings used for correlation.

| Method | Required string params | Optional params |
| --- | --- | --- |
| `get_run` | `run_id` | none |
| `get_entity` | `run_id`, `entity_id` | none |
| `get_signal` | `run_id`, `signal_id` | none |
| `get_signal_history` | `run_id`, `signal_id` | `start_hours`, `end_hours`, `max_points` |

History bounds accept JSON numbers or null (the service's default endpoints).
`max_points` accepts integers only and cannot be null. Booleans are not numbers.
Unknown envelope/parameter fields, missing fields, wrong types, and nonfinite
numbers are `INVALID_REQUEST`. The service still owns nonnegative bounds, window
ordering, point limits, graph bindings, visibility, and history availability.
Omitted `max_points` retains the service's default of 500.

Validation order is envelope shape, protocol version, method allowlist, parameter
shape/JSON safety, then service invocation. No rejected schema reaches the service.

Success has exactly `protocol_version`, `request_id`, `ok: true`, and `result`.
Failure has exactly `protocol_version`, `request_id`, `ok: false`, and `error`:

```json
{"protocol_version":"tep-agent-lab.application-transport/v0","request_id":"req-123","ok":false,"error":{"code":"UNKNOWN_SIGNAL","message":"unknown signal"}}
```

Responses always identify the supported v0 protocol, including version failures.
Malformed requests retain a string request ID if present; otherwise the response
uses `request_id: null`. Error messages never echo exception text or parameters.

| Failure | Stable code |
| --- | --- |
| `UnknownEntity` | `UNKNOWN_ENTITY` |
| `UnknownSignal` | `UNKNOWN_SIGNAL` |
| `AmbiguousSignal` | `AMBIGUOUS_SIGNAL` |
| `ViewUnavailable` | `VIEW_UNAVAILABLE` |
| `VisibilityViolation` | `VISIBILITY_VIOLATION` |
| Schema failure or history `ValueError` | `INVALID_REQUEST` |
| Method outside the four reads | `UNSUPPORTED_METHOD` |
| Version mismatch | `UNSUPPORTED_PROTOCOL_VERSION` |
| Unexpected service/serialization exception | `INTERNAL_ERROR` |

Fixed public messages expose no stack traces, filesystem paths, evaluator data,
session internals, or object reprs. Unexpected exceptions are not retained in the
response. A trusted caller may instrument its service separately if diagnostics
are needed. Errors do not relax application visibility checks.

## Verification

`tests/test_application_transport.py` exercises malformed requests before service
calls, explicit dispatch, error redaction, immutable envelopes, serialization
failure, raw UTF-8 decoding and byte limits, deterministic JSON, real four-method
E0/P0 equivalence, domain validation,
and evaluator-scope rejection. The complete `scripts/check.py` suite also checks
unchanged static E0/E0.1 behavior and exact dependency pins.

SPEC_CONFLICT: none
