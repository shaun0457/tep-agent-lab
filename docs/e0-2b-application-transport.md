# E0.2B — Thin Application Transport

ADR-002 remains authoritative. This milestone defines a versioned decoded-JSON
boundary around an already-constructed `ApplicationViewService`.

```text
[future TypeScript UI] -- application request --> [future Tauri Rust adapter]
[Rust adapter] -- versioned JSON --> [ApplicationTransport]
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
```

`ApplicationRequest` optionally represents the decoded request as a frozen
dataclass. Dispatch validates both raw mappings and typed requests. Request params
and successful serialized results are defensively copied and recursively frozen
with runtime `freeze_json`. The transport does not define another domain DTO:
results come directly from the existing application dataclasses via `to_jsonable`.

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
failure, deterministic JSON, real four-method E0/P0 equivalence, domain validation,
and evaluator-scope rejection. The complete `scripts/check.py` suite also checks
unchanged static E0/E0.1 behavior and exact dependency pins.

SPEC_CONFLICT: none
