# C3 resumed independent contracts

- Branch: `feat/hypothesis-experiment-v0-resume`.
- Base: lab integration `5aad2e2`.
- Owners: `hypothesis-experiment-v0.md`, `investigation-state-v0.md`.
- Files: `src/tep_agent_lab/experiments.py`, `tests/test_experiments.py`, this file.

## Implemented

Immutable Hypothesis, HypothesisEvidenceLink, ExperimentProposal, ExperimentRunSpec,
ExperimentResult, and ExperimentInterpretation records. Canonical enums are used
only where the owning spec lists their vocabulary; proposal/result status remains
nonempty metadata because the spec does not define its lifecycle vocabulary.

New refs use runtime InformationRef. Existing Prediction opaque IDs remain
compatible and require consumer resolution. Collections are detached/frozen;
refs, finite scalars, versions, numeric resource use, and nested evaluator refs
are validated. Frozen run specs verify their canonical key against their own
resolved content. Snapshot IDs cannot bypass content deduplication.

`interpretation_to_deltas` is a pure function. It emits, in order:

1. ADD_EVIDENCE_LINK, target hypothesis ref ID;
2. UPDATE_HYPOTHESIS, target hypothesis ID;
3. ADD_OPEN_QUESTION, target question ID;
4. UPDATE_WORKING_EXPLANATION, target `current_best_explanation`, with
   `conclusion_summary` and `residual_uncertainty`;
5. ADD_EXPERIMENT_INTERPRETATION, target interpretation ID.

All deltas use the exact supplied base revision and MODEL producer. Non-model
producer substitution is rejected. The caller wraps these deltas in one runtime
ModelStateUpdateProposal; this module neither persists nor rebases them.
OpenQuestion payloads follow the C1 schema without defining another C1 state class.

## Verification

```powershell
$env:PYTHONPATH='src;../../industrial-agent-runtime/src'
& 'C:/Users/chengting/AppData/Local/Programs/Python/Python313/python.exe' -B -m unittest discover -s tests -v
```

Result: **47 tests passed**, including 21 experiment tests (10 new contract tests),
plus persistence/record/rule regression. Failed experiment results are serialized,
saved, and reopened through the existing RunLog in an acceptance test.
`git diff --check` passes.

Runtime checkout used for verification: `83b8645d1e80fdfb426f137b39be02498ea8a4ad`.
Its contracts/serialization modules have no diff from the lab's existing
`9841347` pin; no dependency pin was changed. D-034 supplies the resumed runtime
action definitions, but these contracts need only existing StateDelta/ref types.

## Boundaries and deferred acceptance

This is the remaining dependency-independent C3 contract slice, not completed
end-to-end C3. No simulator/physics code, tool execution, hypothesis mutation,
budget authority, or runtime contract copies are introduced.

- Acceptance 1–2: typed competing hypotheses/predictions and PLAN_EXPERIMENT data
  verified; actual atomic C1 persistence remains integration work.
- Acceptance 5–6: deterministic predictions/results and interpretation mapping
  verified; automatic observation ingestion remains C1/runtime integration.
- Acceptance 7: original base revision is preserved; stale rejection belongs to
  the C1 TaskStateStore integration and is not simulated here.
- Acceptance 8: content dedup and changed-snapshot identity pass.
- Acceptance 3–4 and 9: capability resolution, actual isolated execution,
  unsupported-scenario rejection, and optimizer reservation depend on A2/B2/C4
  and remain deferred. Constructing a RunSpec is not execution authorization.
- Host adapters remain responsible for authentic provenance, reference
  existence/ownership, and resolved simulator semantics. Record constructors
  validate data shape, not scientific truth or artifact existence.

SPEC_CONFLICT: none. Commit SHA is delivered separately to avoid self-reference.
