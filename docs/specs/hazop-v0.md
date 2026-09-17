# Simulation-backed HAZOP v0

Status: proposal  
Version: v0  
Owner repo: `tep-agent-lab`

## Goal

Evaluate whether an agent can use process semantics plus executable simulation to generate and test HAZOP-style deviations, distinguish simulable from unsupported hazards, and produce evidence-backed findings.

This is a research/training/decision-support workflow, not a replacement for formal human HAZOP review.

## HAZOP unit of work

A v0 finding starts from:

```text
process_node
parameter
guide_word
deviation
```

Example:

```text
node = reactor_cooling_loop
parameter = flow
guide_word = LESS
```

## Agent workflow

```text
select node/parameter/guide word
 -> formulate candidate deviation
 -> inspect topology/context
 -> check environment capability
 -> compile to supported TEP scenario if possible
 -> fork reference/baseline state
 -> run rollout(s)
 -> evaluate deterministic process/safety outcomes
 -> interpret causes/consequences/safeguards
 -> emit structured finding
```

The agent may delegate independent node/deviation cases to bounded subagents when experiment budget allows.

## Structured finding

```text
finding_id
node
parameter
guide_word
deviation
possible_causes[]
scenario_support: SUPPORTED | PARTIAL | UNSUPPORTED | AMBIGUOUS
simulation_refs[]
observed_consequences[]
existing_safeguards[]
safeguard_observations[]
recommendations[]
uncertainty
unsupported_claims_or_gaps[]
```

Every simulated consequence MUST reference a rollout/safety artifact. Non-simulated engineering reasoning must be labeled accordingly.

## Capability honesty

If a deviation implies physics outside TEP (for example pipe rupture, atmospheric dispersion, ignition, fire radiation, explosion overpressure), the agent may still identify it as a plausible HAZOP concern, but `scenario_support` MUST be `UNSUPPORTED` or `PARTIAL` and no simulated consequence may be fabricated.

## Cause vs deviation separation

The lab SHOULD distinguish:

- deviation: what process variable condition is abnormal;
- initiating cause/hypothesis: why the deviation occurs;
- simulator realization: the available TEP intervention used to approximate/test it.

One deviation may have multiple candidate simulator realizations. Ambiguity must be explicit.

## Safeguard evaluation

A safeguard may be:

- represented directly by TEP/control logic;
- partially represented;
- external/not modeled.

The finding must label which category applies. For represented safeguards, rollout comparisons may quantify response effectiveness.

## Experiment budget

HAZOP can grow combinatorially. Each run/campaign MUST enforce:

```text
max_nodes
max_deviations
max_rollouts
max_total_simulated_horizon
max_subagents
model/tool budgets
```

## v0 scope

Start with one subsystem: reactor + reactor cooling-water loop.

Initial guide words/parameters SHOULD be limited to deviations that have deterministic TEP mappings, such as selected MORE/LESS/NO or disturbance-like conditions for flow/temperature/control response.

Do not attempt full plant-wide HAZOP coverage in the first implementation.

## Evaluation

Possible metrics:

- supported-deviation recognition accuracy;
- false claims of simulator support;
- evidence-backed consequence precision;
- hazard/deviation coverage within the bounded test set;
- duplicate findings;
- number of rollouts per useful finding;
- token/tool/simulation cost;
- quality of unsupported-gap reporting.

Expert/manual review may be needed for qualitative cause/recommendation quality; deterministic scoring should cover support/evidence/provenance wherever possible.

## Invariants

- Formal HAZOP terminology is not used to imply certification.
- Simulation results are kept separate from agent inference.
- Unsupported consequence physics are never reported as simulator evidence.
- The agent cannot expand the campaign beyond deterministic experiment budgets.
- Process/deviation mappings are environment/lab code/data, not prompt-only memory.

## Acceptance criteria

1. Run one reactor-cooling `LESS flow`-type case through capability check and deterministic scenario compilation.
2. Produce an isolated rollout and deterministic consequence/safety summary.
3. Produce a structured finding with valid evidence refs.
4. Submit one deliberately unsupported hazard and verify explicit unsupported handling.
5. Enforce campaign rollout/deviation budgets.
6. Save complete trace from deviation selection through finding output.
