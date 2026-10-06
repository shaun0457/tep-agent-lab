# E0.2A — Application View Service

Transport-neutral Python reads for a future interactive Observatory. ADR-002 owns
the boundary. E0/E0.1's offline HTML and SVG renderer remain unchanged.

```text
[future UI] -- bounded requests --> [thin transport, later]
[thin transport] -- application reads --> [ApplicationViewService]
[ApplicationViewService] -- AGENT queries --> [P0 RunQueries]
[RunQueries] -- semantics --> [ProcessGraphView]
[RunQueries] -- current/recent values --> [TelemetryView]
```

## Use

```python
from industrial_agent_runtime import to_jsonable
from tep_agent_lab.application_views import ApplicationViewService

views = ApplicationViewService(manager.queries)
run = views.get_run(run_id)
reactor = views.get_entity(run_id, "reactor")
signal = views.get_signal(run_id, "XMEAS(7)")
history = views.get_signal_history(
    run_id, "XMEAS(7)", start_hours=0.0, end_hours=0.3, max_points=500,
)
payload = to_jsonable(history)  # JSON-compatible; no transport selected
```

`application_models.py` defines frozen dataclasses `Run`, `Entity`, `Signal`,
`SignalHistory`, and supporting `Binding`, `SignalPoint`, `EntityType`. Nested
collections are tuples and nested records are frozen. No storage paths, live
objects or artifact bodies enter these models.

## Sources and failures

- `get_run`: `RunSummaryView` supplies the distinct application/runtime statuses,
  view version and AGENT manifest checksum/version. `TelemetryView.current`
  supplies time when available. CREATED/RUNNING or unavailable live telemetry
  can yield `None` for optional fields; visibility errors still fail closed.
- `get_entity`: AGENT `ProcessGraphView` supplies node/edge semantics, bindings
  and topology. `upstream`/`downstream` mean immediate neighboring nodes; an
  edge uses its source/target nodes. Missing entities raise `UnknownEntity`.
- `get_signal`: searches actual graph bindings on both nodes and edges. Repeated
  bindings must agree on quantity/relation/unit or raise `AmbiguousSignal`.
  Current values and their timestamp come only from single-signal P0 telemetry.
  Missing bindings raise `UnknownSignal`; unavailable telemetry is explicit.
- `get_signal_history`: validates an AGENT-visible graph binding and semantics,
  finite nonnegative time bounds, and an integer response bound, then requests
  one signal via `RunQueries.telemetry`. Missing telemetry raises `ViewUnavailable`.

The service requires AGENT-bound queries and checks projection scope/run identity
and the existing P0 leakage screen before assembly. Evaluator queries cannot be
passed through to an ordinary client. No graph, signal registry or world state is
copied into a second canonical owner. Each call assembles fresh projections; these
separate P0 calls are not a new atomic cross-view snapshot contract.

## Arbitrary-history investigation

Arbitrary **AGENT-visible graph-bound** signals, including `XMEAS(7)`, are supported
through the existing bounded recent P0 projection. E0's `VARIABLES` only selects
`XMEAS(9)`/`XMEAS(21)` for its report and tool artifacts; it is not the P0 telemetry
allowlist. The behavioral regression test runs real E0/P0 and reads `XMEAS(7)`
through `RunQueries`, without creating another tool artifact.

Owning code evidence:

- `playground_views.py: RunQueries.telemetry`: filters requested variables from
  sanitized history and returns the last `max_records` (hard limit 1000).
- `tep_world.py: sanitize_observation`, `ReferenceWorld.advance/history`: retain
  sanitized measurements/manipulated variables in recorded reference history.
- Pinned tep-sim `environment.py: observe/rollout`: observations contain runtime
  measurements/manipulated variables and rollout records carry those observations.
- `RunQueries.get_artifact` verifies exact issued typed refs/checksums, but reads
  whole artifact bodies. It is neither necessary nor used for this bounded service.

This is **not archival history or a historian**. No P0 start/end query, pagination,
or bounded artifact-body slice currently exists. Windows requiring older records
than P0's recent projection fail closed. Supporting such windows later would
require an owning P0 bounded window/pagination contract, not service-side world or
filesystem access. No owning production contract is changed here.

## Deterministic bounds

- `max_points` defaults to 500; hard maximum `MAX_HISTORY_POINTS = 1000`.
- The source query is bounded to `MAX_TELEMETRY_RECORDS = 1000`, with one variable.
- Omitted bounds default to the first/last available P0 record. The response
  reports available endpoints and whether earlier source records were omitted.
- An explicit start requiring omitted older records or an end beyond the latest
  record raises `ViewUnavailable`; no silent clipping or claim of full history.
- Explicit `end < start`, negative/nonfinite hours, and invalid point limits raise
  `ValueError`. Boolean values are not accepted as numbers.
- Time comparisons allow `1e-9` hours of absolute floating-point endpoint tolerance
  (about 3.6 microseconds). Timestamps and values remain verbatim; no interpolation.
- Filter first, then if `n > max_points = m`, select indices
  `floor(i * (n - 1) / (m - 1))` for `i = 0..m-1`. This preserves endpoints and
  order. For `m = 1`, return the latest selected point. `selected_points` and
  `downsampled` disclose reduction. An available window between samples is empty.
- Provenance identifies the run, P0 view version and `P0 TelemetryView.records`
  source, without exposing storage details or claiming an immutable artifact ref.
- Live-world projections are ephemeral and unavailable while RUNNING or after
  reopening a run without its session, per existing P0 semantics.

## Architecture review

No duplicate graph truth, manual signal map, direct simulator/private session
access, raw artifact filesystem reads, evaluator payloads, unbounded history,
TEP-specific public application types, transport framework, frontend, or dependency
was added. Full `scripts/check.py` includes all unchanged E0/E0.1 acceptance tests.

SPEC_CONFLICT: none
