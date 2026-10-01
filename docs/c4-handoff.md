# C4 blind-RCA Tool Surface handoff

- Branch: `feat/tool-surface-v0`; base: lab `main` `ed70c01`.
- Owning spec: `docs/specs/tool-surface-v0.md` (implementation notes appended).
- Dependency pins (`dependency-pins.json`, CI, `scripts/check.py`):
  - `tep-sim` = `ae1c14dc3d4848acc011b359e74da74b558fcc3a` (A1-A4);
  - `industrial-agent-runtime` = `2f243bd607ff94cc5b78a4d699362893fefdd1cd` (B1-B3).

## Implemented

- `tep_world.py`: harness-owned `ReferenceWorld` (sanitized history, salted
  revision), `SimulationSandbox` (opaque world-unique snapshot/branch handles over
  tep-sim snapshot/fork), `ArtifactStore` (sanitized, checksummed, exact-ref
  resolver), `leakage_findings`, `sanitize_observation`.
- `tool_surface.py`: `BlindRcaToolSurface` with 14 explicit `ToolSpec`s and the
  trusted runtime hooks `validate_request`, `execute`, `verify_result`,
  `reference_revision`, plus `gate_policy()` and `registered_tool_set_version`.
- Lab consumer validation delegates capability, control-mode, bounds, and scenario
  compilation to tep-sim; the lab adds only blind-RCA policy (allowlist, classes,
  visible variables, branch/snapshot handles, horizon/variable/preview bounds).
- Lab result invariants: executor audit binding (result checksum, reference
  revision before/after), provenance/tool version, leakage screen, artifact
  existence/checksum, branch identity/parent, exact actual simulation draw, and
  ingestion limited to `REGISTER_OBSERVATION`/`REGISTER_ARTIFACT_REF`.
- Results are ingested unchanged by the C1 `RcaResultIngestor`: observation, not
  evidence.

Not implemented (out of C4 scope): C5 Tool Bridge analysis including
`compare_rollouts`, B4 subagents, MUTATE/recovery tools, real model providers,
D0 benchmark fixtures, disturbance-relation ablation tools.

## Verification

```powershell
py -3.13 scripts/check.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim
```

Attests both clean exact pins, then runs **87 tests** (21 new C4 tests + 66 lab
regressions) and `compileall`. CI runs the same on Python 3.11 and 3.13.

Acceptance coverage (`tests/test_tool_surface.py`):

1. reactor topology/measurement/actuator queries, with an exhaustive leakage sweep;
2. no disturbance/fault-binding tool, MUTATE class, or evaluator import;
3. typed current observation and bounded, artifact-backed history;
4. and 5. snapshot/fork/rollout on isolated branches with reference revision,
   observation hash, history, and provenance file unchanged;
6. reference MUTATE unknown/ungranted/denied; SIMULATE on `reference` denied;
7. structured UNSUPPORTED/AMBIGUOUS/INVALID results and denials without simulation;
8. dense rollout telemetry as sanitized artifact refs;
9. actual rollout/horizon draws reconcile against runtime reservations;
10. B3 `TOOL_VERSION_MISMATCH` on forged provenance; genuine results accepted;
11. tampered/fabricated/evidence-creating results rejected by lab invariants;
12. full runtime Coordinator run: 4 ObservationRecords, 0 evidence links,
    unsupported and reference-targeted SIMULATE requests denied.

## SPEC_CONFLICT

**SC-1: semantic scenario discovery vs. candidate-cause isolation.**
Evidence: `tool-surface-v0.md` hides the "candidate-cause catalog" but lists
`check_scenario_capability(scenario)` without saying whether supported semantic
scenarios may be enumerated. The A4 `supported_semantic_scenarios` are exactly the
reactor/condenser cooling-water family chosen as the first RCA family (OQ-1), so
enumerating them would hand over the candidate list.
Implemented (conservative): no enumeration; query by id; `AMBIGUOUS` lists tested
candidates only for a semantic term the Agent itself named.
Smallest correction: add to the blind-RCA boundary section: "Supported semantic
scenario enumeration is not in the default blind-RCA allowlist; enabling it is a
recorded `tool_policy` ablation, like disturbance relation tools."

**SC-2 (minor): success status vocabulary.** The spec failure list says `OK`; the
runtime B3 contract ingests only `SUCCESS`. Implemented `SUCCESS`. Correction:
replace `OK` with `SUCCESS (runtime RESULT_SUCCESS)`.

**SC-3 (minor): `compare_rollouts` placement.** Listed under simulation tools, but
it is trajectory analysis, which belongs to the C5 Tool Bridge. Deferred.
Correction: move it to `tool-bridge-v0.md` alongside `compare_trajectories`.

## Notes for later batches

- tep-sim A1 `Observation.active_disturbances` and telemetry records carry hidden
  truth by design (world plane); every Agent-facing consumer must sanitize as C4 does.
- A3 ProcessGraph remains `PENDING_HUMAN_REVIEW`; topology tools report this in
  provenance (`process_graph_review_status`).
- `run_rollout` reserves the maximum horizon per call because runtime v0 does not
  evaluate draw expressions; quotas must be sized accordingly.
