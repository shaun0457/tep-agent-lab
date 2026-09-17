# Knowledge and Rule Registry v0

Status: proposal  
Owner repo: `tep-agent-lab` with environment truth owned by `tep-sim`

## Goal

Represent engineering knowledge with explicit provenance, validation maturity, scope, and execution authority without allowing paper text, Agent claims, or simulation evidence to silently become hard gates.

The canonical machine schema uses **three independent axes**:

```text
origin
validation
authority
```

The earlier K0–K4 labels may remain as documentation shorthand/presets, but they are not the authoritative persisted data model.

## Axis 1 — `origin`

```text
SIMULATOR
FORMAL_DERIVATION
POLICY
LITERATURE
EXPERIMENT
AGENT
```

Meaning:

- `SIMULATOR` — runtime/environment contract or implementation truth;
- `FORMAL_DERIVATION` — mathematically/physically derived invariant with explicit assumptions;
- `POLICY` — experiment/operational policy chosen by the host/research design;
- `LITERATURE` — paper/manual/SOP/document statement;
- `EXPERIMENT` — relation inferred from controlled observed/simulated evidence;
- `AGENT` — model-generated working/candidate claim.

Origin is provenance, not a confidence score.

## Axis 2 — `validation`

```text
NONE
CORROBORATED
SIMULATION_VALIDATED
ROBUST_VALIDATED
REVIEWED
```

Conceptually:

- `NONE` — candidate only;
- `CORROBORATED` — supported by multiple independent sources/observations but not validated by a controlled campaign;
- `SIMULATION_VALIDATED` — controlled validation over an explicit scenario/operating envelope;
- `ROBUST_VALIDATED` — validated across a broader held-out scenario/seed/operating envelope with documented failure cases;
- `REVIEWED` — accepted through an explicit formal/policy review process appropriate to the source/authority.

Validation is always scoped. It never implies universal scientific truth.

## Axis 3 — `authority`

```text
REFERENCE
ADVISORY
PLANNING
OPERATIONAL_PROPOSAL
HARD_GATE
```

Meaning:

- `REFERENCE` — retrievable/context only;
- `ADVISORY` — may annotate/warn/score/prioritize reasoning;
- `PLANNING` — may shape experiment/action planning but cannot execute reference-world mutation;
- `OPERATIONAL_PROPOSAL` — may support a bounded action proposal that must still pass gates/validation;
- `HARD_GATE` — may deterministically allow/block execution within declared scope.

Authority is never self-assigned by an Agent and does not automatically increase when validation maturity increases.

## `Rule`

```text
Rule
  rule_id
  version
  title
  origin
  validation
  authority
  scope
  predicate_or_relation
  inputs[]
  expected_effect_or_constraint?
  source_refs[]
  validation_refs[]
  operating_envelope?
  units?
  support_summary?
  status
  created_by
  reviewed_by?
  created_at
  updated_at
```

### Optional runtime behavior metadata

A rule may additionally declare how an allowed authority is used:

```text
behavior: PRIOR | ANNOTATE | SCORE | WARN | ALLOW | BLOCK
```

`behavior` must be compatible with `authority`:

- REFERENCE: PRIOR/ANNOTATE only;
- ADVISORY: PRIOR/ANNOTATE/SCORE/WARN;
- PLANNING: advisory behaviors + planning filters/ranking;
- OPERATIONAL_PROPOSAL: may validate/shape proposals but not directly mutate;
- HARD_GATE: ALLOW/BLOCK may be used within explicit scope.

## Hard-gate policy

`HARD_GATE` is intentionally rare.

Examples eligible in the TEP sandbox:

- authoritative simulator capability/bounds/control-mode constraints (`origin=SIMULATOR`);
- explicit reviewed lab/runtime policy (`origin=POLICY`, `validation=REVIEWED`);
- reviewed formal invariant with test coverage and explicit scope.

A literature/Agent/experiment relation does not become a HARD_GATE merely because many simulations supported it.

## K0–K4 shorthand mapping

K-labels remain useful conversational shorthand only:

| Shorthand | Typical canonical representation |
|---|---|
| K0 simulator/runtime truth | `origin=SIMULATOR`, validation appropriate to runtime contract, authority may reach HARD_GATE |
| K1 reviewed invariant/policy | `origin=FORMAL_DERIVATION or POLICY`, `validation=REVIEWED`, authority depends on scope |
| K2 validated engineering relationship | usually `origin=EXPERIMENT/LITERATURE/AGENT`, `validation=SIMULATION_VALIDATED or ROBUST_VALIDATED`, authority typically ADVISORY/PLANNING |
| K3 literature/document heuristic | `origin=LITERATURE`, `validation=NONE/CORROBORATED`, authority REFERENCE/ADVISORY |
| K4 Agent hypothesis | `origin=AGENT`, `validation=NONE`, authority REFERENCE working state only |

Do not persist only a K-number without the three canonical axes.

## Ownership

### `tep-sim`

Owns environment truth needed to execute the simulator correctly, including simulator/runtime constraints and canonical bindings.

### `tep-agent-lab`

Owns:

- benchmark/experiment policy rules;
- advisory/planning relationships;
- literature/experiment candidates;
- validation metadata/campaign results;
- authority mapping policy for lab use.

A reviewed lab policy such as allowed actuators/max delta/cooldown is represented explicitly as `origin=POLICY`, not an unnamed layer outside the registry model.

## Knowledge-promotion principle

Promotion and authority escalation are separate operations.

```text
candidate claim
 -> evidence collection
 -> controlled validation
 -> validation maturity may increase
```

Then an independent deterministic policy decides the maximum authority allowed for `(origin, validation, scope)`.

Example:

```text
LITERATURE + NONE + REFERENCE
  -> controlled TEP validation
LITERATURE + SIMULATION_VALIDATED + PLANNING
```

The Agent may propose validation work but cannot directly change `validation` or `authority` metadata.

## Validation campaign governance

A candidate may later be validated through a campaign such as:

```text
ValidationPlan
  candidate_rule_ref
  scenario_family_ref
  deterministic_sampling_policy
  operating_envelope
  held_out_envelope
  seed_policy
  metrics
  pass/fail/tolerance policy
```

Important anti-self-validation rule:

- the proposing Agent may suggest what relation/envelope should be tested;
- exact scenario/seed sampling for promotion is generated/frozen by evaluator/lab policy;
- held-out conditions are required for robust promotion studies;
- the proposing Agent cannot cherry-pick only favorable trials.

Full KnowledgePromotion workflow implementation is deferred until a real K3/K4 promotion study exists; v0 Rule metadata must nevertheless support it.

## Literature extraction

LLM-assisted extraction may create only a candidate with provenance:

```text
origin = LITERATURE
validation = NONE
authority = REFERENCE
```

Required extraction metadata:

```text
source_ref
source_location
claim
variables/entities
conditions/assumptions
units
relationship type
uncertainties
```

Extraction cannot directly assign execution authority.

## Conflict handling

Conflicting rules are preserved rather than silently merged.

Conflict records should identify:

- overlapping scope;
- differing relation/constraint;
- origin/validation/authority of each rule;
- operating-envelope differences;
- resolution status.

For current TEP execution truth, simulator/runtime constraints win over advisory literature/experiment claims inside the simulator's declared scope.

## Versioning / demotion

Rules are versioned.

Simulator changes or contradictory evidence may:

- narrow scope/envelope;
- create a superseding version;
- deprecate a rule;
- reduce validation maturity pending revalidation;
- reduce authority through policy.

Past runs keep exact rule-version refs.

## Agent-facing access

Suggested tools:

```text
query_rules(scope, variables?, origin?, validation?, authority?)
get_rule(rule_id, version?)
get_rule_provenance(rule_ref)
get_rule_validation(rule_ref)
find_rule_conflicts(rule_ref)
submit_rule_candidate(...)
submit_validation_proposal(...)
```

Agents cannot directly edit validation/authority fields.

## Relationship to runtime gates

Consumer `validate_request` may use only rules/policies whose configured authority permits that use.

Advisory/planning rules may inform context/scoring/planning but do not silently become blocking gates.

## Invariants

- Origin, validation maturity, and authority are independent fields.
- LLM extraction never directly creates a hard gate.
- Promotion evidence may raise validation but does not automatically raise authority.
- Authority is assigned by deterministic/reviewed policy, not Agent prose.
- All non-simulator rules preserve provenance/scope.
- Agent working hypotheses remain investigation state unless explicitly promoted through a later workflow.

## Acceptance tests

1. Store a literature rule as `LITERATURE/NONE/REFERENCE` with exact provenance.
2. Reject an Agent request to change its authority to HARD_GATE.
3. Store a reviewed lab intervention limit as `POLICY/REVIEWED/HARD_GATE` with explicit scope.
4. Record a simulation-validated engineering relation without automatically granting HARD_GATE.
5. Preserve two conflicting scoped rules without overwrite.
6. Consumer gate uses an authoritative simulator/policy rule while ignoring a literature reference for blocking authority.
7. Re-score an old run with pinned rule versions/three-axis metadata.
