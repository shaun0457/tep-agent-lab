# C1 independent implementation handoff

Task: C1 investigation state independent slice  
Branch: `feat/investigation-state-v0`

## Implemented

- single-run append-only JSONL event history with sequence/checksum chaining;
- immutable content-addressed JSON artifacts with tamper detection;
- restart/readback validation and explicit refusal of partial/corrupt history;
- typed archival `InvestigationReport`, `DecisionRecord`, and `ExperimentRecord`;
- authoritative runtime `InformationRef` use at exact pinned B1 commit;
- record ref/version/visibility/run validation, versioned supersession, and
  preservation of failed experiment records without copying numeric result truth;
- offline verification script that checks the exact runtime Git pin before tests.

## Acceptance coverage

`python scripts/check.py --runtime ../industrial-agent-runtime`:

- verifies runtime commit `98413477f5294e60f67035de8d4342cd90e8fbd7` and clean checkout;
- runs the lab test suite and byte-compiles source/tests;
- this branch has 14 passing tests (8 persistence and 6 record tests).

Covered independent C1/engineering-record cases include durable restart,
failed/rejected-history retention, caller immutability, tamper/path traversal
rejection, exact state revision and refs, nonexistent/hidden/spoofed ref rejection,
corrected report versioning, and no automatic Rule/context side effect.

## Deferred dependencies

The full C1 task is deliberately not claimed complete. `RcaState`,
`TaskStateStore.apply_batch`, deterministic projection, ObservationRecord ingestion,
parallel result ordering, readiness, and complete state reconstruction require the B1
runtime loop contracts. The loop is currently stopped by the documented B1 public
contract `SPEC_CONFLICT` for ToolCallRequest, FinishProposal, and completion policy.

No multiple databases, cross-run memory/retrieval, rule promotion, simulator physics,
or copied runtime contract was introduced. This independent slice found no additional
`SPEC_CONFLICT`.
