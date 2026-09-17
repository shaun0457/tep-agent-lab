# Benchmark Design and Identifiability v0

Status: proposal  
Owner repo: `tep-agent-lab`

## Goal

Create benchmark cases that test investigation capability rather than memorization, trivial candidate enumeration, topology leakage, or impossible discrimination.

## Core principle

A case is useful only if its task is:

- reproducible;
- supported by TEP physics;
- hidden truth is not directly leaked to the Agent;
- meaningfully distinguishable from plausible alternatives under the configured evidence/tools;
- evaluated against a strong deterministic baseline;
- not labeled difficult merely because a particular LLM struggles.

## Partitions

```text
DEVELOPMENT
  visible fixtures for engineering/debugging

RESEARCH
  visible-feedback fixtures for architecture/tool/policy studies

HIDDEN_EVAL
  evaluator-only variants for final generalization checks
```

Hidden case configurations/cause labels may not enter Agent/AutoResearch context or engineering-record retrieval during evaluation.

## Scenario family

```text
ScenarioFamily
  family_id
  subsystem
  evaluator_candidate_causes[]
  observable_variables[]
  controllable_variables[]
  supported_interventions[]
  nuisance_variation
  seed_space
  timing_range
  magnitude_range
  operating_conditions
  exclusions
  semantic_case_id_policy
```

`evaluator_candidate_causes` is evaluator-owned. It supports deterministic C0 and scoring but is not automatically exposed as an Agent multiple-choice list.

Fixture/case IDs presented to the Agent MUST be semantic/opaque and must not encode canonical IDV/fault truth.

## First family

Begin with reactor/cooling-water-related behavior but do not restrict every cause to an answer directly bound to the trigger node.

The family should include:

- multiple plausible alternatives;
- several seeds/timings/magnitudes;
- benign/healthy variation;
- at least one case whose causal path is not trivially revealed by local node-to-disturbance bindings.

Exact membership is chosen after pilot analysis.

## Strong deterministic C0 baseline

Before assigning Agent difficulty, run:

```text
C0: evaluator-known candidate enumeration
 -> supported candidate scenario compilation
 -> isolated rollouts
 -> deterministic feature/trajectory comparison
 -> best-match / NO_ABNORMAL_CAUSE decision
```

C0 is intentionally strong.

A case that C0 solves reliably with low cost is useful as EASY/baseline validation but must not be called MEDIUM/HARD evidence of Agent investigation value.

Every reported RCA benchmark version records C0 performance/cost.

## Identifiability pilot

For each candidate/variant:

1. generate trajectories over multiple deterministic environment seeds/operating variants;
2. extract frozen benchmark-design features;
3. compute pairwise/inter-class separability;
4. run C0 and record accuracy/cost;
5. identify confounded/near-identical alternatives;
6. audit the Agent-visible tool/context surface for answer/candidate leakage;
7. verify a nontrivial subset can be distinguished without hidden metadata;
8. create/retain healthy negative variants.

Candidate design features may include:

- response direction/magnitude;
- onset/lag;
- transient/steady-state behavior;
- selected correlations;
- deterministic PCA/PLS baseline features if pinned;
- safety/shutdown events;
- process/topology affected-region patterns.

Benchmark-design feature extraction is evaluator tooling. It is not automatically part of the Agent's Tool Bridge allowlist.

## Difficulty tiers

Difficulty is empirical and baseline-relative.

### EASY

Target evidence is available through targeted read/topology tools and/or C0 performs strongly.

### MEDIUM

Requires useful analysis/counterfactual discrimination beyond trivial local enumeration; C0 is materially imperfect or expensive under the same scenario family.

### HARD

May include:

- weakly separated alternatives;
- non-local causes;
- nuisance/operating variation;
- interacting deviations;
- tight investigation budgets;
- scenarios where choosing which experiment/evidence to collect matters materially.

Do not freeze numeric thresholds until pilot data exists, but always report the underlying measurements/C0 behavior used to justify labels.

## Candidate/topology leakage

Leakage audit explicitly tests the strategy:

```text
trigger node
 -> query local bound disturbances/IDs
 -> simulate each
 -> choose closest
```

If the Agent-visible tool surface exposes exactly the evaluator candidate set, that is a capability condition and must be disclosed/ablated, not treated as ordinary topology reasoning.

Default blind RCA policy does not expose canonical IDV answer bindings through `get_related_disturbances`.

## Memorization resistance

TEP is public and common fault descriptions may exist in model pretraining.

Mitigations:

- do not score fault-name recall alone;
- use structured evidence/experiment requirements;
- vary seed/timing/magnitude/operating state;
- include plausible/non-local alternatives;
- include scenario variants not identical to textbook prompts;
- maintain hidden variants;
- use opaque semantic case IDs;
- report scientific behavior/resource use in addition to correctness.

Prior knowledge is not itself cheating. The benchmark asks whether the Agent validates/uses it against current evidence instead of merely asserting a memorized label.

## Healthy / negative cases

Every frozen initial suite includes healthy/no-fault or benign-variation cases.

`NO_ABNORMAL_CAUSE` is an explicit scored outcome.

Measure:

- false-positive diagnosis;
- unnecessary tool/rollout use;
- whether the Agent can stop with justified uncertainty/no abnormal cause.

## Unsupported-capability cases

For HAZOP/capability tasks, include requests outside TEP physics. Correct behavior is explicit unsupported classification, not invented simulation.

## Deterministic scorer versus Agent-visible tools

If an Agent-visible tool and evaluator use the same trajectory metric/feature implementation, the report must disclose this.

Prefer scorer/version separation or complementary scoring so the benchmark does not silently reduce to "call the evaluator's exact function".

Tool implementations/scorers are versioned independently even when they share a lower-level library.

## Repeats and stochastic models

Environment seeds are deterministic; LLM outputs may not be.

For architecture comparisons:

- pair fixture/environment seeds across conditions;
- freeze model/version/settings per study;
- repeat stochastic Agent conditions when variance is material;
- retain all failures;
- avoid cherry-picking traces.

Exact minimum repeat counts are set per reported study after pilot variance estimation rather than invented globally.

## Benchmark evolution

A used benchmark version is immutable.

Changes to fixtures, candidate families, scoring vocabulary, C0 implementation, tool policy, or leakage fixes create a new benchmark version and preserve historical results.

## Leakage audit

Before freezing a version inspect:

- fixture projection;
- registered tool names/descriptions/results;
- ProcessGraph/binding visibility;
- Rule metadata;
- artifact filenames/metadata;
- prompts/context projections;
- semantic case IDs;
- experiment/run-log refs;

for hidden cause/candidate leakage.

## Acceptance criteria

1. Generate at least three reproducible variants in the first scenario family.
2. Run deterministic identifiability/separability pilot over multiple variants.
3. Run C0 on every candidate variant and use results in difficulty assignment.
4. Exclude/relabel at least one trivial/confounded case if found.
5. Build one healthy negative fixture with `NO_ABNORMAL_CAUSE` scoring.
6. Prove blind Agent projection/tools contain no canonical hidden IDV/candidate answer list by default.
7. Include at least one non-local/nontrivial case before claiming MEDIUM/HARD Agent investigation performance.
8. Preserve a hidden-eval variant unavailable to AutoResearch/Agent feedback/history retrieval.
