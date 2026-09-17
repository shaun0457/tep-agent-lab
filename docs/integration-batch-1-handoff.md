# Lab integration batch 1 handoff

Branch: `integration/batch-1`

## Integrated exact inputs

- C1 independent storage/records: `e5dc49f`, `aeb83bb`, `2076e22`;
- C2 rule registry: `7455fe8cc5095925345528220b0c054eb2055e13`;
- C3 prediction/experiment identity: `e70440e`;
- runtime contract pin: `98413477f5294e60f67035de8d4342cd90e8fbd7`.

The modules have disjoint ownership: `persistence.py`/`records.py`, `rules.py`, and
`experiments.py`. Merge order follows dependencies, not worker completion time.

## Integrated verification

```powershell
C:\Users\chengting\AppData\Local\Programs\Python\Python313\python.exe `
  scripts/check.py --runtime ../industrial-agent-runtime
```

Result: runtime pin verified; 37 tests passed; `compileall` passed; `git diff
--check` passed. The suite contains 8 persistence, 6 records, 12 rules, and 11
prediction/identity tests.

Boundary audit: the lab imports generic refs only from the pinned runtime; it does
not copy runtime contracts or simulator physics. No evaluator refs enter records.
Records do not promote rules or alter future context. Predictions do not execute
experiments or infer telemetry features. Rule predicates are inert data and do not
become arbitrary execution.

## Remaining work

This integration branch is a reviewable partial batch, not completion of C1/C3.
Full `RcaState.apply_batch`, projection, deterministic result ingestion,
ObservationRecord lifecycle, state reconstruction/readiness, ModelTurn mapping,
ExperimentRunSpec execution, and interpretation mapping wait for the B1 public
contract conflict to be resolved. Isolated experiment execution also waits for A2.

No additional `SPEC_CONFLICT` was found during lab integration.
