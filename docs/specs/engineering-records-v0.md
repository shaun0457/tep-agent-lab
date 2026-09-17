# Engineering Records v0

Status: proposal  
Owner repo: `tep-agent-lab`

## Goal

Record engineering work in structured, reviewable artifacts that resemble real incident/investigation/decision documentation without confusing raw traces, records, reusable knowledge, or future context.

v0 records are archival/audit artifacts. They do **not** automatically become Rules, Lessons Learned, Runbooks, or context for future benchmark cases.

## Core distinction

```text
Trace
  = machine-level execution history

Observation
  = immutable result returned by tool/simulator/analysis

Evidence
  = explicit relationship from an observation to a claim/hypothesis

Engineering Record
  = structured human/Agent-readable account of engineering work/outcome

Knowledge/Rule
  = separately governed reusable claim/constraint

Context
  = task-specific projection selected for a model turn
```

## Record lifecycle

v0 supports:

```text
DRAFT
VERIFIED
SUPERSEDED
REJECTED
```

`APPROVED` is reserved for later workflows where a meaningful authority/governance process exists. A record being VERIFIED means its refs/schema/provenance are valid; it does not mean every engineering interpretation is scientifically true.

## `InvestigationReport`

Mandatory final RCA archive artifact:

```text
InvestigationReport
  report_id
  investigation_id
  case_id?
  generated_from_state_revision
  incident_ref
  conclusion
  selected_causal_claim
  ranked_hypothesis_refs[]
  supporting_evidence_link_refs[]
  contradicting_evidence_link_refs[]
  experiment_refs[]
  decision_record_refs[]
  uncertainty
  unresolved_questions[]
  safety_summary_ref?
  trace_ref
  artifact_refs[]
  environment/runtime/lab revisions
  model/prompt/tool/rule policy versions
  created_at
  status
```

The report may include prose for readability, but canonical scoring/reconstruction uses the structured refs/fields.

## `DecisionRecord`

Captures why a meaningful investigation/engineering decision was taken.

```text
DecisionRecord
  decision_id
  investigation_id
  decision_type
  decision
  alternatives[]
  evidence_refs[]
  rule_or_policy_refs[]
  expected_outcome?
  actual_outcome_ref?
  tradeoffs[]
  uncertainty
  decided_by
  state_revision
  created_at
```

Examples:

- why a particular counterfactual experiment was selected;
- why an unsupported scenario was abandoned;
- why one recovery proposal was preferred over another in a later recovery study.

Not every tool call requires a DecisionRecord. Use it for decisions whose rationale should survive beyond the raw trace.

## `ExperimentRecord`

Persistent archival representation of a completed/failed experiment:

```text
ExperimentRecord
  record_id
  experiment_ref
  hypothesis_refs[]
  prediction_refs[]
  run_spec_ref
  result_ref
  proposed_interpretation_ref?
  outcome_summary
  accepted_rejected_neutral?
  actual_budget_usage
  artifact_refs[]
  provenance
  created_at
```

This is a record/view over canonical ExperimentProposal/RunSpec/Result, not a second competing source of truth.

## Future record types — not v0 implementation requirements

### `RecoveryRecord` / `MaintenanceRecord`

May later capture:

```text
asset/subsystem
diagnosis
action performed
parameter/part changes
before/after condition
verification
downtime/outcome
```

In the TEP sandbox these would be synthetic engineering records tied to simulation evidence.

### `LessonLearned`

A reusable cross-incident statement derived from multiple records/experiments. It must not be created as authoritative knowledge from one report alone.

### `RunbookCandidate`

A proposed repeatable procedure. It is candidate/planning content until regression/benchmark validation establishes scope/value.

### `ManualChangeProposal`

A proposed change to a maintenance/operations manual or SOP. It is never applied to an authoritative manual merely because an Agent generated it.

## Record generation path

```text
final/meaningful state revision
  + observations/evidence/experiments/decisions
  -> deterministic report assembler + optional Agent prose draft
  -> verify refs/schema/provenance
  -> VERIFIED engineering record
```

The model may draft summaries/explanations, but cannot fabricate refs/results. Verifier checks that cited refs exist and are visible/consistent with the run.

## Relationship to future organizational memory

v0 intentionally stops after writing records.

```text
record creation != automatic retrieval
record verification != knowledge promotion
```

A later study may index/retrieve historical records into future Agent context. That must be an explicit capability condition with leakage/benchmark controls.

## Relationship to knowledge promotion

A future pipeline may use repeated Engineering Records as evidence for a `LessonLearned` or Rule candidate:

```text
multiple records
 -> pattern/candidate proposal
 -> independent validation campaign
 -> validation result
 -> authority policy
 -> reusable lesson/rule/runbook candidate
```

The proposing Agent cannot self-promote the record into higher authority.

## Audit/provenance requirements

Each record must allow a reviewer to answer:

- which incident/state revision produced it;
- which observations/evidence supported it;
- which experiments were run;
- which model/prompt/tool/rule versions were active;
- which unresolved uncertainties remained;
- where dense artifacts/traces can be inspected.

## Invariants

- Raw trace is not the engineering report.
- A VERIFIED record is not automatically an authoritative Rule.
- v0 records are not automatically injected into later benchmark contexts.
- Structured refs remain canonical even when prose summaries exist.
- Every claimed experiment/result in a record must reference an existing run artifact/result.
- Superseding records preserve historical versions rather than rewriting history.

## Acceptance tests

1. Build an InvestigationReport from a completed RCA state revision and verify all cited refs exist.
2. Reject a report containing a nonexistent evidence/experiment ref.
3. Create a DecisionRecord linked to exact state revision and evidence refs.
4. Materialize an ExperimentRecord from existing proposal/run/result without duplicating numeric truth.
5. Mark a corrected report as a new version/superseding record rather than mutating history.
6. Verify that record creation alone does not register a Rule or alter a future ContextProjection.
