"""RCA state, deterministic projection, and result-ingestion contracts.

The generic runtime sees only its TaskStateStore protocol.  This module owns the
RCA operation allowlist and persists every accepted or rejected batch in RunLog.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import math
from typing import Any

from industrial_agent_runtime import (
    ContextProjection, InformationRef, StateDelta, SubtaskResult, TaskStatus,
    ToolResult, Visibility, checksum, to_jsonable,
)
from industrial_agent_runtime.serialization import canonical_json, freeze_json

from .experiments import (
    ExperimentInterpretation, ExperimentProposal, ExperimentResult, EvidenceRelation,
    Hypothesis, HypothesisEvidenceLink, HypothesisStatus, HypothesisType,
    PredictionEvaluation,
)
from .persistence import RunLog


MODEL_OPERATIONS = frozenset({
    "ADD_HYPOTHESIS", "UPDATE_HYPOTHESIS", "ADD_EVIDENCE_LINK",
    "ADD_OPEN_QUESTION", "UPDATE_OPEN_QUESTION", "PLAN_EXPERIMENT",
    "ADD_EXPERIMENT_INTERPRETATION", "ADD_DELEGATED_TASK_REF",
    "UPDATE_WORKING_EXPLANATION", "SET_CONCLUSION_REF",
})
INGESTION_OPERATIONS = frozenset({
    "REGISTER_OBSERVATION", "REGISTER_COMPLETED_EXPERIMENT",
    "REGISTER_SUBTASK_RESULT", "REGISTER_ARTIFACT_REF",
})
RUNTIME_OPERATIONS = frozenset({"SET_GENERIC_STATUS"})


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _ref(value: Any) -> InformationRef:
    if isinstance(value, InformationRef):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("typed InformationRef required")
    return InformationRef(**dict(value))


def _refs(value: Any) -> tuple[InformationRef, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError("InformationRef sequence required")
    return tuple(_ref(item) for item in value)


def _reject_hidden(value: Any) -> None:
    clean = to_jsonable(value)

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if "ref_id" in item and item.get("visibility") == Visibility.EVALUATOR.value:
                raise ValueError("evaluator reference cannot enter RCA state")
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(clean)


def _reject_non_agent_refs(value: Any) -> None:
    """Reject serialized ref envelopes that would leak through agent-visible data."""
    clean = to_jsonable(value)

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if ("ref_id" in item and "visibility" in item
                    and item["visibility"] != Visibility.AGENT.value):
                raise ValueError("agent-visible data contains a hidden reference")
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(clean)


@dataclass(frozen=True, kw_only=True)
class OpenQuestion:
    question_id: str
    question: str
    why_it_matters: str
    related_hypotheses: tuple[InformationRef, ...]
    resolvable_by: str
    priority: str | int | float
    status: str

    def __post_init__(self) -> None:
        for name in ("question_id", "question", "why_it_matters", "status"):
            _text(getattr(self, name), name)
        if self.resolvable_by not in {
            "TOOL", "EXPERIMENT", "SUBTASK", "EXTERNAL_KNOWLEDGE", "HUMAN"
        }:
            raise ValueError("invalid OpenQuestion resolvable_by")
        object.__setattr__(self, "related_hypotheses", _refs(self.related_hypotheses))
        if (type(self.priority) not in (int, float)
                and (not isinstance(self.priority, str) or not self.priority.strip())):
            raise ValueError("question priority must be an immutable scalar")
        if type(self.priority) in (int, float) and not math.isfinite(self.priority):
            raise ValueError("question priority must be finite")


@dataclass(frozen=True, kw_only=True)
class WorkingExplanation:
    leading_hypothesis_ref: InformationRef | None
    current_rank_or_score_summary: str | None
    key_evidence_link_refs: tuple[InformationRef, ...]
    key_counterevidence_link_refs: tuple[InformationRef, ...]
    remaining_uncertainties: tuple[str, ...]
    last_updated_revision: int

    def __post_init__(self) -> None:
        if self.leading_hypothesis_ref is not None:
            object.__setattr__(self, "leading_hypothesis_ref",
                               _ref(self.leading_hypothesis_ref))
        if (self.current_rank_or_score_summary is not None
                and (not isinstance(self.current_rank_or_score_summary, str)
                     or not self.current_rank_or_score_summary.strip())):
            raise ValueError("rank/score summary must be text or null")
        for name in ("key_evidence_link_refs", "key_counterevidence_link_refs"):
            object.__setattr__(self, name, _refs(getattr(self, name)))
        if (not isinstance(self.remaining_uncertainties, (tuple, list))
                or any(not isinstance(item, str) or not item.strip()
                       for item in self.remaining_uncertainties)):
            raise ValueError("remaining uncertainties must be immutable text")
        object.__setattr__(self, "remaining_uncertainties",
                           tuple(self.remaining_uncertainties))
        if type(self.last_updated_revision) is not int or self.last_updated_revision < 0:
            raise ValueError("last_updated_revision must be a nonnegative integer")


@dataclass(frozen=True, kw_only=True)
class ObservationRecord:
    observation_id: str
    producer_request_ref: InformationRef
    tool_or_service_ref: InformationRef
    summary: Any
    artifact_refs: tuple[InformationRef, ...]
    information_refs: tuple[InformationRef, ...]
    provenance: Mapping[str, Any]
    visibility: Visibility
    created_at: str

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id")
        _text(self.created_at, "created_at")
        object.__setattr__(self, "producer_request_ref", _ref(self.producer_request_ref))
        object.__setattr__(self, "tool_or_service_ref", _ref(self.tool_or_service_ref))
        object.__setattr__(self, "artifact_refs", _refs(self.artifact_refs))
        object.__setattr__(self, "information_refs", _refs(self.information_refs))
        object.__setattr__(self, "visibility", Visibility(self.visibility))
        if not isinstance(self.provenance, Mapping):
            raise ValueError("observation provenance must be a mapping")
        if self.visibility != Visibility.AGENT:
            raise ValueError("only agent-visible observations enter RcaState")
        if any(ref.visibility != Visibility.AGENT for ref in (
                self.producer_request_ref, self.tool_or_service_ref,
                *self.artifact_refs, *self.information_refs)):
            raise ValueError("observation refs must be agent-visible")
        _reject_non_agent_refs(self)
        object.__setattr__(self, "summary", freeze_json(self.summary))
        object.__setattr__(self, "provenance", freeze_json(self.provenance))


@dataclass(frozen=True, kw_only=True)
class RcaState:
    investigation_id: str
    goal: str
    incident_ref: InformationRef
    generic_status: TaskStatus = TaskStatus.RUNNING
    revision: int = 0
    case_id: str | None = None
    current_time_ref: InformationRef | None = None
    hypothesis_refs: tuple[InformationRef, ...] = ()
    observation_refs: tuple[InformationRef, ...] = ()
    evidence_link_refs: tuple[InformationRef, ...] = ()
    open_questions: tuple[OpenQuestion, ...] = ()
    planned_experiment_refs: tuple[InformationRef, ...] = ()
    completed_experiment_refs: tuple[InformationRef, ...] = ()
    delegated_task_refs: tuple[InformationRef, ...] = ()
    current_best_explanation: WorkingExplanation | None = None
    uncertainty_summary: str | None = None
    safety_state_ref: InformationRef | None = None
    artifact_refs: tuple[InformationRef, ...] = ()
    conclusion_ref: InformationRef | None = None

    def __post_init__(self) -> None:
        _text(self.investigation_id, "investigation_id")
        _text(self.goal, "goal")
        object.__setattr__(self, "incident_ref", _ref(self.incident_ref))
        object.__setattr__(self, "generic_status", TaskStatus(self.generic_status))
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        for name in ("case_id", "uncertainty_summary"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        for name in ("current_time_ref", "safety_state_ref", "conclusion_ref"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _ref(value))
        for name in ("hypothesis_refs", "observation_refs", "evidence_link_refs",
                     "planned_experiment_refs", "completed_experiment_refs",
                     "delegated_task_refs", "artifact_refs"):
            object.__setattr__(self, name, _refs(getattr(self, name)))
        questions = tuple(_open_question(item) for item in self.open_questions)
        object.__setattr__(self, "open_questions", questions)
        if self.current_best_explanation is not None:
            object.__setattr__(self, "current_best_explanation",
                               _working_explanation(self.current_best_explanation))
        _reject_hidden(self)


def _open_question(value: Any) -> OpenQuestion:
    if isinstance(value, OpenQuestion):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("OpenQuestion object required")
    return OpenQuestion(**dict(value))


def _working_explanation(value: Any) -> WorkingExplanation:
    if isinstance(value, WorkingExplanation):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("WorkingExplanation object required")
    required = {
        "leading_hypothesis_ref", "current_rank_or_score_summary",
        "key_evidence_link_refs", "key_counterevidence_link_refs",
        "remaining_uncertainties", "last_updated_revision",
    }
    if set(value) != required:
        raise ValueError("WorkingExplanation must match the canonical schema")
    return WorkingExplanation(**dict(value))


def _hypothesis(value: Any) -> Hypothesis:
    if isinstance(value, Hypothesis):
        return value
    data = dict(value)
    for name in ("scope_refs", "supporting_evidence_link_refs",
                 "contradicting_evidence_link_refs", "experiment_refs",
                 "falsification_prediction_refs"):
        data[name] = _refs(data[name])
    return Hypothesis(**data)


def _evidence_link(value: Any) -> HypothesisEvidenceLink:
    if isinstance(value, HypothesisEvidenceLink):
        return value
    data = dict(value)
    data["hypothesis_ref"] = _ref(data["hypothesis_ref"])
    data["observation_ref"] = _ref(data["observation_ref"])
    return HypothesisEvidenceLink(**data)


def _proposal(value: Any) -> ExperimentProposal:
    if isinstance(value, ExperimentProposal):
        return value
    data = dict(value)
    for name in ("hypothesis_refs", "prediction_refs", "required_input_refs"):
        data[name] = _refs(data[name])
    return ExperimentProposal(**data)


def _interpretation(value: Any) -> ExperimentInterpretation:
    if isinstance(value, ExperimentInterpretation):
        return value
    data = dict(value)
    data["experiment_ref"] = _ref(data["experiment_ref"])
    data["proposed_evidence_links"] = tuple(
        _evidence_link(item) for item in data["proposed_evidence_links"])
    data["hypothesis_updates"] = tuple(
        _hypothesis(item) for item in data["hypothesis_updates"])
    return ExperimentInterpretation(**data)


def _observation(value: Any) -> ObservationRecord:
    if isinstance(value, ObservationRecord):
        return value
    data = dict(value)
    for name in ("producer_request_ref", "tool_or_service_ref"):
        data[name] = _ref(data[name])
    for name in ("artifact_refs", "information_refs"):
        data[name] = _refs(data[name])
    return ObservationRecord(**data)


def _identity(ref: InformationRef) -> tuple[Any, ...]:
    return (ref.ref_id, ref.kind, ref.owner, ref.version, ref.visibility,
            ref.created_at, ref.checksum)


def _object_ref(ref_id: str, kind: str, value: Any, revision: int,
                created_at: str = "state-event") -> InformationRef:
    return InformationRef(ref_id, kind, "tep-agent-lab", f"revision-{revision}",
                          Visibility.AGENT, created_at, checksum(value))


def project_rca_state(
        state: RcaState, policy: Mapping[str, Any], *,
        task_id: str | None = None,
        visibility_policy_version: str = "rca-visible-v0",
        validate_ref: Callable[[InformationRef], Any] | None = None,
) -> ContextProjection:
    """Build the exact bounded model projection from immutable RCA state."""
    if not isinstance(state, RcaState):
        raise TypeError("RcaState required")
    if not isinstance(policy, Mapping):
        raise ValueError("projection policy must be a mapping")
    allowed = {"max_observations", "max_open_questions"}
    if set(policy) - allowed:
        raise ValueError("unknown projection policy field")
    for key, value in policy.items():
        if type(value) is not int or value < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    max_observations = policy.get("max_observations", len(state.observation_refs))
    observations = (() if max_observations == 0
                    else state.observation_refs[-max_observations:])
    questions = state.open_questions[:policy.get(
        "max_open_questions", len(state.open_questions))]
    content = {
        "investigation_id": state.investigation_id, "case_id": state.case_id,
        "goal": state.goal, "incident_ref": state.incident_ref,
        "hypothesis_refs": state.hypothesis_refs,
        "observation_refs": observations,
        "evidence_link_refs": state.evidence_link_refs,
        "open_questions": questions,
        "planned_experiment_refs": state.planned_experiment_refs,
        "completed_experiment_refs": state.completed_experiment_refs,
        "delegated_task_refs": state.delegated_task_refs,
        "current_best_explanation": state.current_best_explanation,
        "uncertainty_summary": state.uncertainty_summary,
        "safety_state_ref": state.safety_state_ref,
        "artifact_refs": state.artifact_refs,
        "conclusion_ref": state.conclusion_ref,
        "generic_status": state.generic_status,
    }
    included = _collect_refs(content)
    for ref in included:
        if ref.visibility != Visibility.AGENT:
            raise ValueError("projection contains a hidden reference")
        if validate_ref is not None:
            validate_ref(ref)
    clean = to_jsonable(content)
    digest = checksum(clean)
    return ContextProjection(
        f"rca:{state.investigation_id}:{state.revision}:{digest[:12]}",
        task_id or state.investigation_id, state.revision, clean, included,
        visibility_policy_version, len(canonical_json(clean).encode("utf-8")), digest)


class RcaStateStore:
    """Single-writer, event-backed TaskStateStore implementation for blind RCA."""

    def __init__(self, state: RcaState, log: RunLog, *,
                 resolve: Callable[[InformationRef], InformationRef] | None = None,
                 task_id: str | None = None,
                 visibility_policy_version: str = "rca-visible-v0") -> None:
        if log.events():
            raise ValueError("existing run log must be opened with reconstruct()")
        manifest_investigation = log.manifest().get("investigation_id")
        if (manifest_investigation is not None
                and manifest_investigation != state.investigation_id):
            raise ValueError("run log belongs to another investigation")
        self._state = state
        self.log = log
        self._external_resolve = resolve
        self.task_id = _text(task_id or state.investigation_id, "task_id")
        self.visibility_policy_version = _text(
            visibility_policy_version, "visibility_policy_version")
        self._objects: dict[tuple[Any, ...], Any] = {}
        self._register_initial_refs(state)
        self._persist_initial()

    @classmethod
    def reconstruct(cls, log: RunLog, *,
                    resolve: Callable[[InformationRef], InformationRef] | None = None
                    ) -> "RcaStateStore":
        accepted = [event for event in log.events()
                    if event["type"] in {"RCA_STATE_INITIALIZED", "RCA_STATE_UPDATE_ACCEPTED"}]
        if not accepted:
            raise ValueError("run log has no RCA state snapshot")
        snapshot = log.read_artifact(accepted[-1]["payload"]["snapshot_checksum"])
        obj = cls.__new__(cls)
        obj.log = log
        obj._external_resolve = resolve
        obj.task_id = snapshot["task_id"]
        obj.visibility_policy_version = snapshot["visibility_policy_version"]
        obj._state = _decode_state(snapshot["state"])
        manifest_investigation = log.manifest().get("investigation_id")
        if (manifest_investigation is not None
                and manifest_investigation != obj._state.investigation_id):
            raise ValueError("run log state belongs to another investigation")
        obj._objects = {}
        for entry in snapshot["objects"]:
            ref = _ref(entry["ref"])
            obj._objects[_identity(ref)] = freeze_json(entry["value"])
        obj._register_initial_refs(obj._state)
        return obj

    @property
    def state(self) -> RcaState:
        return self._state

    def revision(self) -> int:
        return self._state.revision

    def status(self) -> TaskStatus:
        return self._state.generic_status

    def resolve(self, ref: InformationRef) -> InformationRef:
        ref = _ref(ref)
        if _identity(ref) in self._objects:
            return ref
        if self._external_resolve is not None:
            resolved = self._external_resolve(ref)
            if resolved == ref:
                return ref
        raise ValueError(f"unknown or metadata-mismatched ref: {ref.ref_id}")

    def _visible(self, ref: InformationRef) -> InformationRef:
        if ref.visibility != Visibility.AGENT:
            raise ValueError("RCA model state accepts only AGENT-visible refs")
        return self.resolve(ref)

    def _register_initial_refs(self, state: RcaState) -> None:
        refs = [state.incident_ref]
        for name in ("current_time_ref", "safety_state_ref", "conclusion_ref"):
            value = getattr(state, name)
            if value is not None:
                refs.append(value)
        for name in ("hypothesis_refs", "observation_refs", "evidence_link_refs",
                     "planned_experiment_refs", "completed_experiment_refs",
                     "delegated_task_refs", "artifact_refs"):
            refs.extend(getattr(state, name))
        for ref in refs:
            if ref.visibility != Visibility.AGENT:
                raise ValueError("initial state cannot contain hidden refs")
            self._objects.setdefault(_identity(ref), {"initial_ref": True})

    def _snapshot(self, state: RcaState | None = None,
                  objects: Mapping[tuple[Any, ...], Any] | None = None) -> dict[str, Any]:
        state = self._state if state is None else state
        objects = self._objects if objects is None else objects
        entries = []
        for identity, value in sorted(objects.items(), key=lambda pair: tuple(str(x) for x in pair[0])):
            ref = InformationRef(identity[0], identity[1], identity[2], identity[3],
                                 identity[4], identity[5], identity[6])
            entries.append({"ref": to_jsonable(ref), "value": to_jsonable(value)})
        return {"task_id": self.task_id,
                "visibility_policy_version": self.visibility_policy_version,
                "state": to_jsonable(state), "objects": entries}

    def _persist_initial(self) -> None:
        artifact = self.log.put_artifact(self._snapshot())
        self.log.append("RCA_STATE_INITIALIZED", {
            "revision": self.revision(), "snapshot_checksum": artifact})

    def project(self, policy: Mapping[str, Any]) -> ContextProjection:
        return project_rca_state(
            self._state, policy, task_id=self.task_id,
            visibility_policy_version=self.visibility_policy_version,
            validate_ref=self._visible)

    def apply_batch(self, deltas: Sequence[StateDelta], expected_revision: int | str) -> int:
        batch = tuple(deltas)
        try:
            if type(expected_revision) is not int or expected_revision != self.revision():
                raise ValueError("stale expected revision")
            if not batch or any(not isinstance(delta, StateDelta) for delta in batch):
                raise ValueError("nonempty typed StateDelta batch required")
            state, objects = self._state, dict(self._objects)
            resulting_revision = state.revision + 1
            for delta in batch:
                if delta.proposed_base_revision != expected_revision:
                    raise ValueError("delta revision does not match expected revision")
                state = self._apply_one(state, objects, delta, resulting_revision)
            state = replace(state, revision=resulting_revision)
            snapshot = self._snapshot(state, objects)
            artifact = self.log.put_artifact(snapshot)
            self.log.append("RCA_STATE_UPDATE_ACCEPTED", {
                "expected_revision": expected_revision,
                "resulting_revision": resulting_revision,
                "operations": [delta.operation for delta in batch],
                "deltas": to_jsonable(batch), "snapshot_checksum": artifact})
            self._state, self._objects = state, objects
            return resulting_revision
        except Exception as exc:
            self.log.append("RCA_STATE_UPDATE_REJECTED", {
                "expected_revision": expected_revision,
                "current_revision": self.revision(), "reason": str(exc),
                "deltas": to_jsonable(batch)})
            raise

    def _apply_one(self, state: RcaState, objects: dict[tuple[Any, ...], Any],
                   delta: StateDelta, revision: int) -> RcaState:
        operation = delta.operation
        if delta.producer == "MODEL":
            if operation not in MODEL_OPERATIONS:
                raise ValueError("operation is not model-proposable")
        elif delta.producer == "RESULT_INGESTION":
            if operation not in INGESTION_OPERATIONS:
                raise ValueError("operation is not deterministic result ingestion")
        elif delta.producer == "RUNTIME":
            if operation not in RUNTIME_OPERATIONS:
                raise ValueError("operation is not runtime-controlled")
        else:
            raise ValueError("unknown StateDelta producer")
        _text(delta.target_ref_or_path, "delta target")
        _reject_hidden(delta.value_or_ref)

        def known(ref: InformationRef) -> InformationRef:
            if _identity(ref) in objects:
                if ref.visibility != Visibility.AGENT:
                    raise ValueError("hidden ref")
                return ref
            return self._visible(ref)

        def put(ref: InformationRef, value: Any) -> None:
            key = _identity(ref)
            if key in objects and to_jsonable(objects[key]) != to_jsonable(value):
                raise ValueError("immutable object ref cannot be rewritten")
            objects[key] = freeze_json(value)

        if delta.reason_ref is not None:
            known(_ref(delta.reason_ref))

        value = delta.value_or_ref
        if operation in {"ADD_HYPOTHESIS", "UPDATE_HYPOTHESIS"}:
            obj = _hypothesis(value)
            if obj.investigation_id != state.investigation_id or obj.hypothesis_id != delta.target_ref_or_path:
                raise ValueError("hypothesis identity/investigation mismatch")
            if obj.last_updated_revision != expected_model_revision(delta):
                raise ValueError("hypothesis revision must match proposal revision")
            existing = [ref for ref in state.hypothesis_refs if ref.ref_id == obj.hypothesis_id]
            if (operation == "ADD_HYPOTHESIS") == bool(existing):
                raise ValueError("ADD/UPDATE hypothesis existence mismatch")
            for ref in (*obj.scope_refs, *obj.supporting_evidence_link_refs,
                        *obj.contradicting_evidence_link_refs, *obj.experiment_refs,
                        *obj.falsification_prediction_refs):
                known(ref)
            for ref in (*obj.supporting_evidence_link_refs,
                        *obj.contradicting_evidence_link_refs):
                if ref not in state.evidence_link_refs:
                    raise ValueError("hypothesis cites an unregistered evidence link")
            for ref in obj.experiment_refs:
                if ref not in (*state.planned_experiment_refs,
                               *state.completed_experiment_refs):
                    raise ValueError("hypothesis cites an unregistered experiment")
            ref = _object_ref(obj.hypothesis_id, "Hypothesis", obj, revision, obj.created_at)
            put(ref, obj)
            refs = tuple(item for item in state.hypothesis_refs if item.ref_id != ref.ref_id) + (ref,)
            return replace(state, hypothesis_refs=refs)
        if operation == "ADD_EVIDENCE_LINK":
            obj = _evidence_link(value)
            if obj.hypothesis_ref.ref_id != delta.target_ref_or_path:
                raise ValueError("evidence-link target must be its hypothesis")
            known(obj.hypothesis_ref)
            known(obj.observation_ref)
            if obj.hypothesis_ref not in state.hypothesis_refs:
                raise ValueError("evidence must link a current hypothesis")
            if obj.observation_ref not in state.observation_refs:
                raise ValueError("evidence must link a registered observation")
            ref_id = f"evidence:{checksum(obj)[:24]}"
            if any(item.ref_id == ref_id for item in state.evidence_link_refs):
                raise ValueError("evidence link already exists")
            ref = _object_ref(ref_id, "HypothesisEvidenceLink", obj, revision)
            put(ref, obj)
            return replace(state, evidence_link_refs=state.evidence_link_refs + (ref,))
        if operation in {"ADD_OPEN_QUESTION", "UPDATE_OPEN_QUESTION"}:
            obj = _open_question(value)
            if obj.question_id != delta.target_ref_or_path:
                raise ValueError("question target mismatch")
            existing = [item for item in state.open_questions
                        if item.question_id == obj.question_id]
            if (operation == "ADD_OPEN_QUESTION") == bool(existing):
                raise ValueError("ADD/UPDATE question existence mismatch")
            for ref in obj.related_hypotheses:
                known(ref)
                if ref not in state.hypothesis_refs:
                    raise ValueError("question references an inactive hypothesis ref")
            questions = tuple(item for item in state.open_questions
                              if item.question_id != obj.question_id) + (obj,)
            return replace(state, open_questions=questions)
        if operation == "PLAN_EXPERIMENT":
            obj = _proposal(value)
            if obj.investigation_id != state.investigation_id or obj.experiment_id != delta.target_ref_or_path:
                raise ValueError("experiment identity/investigation mismatch")
            if any(ref.ref_id == obj.experiment_id for ref in state.planned_experiment_refs):
                raise ValueError("experiment already planned")
            for ref in (*obj.hypothesis_refs, *obj.prediction_refs, *obj.required_input_refs):
                known(ref)
            if any(ref not in state.hypothesis_refs for ref in obj.hypothesis_refs):
                raise ValueError("experiment references an inactive hypothesis ref")
            ref = _object_ref(obj.experiment_id, "ExperimentProposal", obj, revision)
            put(ref, obj)
            return replace(state, planned_experiment_refs=state.planned_experiment_refs + (ref,))
        if operation == "ADD_EXPERIMENT_INTERPRETATION":
            obj = _interpretation(value)
            if obj.interpretation_id != delta.target_ref_or_path:
                raise ValueError("interpretation target mismatch")
            known(obj.experiment_ref)
            if obj.experiment_ref not in (*state.planned_experiment_refs,
                                          *state.completed_experiment_refs):
                raise ValueError("interpretation references an unregistered experiment")
            for link in obj.proposed_evidence_links:
                known(link.hypothesis_ref); known(link.observation_ref)
            ref = _object_ref(obj.interpretation_id, "ExperimentInterpretation", obj, revision)
            put(ref, obj)
            return state
        if operation == "ADD_DELEGATED_TASK_REF":
            ref = _ref(value)
            if ref.ref_id != delta.target_ref_or_path:
                raise ValueError("delegated task target mismatch")
            known(ref)
            if any(item.ref_id == ref.ref_id for item in state.delegated_task_refs):
                raise ValueError("delegated task already registered")
            return replace(state, delegated_task_refs=state.delegated_task_refs + (ref,))
        if operation == "UPDATE_WORKING_EXPLANATION":
            if delta.target_ref_or_path != "current_best_explanation":
                raise ValueError("working explanation target mismatch")
            obj = _working_explanation(value)
            if obj.last_updated_revision != expected_model_revision(delta):
                raise ValueError("working explanation revision must match proposal revision")
            for ref in ((obj.leading_hypothesis_ref,) if obj.leading_hypothesis_ref else ()):
                known(ref)
                if ref not in state.hypothesis_refs:
                    raise ValueError("working explanation references an inactive hypothesis")
            for ref in (*obj.key_evidence_link_refs, *obj.key_counterevidence_link_refs):
                known(ref)
                if ref not in state.evidence_link_refs:
                    raise ValueError("working explanation references unknown evidence")
            return replace(state, current_best_explanation=obj,
                           uncertainty_summary="; ".join(obj.remaining_uncertainties) or None)
        if operation == "SET_CONCLUSION_REF":
            ref = _ref(value)
            if delta.target_ref_or_path != "conclusion_ref" or ref.ref_id == "":
                raise ValueError("conclusion target mismatch")
            known(ref)
            return replace(state, conclusion_ref=ref)
        if operation == "REGISTER_OBSERVATION":
            obj = _observation(value)
            if obj.observation_id != delta.target_ref_or_path:
                raise ValueError("observation target mismatch")
            if any(ref.ref_id == obj.observation_id for ref in state.observation_refs):
                raise ValueError("observation already registered")
            # These two refs are authored by trusted deterministic ingestion as
            # part of the observation. Result-carried refs must pre-exist exactly.
            put(obj.producer_request_ref, {"producer_request": True})
            if obj.tool_or_service_ref.kind == "ExperimentRunSpec":
                known(obj.tool_or_service_ref)
            else:
                put(obj.tool_or_service_ref, {"tool_or_service": True})
            for ref in (*obj.artifact_refs, *obj.information_refs):
                known(ref)
            ref = _object_ref(obj.observation_id, "ObservationRecord", obj, revision,
                              obj.created_at)
            put(ref, obj)
            return replace(state, observation_refs=state.observation_refs + (ref,))
        if operation == "REGISTER_COMPLETED_EXPERIMENT":
            if not isinstance(value, Mapping):
                raise ValueError("completed experiment registration must be an object")
            result_ref = _ref(value.get("result_ref"))
            if result_ref.ref_id != delta.target_ref_or_path:
                raise ValueError("completed experiment target mismatch")
            experiment_id = value.get("experiment_id")
            if not any(ref.ref_id == experiment_id for ref in state.planned_experiment_refs):
                raise ValueError("experiment was not planned")
            if any(ref.ref_id == result_ref.ref_id for ref in state.completed_experiment_refs):
                raise ValueError("experiment result already registered")
            put(result_ref, value.get("result", value))
            remaining = tuple(ref for ref in state.planned_experiment_refs
                              if ref.ref_id != experiment_id)
            return replace(state, planned_experiment_refs=remaining,
                           completed_experiment_refs=state.completed_experiment_refs + (result_ref,))
        if operation == "REGISTER_SUBTASK_RESULT":
            if not isinstance(value, Mapping):
                raise ValueError("subtask registration must be an object")
            ref = _ref(value.get("result_ref"))
            if ref.ref_id != delta.target_ref_or_path:
                raise ValueError("subtask target mismatch")
            for child in _refs(value.get("observation_refs", ())):
                known(child)
            for artifact in _refs(value.get("artifact_refs", ())):
                known(artifact)
            put(ref, value)
            if any(item.ref_id == ref.ref_id for item in state.delegated_task_refs):
                raise ValueError("subtask result already registered")
            return replace(state, delegated_task_refs=state.delegated_task_refs + (ref,))
        if operation == "REGISTER_ARTIFACT_REF":
            ref = _ref(value)
            if ref.ref_id != delta.target_ref_or_path:
                raise ValueError("artifact target mismatch")
            known(ref)
            if ref in state.artifact_refs:
                return state
            return replace(state, artifact_refs=state.artifact_refs + (ref,))
        if operation == "SET_GENERIC_STATUS":
            if delta.target_ref_or_path != "generic_status":
                raise ValueError("status target mismatch")
            status = TaskStatus(value)
            allowed = {
                TaskStatus.RUNNING: {TaskStatus.WAITING, TaskStatus.READY, TaskStatus.FAILED,
                                     TaskStatus.DONE, TaskStatus.EXHAUSTED,
                                     TaskStatus.CANCELLED},
                TaskStatus.WAITING: {TaskStatus.RUNNING, TaskStatus.FAILED,
                                     TaskStatus.EXHAUSTED, TaskStatus.CANCELLED},
                TaskStatus.READY: {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED},
            }
            if status not in allowed.get(state.generic_status, set()):
                raise ValueError("illegal generic status transition")
            return replace(state, generic_status=status)
        raise ValueError("unregistered RCA operation")


def expected_model_revision(delta: StateDelta) -> int:
    if type(delta.proposed_base_revision) is not int:
        raise ValueError("RCA v0 uses integer revisions")
    return delta.proposed_base_revision


class RcaResultIngestor:
    """Pure result-to-delta mapper; callers still verify and apply the batch."""

    def derive_deltas(self, result: ToolResult | ExperimentResult | SubtaskResult
                      ) -> tuple[StateDelta, ...]:
        if isinstance(result, ToolResult):
            return self._tool(result)
        if isinstance(result, ExperimentResult):
            return self._experiment(result)
        if isinstance(result, SubtaskResult):
            return self._subtask(result)
        raise TypeError("unsupported result type")

    def _tool(self, result: ToolResult) -> tuple[StateDelta, ...]:
        if result.status != "SUCCESS":
            return ()
        created_at = _text(result.provenance.get("created_at"), "result created_at")
        tool_name = _text(result.provenance.get("tool_name"), "tool_name")
        tool_version = _text(result.provenance.get("tool_version"), "tool_version")
        request_ref = InformationRef(result.request_id, "ToolCallRequest",
                                     "industrial-agent-runtime", "v0",
                                     Visibility.AGENT, created_at)
        tool_ref = InformationRef(tool_name, "ToolService", "tep-agent-lab",
                                  tool_version, Visibility.AGENT, created_at)
        observation_id = f"observation:{result.request_id}"
        record = ObservationRecord(
            observation_id=observation_id, producer_request_ref=request_ref,
            tool_or_service_ref=tool_ref, summary=result.structured_output,
            artifact_refs=result.artifact_refs, information_refs=result.information_refs,
            provenance=result.provenance, visibility=Visibility.AGENT,
            created_at=created_at)
        deltas = [StateDelta("REGISTER_OBSERVATION", observation_id, record,
                             "RESULT_INGESTION")]
        deltas.extend(StateDelta("REGISTER_ARTIFACT_REF", ref.ref_id, ref,
                                 "RESULT_INGESTION") for ref in result.artifact_refs)
        return tuple(deltas)

    def _experiment(self, result: ExperimentResult) -> tuple[StateDelta, ...]:
        if result.status not in {"SUCCESS", "OK", "COMPLETED"}:
            return ()
        result_ref = InformationRef(
            f"experiment-result:{result.experiment_id}", "ExperimentResult",
            "tep-agent-lab", "v0", Visibility.AGENT, result.completed_at,
            checksum(result))
        request_ref = InformationRef(
            result.experiment_id, "ExperimentExecutionRequest", "tep-agent-lab",
            "v0", Visibility.AGENT, result.started_at)
        obs_id = f"observation:experiment:{result.experiment_id}"
        observation = ObservationRecord(
            observation_id=obs_id, producer_request_ref=request_ref,
            tool_or_service_ref=result.run_spec_ref,
            summary={"metrics": result.metric_values,
                     "prediction_evaluations": result.prediction_evaluations},
            artifact_refs=result.rollout_artifact_refs,
            information_refs=result.observation_refs,
            provenance={"kind": "ExperimentResult", "result_ref": result_ref},
            visibility=Visibility.AGENT, created_at=result.completed_at)
        deltas = [StateDelta("REGISTER_OBSERVATION", obs_id, observation,
                             "RESULT_INGESTION"),
                  StateDelta("REGISTER_COMPLETED_EXPERIMENT", result_ref.ref_id,
                             {"experiment_id": result.experiment_id,
                              "result_ref": result_ref, "result": result},
                             "RESULT_INGESTION")]
        deltas.extend(StateDelta("REGISTER_ARTIFACT_REF", ref.ref_id, ref,
                                 "RESULT_INGESTION")
                      for ref in result.rollout_artifact_refs)
        return tuple(deltas)

    def _subtask(self, result: SubtaskResult) -> tuple[StateDelta, ...]:
        if result.status not in {"SUCCESS", "DONE", "COMPLETED"}:
            return ()
        result_ref = InformationRef(
            f"subtask-result:{result.subtask_id}", "SubtaskResult",
            "industrial-agent-runtime", "v0", Visibility.AGENT,
            result.trace_ref.created_at, checksum(result))
        compact = {"result_ref": result_ref, "status": result.status,
                   "observation_refs": result.observation_refs,
                   "artifact_refs": result.artifact_refs,
                   "trace_ref": result.trace_ref,
                   "budget_usage": result.budget_usage,
                   "uncertainty_or_confidence": result.uncertainty_or_confidence}
        deltas = [StateDelta("REGISTER_SUBTASK_RESULT", result_ref.ref_id,
                             compact, "RESULT_INGESTION")]
        deltas.extend(StateDelta("REGISTER_ARTIFACT_REF", ref.ref_id, ref,
                                 "RESULT_INGESTION") for ref in result.artifact_refs)
        return tuple(deltas)


def readiness_deficiencies(store: RcaStateStore, *,
                           required_evidence_refs: Sequence[InformationRef] = (),
                           required_artifact_refs: Sequence[InformationRef] = (),
                           require_conclusion: bool = True) -> tuple[str, ...]:
    """Pure consumer verifier helper; it never changes status or state."""
    state = store.state
    issues: list[str] = []
    if require_conclusion and state.conclusion_ref is None:
        issues.append("missing conclusion_ref")
    for label, required, present in (
        ("evidence", required_evidence_refs, state.evidence_link_refs),
        ("artifact", required_artifact_refs, state.artifact_refs),
    ):
        for ref in required:
            try:
                store.resolve(ref)
            except ValueError:
                issues.append(f"unknown required {label} ref: {ref.ref_id}")
                continue
            if ref not in present:
                issues.append(f"missing required {label} ref: {ref.ref_id}")
    return tuple(issues)


def _collect_refs(value: Any) -> tuple[InformationRef, ...]:
    found: list[InformationRef] = []

    def visit(item: Any) -> None:
        if isinstance(item, InformationRef):
            found.append(item)
        elif hasattr(item, "__dataclass_fields__"):
            for name in item.__dataclass_fields__:
                visit(getattr(item, name))
        elif isinstance(item, Mapping):
            for child in item.values():
                visit(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)

    visit(value)
    unique: list[InformationRef] = []
    seen = set()
    for ref in found:
        key = _identity(ref)
        if key not in seen:
            seen.add(key); unique.append(ref)
    return tuple(unique)


def _decode_state(value: Mapping[str, Any]) -> RcaState:
    data = dict(value)
    data["incident_ref"] = _ref(data["incident_ref"])
    for name in ("current_time_ref", "safety_state_ref", "conclusion_ref"):
        if data.get(name) is not None:
            data[name] = _ref(data[name])
    for name in ("hypothesis_refs", "observation_refs", "evidence_link_refs",
                 "planned_experiment_refs", "completed_experiment_refs",
                 "delegated_task_refs", "artifact_refs"):
        data[name] = _refs(data[name])
    data["open_questions"] = tuple(_open_question(item) for item in data["open_questions"])
    if data.get("current_best_explanation") is not None:
        data["current_best_explanation"] = _working_explanation(data["current_best_explanation"])
    return RcaState(**data)
