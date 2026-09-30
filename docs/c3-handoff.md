# C3 independent implementation handoff

Task: C3 hypothesis/experiment independent slice  
Branch: `feat/hypothesis-experiment-v0`

## Implemented

- immutable typed `Prediction` and v0 feature vocabulary;
- deterministic comparison of already-extracted numeric/categorical features;
- explicit `INCONCLUSIVE` and `UNSCORED` outcomes instead of fabricated values;
- content-based `canonical_experiment_key` independent of snapshot identity;
- exact duplicate rejection;
- regression fixes from independent review for decimal tolerance boundaries and
  mutable/invalid condition/reference fields.

## Acceptance coverage

`py -3.13 -m unittest discover -s tests -v` with `PYTHONPATH=src`:

- 19 tests passed (11 C3 plus 8 inherited persistence regression tests).
- mapping order and equivalent JSON numeric forms normalize deterministically;
- each semantic identity input changes the key;
- a new snapshot ID over identical parent content cannot evade duplicate blocking.

## Deferred dependencies

- Hypothesis/ExperimentProposal/RunSpec/Result full state objects and
  Interpretation-to-StateDelta mapping depend on C1 `RcaState.apply_batch` and the
  frozen B1 runtime types.
- scenario compilation and isolated execution depend on A2 snapshot/fork plus
  later C4 tool adapters and B2/B3 gates/verification.
- feature extraction from telemetry belongs to C5; this slice compares a supplied
  deterministic feature and does not infer one.

No simulator physics, runtime contract, execution bridge, optimizer, state update,
or full C3 completion is claimed. No `SPEC_CONFLICT` was found in this slice.
