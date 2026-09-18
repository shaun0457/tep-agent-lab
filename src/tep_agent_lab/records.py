"""Archival engineering records; no state mutation, retrieval or rule promotion.

The host supplies a run-scoped resolver that validates referenced artifacts and
returns their authoritative InformationRef. Numeric experiment truth stays in
the canonical result, rather than being copied into an ExperimentRecord.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from typing import Any

from industrial_agent_runtime import InformationRef, Visibility, to_jsonable
from industrial_agent_runtime.serialization import freeze_json

from .persistence import RunLog


class RecordStatus(StrEnum):
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class _Record:
    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if (not item.name.endswith("_ref")
                    and not item.name.endswith("_refs")
                    and _contains_information_ref(value)):
                raise ValueError(
                    f"{item.name} must use explicit InformationRef fields"
                )
            if str(item.type).startswith("Mapping"):
                if not isinstance(value, Mapping):
                    raise ValueError(f"{item.name} requires a mapping")
                object.__setattr__(self, item.name, freeze_json(value))
        _validate(self)


@dataclass(frozen=True, kw_only=True)
class InvestigationReport(_Record):
    report_id: str
    investigation_id: str
    generated_from_state_revision: int
    incident_ref: InformationRef
    conclusion: str
    selected_causal_claim: str
    ranked_hypothesis_refs: tuple[InformationRef, ...]
    supporting_evidence_link_refs: tuple[InformationRef, ...]
    contradicting_evidence_link_refs: tuple[InformationRef, ...]
    experiment_refs: tuple[InformationRef, ...]
    decision_record_refs: tuple[InformationRef, ...]
    uncertainty: str
    unresolved_questions: tuple[str, ...]
    trace_ref: InformationRef
    artifact_refs: tuple[InformationRef, ...]
    revisions: Mapping[str, str]
    policy_versions: Mapping[str, str]
    created_at: str
    status: RecordStatus = RecordStatus.DRAFT
    case_id: str | None = None
    safety_summary_ref: InformationRef | None = None


@dataclass(frozen=True, kw_only=True)
class DecisionRecord(_Record):
    decision_id: str
    investigation_id: str
    decision_type: str
    decision: str
    alternatives: tuple[str, ...]
    evidence_refs: tuple[InformationRef, ...]
    rule_or_policy_refs: tuple[InformationRef, ...]
    tradeoffs: tuple[str, ...]
    uncertainty: str
    decided_by: str
    state_revision: int
    created_at: str
    expected_outcome: str | None = None
    actual_outcome_ref: InformationRef | None = None


@dataclass(frozen=True, kw_only=True)
class ExperimentRecord(_Record):
    record_id: str
    experiment_ref: InformationRef
    hypothesis_refs: tuple[InformationRef, ...]
    prediction_refs: tuple[InformationRef, ...]
    run_spec_ref: InformationRef
    result_ref: InformationRef
    outcome_summary: str
    actual_budget_usage: Mapping[str, float]
    artifact_refs: tuple[InformationRef, ...]
    provenance: Mapping[str, Any]
    created_at: str
    proposed_interpretation_ref: InformationRef | None = None
    accepted_rejected_neutral: str | None = None


Record = InvestigationReport | DecisionRecord | ExperimentRecord


def _contains_information_ref(value: Any) -> bool:
    if isinstance(value, InformationRef):
        return True
    if isinstance(value, Mapping):
        return any(_contains_information_ref(key)
                   or _contains_information_ref(child)
                   for key, child in value.items())
    if isinstance(value, (tuple, list)):
        return any(_contains_information_ref(child) for child in value)
    if is_dataclass(value) and not isinstance(value, type):
        return any(_contains_information_ref(getattr(value, item.name))
                   for item in fields(value))
    return False


def _validate(record: Record) -> tuple[InformationRef, ...]:
    references: list[InformationRef] = []
    for item in fields(record):
        value = getattr(record, item.name)
        if item.name.endswith("_refs"):
            if not isinstance(value, tuple) or any(not isinstance(ref, InformationRef) for ref in value):
                raise ValueError(f"{item.name} must contain immutable runtime InformationRefs")
            references.extend(value)
        elif item.name.endswith("_ref"):
            if value is None and "None" not in str(item.type):
                raise ValueError(f"{item.name} is required")
            if value is not None:
                if not isinstance(value, InformationRef):
                    raise ValueError(f"{item.name} requires runtime InformationRef")
                references.append(value)
        elif item.name in ("state_revision", "generated_from_state_revision"):
            if type(value) is not int or value < 0:
                raise ValueError("Record state revision must be a nonnegative integer")
        elif str(item.type) in ("str", "str | None"):
            if value is None and "None" in str(item.type):
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{item.name} requires nonempty text")
        elif str(item.type).startswith("tuple[str"):
            if not isinstance(value, tuple) or any(not isinstance(x, str) or not x for x in value):
                raise ValueError(f"{item.name} requires immutable strings")
    if isinstance(record, InvestigationReport):
        RecordStatus(record.status)
        for keys, mapping in (({"environment", "runtime", "lab"}, record.revisions),
                              ({"model", "prompt", "tool", "rule_policy"}, record.policy_versions)):
            if not keys.issubset(mapping) or any(not isinstance(v, str) or not v for v in mapping.values()):
                raise ValueError("Report requires exact component/policy versions")
    if isinstance(record, ExperimentRecord):
        if not record.provenance:
            raise ValueError("Experiment record requires provenance")
        if any(type(v) not in (int, float) or v < 0 for v in record.actual_budget_usage.values()):
            raise ValueError("Actual budget usage must be nonnegative")
    # Strict JSON conversion also rejects nonfinite numbers and opaque objects.
    to_jsonable(record)
    return tuple(references)


class EngineeringArchive:
    """Append versioned records only after complete ref/schema validation."""

    def __init__(self, log: RunLog, resolve: Callable[[InformationRef], InformationRef]):
        self.log = log
        self.resolve = resolve

    def store(self, record: Record, *, version: str,
              supersedes: InformationRef | None = None) -> InformationRef:
        if not isinstance(record, (InvestigationReport, DecisionRecord, ExperimentRecord)):
            raise TypeError("Expected a v0 engineering record")
        if not isinstance(version, str) or not version:
            raise ValueError("Record version is required")
        # Freeze a detached copy before verification/persistence.
        updates = {}
        for item in fields(record):
            value = getattr(record, item.name)
            if isinstance(value, Mapping):
                updates[item.name] = freeze_json(value)
        record = replace(record, **updates)
        references = _validate(record)
        if isinstance(record, (InvestigationReport, DecisionRecord)):
            if record.investigation_id != self.log.manifest().get("investigation_id"):
                raise ValueError("Record belongs to a different investigation")
        for ref in references:
            if ref.visibility == Visibility.EVALUATOR:
                raise ValueError("Evaluator truth cannot enter an engineering record")
            if self.resolve(ref) != ref:
                raise ValueError("Reference metadata/version differs from authoritative ref")
        record_id = getattr(record, "report_id", getattr(record, "decision_id",
                                                       getattr(record, "record_id", None)))
        kind = type(record).__name__
        existing = [e["payload"] for e in self.log.events() if e["type"] == "ENGINEERING_RECORD"]
        if any(e["ref"]["ref_id"] == record_id and e["ref"]["version"] == version for e in existing):
            raise ValueError("A historical record version cannot be overwritten")
        if supersedes is not None:
            if (supersedes.ref_id != record_id or supersedes.kind != kind
                    or not any(e["ref"] == to_jsonable(supersedes) for e in existing)):
                raise ValueError("Superseded version must exist in this run")
        if (isinstance(record, InvestigationReport)
                and RecordStatus(record.status) is not RecordStatus.DRAFT):
            raise ValueError("Only a DRAFT report can enter verification")
        if isinstance(record, InvestigationReport):
            record = replace(record, status=RecordStatus.VERIFIED)
        artifact = self.log.put_artifact(to_jsonable(record))
        ref = InformationRef(record_id, kind, "tep-agent-lab", version,
                             Visibility.AGENT, record.created_at, artifact)
        self.log.append("ENGINEERING_RECORD", {"ref": to_jsonable(ref),
                        "supersedes": to_jsonable(supersedes), "artifact_checksum": artifact})
        return ref
