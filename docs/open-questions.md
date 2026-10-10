# Open Questions — `tep-agent-lab`

Only unresolved empirical/benchmark choices remain here. The runtime/lab v0 control-state boundary has passed Design Freeze.

## OQ-1 — First RCA scenario set

Use reactor/cooling-water family first, but exact candidate causes, magnitudes, timings, seeds, and operating variants must be selected after the identifiability/C0 pilot.

Avoid both trivial one-signal cases and practically indistinguishable cases unless intentionally labeled as difficulty/uncertainty tests.

## OQ-2 — Incident trigger source

First benchmark default: fixture-provided investigation start point plus compact abnormal-signal evidence, so detector quality does not confound investigation quality.

Later compare with deterministic detector-triggered starts.

## OQ-3 — Subagent trigger policy

Default: Main Agent chooses when to delegate within deterministic limits.

Compare with no subagents/fixed parallel workers/dynamic delegation only as an orchestration study.

## OQ-4 — Semantic stop thresholds

v0 mechanism is fixed: FinishProposal + deterministic structural readiness.

Open research concerns richer policies such as hypothesis-rank margin, unresolved critical-question count, and formal information-value stopping after an explicit belief model exists.

## OQ-5 — Rule validation/promotion thresholds

Canonical Rule schema is fixed as `origin × validation × authority`, but validation criteria for literature/experiment-derived relationships remain relationship-specific.

Need later pilot policies for scenario diversity, operating-envelope coverage, seed variation, tolerance/effect consistency, held-out validation, and contradictory evidence.

Promotion workflow implementation is deferred until a real knowledge study.

## OQ-6 — Initial Tool Bridge dependency set

First RCA bridge stays minimal:

1. response features / trajectory comparison;
2. cross-correlation / lag;
3. optional upstream TEP detector baseline.

Add PCA/PLS/sensitivity/optimization/statistical packages only for a concrete experiment need.

## OQ-7 — Simulation experiment granularity

Prefer semantic scenario/deviation contracts when deterministic mappings exist; allow bounded low-level XMV/IDV experiment controls through explicit typed tools where required.

Pilot experience will determine where semantic compilation is too restrictive.

## OQ-8 — Recovery authority

First recovery benchmark ranks forked strategies. Reference MUTATE remains disabled until gates/revision-bound validation are tested.

Open: which benchmark first enables one reference action and what authority-escalation policy applies.

## OQ-9 — AutoProcessResearch first campaign

Candidate: robust reactor cooling-water recovery strategy.

Still to freeze after recovery benchmark exists:

- exact mutable surface;
- objective/weights;
- hard constraints;
- research vs hidden-eval scenario split;
- optimizer method/trial budget;
- plateau/minimum-improvement thresholds.

## OQ-10 — HAZOP scope

Preserve node/parameter/guide-word/deviation/cause/consequence/safeguard semantics while clearly labeling results as simulation-backed research support, not formal HAZOP completion.

Open: useful worksheet/report depth versus research overhead.

## OQ-11 — External knowledge timing

Add `manufacturing-kg-agent` only after clean no-KG baselines.

Open: which task family benefits enough to justify retrieval and how retrieved evidence is scored.

## OQ-12 — Model/provider study design

Architecture remains provider-independent. First real model is frozen per benchmark run.

Open: whether model comparison becomes a primary study axis or only robustness validation after orchestration/tool architecture stabilizes.

## OQ-13 — Cross-incident organizational memory

No automatic cross-run retrieval initially.

Later compare:

- no history;
- structured Engineering Record retrieval;
- approved validated rule/runbook retrieval.

## OQ-14 — Visualization

2D topology + telemetry + investigation/work/branch timeline is sufficient for v0.

Only add 3D if a concrete spatial-reasoning question appears.

## OQ-15 — D0.2C pilot viability and identifiability

Owning spec: `docs/specs/benchmark-comparative-pilot-v0.md`.

Open empirical checks, answered only by D0.2C1–C3 evidence:

- whether `IDV(4)` produces supported, differing dynamics under the common 0.1 h + 0.5 h pilot window (checked so far only under 0.1 h + 0.2 h); a failure is `SPEC_CONFLICT` or an explicit timeline revision for all classes;
- which candidates are separable across seeds under the frozen C0 features, and which must be excluded or relabeled as not identifiable under this evidence window;
- the benchmark size needed for statistical inference, set from pilot within-class and between-class variation.
