# Program Status

Living orientation file for the Industrial Context Platform / TEP Agent Lab program.
It records merged facts and the next gate. It defines no semantics; owning specs and ADRs
do. Detailed evidence: [program alignment review 2026-10-10](reviews/program-alignment-2026-10-10.md).

Facts here are true as of the commit that last changed this file
(`git log -1 --format=%H -- docs/program-status.md`). Anything merged after that commit
is newer than this file.

## Revisions

| Item | Value |
|---|---|
| Re-baseline reviewed main | `41cbae6dd2a6e2b70d29c4029289a968093c0058` (P1.1B, PR #23); this file and ADR-004 were added on top of it |
| Runtime pin | `shaun0457/industrial-agent-runtime@92651cf305ad735871f26006a790f561fff82654` (B2.1; equals runtime `main`) |
| TEP-sim pin | `shaun0457/tep-sim@ea0b7304090017b14ac13665595b6f5e9d195250` (A3 ProcessGraph 0.2.0; equals tep-sim `main`) |
| NumPy pin | `2.4.6` |

Pin locations. `dependency-pins.json` holds both SHAs and NumPy. `.github/workflows/ci.yml`
hard-codes both SHAs as checkout `ref:`s in each of its two jobs and reads NumPy from the
JSON. `tests/test_dependency_adoption.py` holds the two SHAs as constants. `pyproject.toml`
holds the package `==` versions. `scripts/check.py` holds no pins: it reads the JSON and
attests checkouts, package versions and NumPy. A SHA bump edits the JSON, the four CI refs
and the test constants. A NumPy bump edits only the JSON.

## Targets

| Question | Answer |
|---|---|
| Current product maturity | L0 Research Foundation, with L1 foundations in place (P1.0, P1.1A/B, E0.2 desktop shell). No L1 exit criterion is fully met. |
| Product maturity target | L1 Portfolio MVP ([product-maturity-and-mvp.md](product-maturity-and-mvp.md)) |
| Current product milestone | P1.2A Context Compiler contracts: not started, unblocked (spec accepted in PR #24) |
| Parallel product milestone | P1.1C canonical telemetry integration: not started, unblocked |
| Current research milestone | D0.2C1 comparable pilot fixtures, scorer-v1 and leakage policy v2: not started, unblocked (D0.2C0 design accepted in PR #20) |
| P1 sequencing authority | [ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md) |

## Tracks

Product track (P1 sequencing per ADR-004):

| Milestone | State |
|---|---|
| P0 Playground backend | merged (PR #6) |
| E0 / E0.1 static observatory | merged (PR #7, #8) |
| E0.2A–C application views, transport, desktop, packaged sidecar | merged (PR #10–#14) |
| P1.0 telemetry / context boundary | merged (PR #19) |
| P1.1A telemetry core | merged (PR #22) |
| P1.1B TEP simulation source | merged (PR #23) |
| ADR-004 P1 re-baseline | merged (adds this file) |
| P1.1C canonical telemetry integration | not started; write the implementation note first |
| P1.2 Context Compiler | spec accepted (PR #24); P1.2A not started |
| P1.3 Canonical Context composition | no owning spec yet |
| P1.4 context serving + Operational State | no owning spec yet |
| P1.5 ContextSnapshot + investigation integration | no owning spec yet |
| B5 first real provider (runtime repo) | not started; unblocked (D-049) |
| MVP integration (E1 substrate + context panels) | not started |
| P1.6 write-back, P1.7 second domain | post-L1 |

Research track:

| Milestone | State |
|---|---|
| D0.0 contract freeze, D0.1, D0.2A, D0.2B | merged (PR #15–#18) |
| D0.2C0 comparative pilot design | merged (PR #20) |
| D0.2C1 comparable pilot fixtures + scorer-v1 + leakage policy v2 | next; unblocked |
| D0.2C2 deterministic C0 | pending |
| D0.2C3 identifiability report | pending |
| D0 freeze | pending; needs C0, identifiability, data-informed difficulty, one non-local case |
| D1 capability ladder | blocked on D0 freeze and B5 |
| D2 orchestration ablation | blocked on D1 and B4 |

Cross-track rules (ADR-004 Decision 7): D0 does not block P1.1C–P1.5. Agent capability
claims need C0 and D1 evidence. B5 is shared by the MVP and D1.

## Open PRs at last update

None.

## Next implementation gates

- **P1.2A** can start now. It includes the compiler workspace and the rule-set source
  revision (context-compiler-v0 §Recommended implementation split;
  knowledge-rule-registry-v0 §Rule-set source revision).
- **P1.1C** can start now. The owning contract is frozen; write a short implementation note
  with acceptance tests first (ADR-004 Decision 6 lists the required live-path tests).
- **B5** can start now in `industrial-agent-runtime`.
- **D0.2C1** can start now. It adds leakage policy v2 before it freezes pilot fixtures
  (benchmark-comparative-pilot-v0 §Leakage).

## SPEC_CONFLICTs

| Conflict | State |
|---|---|
| P1 milestone numbering: ADR-003 / plant-telemetry vs North Star / maturity guardrail | resolved by ADR-004 |
| Leakage policy v0/v1 treats the truth mechanism as a hidden label vs scorer-v1's public mechanism vocabulary | scheduled: D0.2C1 adds leakage policy v2 before it freezes pilot fixtures (benchmark-comparative-pilot-v0 §Leakage; owner decision 2026-10-10) |

## Known drift (not SPEC_CONFLICT)

- The cross-repo Decision Register and implementation plan in `tep-sim:docs/ecosystem/` are
  stale after D-050 and reuse "P1" (Investigation UI) and "E1" (HAZOP). Follow-up belongs in tep-sim.
- Spec status vocabulary drift: implemented specs still say `proposal`; several use
  `FROZEN` / `DRAFT` wording outside the documentation standard.
- `RuleRegistry`, `records.py` (InvestigationReport, EngineeringArchive) and the P1.1
  telemetry modules are implemented but not used by any production path.

## Deferred work

- dirty-stream replay harness (old P1.5): before any non-simulated source;
- first external connector (old P1.6): Level 2;
- P1.6 write-back, P1.7 second domain;
- B4 subagents, D2 / O5;
- HAZOP, Recovery, AutoProcessResearch;
- durable telemetry, retention, cross-clock mapping, multi-source resolution, quarantine store;
- macOS/Linux desktop packaging;
- spec status normalization; a possible `CanonicalContextRegistry` rename (glossary only for now);
- Decision Register sync in tep-sim.

## Session protocol

1. Read `AGENTS.md` and this file.
2. `git fetch`. Compare `origin/main` with the commit that last changed this file, then
   read every merge after it. Run `gh pr list` and compare it with "Open PRs at last update".
3. Find the owning spec or ADR for the task (AGENTS.md list; ADR-004 Decision 1).
4. State the milestone and its acceptance criteria in the PR description before coding.
5. Work on a branch or worktree, never on `main`.
6. Self-review against the owning spec. Emit `SPEC_CONFLICT` instead of a local bypass.
7. Run `py -3.13 scripts/check.py --runtime <pinned runtime checkout> --tep-sim <pinned tep-sim checkout>`
   and `git diff --check`.
8. Open the PR. Do not merge without explicit approval.
9. Update this file in the PR that lands the change. Record only facts that will be true
   once that PR merges.
