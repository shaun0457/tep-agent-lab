# tep-sim 0.2.0 adoption handoff

- Branch: `chore/adopt-tep-sim-0.2.0`; base: lab `main` `d088782e046834917238e40005c332db0ededdd6`.
- Scope: dependency/provenance compatibility only. No Lab semantic, state, store, or tool
  change. P0, D0, B5, and F-11 are not started here.

## Dependency transition (current canonical)

| dependency | before (C5) | now |
| --- | --- | --- |
| `tep-sim` Git | `4261dc7ab4994348778133190964c6d59b17bb82` | `ea0b7304090017b14ac13665595b6f5e9d195250` (A3 HUMAN_VERIFIED ProcessGraph 0.2.0 merged) |
| `tep-sim` package | `0.1.0` | `0.2.0` |
| `industrial-agent-runtime` | `92651cf305ad735871f26006a790f561fff82654` | unchanged |
| `numpy` | `2.4.6` | unchanged |

Updated: `pyproject.toml`, `dependency-pins.json`, CI checkout ref. `scripts/check.py`
now also attests that each pinned checkout's package version equals the Lab's exact
`==` declaration. The C4/C5 handoffs and spec implementation notes keep the SHAs used at
their time as historical provenance (`docs/c4-handoff.md`'s "A3 ProcessGraph remains
`PENDING_HUMAN_REVIEW`" note is superseded by this record, not rewritten).

## ProcessGraph

Default `load_process_graph()` (and therefore `ReferenceWorld`):

- `tep-process-graph` `0.2.0`, pinned;
- `review_status` = `HUMAN_VERIFIED`, with a signed `review_record`;
- content sha256 = `cc8ccc81e9f421238863457438465877850b19d9760740279e54a52468fe9a87`.

C4 graph tools report this through `process_graph_review_status`, the `get_process_node`
`graph` block, and the world provenance `process_graph` entry. The registered tool-set
version is derived from ToolSpecs only and is unchanged.

Semantic equivalence is tested, not assumed: for every node, the C4-projectable view
(local projection minus graph provenance, unbounded upstream/downstream, measurements
and actuators including incident streams), every edge, and every binding `describe()`
are identical between packaged 0.1.0 and 0.2.0. Only provenance (version, hash,
review status/record, binding method) differs. No golden value derived from the graph
existed in the Lab, so none was updated.

## Evaluator hidden-status policy

tep-sim 0.2.0 packages evaluator fixture `tep-evaluator-disturbance-bindings` `0.2.0`:
`EVALUATOR_ONLY`, bound to graph 0.2.0 by hash, source status `PENDING_HUMAN_REVIEW`.
This is accepted. Lab policy does not require evaluator `HUMAN_VERIFIED`; evaluator
mappings are benchmark-owned hidden truth, not Agent-visible engineering semantics. The
final D0 benchmark policy is out of scope here.

## Tests affected

- `tests/test_tool_surface.py::ReadToolTests::test_reactor_topology_measurements_and_actuators`:
  expected `process_graph_review_status` changed `PENDING_HUMAN_REVIEW` -> `HUMAN_VERIFIED`
  (the only failure of the unchanged suite against the new pin).
- New `tests/test_dependency_adoption.py`: exact SHA/package pins (pins file, pyproject,
  CI); default graph 0.2.0 / HUMAN_VERIFIED / pinned / promoted hash; 0.1.0 vs 0.2.0
  Agent-visible semantic equality.
- New `PromotedGraphProvenanceTests` in `tests/test_tool_surface.py`: ReferenceWorld and
  world provenance use the promoted graph; for every node carrying evaluator truth, C4
  graph tools stay blind, report `HUMAN_VERIFIED`, and expose neither the evaluator's
  `PENDING_HUMAN_REVIEW` status nor any of its IDV ids.

## Verification

```powershell
py -3.13 scripts/check.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim
```

117 tests (112 existing C1-C5 regressions + 5 new) pass locally on Python 3.13 against the
exact pins; CI runs the same on Python 3.11 and 3.13. C1-C3 contracts and C5 numerical
behavior are unchanged (no source change; full regressions green). No Agent-visible
semantic behavior changed; no `SPEC_CONFLICT`.
