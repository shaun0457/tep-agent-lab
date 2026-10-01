# C5 minimal Tool Bridge handoff

- Branch: `feat/tool-bridge-v0`; base: lab `main` `57bc526c95c9e97aa06ea68c89faae2375fad2bc`.
- Owning spec: `docs/specs/tool-bridge-v0.md`, section "C5 implementation contract
  (v0)" (numerical semantics frozen before code).
- Exact dependency attestation (`dependency-pins.json`, `scripts/check.py`, CI):
  - `tep-sim` = `4261dc7ab4994348778133190964c6d59b17bb82` (Program Re-baseline; moved
    from the pre-rebaseline `ae1c14d`, docs-only upstream delta);
  - `industrial-agent-runtime` = `2f243bd607ff94cc5b78a4d699362893fefdd1cd`
    (unchanged; B2.1 has not merged);
  - `numpy` = `2.4.6` (unchanged). No SciPy or other new dependency.

## Implemented

`src/tep_agent_lab/tool_bridge.py`:

- `AnalysisToolBridge`: three Agent-visible `COMPUTE` ToolSpecs
  (`compute_response_features`, `analyze_cross_correlation`, `compare_trajectories`)
  with the lab hooks `validate_request`, `execute`, `verify_result`/`check_result`.
- `BridgedToolSurface`: composes the unchanged C4 `BlindRcaToolSurface` with the
  bridge for one runtime registry; routes hooks by tool name; grants
  `READ + COMPUTE + SIMULATE` and the analysis tag `tep.agent_visible_analysis`.

Inputs are only exact Agent-visible `HistoryWindowArtifact`/`RolloutTelemetryArtifact`
refs issued by the lab `ArtifactStore`, re-resolved and checksum-verified at
validation and again at dispatch. No resampling/interpolation exists; incompatible
sampling is rejected. Dense per-lag correlation curves are
`CrossCorrelationCurveArtifact`s. Results are ObservationRecords through C1, never
EvidenceLinks.

Not implemented (out of C5 scope): detector baseline (no D0 requirement yet),
SALib/Optuna/PCA/PLS/Granger, graph toolbox, MCP/remote providers, simulator-running
analysis, `SETTLING_TIME` (no public settling contract; structured
`UNSUPPORTED_CAPABILITY`), P0, B5, UI. C4 is unchanged; `compare_rollouts` was not
re-added to C4.

## Verification

```powershell
py -3.13 scripts/check.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim
```

Runs **111 tests** (19 C5 + 92 existing C1-C4 regressions) after attesting both exact
Git pins and NumPy. CI runs the same on Python 3.11 and 3.13.

C5 acceptance coverage (`tests/test_tool_bridge.py`): known feature values and their
edge cases; unsupported/underspecified features denied without defaults; Prediction
compatibility; synthetic lag recovery with sign convention; deterministic ties;
constant/degenerate signals, including non-dyadic constants and held actuators; invalid lag ranges; known trajectory metrics; sampling
incompatibility and no resampling; evaluator-only/unissued/tampered refs; no path or
code input; COMPUTE-only specs with no simulator entry point touched; B3 tool-version
match and forged-version rejection; library-version provenance; tampering/evidence/
stale-reference rejection; full Coordinator run (C4 history + rollout, then the three
bridge tools) yielding 6 ObservationRecords, 0 EvidenceLinks, an unchanged reference
world, and one structured `UNSUPPORTED_CAPABILITY` denial.

## Notes for later batches

- P0 can consume the bridge result/provenance contract as-is; C5 adds no run/context
  registry.
- `SETTLING_TIME` needs an owning-spec decision (target definition and in-window
  confirmation) before it becomes computable.
- Benchmark scoring configuration remains separate from the Agent-visible
  `tep-agent-lab.trajectory-metrics/v0` contract.
- Review follow-ups accepted as later work (bounded today by C4 artifact sizes and the
  runtime `max_tool_calls` budget): enforce the record cap before parsing an artifact
  (needs an `ArtifactStore` size/line-count read, a C4-owned change); cache verified
  series per `(ref_id, checksum)` instead of re-reading at validation, dispatch, and
  verification; prune the executor audit map after verification.
