# E0 — TEP Environment Observatory

Developer/research observability demo on top of the merged P0 Playground Backend.
Code: `examples/e0_environment_observatory.py`. Tests: `tests/test_e0_observatory.py`.

## What E0 is

A runnable demo that executes one real P0 run and renders what happened as one
self-contained HTML page (inline CSS/SVG/JavaScript, no network, no server). It shows
the TEP world, telemetry over simulation time, the ProcessGraph, a baseline snapshot,
a fork, a counterfactual rollout, the runtime calls, and the P0 records they produced.

```text
TEP simulator -> TEPEnvironment -> ReferenceWorld -> C4/C5 tool surface
 -> industrial-agent-runtime Coordinator -> P0 RunManager / RunOutcome
 -> P0 read projections (AGENT scope) -> E0 HTML observatory
```

## What E0 is not

Not a production frontend, not a P1 transport (no HTTP/WebSocket/server), not an Agent
Ops platform, not a canonical Context Layer, not a D0 benchmark (no case freeze, no
scoring), and not a real historian adapter. It adds no runtime dependency and no new
state, world, telemetry, or ProcessGraph store.

## Run it

Requires the lab installed with its exact pins (see `README.md` / `scripts/check.py`),
or `PYTHONPATH` pointing at the pinned checkouts.

```powershell
py examples/e0_environment_observatory.py --output-root runs/e0-demo --open
```

- `--output-root` (default `runs/e0-demo`, ignored by git):
  - P0 run records: `<root>/p0-runs/<run-id>/`;
  - report: `<root>/observatory/<run-id>/observatory.html`.
- `--run-id` (default `e0-observatory`); `--lab-revision` (default `git rev-parse HEAD`).
- `--open` opens the file with `webbrowser`; otherwise the path is printed.
- An existing run id or report is refused (exit 2); nothing is overwritten.
- Exit 0 only for `RunStatus COMPLETED`, `TaskStatus DONE`, the full tool sequence,
  and a clean post-run lineage check. A failure after the run prints where the P0
  records are; no partial report is written.
- On Windows keep the output root short: tep-sim world artifact paths are nested and
  can exceed `MAX_PATH`.

## Demo scenario (developer-only, intentionally known)

World: `seed = 11`, `CLOSED_LOOP`, record interval 60 s, variables `XMEAS(9)`, `XMEAS(21)`.

Trusted harness (`RunManager.prepare(case_setup=...)`, never an Agent tool):
reset -> advance 0.1 h -> designate pre-incident baseline -> apply the developer-demo
cause -> advance 0.2 h. The cause and its timing are trusted demo metadata. This is not a
benchmark case.

Scripted `FakeProvider` (local to the example; typed `ModelTurn`s only), every call
through B2 gates, the lab consumer, the Executor, B3 verification and ingestion:

1. `get_capability_summary` — Agent-visible baseline snapshot listing;
2. `get_history` (0.3 h, both variables) -> `HistoryWindowArtifact`;
3. `fork_environment` from the harness-designated baseline;
4. `run_rollout` (0.2 h) on the baseline-descended branch -> `RolloutTelemetryArtifact`;
5. `compare_trajectories` (C5): history vs rollout, `EXACT_TIMESTAMPS`, window
   0.1–0.3 h, `NONE`, `MEAN_ABSOLUTE_ERROR`/`ROOT_MEAN_SQUARE_ERROR`/`FINAL_ERROR`
   (descriptive, not scoring);
6. finish (`require_conclusion=False`; no causal claim, so no evidence links).

Handle discovery: the model `ContextProjection` carries observation *refs*, not their
summaries, so a scripted provider cannot read a fork result's `branch_id`. The baseline
handle is the return value of the harness `designate_baseline()`. The branch handle is
the world's next sequential handle (`branch-0002`). `lineage_findings` checks both after
the run against the Agent-visible capability/fork/rollout observations and
`BranchTreeView`. The provider receives only the baseline handle cell, never the harness.

The script is developer-authored and intentionally knows the demo timing: its comparison
window (0.1–0.3 h) and fork origin equal the injection time. The fork time is already in
the Agent-visible fork result, so nothing beyond the tool results enters AGENT data, but
the demo is no evidence that an Agent can localise an onset.

## Report sections

- **A Run/World summary**: application `RunStatus` vs runtime `TaskStatus` (P0 frozen
  semantics: COMPLETED is hosting success, not task success), simulation time, seed,
  control mode, record interval, ProcessGraph version/checksum/review status, AGENT
  manifest checksum, tool-set/gate/verifier identity.
- **B Data flow**: documentation only.
- **C Time series**: inline SVG + vanilla JS, variable selector, raw engineering units,
  per-variable auto-scaled y axis, simulation time on x. Reference = the
  `HistoryWindowArtifact`; counterfactual = the `RolloutTelemetryArtifact`. Markers:
  branch origin (Agent-visible fork result) and the injection time (developer block).
  C5 metrics table below.
- **D Process telemetry overlay**: original embedded equipment/stream SVG, validated
  against `ProcessGraphView`; select a node or stream for kind/name/tag, measurements,
  actuators, incident bindings and upstream/downstream. Section C selection highlights
  exact bindings; `[plot]` buttons reuse section C's existing chart.
- **E Snapshot/branch tree**: `BranchTreeView` parent links: reference -> baseline
  snapshot -> counterfactual branch -> rollout.
- **F Runtime event timeline**: `RunEventView` (lifecycle, runtime trace, lab RunLog)
  with source identity kept, and per-turn tool calls. `Trace != Observation != Evidence`
  is stated and no trace item is relabelled as evidence.
- **G Investigation/artifacts/budgets**: counts of observations, evidence links,
  hypotheses, experiments; budget limits vs usage; issued artifacts; raw AGENT views in
  `<details>`.
- **Trusted demo setup** (red block): *DEVELOPER / EVALUATOR ONLY — not Agent-visible
  context*.

## Agent-visible vs developer-only

- `build_agent_report(queries)` accepts only AGENT-scoped `RunQueries`
  (`manager.queries(run_id)`), reads each artifact with its exact listed
  `InformationRef` through `get_artifact` (P0 verifies the checksum), and fails closed
  if the payload has any `leakage_findings`.
- `DemoHarness.developer_setup()` is the only place the known cause appears. It is
  embedded as a separate `<script type="application/json" id="developer-setup">` block,
  never merged into the AGENT payload (`id="agent-payload"`), a view, a
  `ContextProjection`, or an artifact.
- No raw filesystem path enters the page; there is no file browser.

## Data ownership

```text
TEPEnvironment owns simulation
ReferenceWorld owns harness reference history
runtime owns execution trace/task lifecycle
Lab owns investigation/artifacts
P0 owns application run lifecycle/projections
E0 owns no engineering truth
```

## Telemetry today

Simulated telemetry already exists end to end:

```text
TEPEnvironment.rollout()
 -> telemetry JSONL
 -> ReferenceWorld.history()
 -> C4 get_history
 -> P0 TelemetryView
```

E0 only visualizes that pipeline. External plant time series (PI Historian, OPC UA,
Influx, MES) remain future work.

## Known limitations

- `TelemetryView`/`BranchTreeView` need the in-process session (P0 interpretation 5), so
  the report is built in the same process as the run; it is not rebuilt from disk later.
- The scripted provider cannot read tool outputs (see handle discovery above).
- The AGENT event feed has no request payloads; tool calls are matched to turns by feed
  order and state-revision order.
- One fixed scenario; no benchmark, no scoring, no real LLM.

## E0.1 — P&ID-style process telemetry overlay

```text
[SVG presentation geometry]
       | canonical data-process-node-id / data-process-edge-id
       v
[P0 ProcessGraphView: semantic/topology truth]
       | binding.runtime_variable_id + binding.unit
       v
[P0 TelemetryView.current: dynamic truth] -> [detail card / selected-signal readout]
       | exact Agent-visible history/rollout artifact values
       v
[existing section C chart] <-> [selection / exact binding highlight]
```

The visible label is **TEP process schematic — P&ID-style, not authoritative P&ID
geometry**. This is a P&ID-style schematic, not an authoritative engineering P&ID.
`examples/assets/e0/tep_process_schematic.svg` is original curated geometry, with
simplified vessels, mixer, condenser, compressor, feed/outlet shapes and utilities.
It contains no bindings, units, telemetry or hidden cause labels. Line crossings
do not add junctions. No external figure was copied or scraped.

SVG = presentation geometry; ProcessGraph = semantic/topology truth;
P0 telemetry = dynamic truth. E0.1 owns coordinates, icons and selection only.
The asset covers all 17 nodes and 18 edges of the pinned human-verified 0.2.0 graph.
Every interactive group carries exactly one canonical semantic ID. Report rendering
rejects unknown/duplicate IDs, executable elements and external references before
publication, then embeds the SVG inline. No sibling file, server or network is needed.

Names, kinds, tags, neighborhoods and incident streams come from `ProcessGraphView`.
The derived `process_overlay` display payload contains those exact entity records,
binding-derived signal indexes and values copied from `TelemetryView.current`.
It is screened with the complete AGENT report. It is not a separate engineering
graph or manually maintained signal map. Bold labels indicate current telemetry
availability; purple highlights exact selected bindings; blue outlines selection.
No color asserts health, warning or fault state.

Click/focus equipment or a stream to inspect bindings. Only signals already present
in section C's artifact-backed series get `[plot]` buttons. These set the existing
selector and call the existing chart renderer. Current readouts are explicitly
reference-world values at the P0 projection timestamp, not counterfactual current
values. Units are binding metadata. Unobserved bindings say
`telemetry not present in this E0 projection`.

The current demo shows `XMEAS(9)` (node `reactor`) and `XMEAS(21)` (edge
`reactor_cooling_water_out`). Both are temperature measurements, so **no edge is
animated**. Direction arrows represent graph topology only. The optional dash rule
requires an edge binding with `XMEAS`, `MEASURES`, `quantity == flow`, and a positive
current value available in the P0 projection. An actuator position, temperature,
missing, zero or negative flow does not activate it. Dash speed is constant and does
not encode flow rate. `prefers-reduced-motion` disables motion while retaining the
static arrow and binding details. This PR does not add flow telemetry to the demo.

All A–G sections and the separate developer setup block remain. The FakeProvider
sequence, RunManager/runtime/gates/verification/ingestion path, production contracts
and dependency pins are unchanged. No direct simulator read was added for UI values.

Future replacement path (not implemented here):

```text
[current curated SVG geometry]
       | replace presentation asset
       v
[future DEXPI / authoritative P&ID geometry]
       | same canonical semantic ID binding concept
       v
[same P0 ProcessGraphView + TelemetryView]
```

No DEXPI parser, engineering geometry claim or production frontend is introduced.
The main limitation is schematic spacing and routing: geometry is curated for this
pinned graph, not scaled equipment, actual pipe routing or a live process monitor.
SVG identity validation, binding-change regression tests, current-projection tests,
animation eligibility, leakage, embedded output and inline Node syntax checks cover
the boundary. `SPEC_CONFLICT: none`.
