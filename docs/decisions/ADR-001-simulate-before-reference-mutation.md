# ADR-001 — Simulate before reference-world mutation

Status: accepted  
Date: 2026-09-15

## Context

The TEP sandbox is forkable and intended for counterfactual experimentation. Agent-generated recovery actions are uncertain, while isolated rollouts are cheap relative to reference-world mutation.

Runtime side-effect semantics now make the boundary explicit:

```text
SIMULATE = isolated branch/sandbox only
MUTATE   = reference/external state change
```

SIMULATE never has a recovery exception that permits reference mutation.

## Decision

For recovery experiments, the default path is:

```text
propose candidate
 -> deterministic validate_request for simulation
 -> apply only in isolated fork
 -> rollout and score
 -> compare candidate(s) + no-action baseline
 -> choose/recommend candidate
 -> if reference application is enabled:
      revalidate exact frozen MUTATE request
      bind expected reference-state revision
      optional configured authority escalation/approval
      apply only if revision/policy still match
 -> post-action verification
```

Direct model-to-reference mutation is prohibited.

## Rationale

This:

- uses the simulator as an experimental world rather than direct control target;
- creates evidence for action selection;
- separates investigation/recovery reasoning from execution authority;
- enables comparison with no-action/alternative strategies;
- makes safety/provenance/evaluation clearer.

## Consequences

Positive:

- strong provenance;
- clear SIMULATE/MUTATE boundary;
- natural counterfactual benchmark;
- stale-state protection for enabled mutation.

Costs:

- extra simulation cost/latency;
- fork fidelity matters;
- future real-time systems may need different latency/authority policy.

## Initial implementation policy

First recovery benchmark may rank simulated strategies without enabling any reference MUTATE tool.

Blind RCA and AutoProcessResearch keep reference MUTATE unavailable by default.

## Exceptions / ablations

A research condition may disable simulate-before-apply only when explicitly specified and labeled. Such a condition must still obey runtime MUTATE authority/revision/gate rules.

## Revisit trigger

Revisit after recovery benchmarks quantify simulation cost versus quality/safety benefit, or a future target environment has latency/fidelity constraints incompatible with pre-action rollouts.
