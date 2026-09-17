# AutoProcessResearch v0

Status: proposal  
Owner repo: `tep-agent-lab`

## Goal

Adapt the bounded autonomous experiment-loop pattern popularized by Karpathy's `autoresearch` to a process-simulation playground without allowing the Agent to modify TEP physics, evaluator/scoring code, hidden scenarios, or its own authority.

This is a **later separate research task family**, not normal RCA runtime and not an orchestration-ablation row.

```text
Research goal
 -> hypothesis/change proposal
 -> bounded experiment/search
 -> deterministic score
 -> append record
 -> ACCEPT / REJECT / NEUTRAL
 -> repeat until deterministic stop
 -> hidden evaluation
```

## Core design pattern

Borrow:

- small explicit mutable surface;
- frozen preparation/evaluation logic;
- measurable objective;
- fixed/bounded per-trial and total budgets;
- append-only failed/negative results;
- monotonic best-candidate pointer under frozen acceptance policy.

Unlike code-training autoresearch, the Agent does not edit simulator source. The mutable artifact is a bounded strategy/configuration/search surface.

## Prerequisites

Do not implement this mode before:

- recovery/search objective exists;
- simulator fork/scoring is stable;
- runtime extra-dimensional budget accounting works;
- Tool Bridge optimizer/search paths account for nested simulation use;
- RESEARCH/HIDDEN_EVAL partitions are frozen;
- normal RCA/recovery tracing/evaluation is reliable.

## `ResearchSpec`

```text
ResearchSpec
  research_id
  goal
  research_mode
  mutable_surface_schema
  frozen_evaluator_ref
  baseline_ref
  primary_objective
  direction: MINIMIZE | MAXIMIZE
  hard_constraints[]
  secondary_metrics[]
  research_scenario_distribution_ref
  hidden_eval_ref
  parameter/search_bounds?
  per_trial_budget
  total_budget
  seed_policy
  acceptance_policy
  stop_policy
  tool_policy
```

The harness owns/freeze the spec. The Agent cannot modify evaluator/objective/constraints/scenario partitions/tool authority/budgets during the campaign.

## Mutable versus frozen surface

### Potential mutable surface

- recovery strategy structure represented by an explicit future schema;
- allowed actuator targets/sequence;
- bounded controller/action parameters;
- experiment/diagnostic policy parameters;
- detector/policy parameters in an explicitly separate study.

The exact `change_set`/strategy representation must be specified before the first campaign; free-form code modification is not permitted.

### Frozen

- TEP simulator/source physics;
- benchmark/evaluator/scorer implementation;
- hidden scenarios;
- safety/capability truth;
- Rule authority policy;
- ToolSpec authority/allowlist;
- primary objective/weights;
- total/per-trial budget.

## Explicit authority boundary

AutoProcessResearch tool policy MUST exclude reference-world `MUTATE` by default.

All simulator experiments run via isolated `SIMULATE` paths. An AutoResearch campaign cannot apply its candidate directly to the reference branch merely because it scored well.

If a later study explicitly tests applying a selected candidate, that is a separate recovery/MUTATE protocol using state-revision-bound validation.

## Experiment loop

```text
INITIALIZE
 -> evaluate baseline
 -> load best candidate + append-only trial history
 -> Main Agent proposes one conceptual hypothesis/change
 -> compile/validate mutable surface + budget + constraints
 -> optional dependency-aware TOOL WorkBatch for independent trial evaluation
 -> isolated simulation/search
 -> deterministic scorer
 -> post-execution result/provenance verification
 -> append ExperimentRecord/trial record
 -> ACCEPT / REJECT / NEUTRAL
 -> update best pointer if accepted
 -> STOP_CHECK
 -> repeat
```

There is no required full Dynamic DAG engine. Independent rollout work may use ordinary runtime WorkBatch semantics.

## One conceptual change per trial

Prefer one attributable conceptual change per top-level trial.

A deterministic optimizer may evaluate many numeric points inside one conceptual trial such as "search this bounded parameter space." Nested trials/rollouts remain visible in runtime budgets and provenance.

## `ResearchCandidate`

```text
ResearchCandidate
  candidate_id
  parent_candidate_ref?
  hypothesis_ref
  typed_change_set
  mutable_surface_refs
  rationale
  complexity_delta?
```

`typed_change_set` must validate against the frozen mutable-surface schema.

## Scoring

Use one primary scalar objective plus hard constraints and recorded secondary metrics in the first implementation.

Example:

```text
hard constraints:
  supported operation
  no prohibited process/safety state

primary objective:
  frozen weighted recovery loss

secondary:
  min safety margin
  time to recover
  production deviation
  control effort
  shutdown event
```

Weights/scalarization are frozen by ResearchSpec, not changed per trial by the Agent.

## Acceptance policy

```text
ACCEPT:
  valid run
  hard constraints pass
  objective improves by frozen minimum delta

REJECT:
  invalid/failure/constraint violation/objective regression

NEUTRAL:
  valid but no meaningful improvement under frozen tolerance
```

Acceptance updates only the best-candidate pointer. No trial is deleted.

## Noisy/stochastic objectives

ResearchSpec may require repeated deterministic environment seeds/replications.

The aggregation policy (mean/quantile/worst-case/etc.) and seed sampling are frozen before seeing candidate results. The proposing Agent cannot cherry-pick favorable validation seeds.

## Deterministic optimizer delegation

For bounded numeric search:

```text
Main Agent
  chooses mechanism/variables/bounds/objective rationale
        |
        v
SIMULATE Tool Bridge optimizer
  reserves optimizer_trials + rollouts + horizon
  executes isolated trials
        |
        v
best numeric candidate + full trial history
        |
        v
Main Agent interprets
```

Optimizer method/version/seed and every nested trial/resource draw are pinned in provenance.

## Trial record

Each top-level trial is append-only and may be materialized as an `ExperimentRecord`:

```text
trial_id
candidate_ref
hypothesis_ref
parent_best_ref
resolved run/search spec
objective + secondary metrics
constraint verdict
status: ACCEPT | REJECT | NEUTRAL | FAILED
failure_class?
artifact refs
model/tool/library/scorer versions
actual budget usage
interpretation refs
```

## Stopping

Deterministic backstops may include:

- total model/tool/trial/rollout/horizon budget exhaustion;
- wall/compute budget if configured;
- target score reached;
- maximum accepted improvements;
- plateau over N valid trials under frozen tolerance;
- no valid bounded proposal remains;
- external cancellation.

Agent may recommend stopping, but cannot disable deterministic limits.

## Overfitting protection

Use:

```text
RESEARCH scenarios — declared iterative feedback
HIDDEN_EVAL — not exposed after every trial; reserved for final/generalization checks
```

A candidate that improves visible research score is not claimed generally better without held-out evaluation.

Historical Engineering Record retrieval is disabled unless explicitly part of the research condition.

## Suggested first campaign

Possible later question:

> Find a robust bounded recovery strategy for a curated reactor cooling-water-related disturbance family.

Prerequisite comparisons:

- no action;
- deterministic hand-designed baseline if one is justifiable;
- Agent conceptual candidate loop;
- Agent + deterministic optimizer.

Full mutable Dynamic DAG and subagent swarms are not prerequisites.

## Evaluation

AutoProcessResearch metrics are defined under the separate task-family section of `evaluation-v0.md`, including:

- hidden-eval objective;
- trials to improvement;
- accepted/rejected/failed counts;
- duplicate/redundant rate;
- nested simulation/search cost;
- constraint violations attempted;
- robustness across scenarios/seeds;
- accepted-strategy complexity.

## Invariants

- AutoResearch is not normal RCA orchestration.
- Evaluator/objective/hidden scenarios/authority are frozen per campaign.
- Reference MUTATE is unavailable by default.
- Simulator-running search tools are SIMULATE and budget-visible.
- Every trial/negative result is retained.
- Agent cannot self-change its authority/budget/evaluator.
- Hidden evaluation remains inaccessible to the research Agent.

## Acceptance tests

1. Run baseline and create initial best-candidate ref.
2. Execute one valid isolated candidate and ACCEPT an improvement.
3. Execute a regression and REJECT without deleting history.
4. Block attempts to change evaluator/objective/hidden scenarios/authority policy.
5. Verify reference MUTATE tool is absent/denied in AutoResearch mode.
6. Delegate bounded numeric search to a SIMULATE optimizer bridge and reconcile nested resource usage.
7. Stop deterministically on plateau/budget.
8. Evaluate final candidate on held-out hidden scenarios unavailable during iterative search.
