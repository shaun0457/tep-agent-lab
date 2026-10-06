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
- **D ProcessGraph**: from `ProcessGraphView`; node kind/name/tag, bound measurements
  and actuators (node and incident streams), upstream/downstream from edges. Layered
  layout labelled *Schematic topology — not P&ID geometry*; no geometry or flow physics
  is inferred.
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
