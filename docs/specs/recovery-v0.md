# Recovery Planning v0

Status: proposal  
Version: v0  
Owner repo: `tep-agent-lab`

## Goal

Evaluate whether an Agent can propose and compare bounded recovery strategies using isolated counterfactual rollouts before any reference-world intervention is considered.

This is a later task-family proposal. It does not block first RCA implementation.

## Core policy

**Simulate before apply** is the default when the scenario is forkable and the study enables recovery actions.

```text
SIMULATE = isolated branch only
MUTATE   = reference-world change
```

An Agent never directly writes XMV/reference values.

## Recovery proposal

```text
RecoveryProposal
  proposal_id
  incident_id
  objective
  candidate_interventions[]
  expected_effect_predictions[]
  verification_horizon
  expected_tradeoffs[]
  confidence_or_rank?
  supporting_evidence_link_refs[]
```

The exact `RecoveryStrategy`/sequence representation is deferred until the recovery implementation phase and must be typed before execution/AutoResearch depends on it.

## Candidate generation

Main Agent may generate bounded candidates from a fixture/policy-provided action space.

Subagents may evaluate candidates only in isolated branches and cannot MUTATE reference state.

Free-form simulator mutation is never exposed.

## Counterfactual evaluation

For each candidate:

```text
reference snapshot
 -> fork
 -> runtime G0-G3 + lab validate_request
 -> tep-sim capability/control validation as composed by lab
 -> apply candidate in fork only
 -> rollout
 -> deterministic outcome/safety metrics
 -> post-execution verify_result
 -> candidate result artifact/ExperimentRecord
```

Include a no-action branch when meaningful.

## Candidate metrics

Retain an explicit vector, e.g.:

```text
recovery_success
max temperature/pressure excursion
minimum safety margin
shutdown occurrence/time
settling/recovery time
production/process deviation proxy
intervention magnitude
number of manipulated variables changed
```

A benchmark may define a frozen scalar ranking, but raw metrics remain canonical.

## Reference MUTATE path

The first recovery benchmark MAY remain ranking-only with no reference mutation.

If a later benchmark enables reference application:

```text
selected RecoveryProposal
 -> generic G0-G3
 -> lab validate_request
      explicit POLICY rules
      tep-sim capability/bounds/control-mode checks
      simulate-before-apply requirement
 -> freeze exact MUTATE request
 -> bind expected reference-state revision
 -> optional configured authority escalation/approval
 -> recheck revision
 -> apply exact frozen action
 -> post-action verification
```

The approval/escalation step authorizes a frozen, already-validated request; it is not assumed to supply missing chemical-domain truth.

A stale validation token/revision MUST fail before application.

## Policy representation

Each fixture/study defines explicit versioned policy data/rules such as:

```text
allowed_actuators
max_delta/rate per actuator
max simultaneous changes
max reference-world interventions
cooldown / verification horizon
forbidden states/actions
```

These are not prompt-only instructions. Where represented as Rules they use, for example:

```text
origin = POLICY
validation = REVIEWED
authority = HARD_GATE or OPERATIONAL_PROPOSAL as appropriate
```

## Post-action verification

A deterministic result may classify:

```text
RECOVERED
IMPROVED_NOT_RECOVERED
NO_EFFECT
WORSENED
SHUTDOWN
INCONCLUSIVE
```

The Agent may replan only through a new bounded request if budget remains.

## Retry/termination policy

Recovery loops are bounded by standard/extra-dimensional runtime budgets plus fixture-specific limits such as `max_recovery_rounds`.

After exhaustion the run terminates with an explicit configured status/outcome rather than indefinite model replanning.

## Baselines

At minimum compare what is actually supportable without inventing SME policy:

1. no action;
2. simple deterministic controller/recovery baseline **only if a defensible baseline is available for the chosen fixture**;
3. Main Agent proposal without counterfactual pre-test;
4. Main Agent proposal with counterfactual evaluation;
5. bounded subagent candidate evaluation when studying orchestration.

Do not fabricate a weak "hand-designed" baseline merely to complete the matrix.

## Evaluation metrics

- recovery success/shutdown rate;
- time to recover;
- minimum safety margin;
- intervention magnitude/count;
- invalid/rejected proposal rate;
- simulator rollouts/horizon;
- model/tool/subtask cost;
- predicted-versus-observed recovery effect;
- authority/gate violations;
- stale-revision protection behavior.

## Engineering record

An enabled/applied recovery study later produces a `RecoveryRecord` or equivalent extension of the Engineering Records spec before cross-incident retrieval is considered.

Until then, proposal/experiment/decision records preserve the complete evidence/action history.

## Invariants

- SIMULATE never mutates reference state.
- Reference application is MUTATE only.
- Agent cannot bypass action-space/policy/capability gates.
- Enabled MUTATE is bound to expected state revision.
- Deterministic metric vectors remain canonical.
- Retries are explicit and bounded.
- Every applied action links proposal, validation, exact parameters, expected revision, and post-action result.

## Acceptance criteria

1. Generate at least two allowed candidates plus no-action for one recovery fixture.
2. Run isolated rollouts and retain deterministic metric vectors.
3. Rank candidates without reference mutation.
4. Reject one invalid candidate before any reference-world change.
5. If/when a benchmark enables MUTATE, reject a stale revision-bound request and successfully apply one valid frozen request.
6. Produce deterministic post-action outcome/provenance for the enabled mutation study.
