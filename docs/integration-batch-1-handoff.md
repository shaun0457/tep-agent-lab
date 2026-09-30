# Lab integration batch 1 handoff

Branch: `integration/batch-1`

## Integrated inputs

- C1 RCA store/result ingestion: `4e546f3` plus handoff/style commits through
  `59f82f8`, with approved contract closure integrated as `2c2d99c`;
- C1 Engineering Records safety fixes: `ddf553c`;
- C2 rule registry: `7455fe8cc5095925345528220b0c054eb2055e13`;
- C3 immutable hypothesis/experiment contracts and mapping:
  `17790ab5c4f35ffcb7a012985fb9da268735d2b9`, integrated as `eea9565`;
- exact runtime pin: `6c8a8d222a7dbf2bd6ed4b9162e3c6b7424d8ec7`.

## Delivered behavior

- durable atomic `RcaStateStore`, accepted/rejected state events,
  reconstruction, bounded exact projection, and readiness checks;
- automatic one-observation registration for every successful visible tool
  result while keeping evidence links explicit;
- deterministic current-revision ingestion for TOOL WorkBatch waves;
- planned/completed experiment state, compact SubtaskResult metadata, and
  canonical Engineering Records;
- rule metadata over `origin x validation x authority`;
- immutable Hypothesis, Prediction, ExperimentProposal/RunSpec/Result/
  Interpretation objects, deterministic prediction evaluation, canonical
  experiment identity, and pure interpretation delta construction;
- runtime-owned terminal lifecycle persistence and shared revision-free
  `WorkingExplanationUpdate`, with the store assigning resulting revision.

## Verification

```powershell
C:\Users\chengting\AppData\Local\Programs\Python\Python313\python.exe `
  scripts/check.py --runtime ../industrial-agent-runtime
```

Result: runtime pin verified; **66 tests passed**; `compileall` passed. The suite
also proves stale same-turn action suppression, atomic rollback, blind-state
visibility, exact-one observation ingestion, experiment completion, compact
subtask retention, stable WorkBatch ingestion revisions, reconstruction, and
missing-ref readiness rejection.

Boundary audit: lab imports generic contracts from the exact runtime pin and
does not copy simulator physics or runtime scheduling. It adds no provider SDK,
LangGraph, MCP, arbitrary Python/shell tool, learned memory, or knowledge
promotion path.

## Resolved contract conflicts

Decision D-035 closes the two implementation seams recorded by the prior
handoff:

1. `TaskStateStore.transition_status` now persists Coordinator-owned terminal
   lifecycle changes without a lab operation name.
2. C3 and C1 share `WorkingExplanationUpdate`; only C1 materializes
   `last_updated_revision` from the accepted resulting revision.

The cross-repo test proves two lexical WorkBatch ingestions apply at revisions
`0->1` and `1->2`, then verified Finish persists `DONE` at `2->3`. No C1/C3
`SPEC_CONFLICT` remains.

## Deferred by dependency/order

- B2/B3 full gates, resource reconciliation, and verifier policies;
- B4 subtask execution;
- C4/A2 scenario compilation and isolated ExperimentRunSpec execution;
- semantic redundancy detection, optimization bridge, learned memory, and
  knowledge promotion.
