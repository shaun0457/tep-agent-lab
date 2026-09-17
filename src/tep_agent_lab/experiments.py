"""Independent C3 prediction evaluation and content-based experiment identity.

Ref fields are opaque reference IDs, resolved by C1/runtime integration. This
module neither defines InformationRef nor executes/compiles simulator requests.
Feature extraction is separate: callers supply a deterministically measured feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any, Iterable

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
    return type(value) in (int, float) and math.isfinite(value)


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
    if isinstance(value, dict):
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
