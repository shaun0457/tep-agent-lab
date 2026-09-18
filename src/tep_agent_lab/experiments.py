"""Independent C3 prediction evaluation and content-based experiment identity.

New object references use runtime InformationRef; Prediction retains its original
opaque reference IDs, resolved by C1 integration. This module neither defines
InformationRef nor executes/compiles simulator requests.
Feature extraction is separate: callers supply a deterministically measured feature.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
import math
from typing import Any, Iterable

from industrial_agent_runtime import InformationRef, StateDelta, Visibility, to_jsonable
from industrial_agent_runtime.serialization import freeze_json

from .persistence import content_checksum


class Feature(StrEnum):
    DIRECTION = "DIRECTION"
    DELTA = "DELTA"
    PEAK = "PEAK"
    MINIMUM = "MINIMUM"
    LAG = "LAG"
    ONSET_TIME = "ONSET_TIME"
    SETTLING_TIME = "SETTLING_TIME"
    STEADY_STATE_RANGE = "STEADY_STATE_RANGE"
    INTEGRATED_ERROR = "INTEGRATED_ERROR"
    CORRELATION = "CORRELATION"
    TRAJECTORY_DISTANCE = "TRAJECTORY_DISTANCE"
    EVENT_OR_SHUTDOWN = "EVENT_OR_SHUTDOWN"
    QUALITATIVE_UNSCORED = "QUALITATIVE_UNSCORED"


class MatchStatus(StrEnum):
    MATCH = "MATCH"
    CONTRADICT = "CONTRADICT"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNSCORED = "UNSCORED"


def _finite(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Prediction:
    prediction_id: str
    hypothesis_ref: str
    variable_ref: str
    feature: Feature
    window_or_horizon: float
    expected_value_or_range: float | tuple[float, float] | str | bool
    tolerance: float = 0.0
    preprocessing_ref: str | None = None
    metric_ref: str | None = None
    conditions: tuple[str, ...] = ()

    def __post_init__(self):
        if any(not isinstance(x, str) or not x for x in
               (self.prediction_id, self.hypothesis_ref, self.variable_ref)):
            raise ValueError("Prediction requires nonempty reference identities")
        object.__setattr__(self, "feature", Feature(self.feature))
        if not isinstance(self.conditions, (tuple, list)):
            raise ValueError("Conditions must be a sequence of strings")
        object.__setattr__(self, "conditions", tuple(self.conditions))
        if any(not isinstance(item, str) or not item for item in self.conditions):
            raise ValueError("Conditions must be nonempty immutable strings")
        for reference in (self.preprocessing_ref, self.metric_ref):
            if reference is not None and (not isinstance(reference, str) or not reference):
                raise ValueError("Optional refs must be nonempty reference identities")
        if not _finite(self.window_or_horizon) or self.window_or_horizon <= 0:
            raise ValueError("Prediction horizon must be positive and finite")
        if not _finite(self.tolerance) or self.tolerance < 0:
            raise ValueError("Tolerance must be finite and nonnegative")
        expected = self.expected_value_or_range
        if self.feature == Feature.QUALITATIVE_UNSCORED:
            valid = isinstance(expected, str) and bool(expected)
        elif self.feature == Feature.DIRECTION:
            valid = isinstance(expected, str) and expected in {"INCREASE", "DECREASE", "UNCHANGED"}
        elif self.feature == Feature.EVENT_OR_SHUTDOWN:
            valid = isinstance(expected, (str, bool))
        else:
            valid = _finite(expected) or (
                isinstance(expected, tuple) and len(expected) == 2
                and all(_finite(x) for x in expected) and expected[0] <= expected[1])
        if not valid:
            raise ValueError("Expected value does not match prediction feature")


@dataclass(frozen=True)
class PredictionEvaluation:
    prediction_ref: str
    observed_feature: float | str | bool | None
    match_status: MatchStatus
    metric_distance: float | None = None

    def __post_init__(self):
        _text(self.prediction_ref)
        object.__setattr__(self, "match_status", MatchStatus(self.match_status))
        if self.observed_feature is not None and not (
            isinstance(self.observed_feature, (str, bool)) or _finite(self.observed_feature)
        ):
            raise ValueError("Observed feature must be an immutable finite scalar")
        if self.metric_distance is not None and (
            not _finite(self.metric_distance) or self.metric_distance < 0
        ):
            raise ValueError("Metric distance must be finite and nonnegative")


def evaluate_prediction(prediction: Prediction, observed_feature: Any,
                        *, supported: bool = True) -> PredictionEvaluation:
    """Compare already extracted features; never guess a missing metric/extractor.

    Numeric distance is distance to the closed expected interval, in the feature's
    units. A scalar is a zero-width interval. Missing/invalid results are inconclusive.
    Direction/event inputs are categorical features, not raw trajectory guesses.
    """
    if not supported or prediction.feature == Feature.QUALITATIVE_UNSCORED:
        return PredictionEvaluation(prediction.prediction_id, None, MatchStatus.UNSCORED)
    if observed_feature is None:
        return PredictionEvaluation(prediction.prediction_id, None, MatchStatus.INCONCLUSIVE)
    expected = prediction.expected_value_or_range
    if prediction.feature in (Feature.DIRECTION, Feature.EVENT_OR_SHUTDOWN):
        valid = type(observed_feature) is type(expected)
        if prediction.feature == Feature.DIRECTION:
            valid = valid and observed_feature in {"INCREASE", "DECREASE", "UNCHANGED"}
        if not valid:
            return PredictionEvaluation(prediction.prediction_id, None, MatchStatus.INCONCLUSIVE)
        status = MatchStatus.MATCH if observed_feature == expected else MatchStatus.CONTRADICT
        return PredictionEvaluation(prediction.prediction_id, observed_feature, status)
    if not _finite(observed_feature):
        return PredictionEvaluation(prediction.prediction_id, None, MatchStatus.INCONCLUSIVE)
    low, high = expected if isinstance(expected, tuple) else (expected, expected)
    distance = max(low - observed_feature, observed_feature - high, 0.0)
    status = (MatchStatus.MATCH
              if low - prediction.tolerance <= observed_feature <= high + prediction.tolerance
              else MatchStatus.CONTRADICT)
    return PredictionEvaluation(prediction.prediction_id, observed_feature, status, distance)


def _normalized(value: Any) -> Any:
    # Equal JSON numeric values have the same identity, including signed zero.
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Experiment identity cannot contain non-finite numbers")
        return int(value) if value.is_integer() else value
    if isinstance(value, Mapping):
        return {key: _normalized(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalized(child) for child in value]
    return value


def canonical_experiment_key(*, parent_state_content_checksum: str,
                             resolved_interventions: Any,
                             tool_config_versions: dict[str, Any],
                             horizon: float, seed_policy: Any,
                             metric_scorer_versions: dict[str, Any],
                             preprocessing: Any = None) -> str:
    """Hash resolved content, never an opaque snapshot/run/branch ID.

    The caller supplies horizon in the resolved tool contract's canonical unit;
    resolved tool/config versions bind that unit and intervention semantics.
    Intervention schedule order is retained because order can affect execution.
    """
    if not isinstance(parent_state_content_checksum, str) or not parent_state_content_checksum:
        raise ValueError("Parent state content checksum is required")
    if not _finite(horizon) or horizon <= 0:
        raise ValueError("Horizon must be positive and finite")
    if not tool_config_versions or not metric_scorer_versions:
        raise ValueError("Resolved tool/config and metric/scorer versions are required")
    return content_checksum(_normalized({
        "identity_version": "v0", "parent_state_content_checksum": parent_state_content_checksum,
        "resolved_interventions": resolved_interventions,
        "tool_config_versions": tool_config_versions, "horizon": horizon,
        "seed_policy": seed_policy, "metric_scorer_versions": metric_scorer_versions,
        "preprocessing": preprocessing,
    }))


class DuplicateExperiment(ValueError):
    """Lab pre-execution policy found an exact content duplicate."""


def reject_exact_duplicate(candidate_key: str, prior_keys: Iterable[str]) -> None:
    if candidate_key in prior_keys:
        raise DuplicateExperiment(candidate_key)


class HypothesisStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    SUPPORTED = "SUPPORTED"
    WEAKENED = "WEAKENED"
    REJECTED = "REJECTED"
    UNTESTABLE = "UNTESTABLE"
    UNRESOLVED = "UNRESOLVED"


class HypothesisType(StrEnum):
    ROOT_CAUSE = "ROOT_CAUSE"
    MECHANISM = "MECHANISM"
    PROCESS_RELATION = "PROCESS_RELATION"
    RECOVERY_EFFECT = "RECOVERY_EFFECT"
    HAZOP_CAUSE = "HAZOP_CAUSE"
    RESEARCH_IDEA = "RESEARCH_IDEA"


class EvidenceRelation(StrEnum):
    SUPPORT = "SUPPORT"
    CONTRADICT = "CONTRADICT"
    CONTEXT = "CONTEXT"
    NEUTRAL = "NEUTRAL"


class ExperimentType(StrEnum):
    COUNTERFACTUAL_ROLLOUT = "COUNTERFACTUAL_ROLLOUT"
    PARAMETER_SWEEP = "PARAMETER_SWEEP"
    SENSITIVITY_ANALYSIS = "SENSITIVITY_ANALYSIS"
    SIGNAL_ANALYSIS = "SIGNAL_ANALYSIS"
    RULE_VALIDATION = "RULE_VALIDATION"
    RECOVERY_COMPARISON = "RECOVERY_COMPARISON"
    AUTORESEARCH_TRIAL = "AUTORESEARCH_TRIAL"


def _text(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text")


def _ref(value: Any) -> None:
    if not isinstance(value, InformationRef):
        raise ValueError("Expected runtime InformationRef")
    if value.visibility == Visibility.EVALUATOR:
        raise ValueError("Evaluator refs cannot enter investigation contracts")
    if value.checksum is not None:
        _text(value.checksum)


def _json(value: Any) -> Any:
    """Detach JSON metadata and reject nested evaluator reference envelopes."""
    clean = to_jsonable(value)

    def check(item: Any) -> None:
        if isinstance(item, dict):
            if "ref_id" in item and item.get("visibility") == "EVALUATOR":
                raise ValueError("Evaluator reference embedded in metadata")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)

    check(clean)
    return freeze_json(clean)


def _sequence(value: Any, kind: type) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise ValueError("Expected a sequence")
    for item in value:
        if not isinstance(item, kind):
            raise ValueError(f"Expected {kind.__name__} entries")
        if kind is str:
            _text(item)
        elif kind is InformationRef:
            _ref(item)
    return tuple(value)


def _numbers(value: Mapping, *, nonnegative: bool) -> Any:
    if not isinstance(value, Mapping):
        raise ValueError("Expected numeric mapping")
    for key, number in value.items():
        _text(key)
        if not _finite(number) or (nonnegative and number < 0):
            raise ValueError("Expected finite numeric values")
    return _json(value)


def _versions(value: Mapping) -> Any:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("Exact version mapping is required")
    for key, version in value.items():
        _text(key)
        _text(version)
    return _json(value)


def _revision(value: Any) -> None:
    if not ((type(value) is int and value >= 0)
            or (isinstance(value, str) and value.strip())):
        raise ValueError("Expected an exact nonnegative integer or opaque revision")


class _Contract:
    """Shared validation of canonical string/ref fields, without state mutation."""

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            annotation = str(item.type)
            if annotation == "str":
                _text(value)
            elif annotation == "str | None" and value is not None:
                _text(value)
            elif annotation == "InformationRef":
                _ref(value)
            elif annotation == "InformationRef | None" and value is not None:
                _ref(value)
            elif annotation in ("tuple[InformationRef, ...]", "tuple[str, ...]"):
                kind = InformationRef if "InformationRef" in annotation else str
                object.__setattr__(self, item.name, _sequence(value, kind))


@dataclass(frozen=True, kw_only=True)
class Hypothesis(_Contract):
    hypothesis_id: str
    investigation_id: str
    claim: str
    hypothesis_type: HypothesisType
    scope_refs: tuple[InformationRef, ...]
    status: HypothesisStatus
    supporting_evidence_link_refs: tuple[InformationRef, ...]
    contradicting_evidence_link_refs: tuple[InformationRef, ...]
    experiment_refs: tuple[InformationRef, ...]
    assumptions: tuple[str, ...]
    falsification_prediction_refs: tuple[InformationRef, ...]
    created_by: str
    created_at: str
    last_updated_revision: int | str
    prior_weight: float | None = None
    current_rank_or_score: float | None = None

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "hypothesis_type", HypothesisType(self.hypothesis_type))
        object.__setattr__(self, "status", HypothesisStatus(self.status))
        _revision(self.last_updated_revision)
        for number in (self.prior_weight, self.current_rank_or_score):
            if number is not None and not _finite(number):
                raise ValueError("Hypothesis weights/scores must be finite")


@dataclass(frozen=True, kw_only=True)
class HypothesisEvidenceLink(_Contract):
    hypothesis_ref: InformationRef
    observation_ref: InformationRef
    relation: EvidenceRelation
    reason_summary: str
    producer: str
    strength: float | None = None

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "relation", EvidenceRelation(self.relation))
        if self.strength is not None and not _finite(self.strength):
            raise ValueError("Evidence strength must be finite")


@dataclass(frozen=True, kw_only=True)
class ExperimentProposal(_Contract):
    experiment_id: str
    investigation_id: str
    goal: str
    hypothesis_refs: tuple[InformationRef, ...]
    experiment_type: ExperimentType
    rationale: str
    discriminating_question: str
    prediction_refs: tuple[InformationRef, ...]
    scenario_or_intervention: Any
    required_input_refs: tuple[InformationRef, ...]
    requested_tools: tuple[str, ...]
    seed_policy: Any
    horizon: float
    metrics: tuple[str, ...]
    budget_request: Mapping[str, float]
    safety_constraints: tuple[str, ...]
    status: str

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "experiment_type", ExperimentType(self.experiment_type))
        if not _finite(self.horizon) or self.horizon <= 0:
            raise ValueError("Experiment horizon must be positive and finite")
        for name in ("scenario_or_intervention", "seed_policy"):
            object.__setattr__(self, name, _json(getattr(self, name)))
        object.__setattr__(self, "budget_request", _numbers(self.budget_request, nonnegative=True))


@dataclass(frozen=True, kw_only=True)
class ExperimentRunSpec(_Contract):
    run_spec_id: str
    experiment_id: str
    parent_state_content_checksum: str
    branch_or_snapshot_ref: InformationRef
    tool_config_versions: Mapping[str, str]
    resolved_interventions: Any
    seeds_or_seed_policy: Any
    horizon: float
    metric_scorer_versions: Mapping[str, str]
    resource_limits: Mapping[str, float]
    canonical_experiment_key: str
    preprocessing: Any = None

    def __post_init__(self):
        super().__post_init__()
        for name in ("tool_config_versions", "metric_scorer_versions"):
            object.__setattr__(self, name, _versions(getattr(self, name)))
        for name in ("resolved_interventions", "seeds_or_seed_policy", "preprocessing"):
            object.__setattr__(self, name, _json(getattr(self, name)))
        object.__setattr__(self, "resource_limits", _numbers(self.resource_limits, nonnegative=True))
        expected = canonical_experiment_key(
            parent_state_content_checksum=self.parent_state_content_checksum,
            resolved_interventions=self.resolved_interventions,
            tool_config_versions=self.tool_config_versions, horizon=self.horizon,
            seed_policy=self.seeds_or_seed_policy,
            metric_scorer_versions=self.metric_scorer_versions, preprocessing=self.preprocessing)
        if self.canonical_experiment_key != expected:
            raise ValueError("Run spec canonical key does not match its frozen content")


@dataclass(frozen=True, kw_only=True)
class ExperimentResult(_Contract):
    experiment_id: str
    run_spec_ref: InformationRef
    status: str
    metric_values: Mapping[str, float]
    prediction_evaluations: tuple[PredictionEvaluation, ...]
    observation_refs: tuple[InformationRef, ...]
    rollout_artifact_refs: tuple[InformationRef, ...]
    cost_usage: Mapping[str, float]
    started_at: str
    completed_at: str
    safety_summary_ref: InformationRef | None = None
    failure_class: str | None = None

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "metric_values", _numbers(self.metric_values, nonnegative=False))
        object.__setattr__(self, "cost_usage", _numbers(self.cost_usage, nonnegative=True))
        object.__setattr__(self, "prediction_evaluations",
                           _sequence(self.prediction_evaluations, PredictionEvaluation))


def _question(value: Mapping[str, Any]) -> Any:
    required = {"question_id", "question", "why_it_matters", "related_hypotheses",
                "resolvable_by", "priority", "status"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("OpenQuestion must match the investigation-state schema")
    for name in ("question_id", "question", "why_it_matters", "status"):
        _text(value[name])
    if value["resolvable_by"] not in ("TOOL", "EXPERIMENT", "SUBTASK", "EXTERNAL_KNOWLEDGE", "HUMAN"):
        raise ValueError("Invalid OpenQuestion resolvable_by")
    related = value["related_hypotheses"]
    if not isinstance(related, (tuple, list)):
        raise ValueError("Question hypothesis refs must be a sequence")
    for reference in related:
        # Frozen question payloads retain the complete runtime ref envelope.
        if isinstance(reference, Mapping):
            reference = InformationRef(**reference)
        _ref(reference)
    if not (isinstance(value["priority"], str) and value["priority"].strip()) and not _finite(value["priority"]):
        raise ValueError("Question priority must be an immutable text/numeric scalar")
    return _json(value)


@dataclass(frozen=True, kw_only=True)
class ExperimentInterpretation(_Contract):
    interpretation_id: str
    experiment_ref: InformationRef
    proposed_evidence_links: tuple[HypothesisEvidenceLink, ...]
    hypothesis_updates: tuple[Hypothesis, ...]
    conclusion_summary: str
    residual_uncertainty: str
    next_questions: tuple[Mapping[str, Any], ...]

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "proposed_evidence_links",
                           _sequence(self.proposed_evidence_links, HypothesisEvidenceLink))
        object.__setattr__(self, "hypothesis_updates", _sequence(self.hypothesis_updates, Hypothesis))
        if not isinstance(self.next_questions, (tuple, list)):
            raise ValueError("Expected OpenQuestion sequence")
        object.__setattr__(self, "next_questions", tuple(_question(q) for q in self.next_questions))


def interpretation_to_deltas(interpretation: ExperimentInterpretation, *,
                             base_revision: int | str, producer: str = "MODEL"
                             ) -> tuple[StateDelta, ...]:
    """Pure proposal mapping; only C1 apply_batch can persist or reject it.

    Ref existence, ownership and stale/atomic checks remain at that boundary.
    This function never rewrites the revision to the consumer's current state.
    """
    if not isinstance(interpretation, ExperimentInterpretation):
        raise TypeError("Expected ExperimentInterpretation")
    _revision(base_revision)
    if producer != "MODEL":
        raise ValueError("Experiment interpretation is a MODEL proposal")
    values: list[tuple[str, str, Any]] = []
    for link in interpretation.proposed_evidence_links:
        if link.producer != producer:
            raise ValueError("Evidence producer must match the interpretation proposal")
        values.append(("ADD_EVIDENCE_LINK", link.hypothesis_ref.ref_id, link))
    for hypothesis in interpretation.hypothesis_updates:
        values.append(("UPDATE_HYPOTHESIS", hypothesis.hypothesis_id, hypothesis))
    for question in interpretation.next_questions:
        values.append(("ADD_OPEN_QUESTION", question["question_id"], question))
    values.append(("UPDATE_WORKING_EXPLANATION", "current_best_explanation", {
        "conclusion_summary": interpretation.conclusion_summary,
        "residual_uncertainty": interpretation.residual_uncertainty}))
    values.append(("ADD_EXPERIMENT_INTERPRETATION", interpretation.interpretation_id, interpretation))
    return tuple(StateDelta(operation, target, value, producer, base_revision)
                 for operation, target, value in values)
