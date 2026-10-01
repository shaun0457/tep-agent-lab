# C4 blind-RCA Tool Surface handoff

- Branch: `feat/tool-surface-v0`; base: lab `main` `ed70c01`.
- Owning spec: `docs/specs/tool-surface-v0.md` (accepted adjudications and
  implementation notes appended).
- Exact dependency attestation is owned by `dependency-pins.json`,
  `scripts/check.py`, and CI (`pyproject.toml` declares package-level dependencies
  only and does not encode Git revisions):
  - `tep-sim` = `ae1c14dc3d4848acc011b359e74da74b558fcc3a` (A1-A4);
  - `industrial-agent-runtime` = `2f243bd607ff94cc5b78a4d699362893fefdd1cd` (B1-B3);
  - numerical stack: `numpy` = `2.4.6` (attested against the test interpreter and
    recorded in every result's `provenance.world.numpy_version`).

## Implemented

- `tep_world.py`: harness-owned `ReferenceWorld` (sanitized history, salted
  revision, `designate_baseline` for trusted counterfactual origins),
  `SimulationSandbox` (opaque world-unique snapshot/branch handles over tep-sim
  snapshot/fork, with `baseline`/`reference` lineage), `ArtifactStore`
  (sanitized, checksummed, never-overwritten, exact-ref resolver),
  `leakage_findings`, `sanitize_observation`.
- `tool_surface.py`: `BlindRcaToolSurface` with 14 explicit `ToolSpec`s and the
  trusted runtime hooks `validate_request`, `execute`, `verify_result`,
  `reference_revision`, plus `gate_policy()` and `registered_tool_set_version`.
- Lab consumer validation delegates capability, control-mode, bounds, and scenario
  compilation to tep-sim; the lab adds only blind-RCA policy (allowlist, classes,
  visible variables, branch/snapshot handles, horizon/variable/preview bounds,
  baseline-lineage scenario rule).
- Lab result invariants (fail closed on any error): `SUCCESS` only, executor audit
  binding (result checksum, reference revision before/after), provenance/tool
  version, leakage screen, artifact existence/checksum, branch identity/parent,
  exact actual simulation draw, and ingestion limited to
  `REGISTER_OBSERVATION`/`REGISTER_ARTIFACT_REF`.
- Failed-branch retirement: once `run_rollout` has applied a scenario or started
  advancing a branch, any failure that prevents a `SUCCESS` result (simulation,
  telemetry read/sanitization, safety evaluation, artifact persistence,
  post-processing) retires the branch and its snapshots, while still reporting the
  actually simulated draw. tep-sim validation rejections before any commit, and
  deterministic pre-execution denials, leave the branch available.
- Results are ingested unchanged by the C1 `RcaResultIngestor`: observation, not
  evidence.

Not implemented (out of C4 scope): C5 Tool Bridge analysis including rollout and
trajectory comparison, B4 subagents, MUTATE/recovery tools, real model providers,
D0 benchmark fixtures, scenario enumeration or alias contracts, disturbance-relation
ablation tools, baseline-management Agent tools.

## Counterfactual origin (baseline) semantics

The trusted benchmark harness is authoritative for calling
`ReferenceWorld.designate_baseline()` before any hidden incident mutation. Neither
the Agent nor `active_disturbances` designates an origin. The method's
`active_disturbances` check is only an additional IDV-specific guard: it cannot
detect other hidden incident state such as XMV, constraint, or operating-condition
manipulation, and is not proof that a state is clean. `get_capability_summary`
lists baseline snapshot ids only, without timing; branch telemetry still carries
simulation time, so origin-timing visibility is a D0 policy decision.

## Verification

```powershell
py -3.13 scripts/check.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim
```

Attests both clean exact Git pins and the NumPy pin, then runs **92 tests** (26 C4
tests + 66 lab regressions) and `compileall`. An interpreter with another NumPy
fails attestation (`NumPy pin mismatch`). CI runs the same on Python 3.11 and 3.13
and installs NumPy from `dependency-pins.json`. tep-sim is private: CI checks it
out with the read-only deploy key secret `TEP_SIM_DEPLOY_KEY`.

Acceptance coverage (`tests/test_tool_surface.py`):

1. reactor topology/measurement/actuator queries, with an exhaustive leakage sweep;
2. no disturbance/fault-binding tool, MUTATE class, or evaluator import;
3. typed current observation and bounded, artifact-backed history;
4. and 5. snapshot/fork/rollout on isolated branches with reference revision,
   observation hash, history, and provenance file unchanged;
6. reference MUTATE unknown/ungranted/denied; SIMULATE on `reference` denied;
7. structured UNSUPPORTED/AMBIGUOUS/INVALID results and denials without simulation;
   scenarios on incident-lineage forks are denied by lineage (also on a healthy
   plant, so the rule itself reveals nothing);
8. dense rollout telemetry as sanitized artifact refs;
9. actual rollout/horizon draws reconcile against runtime reservations;
10. B3 `TOOL_VERSION_MISMATCH` on forged provenance; genuine results accepted;
11. tampered/fabricated/evidence-creating results rejected by lab invariants;
12. full runtime Coordinator run: 4 ObservationRecords, 0 evidence links,
    unsupported and reference-targeted SIMULATE requests denied.

Closure tests: forced artifact-persistence, telemetry-read, and safety-evaluation
failures after a rollout (with and without a scenario) retire the advanced branch,
report the real draw, and leave the reference and a sibling branch unchanged and
usable; pre-commit rejections do not retire; results record the attested NumPy.

## Accepted adjudications (C4 review closure)

- **SC-1 semantic scenario discovery:** no enumeration of the supported scenario
  catalog or evaluator candidate family; query by proposed scenario/mechanism;
  `AMBIGUOUS` returns no candidate mappings; enumeration only as a later explicit,
  recorded tool-policy/capability ablation. A versioned non-enumerative
  alias/normalization contract may be studied in D0, not added now.
- **SC-2 result status:** runtime canonical `SUCCESS`; the spec's `OK` replaced.
- **SC-3 rollout comparison:** removed from the C4 simulation surface; C5 Tool
  Bridge trajectory analysis owns it.
- **SC-4 counterfactual origin:** scenarios only on branches descending from a
  trusted harness-designated origin; incident/reference-lineage branches roll
  forward only; harness authoritative; `active_disturbances` is defense in depth,
  never proof of cleanliness; D0 defines origin-timing visibility.
- **SC-5 dynamic request reservation:** accepted runtime gap. Conservative maximum
  reservation stays; the lab does not parse or evaluate budget expressions. A
  runtime B2.1 request-bound reservation contract (preserving D-037, no expression
  DSL) is planned before D0.

No open `SPEC_CONFLICT` remains in C4.

## Notes for later batches

- tep-sim A1 `Observation.active_disturbances` and telemetry records carry hidden
  truth by design (world plane); every Agent-facing consumer must sanitize as C4 does.
- A3 ProcessGraph remains `PENDING_HUMAN_REVIEW`; topology tools report this in
  provenance (`process_graph_review_status`).
- D0: because forks clone the RNG, a baseline fork with the true cause applied at
  the injection instant reproduces the reference history exactly. Simulate-and-match
  is legitimate physics inference, but exact replay makes it trivial; D0 decides
  origin offset/timing visibility and seed policy (OQ-1).
- One scenario per rollout: tep-sim applies one scenario atomically; several would
  each be validated against a state the earlier ones change.
