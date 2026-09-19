# C1 resume handoff

Branch: `feat/investigation-state-v0-resume`
Implementation base commit: `4e546f3`
Contract-closure commit: recorded in the coordinator handoff after commit

## Delivered

- `RcaState`, `OpenQuestion`, shared revision-free
  `WorkingExplanationUpdate`, materialized `WorkingExplanation`, and immutable
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
  current-revision ingestion for one WorkBatch wave;
- runtime-owned terminal lifecycle persistence through
  `TaskStateStore.transition_status`, including exact-revision checks,
  same-terminal idempotence, terminal overwrite denial, audit events, and
  reconstruction from the accepted terminal snapshot;
- C3 interpretation mapping from `conclusion_summary` and
  `residual_uncertainty` into the shared typed update, with C1 assigning the
  resulting revision.

Changed files:

- `src/tep_agent_lab/investigation.py`
- `src/tep_agent_lab/__init__.py`
- `src/tep_agent_lab/experiments.py`
- `tests/test_investigation.py`
- `tests/test_experiments.py`
- `docs/specs/investigation-state-v0.md`
- `docs/specs/hypothesis-experiment-v0.md`

## Verification

Using runtime commit `6c8a8d2` on `PYTHONPATH`:

```text
python -m unittest discover -s tests -v  # tep-agent-lab
Ran 66 tests in 1.067s
OK

python -m unittest discover -s tests -v  # industrial-agent-runtime
Ran 62 tests in 1.902s
OK
```

The lab run includes 16 C1 store/coordinator tests. The cross-repo closure test
proves two lexical WorkBatch ingestions apply at revisions `0->1` and `1->2`,
then a verified finish persists `DONE` at `2->3`. `py_compile` and
`git diff --check` passed. The implementation
imports only generic runtime contracts and does not import simulator physics,
provider SDKs, LangGraph, or MCP.

## Resolved contract conflicts

The approved minimum corrections are implemented in both owning specs and code:

1. terminal status changes use the generic runtime protocol and cannot be
   expressed as a model/result delta;
2. C3 and C1 share one `WorkingExplanationUpdate` shape, while only the store
   writes `last_updated_revision`.

No C1 `SPEC_CONFLICT` remains.

## Deferred by owning phase

- full B2 gates/resource reservation and B3 verifier policies;
- B4 subtask execution (C1 only maps a returned `SubtaskResult`);
- C4/A2 experiment compilation and isolated simulator execution;
- semantic experiment redundancy, learned memory, and knowledge promotion.
