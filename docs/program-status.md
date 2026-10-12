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
| Current product maturity | L0 Research Foundation, with L1 foundations in place (P1.0, P1.1A/B/C, P1.2A compiler contracts, P1.2B deterministic corpus-to-staged-revision path, E0.2 desktop shell on application transport v1). No L1 exit criterion is fully met. |
| Product maturity target | L1 Portfolio MVP ([product-maturity-and-mvp.md](product-maturity-and-mvp.md)) |
| Current product milestone | P1.2C model-assisted extraction / semantic grounding: next, unblocked (P1.2B S1–S5 merged) |
| Parallel product milestone | P1.1C canonical telemetry integration: merged; P1.4 can read live telemetry through snapshot-bound P0/application reads once its owning spec exists |
| Current research milestone | D0.2C and D1 paused (owner decision 2026-10-11); D0.2C1 not started (D0.2C0 design accepted in PR #20) |
| P1 sequencing authority | [ADR-004](decisions/ADR-004-p1-milestone-rebaseline.md) |
| Product positioning | the domain-neutral industrial context layer is the product; domain semantics live in domain packs; the Agent is its first consumer; L1 stays on the process pack through TEP; second domain not chosen ([ADR-005](decisions/ADR-005-context-layer-product-positioning.md)) |

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
| ADR-005 context-layer product positioning | merged (accepted) |
| P1.1C canonical telemetry integration | merged: event-time-bounded scans, run-scoped P0 ingestion, snapshot-bound P0 views, Application API v1 / transport v1 (desktop, Rust bridge, sidecar); note `docs/p1-1c-canonical-telemetry-integration.md`, spec `docs/specs/application-api-v1.md` |
| P1.2 Context Compiler | spec accepted (PR #24); P1.2A contracts, compiler workspace, rule-set revision and staged (non-canonical) publication merged ([note](p1-2a-compiler-contracts.md)); P1.2B merged (S1–S5, PRs #30–#33 plus the S5 completion PR): deterministic corpus, fragments, extraction, recorded human demo review and verified staged revision ([note](p1-2b-engineering-corpus.md)); P1.2C next; P1.2D adds the real review workflow and repository transaction (one git commit, head CAS) |
| P1.3 Canonical Context composition | no owning spec yet |
| P1.4A serving + Operational State | owning spec [live-operational-state-v0.md](specs/live-operational-state-v0.md) accepted with owner decisions of 2026-10-12; implementation not started; first slice LOS-A-S1; P1.4 split by owner decision 2026-10-11 |
| P1.4B live plant mode | MVP-required; TEP streams continuously through TEPSimulationSource into the canonical telemetry store; owning spec [live-operational-state-v0.md](specs/live-operational-state-v0.md) accepted with owner decisions of 2026-10-12 and the [Playground live-mode amendment](specs/playground-backend-v0.md#live-plant-session-mode-amendment-2026-10-12), subject to ADR-005 constraints; LOS-B-S1 delivered (issue #36): clean continuously stepped simulated stream, atomic session commits and snapshot-bound reads ([note](p1-4b-live-plant-mode.md)); LOS-B-S2 (#37) is next; LOS-B-S4 is scheduled after LOS-B-S3 and does not gate the P1.4B exit |
| P1.5 ContextSnapshot + investigation integration | no owning spec yet |
| B5 first real provider for the investigation Agent (runtime repo) | deferred until the context layer is in place (owner decision 2026-10-11); still required for the MVP exit criterion's real-model investigation (D-049) |
| MVP integration (E1 substrate + context panels) | not started |
| P1.6 write-back, P1.7 second domain | post-L1 |

Research track — paused (owner decision 2026-10-11) to focus on the context layer:
"what the plant is" (P1.2 Context Compiler, P1.3 canonical plant context) and "what is
happening in the plant" (P1.1C canonical telemetry, P1.4 Operational State and live plant mode).

| Milestone | State |
|---|---|
| D0.0 contract freeze, D0.1, D0.2A, D0.2B | merged (PR #15–#18) |
| D0.2C0 comparative pilot design | merged (PR #20) |
| D0.2C1 comparable pilot fixtures + scorer-v1 + leakage policy v2 | paused; not started |
| D0.2C2 deterministic C0 | paused; pending |
| D0.2C3 identifiability report | paused; pending |
| D0 freeze | deferred with the track; criteria unchanged: C0, identifiability, data-informed difficulty, one non-local case |
| D1 context-value study (redirected by [ADR-005 Decision 3](decisions/ADR-005-context-layer-product-positioning.md#decision-3--research-track-redirect)) | paused; on resumption, blocked on D0 freeze and B5; the C6 arm also needs P1.2, P1.3 and P1.5; OQ-16 remains open, deferred with the track |
| D2 orchestration ablation | parked (ADR-005) |

Cross-track rules (ADR-004 Decision 7): D0 does not block P1.1C–P1.5. While D2 is parked,
no Agent capability claim is made; D1 produces context-value evidence only, which is not
capability evidence ([ADR-005 Decision 3](decisions/ADR-005-context-layer-product-positioning.md#decision-3--research-track-redirect)).
The pause changes scheduling only: Decision 3, the D0 freeze criteria, C0 as the mandatory
baseline and ADR-004 Decision 7 are unchanged. On resumption, D0 still blocks D1 runs
and any capability claim; no capability claim is made meanwhile. B5 is shared by the MVP and D1.

## Open PRs at last update

None.

## Next implementation gates

- **P1.2B** is merged (S1–S5, PRs #30–#33 plus the S5 completion PR): exact corpus
  registration, deterministic fragments/extraction and recorded human demo review
  through verified non-canonical staging. **P1.2C** is next: model-assisted
  extraction and grounding behind the same contracts. P1.2D follows with the real
  review workflow and repository publication transaction that commits a P1.2A
  `StagedPublication` with head CAS (context-compiler-v0 §Recommended implementation
  split, §Publication; [P1.2B completion note](p1-2b-engineering-corpus.md)).
- Write the **P1.3 owning spec** early, alongside P1.2B. The **P1.4 owning spec**
  [live-operational-state-v0.md](specs/live-operational-state-v0.md) is accepted,
  covering **P1.4A** (serving + Operational State) and **P1.4B** (live plant mode).
  Owner decisions of 2026-10-12 and the [Playground live-mode amendment](specs/playground-backend-v0.md#live-plant-session-mode-amendment-2026-10-12)
  resolve SC-1/SC-2.
  First P1.4A slice: LOS-A-S1. P1.4B LOS-B-S1 is delivered (issue #36);
  LOS-B-S2 (#37), pacing and pause/resume, is next. LOS-B-S4 follows LOS-B-S3 as scheduled work,
  without gating the P1.4B exit. Existing ADR-005 live plant constraints still bind.
- **B5** is deferred until the context layer is in place; it remains required for the
  MVP exit criterion's real-model investigation (owner decision 2026-10-11).
- **D0.2C and D1** are paused (owner decision 2026-10-11). On resumption, D0.2C1 adds
  leakage policy v2 before freezing pilot fixtures (benchmark-comparative-pilot-v0
  §Leakage); D0 still blocks D1 runs and any capability claim.

## SPEC_CONFLICTs

| Conflict | State |
|---|---|
| P1 milestone numbering: ADR-003 / plant-telemetry vs North Star / maturity guardrail | resolved by ADR-004 |
| Leakage policy v0/v1 treats the truth mechanism as a hidden label vs scorer-v1's public mechanism vocabulary | deferred with the paused research track: D0.2C1 adds leakage policy v2 before it freezes pilot fixtures (benchmark-comparative-pilot-v0 §Leakage; owner decision 2026-10-10) |

## Known drift (not SPEC_CONFLICT)

- The cross-repo Decision Register and implementation plan in `tep-sim:docs/ecosystem/` are
  stale after D-050 and reuse "P1" (Investigation UI) and "E1" (HAZOP). Follow-up belongs in tep-sim.
- Spec status vocabulary drift: implemented specs still say `proposal`; several use
  `FROZEN` / `DRAFT` wording outside the documentation standard.
- `RuleRegistry`, `records.py` (InvestigationReport, EngineeringArchive) and the P1.2A
  `context_compiler` / `rule_sets` modules are implemented but not used by any production
  path. The P1.1 telemetry modules serve P0 application reads since P1.1C; Agent tools
  still read `ReferenceWorld.history()` until P1.5.

## Deferred work

- dirty-stream replay harness (old P1.5): before any non-simulated source;
- first external connector (old P1.6): Level 2;
- P1.6 write-back; P1.7 second domain (domain not chosen; owner question in ADR-005);
- B4 subagents; D2 / O5 (parked by ADR-005);
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
