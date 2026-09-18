# C1 resume handoff

Branch: `feat/investigation-state-v0-resume`
Implementation commit: `4e546f3`

## Delivered

- `RcaState`, `OpenQuestion`, `WorkingExplanation`, and immutable
  `ObservationRecord` contracts;
- `RcaStateStore` implementing the runtime `TaskStateStore` protocol with
  optimistic revision checks, producer/operation allowlists, atomic batches,
  append-only accepted/rejected events, and deterministic reconstruction;
- bounded `project_rca_state` projection with exact refs, checksum, revision,
  visibility checks, and store-backed ref resolution;
- deterministic `RcaResultIngestor` mappings for successful `ToolResult`,
  `ExperimentResult`, and `SubtaskResult` values;
- exact one-observation registration for each successful visible tool result,
  with evidence creation kept as a later explicit model delta;
- planned-to-completed experiment transition, compact child-result metadata,
  readiness deficiencies, and Engineering Record coverage from the prior C1
  tranche;
- B1 Coordinator integration proving stale same-turn suppression and lexical
  current-revision ingestion for one WorkBatch wave.

Changed files:

- `src/tep_agent_lab/investigation.py`
- `src/tep_agent_lab/__init__.py`
- `tests/test_investigation.py`

## Verification

Using runtime commit `dc1845fa930683364abd04a8d0d4b910168eb3d8` on `PYTHONPATH`:

```text
python -m unittest discover -s tests -v
Ran 63 tests in 0.601s
OK
```

The run includes the full lab regression suite and 13 C1 store/coordinator
tests. `py_compile` and `git diff --cached --check` passed. The implementation
imports only generic runtime contracts and does not import simulator physics,
provider SDKs, LangGraph, or MCP.

## SPEC_CONFLICT

Two dependent paths remain stopped; the independent C1 implementation does not
invent either missing contract.

1. `investigation-state-v0.md` says Finish/hard-stop lifecycle changes update
   `RcaState.generic_status`, but runtime `TaskStateStore` has no lifecycle
   transition method and the domain-independent Coordinator cannot name the
   lab-owned `SET_GENERIC_STATUS` operation. C1 validates a trusted `RUNTIME`
   delta and keeps readiness pure, but B1 does not invoke it. Smallest proposed
   change: add a runtime-owned typed lifecycle transition method/delta and call
   it on terminal Coordinator paths.
2. `hypothesis-experiment-v0.md` maps interpretation conclusion/uncertainty into
   `UPDATE_WORKING_EXPLANATION`, while `investigation-state-v0.md` requires the
   different canonical `WorkingExplanation` fields. C1 accepts the canonical
   object and deliberately rejects the incompatible C3 patch. Smallest proposed
   change: define one typed update payload and its deterministic materialization
   in the owning specs, then share it between C3 mapping and C1 validation.

## Deferred by owning phase

- full B2 gates/resource reservation and B3 verifier policies;
- B4 subtask execution (C1 only maps a returned `SubtaskResult`);
- C4/A2 experiment compilation and isolated simulator execution;
- semantic experiment redundancy, learned memory, and knowledge promotion.
