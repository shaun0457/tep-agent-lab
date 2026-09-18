# Lab integration batch 1 handoff

Branch: `integration/batch-1`

## Integrated inputs

- C1 RCA store/result ingestion: `4e546f3` plus handoff/style commits through
  `59f82f8`;
- C1 Engineering Records safety fixes: `ddf553c`;
- C2 rule registry: `7455fe8cc5095925345528220b0c054eb2055e13`;
- C3 immutable hypothesis/experiment contracts and mapping:
  `17790ab5c4f35ffcb7a012985fb9da268735d2b9`, integrated as `eea9565`;
- exact runtime pin: `dc1845fa930683364abd04a8d0d4b910168eb3d8`.

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
  experiment identity, and pure interpretation delta construction.

## Verification

```powershell
C:\Users\chengting\AppData\Local\Programs\Python\Python313\python.exe `
  scripts/check.py --runtime ../industrial-agent-runtime
```

Result: runtime pin verified; **63 tests passed**; `compileall` passed. The suite
also proves stale same-turn action suppression, atomic rollback, blind-state
visibility, exact-one observation ingestion, experiment completion, compact
subtask retention, stable WorkBatch ingestion revisions, reconstruction, and
missing-ref readiness rejection.

Boundary audit: lab imports generic contracts from the exact runtime pin and
does not copy simulator physics or runtime scheduling. It adds no provider SDK,
LangGraph, MCP, arbitrary Python/shell tool, learned memory, or knowledge
promotion path.

## SPEC_CONFLICT

Two narrow dependent paths remain stopped:

1. The lab spec requires runtime-controlled `generic_status` transitions, but
   `TaskStateStore` has no lifecycle transition method and the generic
   Coordinator cannot name lab-owned `SET_GENERIC_STATUS`.
2. C3 interpretation mapping emits conclusion/uncertainty fields for
   `UPDATE_WORKING_EXPLANATION`, while C1 specifies a different canonical
   `WorkingExplanation` shape and no deterministic conversion.

The exact evidence and smallest proposed contract changes are recorded in
`docs/c1-resume-handoff.md`. C1 implements the independent operations and fails
closed on the undefined integrations.

## Deferred by dependency/order

- B2/B3 full gates, resource reconciliation, and verifier policies;
- B4 subtask execution;
- C4/A2 scenario compilation and isolated ExperimentRunSpec execution;
- semantic redundancy detection, optimization bridge, learned memory, and
  knowledge promotion.
