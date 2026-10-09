# Program Alignment Review — 2026-10-10

Type: review record (evidence and disposition). It is not a spec. Normative outcomes live in
[ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md); living state lives in
[program-status.md](../program-status.md).

Verdict: **Program alignment: FIX REQUIRED.** The governance repair (ADR-004 and the doc
corrections in the same PR) resolves the P1 numbering conflict. PR #24 still needs fixes
before P1.2A can start, and the cross-repository Decision Register needs a follow-up in tep-sim.

## 1. Reviewed revisions

| Item | Value | Verified from |
|---|---|---|
| `tep-agent-lab` `origin/main` | `41cbae6dd2a6e2b70d29c4029289a968093c0058` (merge of PR #23, P1.1B) | `git fetch`; `gh pr list --state merged` |
| Runtime pin | `industrial-agent-runtime@92651cf305ad735871f26006a790f561fff82654` (B2.1 merge; equals runtime `origin/main`) | `dependency-pins.json`, both CI jobs, `tests/test_dependency_adoption.py`, `scripts/check.py` attestation |
| TEP-sim pin | `tep-sim@ea0b7304090017b14ac13665595b6f5e9d195250` (A3 ProcessGraph 0.2.0; equals tep-sim `origin/main`); submodule `vendor/tep-sim-upstream@9a6c8e5` | same |
| NumPy pin | `2.4.6` | same |
| Package pins | `industrial-agent-runtime==0.1.0`, `tep-sim==0.2.0` (`pyproject.toml`) | `scripts/check.py` |
| PR #20 | head `4c824ba5494b320eb201424fdca373b700a88bcc`, merge-base `243a5ba`, CI green (desktop, 3.11, 3.13) | `gh pr view 20` |
| PR #24 | head `bf14ec2d8110e75995b12da262f0b1dfc2d68903`, merge-base `dc033b9`, CI green | `gh pr view 24` |

Main history since the D0.2B merge `83a96c1`, first-parent: `e0f0225` (P1.0, PR #19),
`243a5ba` (North Star + maturity guardrail, PR #21), `dc033b9` (P1.1A, PR #22),
`41cbae6` (P1.1B, PR #23). CI on `main` passed on each of the last eight merges.

Method: the lab repository was read through owners and code paths, not by filename:
AGENTS, README, architecture, roadmap, open questions, North Star, maturity guardrail,
ADR-001..003, every spec, every handoff and implementation note, and all production
modules involved in the ownership map. The production import graph was traced. In the
pinned dependencies the review read the runtime provider surface and history, the tep-sim
ProcessGraph fixture and API, and `tep-sim/docs/ecosystem/` (Decision Register,
implementation plan, documentation standard). Both open PRs were read in full, including
review comments and commit history.

## 2. Architecture ownership map

Status legend: **INT** = implemented and used by a production path (P0 run, benchmark
harness, desktop/application); **LIB** = implemented and tested, not used by any production
path; **SPEC** = specified only; **DRAFT** = in an open PR; **NONE** = no owning contract.

| Concern | Canonical owner | Normative spec / ADR | Current implementation | Status | Tests | Open PR | Conflicts / drift |
|---|---|---|---|---|---|---|---|
| Process physics | tep-sim `TEPEnvironment` + vendored upstream `tep` | D-002, D-013; tep-sim A1/A4 | tep-sim | INT | tep-sim; lab tool-surface/playground | — | none |
| ProcessGraph / plant semantics | tep-sim ProcessGraph `tep-process-graph` 0.2.0, HUMAN_VERIFIED (17 nodes, 18 edges, 53 bindings; nodes own `name` and `tag`) | D-003; ADR-003; plant-telemetry §Canonical Plant Context | tep-sim `process.py`; P0 `PROCESS_GRAPH` source; `RunQueries.process_graph`; `tep_telemetry` binding table | INT | test_playground, test_e0_observatory, test_tep_telemetry_source, test_dependency_adoption | #24 grounds against it | generic PlantEntity/SignalDescriptor/Relationship projection absent (P1.3); PR #24 "selected plant-context revision" undefined (P24-5); approved-alias risk (P24-8) |
| Source inventory | P0 `CanonicalContextRegistry` / `ContextSourceRef` | playground-backend-v0 (status text "proposal"); D-050 | `canonical_context.py`, `playground.py._assemble` | INT | test_playground | #24 adds an `ENGINEERING_CONTEXT_REVISION` kind (feasible: kinds are open, the lab revision is attested) | "Canonical Context" naming collides with North Star usage (G7) |
| RuleRegistry | lab `rules.py` | knowledge-rule-registry-v0 (status text "proposal"); D-023 | immutable in-memory registry; no serialization, snapshot identity, loader or source kind | LIB | test_rules (12) | #24 depends on an "owning rule-configuration revision" | that revision does not exist (I3 / P24-2) |
| Telemetry source | `TEPSimulationSource` | plant-telemetry §Sources | `tep_telemetry.py` | LIB | test_tep_telemetry_source (53) | — | none |
| Telemetry ingestion | `TelemetryIngestor` | plant-telemetry §Identity, deduplication | `telemetry.py` | LIB | test_telemetry_core (39) | — | none |
| Time-series store | `InMemoryTimeSeriesStore` | plant-telemetry §Append store | `telemetry.py`; no index, no retention | LIB | test_telemetry_core | — | scan bound grows with run length (I2) |
| Reader / T-K snapshots | `TimeSeriesReader`, `TelemetryReadSnapshot` | plant-telemetry §Bounded reads, §Hard temporal leakage | `telemetry.py`; `visibility_policy_ref` pinned, not evaluated | LIB | test_telemetry_core | — | production telemetry still uses `ReferenceWorld.history` with no K (I1) |
| Production telemetry path (current) | `ReferenceWorld.history` -> `RunQueries.telemetry` (max 1000) -> `ApplicationViewService`; Agent `get_history` | playground-backend-v0; tool-surface-v0 | `tep_world.py`, `playground_views.py`, `tool_surface.py` | INT | test_playground, test_application_views, test_tool_surface | — | dual path with the canonical store until P1.1C/P1.5 |
| Engineering knowledge material | referenced source owners; P0 registry attests | ADR-003; plant-telemetry §Engineering Context | no DocumentRef/KnowledgeRef types | SPEC | — | #24 | — |
| Candidate Context | Context Compiler (P1.2) | context-compiler-v0 | — | DRAFT | — | #24 | candidate/review storage unspecified (P24-4) |
| Reviewed Engineering Context | `EngineeringContextRevision` (P1.2) | context-compiler-v0 | — | DRAFT | — | #24 | Rule-routed publication blocked by I3 |
| Plant context revision | `PlantContextRevision` (P1.3; = plant-telemetry "Canonical Plant Context") | plant-telemetry §Canonical Plant Context; ADR-004 Decision 4 | telemetry store `context_ref` is an opaque caller string | SPEC | — | — | I9 |
| Context Server | P1.4 | North Star (non-normative); plant-telemetry read semantics | — | NONE | — | — | needs an owning spec |
| Operational state interpretation | P1.4 State Interpreter | maturity guardrail (non-normative) | — | NONE | — | — | needs an owning spec |
| ContextSnapshot | derived reference assembly | plant-telemetry §ContextSnapshot (frozen) | — | SPEC | — | — | — |
| InvestigationContextView | derived read/projection | plant-telemetry §Investigation Context composition | — | SPEC | — | — | — |
| RcaState | `RcaStateStore` (runtime `TaskStateStore`) | investigation-state-v0 (accepted); D-032, D-035 | `investigation.py` | INT | test_investigation (16), test_playground | — | none |
| ObservationRecord | deterministic result ingestion | investigation-state-v0; D-022 | `RcaResultIngestor`; `REGISTER_OBSERVATION` is ingestion-only | INT | test_investigation | — | none |
| EvidenceLink | `HypothesisEvidenceLink` via model op `ADD_EVIDENCE_LINK` | hypothesis-experiment-v0 (accepted) | requires a registered observation and an active hypothesis | INT | test_investigation, test_experiments | — | none |
| Hypotheses | `Hypothesis` | hypothesis-experiment-v0 | `experiments.py` | INT | test_experiments (21) | — | none |
| Experiments | ExperimentProposal / RunSpec / Result / Interpretation; canonical key | hypothesis-experiment-v0; ADR-001 | `experiments.py`, `tool_bridge.py` | INT | test_experiments, test_tool_bridge | — | none |
| InvestigationReport | `records.py` / `EngineeringArchive` | engineering-records-v0 (status text "proposal") | not produced by any P0 run | LIB | test_records (9) | — | needed for MVP diagnosis and "why" (I4) |
| Agent/runtime authority | runtime Coordinator, gates, verifier, budgets | runtime specs; D-012, D-036, D-038, D-048 | B1–B3, B2.1 implemented; B4 and B5 not | INT | runtime tests | — | B5 is on the MVP path but absent from lab docs (I8) |
| Agent tool surface | `BlindRcaToolSurface` + `AnalysisToolBridge` | tool-surface-v0, tool-bridge-v0 (status text "proposal"); ADR-001 | `tool_surface.py`, `tool_bridge.py` | INT | test_tool_surface (28), test_tool_bridge (20) | — | none |
| Application/UI reads | `ApplicationViewService` (Run, Entity, Signal, SignalHistory), `ApplicationTransport`, desktop backend, Tauri shell, sidecar | ADR-002; E0.2 notes | AGENT scope enforced; no world access | INT | test_application_views (19), test_application_transport (18), test_desktop_backend (10), Rust/TS tests in CI | — | MVP panels (context, operational state, investigation trace, "why") not built |
| Benchmarks / evaluator truth | `BenchmarkHarness`, `LeakageAudit`, scorer-v0, fixtures `rca-dev-001..004`, P0 benchmark binding | benchmark-case-v0 (frozen), benchmark-design-v0, evaluation-v0 | `benchmark.py` | INT (harness) | test_benchmark (61), _family (25), _healthy (30) | #20 | mechanism-vocabulary vs leakage policy (OQ-15) |
| D0 C0 baseline | evaluator-only C0 | evaluation-v0 C0; benchmark-design-v0; #20 §Deterministic C0 | — | DRAFT | — | #20 | none |
| Model/provider boundary | runtime `ModelProvider` protocol | runtime-v0; D-049; OQ-12 | `FakeProvider` only; lab scripted demo provider | partial | runtime tests | #24 picks no provider | P1.2C model-call boundary unspecified (P24-13) |

Duplicate or competing ownership found:

- telemetry: two read paths (live `ReferenceWorld.history` and the canonical store) by
  design during P1.1; P1.1C and P1.5 retire the dual path for application and Agent reads;
- naming: ProcessGraph `name`/`tag` vs Context Compiler "reviewed aliases" (P24-8);
- scope: `Rule.scope` exact strings vs typed plant `scope_refs` (P24-3);
- governance: tep-sim's cross-repo Decision Register vs lab ADRs (G3);
- labels: "P1" and "E1" mean different things in tep-sim's implementation plan (G3);
- terminology: four "canonical context" concepts (G7, resolved by the ADR-004 glossary).

No duplicate ProcessGraph, RuleRegistry, RcaState store or runtime authority path exists
in code.

## 3. Normative-document classification

| Document | Class | Notes |
|---|---|---|
| `AGENTS.md` | NORMATIVE (agent summary) | was STALE: missing frozen specs and ADRs, research-only scope; updated here |
| `README.md` | NON-NORMATIVE overview | pointer added here |
| `docs/architecture.md` | NORMATIVE (boundaries) | was STALE: no P1 plane; pointer section added here |
| `docs/roadmap.md` | planning (points to owners) | was STALE: no product track, legacy Phase 0–12 labels; updated here |
| `docs/open-questions.md` | NORMATIVE for open questions | current; OQ-15 pending in #20 |
| `docs/program-status.md` | living status (new) | facts only |
| `docs/industrial-context-platform-north-star.md` | NON-NORMATIVE NORTH STAR | P1 list adopted and refined by ADR-004 |
| `docs/product-maturity-and-mvp.md` | NON-NORMATIVE product guardrail | L0–L4 and the scope rule stay the product reference |
| ADR-001 | NORMATIVE / ACCEPTED | — |
| ADR-002 | NORMATIVE / ACCEPTED | E0.2 complete |
| ADR-003 | NORMATIVE / ACCEPTED (semantics) | numbering SUPERSEDED and one scope statement amended by ADR-004 |
| ADR-004 | NORMATIVE / ACCEPTED (this PR) | replaces the PR #24 draft |
| `specs/plant-telemetry-contract-v0.md` | FROZEN | sequence table SUPERSEDED by ADR-004; P1.1A/B implement the telemetry subset |
| `specs/benchmark-case-v0.md` | FROZEN | implemented by D0.1–D0.2B |
| `specs/benchmark-design-v0.md`, `specs/evaluation-v0.md` | NORMATIVE / ACCEPTED | evaluation-v0 is the sole comparison matrix |
| `specs/investigation-state-v0.md`, `specs/hypothesis-experiment-v0.md` | NORMATIVE / ACCEPTED | implemented |
| `specs/playground-backend-v0.md` | NORMATIVE, status text STALE | says `proposal` and "documentation-only until Re-baseline v1"; implemented in PR #6 |
| `specs/knowledge-rule-registry-v0.md`, `specs/tool-surface-v0.md`, `specs/tool-bridge-v0.md`, `specs/engineering-records-v0.md`, `specs/rca-v0.md` | NORMATIVE (implemented subset), status text STALE | say `proposal` |
| `specs/hazop-v0.md`, `specs/recovery-v0.md`, `specs/autoresearch-v0.md` | RESEARCH PLAN | not scheduled |
| `specs/context-compiler-v0.md` (PR #24) | DRAFT | FIX REQUIRED (§5) |
| `specs/benchmark-comparative-pilot-v0.md` (PR #20) | DRAFT (frozen on merge) | §6 |
| c1–c5, c1/c3 resume, integration-batch-1, p0, tep-sim adoption, e0-*, d0-* notes | IMPLEMENTATION NOTE | historical |
| `docs/p1-1a-telemetry-core.md`, `docs/p1-1b-tep-simulation-source.md` | IMPLEMENTATION NOTE | next-milestone pointers were stale; corrected here |
| `tep-sim:docs/ecosystem/decision-register.md` | NORMATIVE (D-001..D-050) | STALE after D-050 (G3) |
| `tep-sim:docs/ecosystem/implementation-plan.md` | planning | STALE / CONFLICTING labels (G3) |

The documentation standard (`tep-sim:docs/ecosystem/documentation-standard.md`) allows spec
statuses `proposal | accepted | deprecated` only. The lab also uses `FROZEN ...`,
`DRAFT FOR ...` and `accepted design guidance`. Mapping used in this review:
FROZEN = accepted with a semantic freeze; implemented-but-`proposal` = accepted in practice,
status text stale. Normalizing the files is deferred (G6) because a status change is a
decision, not a typo fix.

Normative dependency graph:

```text
tep-sim docs/ecosystem/decision-register.md   (D-001..D-050; stale after D-050)
  |
  +--> runtime specs (runtime-v0, deterministic-gates-v0, ...)   [runtime repo]
  +--> tep-sim world contracts (ProcessGraph 0.2.0, capability)  [tep-sim repo]
  +--> tep-agent-lab
        +-- ADR-001 ------------------> tool-surface-v0, recovery-v0
        +-- investigation-state-v0, hypothesis-experiment-v0,
        |   knowledge-rule-registry-v0, engineering-records-v0, rca-v0
        |       -> investigation.py, experiments.py, rules.py, records.py
        +-- playground-backend-v0 (D-044..D-050)
        |       -> playground.py, canonical_context.py, playground_views.py
        +-- ADR-002 ------------------> application_*.py, desktop/
        +-- benchmark-design-v0, evaluation-v0
        |       -> benchmark-case-v0 (frozen) -> benchmark.py, fixtures
        |       -> [PR #20] benchmark-comparative-pilot-v0
        +-- North Star + maturity guardrail (non-normative)
        |       | adopted and refined by
        |       v
        +-- ADR-003 + plant-telemetry-contract-v0 (frozen semantics)
        |       | numbering and P1.2 scope superseded by
        |       v
        |   ADR-004 (P1 sequencing, semantic-ownership rule, P1.2/P1.3 boundary)
        |       -> telemetry.py, tep_telemetry.py (P1.1A/B)
        |       -> [PR #24] context-compiler-v0 (P1.2)
        |       -> future P1.1C note, P1.3 / P1.4 / P1.5 specs
        +-- roadmap.md, program-status.md, AGENTS.md (point to owners; define nothing)
```

Precedence rule: for a semantic question, the owning spec/ADR section wins over roadmap,
status, handoff and North Star text. Accepted or frozen beats proposal or draft. A later
accepted ADR overrides earlier text only where it names that text. Non-normative product
documents never override a contract. Implementation notes describe only the implemented subset.

Milestone numbers embedded in semantic text (all mapped by ADR-004 Decision 3):
ADR-003 §Implementation boundary (P1.2–P1.6, including the P0-bypass constraint inside the
P1.4 bullet); plant-telemetry §Existing architecture consistency ("P1.4 must evolve the
owning public P0/application query boundary"); plant-telemetry §Frozen implementation
sequence (table and "later engineering fixture/reference work is planned here");
`docs/p1-1a-telemetry-core.md` "(P1.4)"; `docs/p1-1b-tep-simulation-source.md` §Next milestone.
P1.0/P1.1 references elsewhere are unaffected.

## 4. Findings

Severity: BLOCKER / HIGH / MEDIUM / LOW / NOTE. `SPEC_CONFLICT` is used only for a
contradiction with accepted or frozen semantics.

### Governance

- **G1 BLOCKER — resolved by ADR-004.** Incompatible P1 sequences: ADR-003 and plant-telemetry
  (frozen) vs North Star and the maturity guardrail (non-normative), plus a third meaning in
  tep-sim's implementation plan. This was a real `SPEC_CONFLICT` for any P1.2+ work.
- **G2 HIGH — resolved by ADR-004.** The PR #24 draft ADR-004 claimed to be sequencing-only
  while it widened ADR-003's extraction scope; dropped the old P1.5 and P1.6 rows without a
  disposition; left live ingestion and the P0/application migration unowned; missed numbers
  embedded in semantic text; did not separate L1 from post-L1 work; and was bundled into a
  draft spec PR.
- **G3 HIGH — open, cross-repo follow-up.** The program Decision Register,
  documentation standard and implementation plan live in `tep-sim/docs/ecosystem/`. The
  register stops at D-050 (P0). ADR-002..004, the North Star adoption and D0.x decisions are
  unregistered, although the register requires that "an accepted decision must not change
  only inside code/chat". The implementation plan still lists C5 and B2.1 as next, and uses
  P1 = Investigation UI and E1 = HAZOP, both of which collide with lab labels. Dependency
  repositories are not modified in this audit. `docs/program-status.md` is the lab-side
  current state until a tep-sim PR registers or delegates.
- **G4 MEDIUM — resolved here.** `AGENTS.md` omitted `benchmark-case-v0.md` (frozen),
  `plant-telemetry-contract-v0.md` (frozen), ADR-002 and ADR-003. Its product boundary and
  "implementation order" covered research work only. `docs/specs/README.md` did not index
  the plant-telemetry contract.
- **G5 MEDIUM — resolved here.** `roadmap.md` had no product track, kept legacy "Phase 0–12"
  labels next to the D-series, and opened with a stale intro. `architecture.md` did not
  mention the P1 telemetry/context plane. `README.md` had no pointer to current state.
- **G6 LOW — open.** Spec status drift (§3). Recommend one normalization PR.
- **G7 MEDIUM — resolved by the ADR-004 glossary.** "Canonical context" names four concepts:
  the P0 registry, the North Star product phrase, the frozen "Canonical Plant Context" and
  P1.3 composition. New types must not be named `CanonicalContext*`. A code rename of
  `CanonicalContextRegistry` is deferred.

### Implementation versus contract

- **I1 HIGH — owned by P1.1C.** No milestone owned live ingestion or the public P0/application
  snapshot-bound reads. Production telemetry is still `ReferenceWorld.history()` with no K
  and no immutable snapshot. This is a documented migration obligation, not a hidden bypass.
- **I2 MEDIUM — P1.1C.** The reader's `max_scan_records` counts a binding's whole K-committed
  prefix, so a long or live run eventually fails every read, however narrow the interval.
  Documented in P1.1B; it must be fixed before P1.4 reads live operational state.
- **I3 HIGH — P1.2A.** `RuleRegistry` has no serialized source, snapshot identity, loader or
  ContextSourceRef kind, and no production path uses it. PR #24's Rule-routed publication
  and the frozen ContextSnapshot `rule_refs[] -> registry/source provenance` both presuppose
  one. The extension belongs to the RuleRegistry owner (`knowledge-rule-registry-v0`,
  `rules.py`), not to the compiler.
- **I4 MEDIUM — P1.5 / MVP.** `InvestigationReport` and `EngineeringArchive` are tested but
  no P0 run produces a report. The MVP's structured diagnosis and "why did the Agent know
  this?" depend on that integration.
- **I5 NOTE — aligned.** `Observation != Evidence` holds in code: `REGISTER_OBSERVATION` is
  ingestion-only; `ADD_EVIDENCE_LINK` is a model operation that requires a registered
  observation and an active hypothesis.
- **I6 NOTE — aligned.** `ApplicationViewService` accepts AGENT-scope queries only and never
  touches the world or session. Application API != Agent Tool Surface holds.
- **I7 NOTE — aligned.** `telemetry.py` imports the stdlib only; XMEAS/XMV live in
  `TEPSignalBinding`; canonical `signal_id` is the ProcessGraph `semantic_entity_id`;
  DISTURBS bindings are rejected. T and K apply before ordering and reduction. Sequence is
  independent of event time; same-tick observations are kept; each reset needs a new
  incarnation. All tested.
- **I8 MEDIUM — tracked in program-status.** The runtime pin has `FakeProvider` only. B5 is
  required for the MVP's real-model investigation and for D1 (D-049), but no lab document
  listed it on the MVP path.
- **I9 LOW — P1.1C, then P1.3.** The telemetry store `context_ref` is an opaque caller
  string, not a plant-context revision (documented P1.1A limitation).
- **I10 NOTE — aligned.** `CanonicalContextRegistry` matches playground-backend-v0: it
  attests on register and on resolve, freezes at READY, makes hidden sources
  indistinguishable from unknown ones, and P0 rejects AGENT aliases of hidden content.
  PR #24's prepare-time registration of an `ENGINEERING_CONTEXT_REVISION` source needs no
  P0 change: kinds are open and the lab revision is already an attested repository.

## 5. PR #24 re-review (head `bf14ec2`)

Two reviews were posted on 2026-10-09: one at `b0be274` judged `SPEC_CONFLICT`
(B1, H1–H5, M1–M5, L1–L3); a second judged FIX (three HIGH, two MEDIUM). The author pushed
fixes and requested a re-review. No re-review had been posted before this audit.

Disposition of the earlier findings:

| Finding | State at `bf14ec2` |
|---|---|
| B1 numbering vs ADR-003 / plant-telemetry | Addressed by the draft ADR-004, which is incomplete (G2). **Resolved by this PR's ADR-004**; the draft must be dropped |
| H1 review principal / v0 auto-accept | Resolved (human or `policy_id@version` only; model/Agent forbidden; every published item needs an effective review). Surface gap remains: P24-6 |
| H2 Rule axes / installation path | Axes resolved (fidelity APPROVE keeps LITERATURE/NONE/REFERENCE). Installation path **open**: P24-2 |
| H3 mixed visibility / indirect leakage | Resolved (taint from every influencing input; no downgrade; filtered counts) |
| H4 KnowledgeRef content | Resolved (content is the exact fragment; normalized claim is a labeled annotation). Semantics gap: P24-7 |
| H5 revision storage / run entry | Resolved for revisions (repository-backed artifact, prepare-time registration, no latest). Candidates and reviews: P24-4 |
| M1 effective review / stale reviews | Stale reviews resolved (compatibility tuple). Effective-review completeness **open**: P24-1 |
| M2 REMAP landing / content corrections | Resolved |
| M3 partial failure / complete set / concurrency | Resolved (candidate-local exclusion, revision-level abort, complete set, compare-and-set) |
| M4 candidate identity | Resolved (content key vs candidate version) |
| M5 field duplication / routing | Resolved (deterministic kind routing; Rule items use `ExtractionMetadata`) |
| L1 target kinds / DocumentRef | Resolved |
| L2 closed enum | Resolved |
| L3 model-generated explanation labeling | Resolved |
| Second review HIGH: effective review among multiple reviews | Partially resolved; see P24-1 |
| Second review HIGH: REMAP revalidation | Resolved |
| Second review HIGH: visibility over all inputs | Resolved |
| Second review MEDIUM: review/publication compatibility | Resolved |
| Second review MEDIUM: atomic publication / dependency closure | Resolved |

New and remaining findings:

- **P24-1 HIGH.** The effective decision is computed over "the publication's declared review
  set", so a publisher can leave a later REJECT out of the declared set and publish the
  earlier APPROVE. Fix: compute the effective decision over the complete append-only
  ReviewRecord log for that candidate version, up to a log position pinned in the revision.
- **P24-2 HIGH (= I3).** "An immutable owning rule-configuration/registry source revision"
  does not exist, and the spec does not say who publishes a new one. Fix: P1.2A includes a
  RuleRegistry source extension owned by `knowledge-rule-registry-v0` (a serialized rule set
  as a ContextSourceRef kind such as `ENGINEERING_RULE`, with a content-checksum snapshot
  identity and a loader). The compiler contributes inert candidate Rules to a new rule-set
  revision under the same review gate. `Rule.scope` matching stays unchanged.
- **P24-3 MEDIUM.** How a Rule-routed item's approved plant scope is published is undefined:
  `Rule.scope` is an exact string, while plant scope refs are typed. plant-telemetry already
  says assembly maps plant scope to rule scopes through versioned binding provenance. Fix:
  record `(RuleRef, scope_refs, effective review)` bindings in the EngineeringContextRevision.
- **P24-4 MEDIUM.** Storage of candidates and ReviewRecords, and the lineage head used for
  compare-and-set, are unspecified. Fix: a non-canonical, append-only compiler workspace that
  consumers cannot resolve. Each compile session builds and freezes its own
  `CanonicalContextRegistry` instance over exact attested revisions. Publication commits the
  revision artifact into this repository.
- **P24-5 MEDIUM.** "Selected plant-context revision" is undefined before P1.3. Fix: adopt
  ADR-004 Decision 4 (the exact `PROCESS_GRAPH` ContextSourceRef; ProcessGraph ids as targets;
  P1.3 preserves identities).
- **P24-6 MEDIUM.** The spec does not say review and publication are application-only
  operations. If either were registered as an Agent tool, the model could act as reviewer in
  practice. "Authenticated" is undefined for a local, single-user v0. Fix: review and publish
  are Application API operations, never Agent tools; v0 records a trusted application-session
  principal; Agent runs cannot invoke them.
- **P24-7 MEDIUM.** The same APPROVE yields `validation_status=VERIFIED` for a KnowledgeRef
  but `validation=NONE` for a Rule. Fix: define KnowledgeRef VERIFIED as "scope binding and
  extraction fidelity verified against the cited fragment", not engineering truth or
  validation maturity.
- **P24-8 MEDIUM.** Approved bindings and "reviewed aliases" can grow into a global alias
  registry parallel to ProcessGraph `name`/`tag`. Fix: approved bindings resolve mentions
  inside a fragment only. A reusable alias dictionary is its own versioned, reviewed source
  and never edits ProcessGraph naming.
- **P24-9 MEDIUM.** A troubleshooting SOP for the reactor-cooling subsystem can encode
  symptom-to-cause answers for the D0 candidate family (IDV(4), IDV(11), IDV(14)). Visibility
  taint covers evaluator-only inputs, not answer-bearing Agent-visible content. Fix:
  compiled knowledge enters a blind benchmark run only as a C6 condition under a versioned
  leakage audit; the P1.2 fixture corpus records authoring provenance independent of
  evaluator truth.
- **P24-10 LOW.** "Unique terminal" and "incompatible" are undefined: two compatible terminal
  APPROVEs are not unique. `supersedes_review_ref` should be limited to the same candidate
  version and must not form cycles.
- **P24-11 LOW.** `DRAFT FOR P1.2 DESIGN REVIEW` is outside the status vocabulary, and the PR
  lists a draft spec among canonical specs in `AGENTS.md`.
- **P24-12 LOW.** The base `dc033b9` is behind main. Rebase, and drop `d3b09cf` (draft
  ADR-004), `463011d` (ADR-003 note) and `91f958c` (plant-telemetry note). The new
  `docs/specs/README.md` section heading will conflict trivially with this PR's heading.
- **P24-13 NOTE.** The P1.2C model-call boundary (provider interface; recording exact model
  inputs for taint and provenance) is unspecified. A compiler run is not an Agent run and
  does not go through the Coordinator.

Confirmed sound at `bf14ec2`: confidence semantics; candidate immutability; no second
ProcessGraph, RuleRegistry or RcaState store; Rule reuse through `literature_candidate`;
fragment-based KnowledgeRef content; visibility taint; REMAP revalidation; dependency-closure
publication; prepare-time registration with no latest resolution; non-goals (ContextSnapshot,
State Interpreter, connectors, write-back).

**PR #24 verdict: FIX REQUIRED** (2 HIGH, 7 MEDIUM, 3 LOW, 1 NOTE). No remaining
`SPEC_CONFLICT` once it rebases onto ADR-004.

## 6. PR #20 re-review (head `4c824ba`)

- Round-1 BLOCKER (the cross-case projection invariant contradicted the Agent-visible seed
  design): **resolved**. Invariants A (within seed), B (across seeds without `world.seed`),
  C (per-class seed multiset `{21,22,23}`) and a projection-only check are defined over an
  explicit `AgentVisibleCaseView`.
- Target-space reporting rule: **resolved** (4-class EVALUATOR space vs 3-class primary
  equivalence; deterministic collapse; numeric comparison only on the same space).
- **P20-1 LOW.** The base `243a5ba` is behind main by P1.1A/B; there is no file overlap.
  Rebase and rerun CI. Its `roadmap.md`, `AGENTS.md` and `docs/specs/README.md` hunks do not
  overlap this PR's edits.
- **P20-2 NOTE.** The known leakage-vocabulary conflict is tracked as OQ-15, which PR #20
  proposes; it is not on main yet. It must be resolved before any milestone exposes the
  mechanism vocabulary to a model.
- **P20-3 NOTE.** The pilot tool policy exposes no engineering knowledge, so there is no
  interaction with P1.2.

**PR #20 verdict: PASS, pending owner re-review and rebase.** This audit checked the BLOCKER
fix and consistency with owning contracts. It did not re-derive the statistical design.

## 7. P1.2 / P1.3 boundary analysis

The intended split (P1.2 compiles and reviews engineering knowledge; P1.3 composes it with
plant semantics) is sound if P1.3 stays a projection plus validation plus a derived view.
The specific risks examined:

| Risk | Finding |
|---|---|
| Second ProcessGraph | Avoided only if P1.3 projects ProcessGraph deterministically and preserves identities; ADR-004 requires it |
| Second RuleRegistry | Real risk in PR #24 (I3 / P24-2): the rule-set revision must come from the RuleRegistry owner |
| Mutable alternative to CanonicalContextRegistry | Avoided: revisions enter runs only through prepare-time registration; no "current context" resolver (ADR-004 Decision 4) |
| Duplicate canonical truth database | Avoided if the composed view is derived and rebuildable and bodies are never copied |
| Premature universal ontology | Avoided: namespaced, extensible kinds; minimal extraction vocabulary |
| ContextSnapshot owner inside P1.2 | None; P1.2 lists ContextSnapshot as a non-goal |
| Agent-generated truth promotion | None; P1.6 is post-L1 and candidate-only |

`CanonicalContextRegistry` vs `EngineeringContextRevision`: the ownership is correct. The
registry is the run-scoped attested inventory, and the revision is an immutable published
artifact that a later run registers. `EngineeringContextRevision` is a good, distinct name.
The confusion sits on the other side: the North Star's "Canonical Context Revision" and the
P1.3 phrase "Canonical Context composition" reuse the registry's word. ADR-004 fixes the
vocabulary and names the P1.3 output `PlantContextRevision`, which is the frozen contract's
own "Canonical Plant Context".

One ordering issue remains in the new sequence: P1.4 serving precedes the P1.5
ContextSnapshot, while the North Star says the Context Server produces ContextSnapshots.
ADR-004 Decisions 1 and 6 resolve this without renumbering: P1.4 pins exact inputs, and any
persisted assembly record is the ContextSnapshot as specified.

## 8. Tests and CI

- `py -3.13 scripts/check.py --runtime <92651cf checkout> --tep-sim <ea0b730 checkout>`:
  all pins and package versions attested; **436 tests, 0 failures, 0 skipped**; compileall
  clean. Per module: benchmark 61, tep_telemetry_source 53, playground 43, telemetry_core 39,
  benchmark_healthy 30, tool_surface 28, benchmark_family 25, experiments 21, tool_bridge 20,
  application_views 19, e0_observatory 18, application_transport 18, investigation 16,
  rules 12, desktop_backend 10, records 9, persistence 8, sidecar_resources 3,
  dependency_adoption 3.
- Conditional skip: `tests/test_e0_observatory.py` skips its inline-script syntax check when
  `node` is missing; it ran locally.
- CI on `main`: eight consecutive green merges, including the Windows desktop job (Rust
  bridge, Tauri IPC, frozen sidecar smoke, NSIS bundle). The desktop job was not rerun locally.
- Flaky tests: none observed (one local run plus CI history). Not proven absent.
- Uncovered critical paths: RuleRegistry, records and canonical telemetry have unit tests
  only, because no production path uses them. No end-to-end test runs a P0 run through the
  canonical telemetry store, because that wiring does not exist yet (P1.1C).
- Time-bound test: `test_existing_production_path_does_not_use_adapter` encodes the P1.1B
  parallel-path invariant. P1.1C must replace it with a positive integration test.
- Duplicated helpers (LOW): `make_store`/`ingest_at` (telemetry_core, tep_telemetry_source);
  `harness`/`fixture_bytes`/`case_record`/`truth_record`/`forbidden` (benchmark tests);
  `make_world` (benchmark_healthy, tool_surface). Consolidate when next touched.
- Tests encoding obsolete semantics: none found. scorer-v0 tests deliberately pin historical
  behavior.
- CI green is not architecture alignment: CI passed while G1, G3 and I1 existed.

## 9. Research track

| Milestone | State |
|---|---|
| D0.0 contract freeze | merged (PR #15) |
| D0.1 contract implementation | merged (PR #16) |
| D0.2A incident family | merged (PR #17) |
| D0.2B healthy negative | merged (PR #18) |
| D0.2C0 comparative pilot design | PR #20, PASS pending re-review and rebase |
| D0.2C1 comparable fixtures + scorer-v1 | next |
| D0.2C2 deterministic C0 | pending (no C0 code exists) |
| D0.2C3 identifiability report | pending |
| D0 freeze | pending |
| D1 capability ladder (C1–C5; C6 knowledge later) | blocked on D0 freeze and B5 |
| D2 orchestration ablation | blocked on D1 and B4 |

## 10. Product track

| Milestone | State |
|---|---|
| P0, E0/E0.1, E0.2A–C | merged |
| P1.0 | merged (docs) |
| P1.1A / P1.1B | merged (library; not yet integrated) |
| P1.1C | not started; unblocked by ADR-004 |
| P1.2 | spec in PR #24, FIX REQUIRED |
| P1.3, P1.4, P1.5 | no owning spec |
| B5 (runtime) | not started |
| MVP integration | not started |

Level-1 exit criteria (maturity guardrail): none fully met. Partially met: #4 (canonical
SignalSample/store/reader contracts exist but are not live) and #10 (TEP stays below the
generic telemetry boundary).

Estimate: about 6 spec PRs (P1.2 fix, P1.1C note, P1.3, P1.4, P1.5, MVP UI) and 15–17
implementation PRs (P1.1C, P1.2A–D, P1.3, P1.4, P1.5, B5, MVP UI, end-to-end demo) remain.
The 2026-10-06..09 cadence was 3–5 merges per day, but those milestones were smaller and
spec reviews took several rounds. The floor is 2–3 weeks; a realistic window with review
loops is 4–6 weeks (mid-to-late November 2026). The main risks are P1.2 review iterations,
real-provider integration and MVP UI scope.

## 11. Recommended critical path

```text
ADR-004 (this PR)
  -> PR #24 fixed, rebased, accepted -> P1.2A (incl. RuleRegistry source extension)
       -> P1.2B -> P1.2C -> P1.2D -> P1.3 -> P1.4 -> P1.5 -> real-model run -> MVP integration
  || P1.1C (parallel; must finish before P1.4)
  || B5 in industrial-agent-runtime (parallel; must finish before the real-model run)
  || research: PR #20 -> D0.2C1 -> D0.2C2 -> D0.2C3 -> D0 freeze -> D1 (needs B5)
```

The knowledge lane (P1.2 -> P1.3) is the longest chain. P1.1C and B5 should start now so
they do not serialize at the end.

Next three engineering steps:

1. Merge this re-baseline; fix PR #24 (P24-1, P24-2, then the MEDIUM items), drop its draft
   ADR-004, rebase, re-review and merge; then implement P1.2A, including the RuleRegistry
   source/snapshot extension.
2. In parallel, P1.1C: event-time-bounded scans, run-scoped ingestion wiring in P0, and
   snapshot-bound public P0/application reads. Replace the parallel-path test with a
   positive integration test.
3. In parallel, B5 first real provider in `industrial-agent-runtime` (D-049).

## 12. Deferred work

Dirty-stream replay (old P1.5); first external connector (old P1.6); P1.6 write-back;
P1.7 second domain; B4 / D2 / O5; HAZOP, Recovery, AutoProcessResearch; durable telemetry
and retention; cross-clock mapping; multi-source resolution; quarantine store; macOS/Linux
packaging; spec status normalization; a `CanonicalContextRegistry` rename; the tep-sim
Decision Register sync; test-helper consolidation.

## 13. Implicit information made repo-resident

| Previously implicit (chat or handoff) | Now |
|---|---|
| Current program state, open PR states, next gate | `docs/program-status.md` |
| Authoritative P1 numbering and old-row dispositions | ADR-004 Decisions 2–3 |
| Owner of live ingestion and application migration | ADR-004 (P1.1C) |
| P1.2/P1.3 boundary and naming | ADR-004 Decision 4 |
| Research/product dependency rules | ADR-004 Decision 7; program-status |
| B5 on the MVP path | ADR-004 Decision 2; program-status |
| PR #24 / #20 review state | §5, §6 |
| Location and staleness of the cross-repo Decision Register | G3; program-status |
| Pin-update locations | program-status |
| Session protocol for future planning sessions | program-status; AGENTS.md |

## 14. Files changed by the re-baseline PR

- new: `docs/decisions/ADR-004-p1-milestone-rebaseline.md`, `docs/program-status.md`,
  this review;
- additive supersession, scope-amendment and status notes; no existing semantic text
  edited: `docs/decisions/ADR-003-industrial-environment-boundary.md`,
  `docs/specs/plant-telemetry-contract-v0.md`. The one scope change is ADR-004 Decision 5;
  the notes point to it;
- pointer notes: `docs/industrial-context-platform-north-star.md`,
  `docs/product-maturity-and-mvp.md`;
- orientation and stale-pointer fixes: `AGENTS.md`, `README.md`, `docs/roadmap.md`,
  `docs/architecture.md`, `docs/specs/README.md`, `docs/p1-1a-telemetry-core.md`,
  `docs/p1-1b-tep-simulation-source.md`.

No production code, tests, fixtures, CI, pins or dependency repositories changed.
