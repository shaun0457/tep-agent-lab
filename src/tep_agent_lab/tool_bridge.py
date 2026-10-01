"""C5 minimal Tool Bridge v0 (docs/specs/tool-bridge-v0.md, "C5 implementation contract").

Three Agent-visible COMPUTE analysis adapters over already existing, verified lab
artifacts: ``compute_response_features``, ``analyze_cross_correlation``, and
``compare_trajectories``. They never run, fork, or step a simulator.

The bridge is not an authorization layer. Generic schema/budget/side-effect gating
(B2) and result verification (B3) stay in the runtime; ``AnalysisToolBridge`` only
supplies the lab hooks for its own tools (``validate_request``, ``execute``,
``verify_result``), and ``BridgedToolSurface`` composes it with the unchanged C4
surface for one runtime registry. Results are observations, never evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
from typing import Any, NoReturn

import numpy
from industrial_agent_runtime import (
    GateDecision, GatePolicy, InformationRef, SideEffectClass, StateDelta, Task,
    ToolCallRequest, ToolResult, ToolSpec, Visibility, checksum, to_jsonable,
)
from tep_sim import REGISTRY

from .experiments import Feature
from .tep_world import (ARTIFACT_OWNER, ARTIFACT_VERSION, ArtifactStore, WorldError,
                        leakage_findings)
from .tool_surface import (INGESTIBLE_OPERATIONS, SUCCESS, BlindRcaToolSurface,
                           ResultInvariantError, _obj, _safe_reason, _VARIABLE)

BRIDGE_VERSION = "tep-agent-lab.tool-bridge/v0"
BRIDGE_POLICY_VERSION = "tep-agent-lab.analysis-bridge-policy/v0"
BRIDGED_POLICY_VERSION = "tep-agent-lab.blind-rca-analysis-policy/v0"
NUMERICAL_SEMANTICS_VERSION = "tep-agent-lab.bridge-numerics/v0"
TRAJECTORY_METRICS_VERSION = "tep-agent-lab.trajectory-metrics/v0"
IMPLEMENTATION_ID = "tep-agent-lab.bridge.numpy-local/v0"
ANALYSIS_POLICY_TAG = "tep.agent_visible_analysis"
# Float representation tolerance of hour-encoded timestamps; not an engineering threshold.
TIME_REPRESENTATION_TOLERANCE_SECONDS = 1e-6
CORRELATION_TIE_DECIMALS = 12
# A (preprocessed) series or lag overlap is degenerate (constant) when its range is at
# most this fraction of the raw window's largest magnitude: floating-point precision,
# not an engineering threshold (an exact `== 0` test misses non-dyadic constants).
DEGENERACY_RELATIVE_TOLERANCE = 1e-9
# Seconds magnitudes above 2**53 cannot be represented exactly.
MAX_ABS_SECONDS = 2 ** 53
_LOG = logging.getLogger(__name__)
INPUT_KINDS = frozenset({"HistoryWindowArtifact", "RolloutTelemetryArtifact"})
CURVE_KIND = "CrossCorrelationCurveArtifact"
LAG_CONVENTION = ("r(L) = corr(x(t), y(t + L)); L > 0 means y follows x by L seconds "
                  "(X_LEADS_Y); L < 0 means x follows y (Y_LEADS_X)")
INTERPRETATION_NOTE = ("correlation and lag are observations; they do not establish "
                       "causality or a physical mechanism")

# Features with a frozen deterministic definition, and the parameters each requires.
FEATURE_PARAMETERS: Mapping[str, frozenset[str]] = {
    Feature.DELTA: frozenset(), Feature.DIRECTION: frozenset({"deadband"}),
    Feature.PEAK: frozenset(), Feature.MINIMUM: frozenset(),
    Feature.ONSET_TIME: frozenset({"threshold"}), Feature.STEADY_STATE_RANGE: frozenset(),
    Feature.INTEGRATED_ERROR: frozenset({"integrand"}),
}
BASELINE_FEATURES = frozenset({Feature.DELTA, Feature.DIRECTION, Feature.ONSET_TIME,
                               Feature.INTEGRATED_ERROR})
# Listed by the spec but not definable without a missing public contract (v0).
UNSUPPORTED_FEATURES: Mapping[str, str] = {
    Feature.SETTLING_TIME: "SETTLING_TIME has no frozen settling-target/confirmation "
                           "contract in v0",
    Feature.LAG: "LAG needs a second signal; use analyze_cross_correlation",
    Feature.TRAJECTORY_DISTANCE: "TRAJECTORY_DISTANCE needs a reference trajectory; "
                                 "use compare_trajectories",
    Feature.CORRELATION: "CORRELATION needs a second signal; use analyze_cross_correlation",
    Feature.EVENT_OR_SHUTDOWN: "EVENT_OR_SHUTDOWN is reported by the run_rollout safety "
                               "summary, not by response features",
    Feature.QUALITATIVE_UNSCORED: "QUALITATIVE_UNSCORED is by definition not computable",
}
CORRELATION_PREPROCESSING = ("NONE", "FIRST_DIFFERENCE", "LINEAR_DETREND")
CORRELATION_SELECTION = ("MAX_CORRELATION", "MAX_ABS_CORRELATION")
ALIGNMENT_POLICIES = ("EXACT_TIMESTAMPS", "ELAPSED_FROM_WINDOW_START")
TRAJECTORY_PREPROCESSING = ("NONE", "REMOVE_INITIAL_VALUE")
TRAJECTORY_METRICS = ("MEAN_ERROR", "MEAN_ABSOLUTE_ERROR", "ROOT_MEAN_SQUARE_ERROR",
                      "MAX_ABSOLUTE_ERROR", "FINAL_ERROR")


@dataclass(frozen=True)
class BridgeLimits:
    """Model-visible bounds of the analysis bridge (lab policy)."""

    max_variables: int = 8
    max_lags: int = 241
    max_lag_seconds: int = 4 * 3600
    max_records: int = 20000

    def __post_init__(self) -> None:
        for name in ("max_variables", "max_lags", "max_lag_seconds", "max_records"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")


def bridge_tool_version(name: str) -> str:
    return f"{BRIDGE_VERSION}/{name}@1"


def _fail(status: str, reason: str) -> NoReturn:
    raise WorldError(status, reason)


# -- schemas (runtime G0 subset) ------------------------------------------------------
_NUMBER = {"type": "number"}
_TEXT = {"type": "string"}
_INPUT_REF = _obj({"ref_id": {"type": "string", "pattern": r"^tep-artifact-[0-9]{6,}$"},
                   "kind": {"enum": sorted(INPUT_KINDS)},
                   "owner": {"const": ARTIFACT_OWNER},
                   "version": {"const": ARTIFACT_VERSION},
                   "visibility": {"const": "AGENT"},
                   "created_at": {"type": "string", "minLength": 1, "maxLength": 64},
                   "checksum": {"type": "string", "pattern": r"^[0-9a-f]{64}$"}},
                  ("ref_id", "kind", "owner", "version", "visibility", "created_at",
                   "checksum"))
_OUTPUT_REF = _obj({"ref_id": _TEXT, "kind": _TEXT, "owner": _TEXT, "version": _TEXT,
                    "visibility": {"const": "AGENT"}, "created_at": _TEXT,
                    "checksum": _TEXT},
                   ("ref_id", "kind", "owner", "version", "visibility", "created_at",
                    "checksum"))
_WINDOW = _obj({"start_hours": {"type": "number", "minimum": 0},
                "end_hours": {"type": "number", "minimum": 0}},
               ("start_hours", "end_hours"))
_WINDOW_OUT = _obj({"start_hours": _NUMBER, "end_hours": _NUMBER,
                    "samples": {"type": "integer", "minimum": 0}},
                   ("start_hours", "end_hours", "samples"))
_NULL_NUMBER = {"type": ["number", "null"]}
_NULL_INTEGER = {"type": ["integer", "null"]}


@dataclass(frozen=True)
class _Series:
    """Checksum-verified, strictly time-ordered columns of one input artifact."""

    ref: InformationRef
    seconds: numpy.ndarray
    values: Mapping[str, numpy.ndarray]


@dataclass(frozen=True)
class _Window:
    """Selected samples; ``interval`` is their uniform step (None for one sample)."""

    start: int
    end: int
    indices: numpy.ndarray
    interval: int | None

    def describe(self) -> dict[str, Any]:
        return {"start_hours": self.start / 3600, "end_hours": self.end / 3600,
                "samples": int(len(self.indices))}


@dataclass(frozen=True)
class _BridgeTool:
    spec: ToolSpec
    prepare: Callable[[Mapping[str, Any]], Any]
    run: Callable[[Any, ToolCallRequest, str], tuple[Any, tuple[InformationRef, ...],
                                                     Mapping[str, Any]]]
    input_fields: tuple[str, ...]
    output_kind: str | None = None

    def input_refs(self, arguments: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        return tuple(arguments[name] for name in self.input_fields)


def _seconds(hours: Any, what: str, code: str = "INVALID_REQUEST") -> int:
    if type(hours) not in (int, float) or not math.isfinite(hours):
        _fail(code, f"{what} must be a finite number of hours")
    value = hours * 3600
    if abs(value) > MAX_ABS_SECONDS:
        _fail(code, f"{what} is outside the representable time range")
    whole = round(value)
    if abs(value - whole) > TIME_REPRESENTATION_TOLERANCE_SECONDS:
        _fail(code, f"{what} must be an integral number of seconds")
    return int(whole)


def _finite_numbers(value: Any) -> bool:
    value = to_jsonable(value)
    if isinstance(value, dict):
        return all(_finite_numbers(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_numbers(item) for item in value)
    if isinstance(value, float):
        return math.isfinite(value)
    return True


def _unit(variable: str) -> str | None:
    return REGISTRY[variable].unit or None


class AnalysisToolBridge:
    """First-RCA analysis bridge: COMPUTE ToolSpecs plus their lab runtime hooks."""

    def __init__(self, artifacts: ArtifactStore, *,
                 reference_revision: Callable[[], Any],
                 limits: BridgeLimits | None = None,
                 clock: Callable[[], str] | None = None) -> None:
        if not isinstance(artifacts, ArtifactStore):
            raise TypeError("ArtifactStore required")
        if not callable(reference_revision):
            # Required: without it the reference-unchanged invariant would be vacuous.
            raise TypeError("reference_revision guard required")
        self.artifacts = artifacts
        self.limits = limits or BridgeLimits()
        self._reference_revision = reference_revision
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._audit: dict[str, tuple[Any, Any, str]] = {}
        # The imported library is the implementation: its version is recorded, not assumed.
        self.implementation = {"implementation_id": IMPLEMENTATION_ID,
                               "library_name": "numpy",
                               "library_version": numpy.__version__,
                               "numerical_semantics_version": NUMERICAL_SEMANTICS_VERSION}
        self._tools = {tool.spec.name: tool for tool in self._build()}

    # -- registry -----------------------------------------------------------------------
    def tool_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name].spec for name in sorted(self._tools))

    def handles(self, name: Any) -> bool:
        return name in self._tools

    def _registered(self, name: str, spec: Any) -> _BridgeTool | None:
        tool = self._tools.get(name)
        if tool is None or not isinstance(spec, ToolSpec) or checksum(spec) != checksum(tool.spec):
            return None
        return tool

    def _revision(self) -> Any:
        return self._reference_revision()

    # -- consumer pre-execution validation (after runtime G0-G3) ------------------------
    def validate_request(self, request: ToolCallRequest, spec: ToolSpec, task: Task,
                         expected_state_revision: Any,
                         budget_usage: Mapping[str, Any]) -> GateDecision:
        def decide(decision: str, code: str, reason: str) -> GateDecision:
            return GateDecision(request.request_id, decision, "CONSUMER", code,
                                _safe_reason(reason), BRIDGE_POLICY_VERSION,
                                expected_state_revision=expected_state_revision)

        tool = self._registered(request.tool_name, spec)
        if tool is None:
            return decide("DENY", "POLICY_DENIED", "tool is not a registered bridge tool")
        if spec.side_effect_class != SideEffectClass.COMPUTE:
            return decide("DENY", "POLICY_DENIED", "bridge analysis tools are COMPUTE only")
        try:
            tool.prepare(request.arguments)
        except WorldError as exc:
            return decide("DENY", exc.status, exc.reason)
        except Exception:
            _LOG.exception("bridge validation error for %s", request.tool_name)
            return decide("DENY", "INVALID_REQUEST", "request could not be validated")
        return decide("ALLOW", "VALID", "request satisfies the analysis bridge contract")

    # -- executor -----------------------------------------------------------------------
    def execute(self, request: ToolCallRequest, spec: ToolSpec) -> ToolResult:
        created_at = self._clock()
        provenance: dict[str, Any] = {
            "tool_name": request.tool_name, "tool_version": bridge_tool_version(request.tool_name),
            "bridge_version": BRIDGE_VERSION, "request_id": request.request_id,
            "created_at": created_at, "side_effect_class": SideEffectClass.COMPUTE.value,
            "implementation": dict(self.implementation)}
        tool = self._registered(request.tool_name, spec)
        before = self._revision()
        output, refs, status, error = None, (), SUCCESS, None
        try:
            if tool is None:
                _fail("POLICY_DENIED", "tool is not a registered bridge tool")
            plan = tool.prepare(request.arguments)  # artifacts are re-verified at dispatch
            output, refs, extra = tool.run(plan, request, created_at)
            provenance.update(extra)
        except WorldError as exc:
            status, error = exc.status, _safe_reason(exc.reason)
        except Exception:
            _LOG.exception("bridge execution error for %s", request.tool_name)
            status, error = "ANALYSIS_FAILED", "internal analysis error"
        if status != SUCCESS:
            output, refs = {"failure": status, "reason": error}, ()
        after = self._revision()
        if after != before and status == SUCCESS:
            status, error = "STALE_STATE", "reference world changed during execution"
            output, refs = {"failure": status, "reason": error}, ()
        result = ToolResult(request.request_id, status, output, {}, provenance,
                            artifact_refs=refs, error=error)
        self._audit[request.request_id] = (before, after, checksum(result))
        return result

    # -- consumer post-execution invariants (inside runtime B3) -------------------------
    def check_result(self, result: ToolResult, request: ToolCallRequest, spec: ToolSpec,
                     deltas: Sequence[StateDelta]) -> None:
        def fail(reason: str):
            raise ResultInvariantError(reason)

        tool = self._registered(request.tool_name, spec)
        if tool is None:
            fail("result is not from a registered bridge tool")
        if not isinstance(result, ToolResult) or result.status != SUCCESS:
            fail("only successful tool results are ingestible")
        audit = self._audit.get(request.request_id)
        if audit is None or audit[2] != checksum(result):
            fail("result was not produced unmodified by this bridge's executor")
        if audit[0] != audit[1] or audit[1] != self._revision():
            fail("reference world changed across execution")
        provenance = result.provenance
        for name, expected in (("tool_name", request.tool_name),
                               ("tool_version", bridge_tool_version(request.tool_name)),
                               ("bridge_version", BRIDGE_VERSION),
                               ("request_id", request.request_id),
                               ("side_effect_class", SideEffectClass.COMPUTE.value)):
            if provenance.get(name) != expected:
                fail(f"provenance {name} mismatch")
        if to_jsonable(provenance.get("implementation")) != self.implementation:
            fail("provenance implementation differs from the dispatched bridge")
        if not isinstance(provenance.get("created_at"), str):
            fail("provenance created_at missing")
        envelopes = tool.input_refs(request.arguments)
        expected_inputs = [{"artifact_id": ref["ref_id"], "kind": ref["kind"],
                            "checksum": ref["checksum"]} for ref in envelopes]
        if to_jsonable(provenance.get("inputs")) != to_jsonable(expected_inputs):
            fail("provenance inputs differ from the request's artifacts")
        for envelope in envelopes:
            ref = InformationRef(**dict(envelope))
            if ref.visibility != Visibility.AGENT or not self.artifacts.verify(ref):
                fail("input artifact is unknown, hidden, or corrupted")
        if dict(result.actual_budget_draw):
            fail("COMPUTE analysis draws no budget dimension")
        findings = leakage_findings({"output": result.structured_output,
                                     "provenance": provenance, "error": result.error,
                                     "refs": [*result.artifact_refs, *result.information_refs]})
        if findings:
            fail("hidden vocabulary in Agent-visible result: " + findings[0])
        if result.information_refs:
            fail("the bridge issues no information refs")
        output = result.structured_output
        if not isinstance(output, Mapping):
            fail("structured output must be an object")
        for ref in result.artifact_refs:
            if (ref.visibility != Visibility.AGENT or ref.kind != tool.output_kind
                    or not self.artifacts.verify(ref)):
                fail("artifact ref is unknown, hidden, corrupted, or of the wrong kind")
        embedded = output.get("curve_artifact_ref") if isinstance(output, Mapping) else None
        if [to_jsonable(ref) for ref in result.artifact_refs] != (
                [to_jsonable(embedded)] if embedded else []):
            fail("declared artifact refs differ from the output artifact ref")
        for name in tool.input_fields:
            if to_jsonable(output.get(name)) != to_jsonable(request.arguments[name]):
                fail("output input ref differs from the request")
        if not _finite_numbers(output):
            fail("structured numerical result is not finite")
        if not {delta.operation for delta in deltas} <= INGESTIBLE_OPERATIONS:
            fail("analysis results register observations/artifacts only, never evidence")

    def verify_result(self, result: ToolResult, request: ToolCallRequest, spec: ToolSpec,
                      deltas: Sequence[StateDelta], expected_state_revision: Any) -> bool:
        try:
            self.check_result(result, request, spec, deltas)
        except ResultInvariantError:
            return False
        except Exception:  # any invariant that cannot be established fails closed
            _LOG.exception("bridge result verification error for %s", request.tool_name)
            return False
        return True

    # -- tool catalog -------------------------------------------------------------------
    def _spec(self, name: str, description: str, input_schema: Mapping[str, Any],
              output_schema: Mapping[str, Any]) -> ToolSpec:
        return ToolSpec(name, description, input_schema, output_schema,
                        SideEffectClass.COMPUTE, required_policy_tags=(ANALYSIS_POLICY_TAG,),
                        provider_metadata={"tool_version": bridge_tool_version(name),
                                           "bridge_version": BRIDGE_VERSION,
                                           "owner": "tep-agent-lab",
                                           **self.implementation})

    def _build(self) -> list[_BridgeTool]:
        limits = self.limits
        variables = {"type": "array", "items": _VARIABLE, "minItems": 1,
                     "maxItems": limits.max_variables}
        lag = {"type": "integer", "minimum": -limits.max_lag_seconds,
               "maximum": limits.max_lag_seconds}
        feature_request = _obj({
            "feature": {"enum": [str(item) for item in (*FEATURE_PARAMETERS,
                                                        *UNSUPPORTED_FEATURES)]},
            "deadband": {"type": "number", "minimum": 0},
            "threshold": {"type": "number", "exclusiveMinimum": 0},
            "integrand": {"enum": ["SIGNED", "ABSOLUTE"]}}, ("feature",))
        feature_out = _obj({
            "feature": _TEXT, "status": {"enum": ["DEFINED", "NOT_DETECTED"]},
            "value": {"type": ["number", "string", "null"]},
            "value_unit": {"type": ["string", "null"]},
            "time_hours": _NULL_NUMBER, "elapsed_seconds": _NULL_INTEGER,
            "parameters": {"type": "object"}},
            ("feature", "status", "value", "value_unit", "time_hours", "elapsed_seconds",
             "parameters"))
        best = {**_obj({"lag_seconds": {"type": "integer"}, "lag_samples": {"type": "integer"},
                        "correlation": _NUMBER,
                        "overlap_samples": {"type": "integer", "minimum": 0},
                        "lag_interpretation": {"enum": ["X_LEADS_Y", "Y_LEADS_X",
                                                        "ZERO_LAG"]}},
                       ("lag_seconds", "lag_samples", "correlation", "overlap_samples",
                        "lag_interpretation")),
                "type": ["object", "null"]}
        metric_out = _obj({"metric": _TEXT, "value": _NUMBER,
                           "value_unit": {"type": ["string", "null"]},
                           "time_hours": _NULL_NUMBER},
                          ("metric", "value", "value_unit", "time_hours"))
        return [
            _BridgeTool(self._spec(
                "compute_response_features",
                "Deterministic response features (DELTA, DIRECTION, PEAK, MINIMUM, "
                "ONSET_TIME, STEADY_STATE_RANGE, INTEGRATED_ERROR) of listed variables in "
                "an existing history/rollout artifact over explicit windows. Thresholds, "
                "deadbands, and baselines are always explicit request inputs.",
                _obj({"trajectory_ref": _INPUT_REF, "variables": variables,
                      "analysis_window": _WINDOW, "baseline_window": _WINDOW,
                      "features": {"type": "array", "items": feature_request,
                                   "minItems": 1, "maxItems": len(FEATURE_PARAMETERS)}},
                     ("trajectory_ref", "variables", "analysis_window", "features")),
                _obj({"trajectory_ref": _OUTPUT_REF,
                      "analysis_window": _WINDOW_OUT,
                      "baseline_window": {**_WINDOW_OUT, "type": ["object", "null"]},
                      "sampling_interval_seconds": _NULL_INTEGER,
                      "variables": {"type": "array", "items": _obj({
                          "variable": _TEXT, "unit": {"type": ["string", "null"]},
                          "baseline_mean": _NULL_NUMBER,
                          "features": {"type": "array", "items": feature_out}},
                          ("variable", "unit", "baseline_mean", "features"))}},
                     ("trajectory_ref", "analysis_window", "baseline_window",
                      "sampling_interval_seconds", "variables"))),
                self._prepare_features, self._features,
                ("trajectory_ref",)),
            _BridgeTool(self._spec(
                "analyze_cross_correlation",
                "Bounded lagged Pearson correlation between two variables of one existing "
                "history/rollout artifact. " + LAG_CONVENTION + ". The full per-lag "
                "curve is returned as an artifact.",
                _obj({"data_ref": _INPUT_REF, "x": _VARIABLE, "y": _VARIABLE,
                      "window": _WINDOW,
                      "lag_range": _obj({"min_lag_seconds": lag, "max_lag_seconds": lag},
                                        ("min_lag_seconds", "max_lag_seconds")),
                      "min_overlap_samples": {"type": "integer", "minimum": 3,
                                              "maximum": limits.max_records},
                      "preprocessing": {"enum": list(CORRELATION_PREPROCESSING)},
                      "selection": {"enum": list(CORRELATION_SELECTION)}},
                     ("data_ref", "x", "y", "window", "lag_range", "min_overlap_samples",
                      "preprocessing", "selection")),
                _obj({"data_ref": _OUTPUT_REF, "x": _TEXT, "y": _TEXT, "window": _WINDOW_OUT,
                      "sampling_interval_seconds": _NULL_INTEGER,
                      "analyzed_samples": {"type": "integer", "minimum": 0},
                      "preprocessing": _TEXT, "selection": _TEXT,
                      "lag_convention": _TEXT, "lag_unit": {"const": "s"},
                      "outcome": {"enum": ["DEFINED", "UNDEFINED"]},
                      "undefined_reason": {"enum": [None, "CONSTANT_SIGNAL",
                                                    "NO_DEFINED_LAG"]},
                      "constant_signals": {"type": "array", "items": {"enum": ["x", "y"]}},
                      "best": best,
                      "tied_lags_seconds": {"type": "array", "items": {"type": "integer"},
                                            "maxItems": limits.max_lags},
                      "zero_lag_correlation": _NULL_NUMBER,
                      "evaluated_lags": {"type": "integer", "minimum": 0},
                      "undefined_lags": {"type": "integer", "minimum": 0},
                      "interpretation_note": _TEXT,
                      "curve_artifact_ref": {**_OUTPUT_REF, "type": ["object", "null"]}},
                     ("data_ref", "x", "y", "window", "sampling_interval_seconds",
                      "analyzed_samples", "preprocessing", "selection", "lag_convention",
                      "lag_unit", "outcome", "undefined_reason", "constant_signals", "best",
                      "tied_lags_seconds", "zero_lag_correlation", "evaluated_lags",
                      "undefined_lags", "interpretation_note", "curve_artifact_ref"))),
                self._prepare_correlation, self._correlation,
                ("data_ref",), CURVE_KIND),
            _BridgeTool(self._spec(
                "compare_trajectories",
                "Explicit error metrics (candidate minus reference) between two existing "
                "history/rollout artifacts for listed variables under an allowlisted "
                "alignment policy; incompatible sampling is rejected, never resampled.",
                _obj({"reference_ref": _INPUT_REF, "candidate_ref": _INPUT_REF,
                      "variables": variables,
                      "alignment_policy": {"enum": list(ALIGNMENT_POLICIES)},
                      "reference_window": _WINDOW, "candidate_window": _WINDOW,
                      "metrics": {"type": "array", "items": {"enum": list(TRAJECTORY_METRICS)},
                                  "minItems": 1, "maxItems": len(TRAJECTORY_METRICS)},
                      "preprocessing": {"enum": list(TRAJECTORY_PREPROCESSING)}},
                     ("reference_ref", "candidate_ref", "variables", "alignment_policy",
                      "reference_window", "metrics", "preprocessing")),
                _obj({"reference_ref": _OUTPUT_REF, "candidate_ref": _OUTPUT_REF,
                      "metric_contract_version": {"const": TRAJECTORY_METRICS_VERSION},
                      "alignment": _obj({
                          "policy": _TEXT, "reference_window": _WINDOW_OUT,
                          "candidate_window": _WINDOW_OUT,
                          "sampling_interval_seconds": _NULL_INTEGER,
                          "aligned_samples": {"type": "integer", "minimum": 1},
                          "resampled": {"const": False}},
                          ("policy", "reference_window", "candidate_window",
                           "sampling_interval_seconds", "aligned_samples", "resampled")),
                      "preprocessing": _TEXT,
                      "error_definition": {"const": "candidate - reference"},
                      "variables": {"type": "array", "items": _obj({
                          "variable": _TEXT, "unit": {"type": ["string", "null"]},
                          "metrics": {"type": "array", "items": metric_out}},
                          ("variable", "unit", "metrics"))}},
                     ("reference_ref", "candidate_ref", "metric_contract_version",
                      "alignment", "preprocessing", "error_definition", "variables"))),
                self._prepare_comparison, self._comparison,
                ("reference_ref", "candidate_ref")),
        ]

    # -- shared data access -------------------------------------------------------------
    def _variables(self, names: Sequence[str]) -> list[str]:
        names = list(names)
        if len(set(names)) != len(names):
            _fail("INVALID_REQUEST", "variables must be distinct")
        for name in names:
            if (not isinstance(name, str) or name not in REGISTRY
                    or not name.startswith(("XMEAS(", "XMV("))):
                _fail("INVALID_REQUEST", "unknown or non-visible variable")
        return names

    def _input_ref(self, envelope: Any) -> InformationRef:
        if not isinstance(envelope, Mapping):
            _fail("INVALID_REQUEST", "artifact ref must be an InformationRef envelope")
        try:
            ref = InformationRef(**dict(envelope))
        except (TypeError, ValueError):
            _fail("INVALID_REQUEST", "artifact ref is not a valid InformationRef")
        if ref.visibility != Visibility.AGENT:
            _fail("POLICY_DENIED", "only Agent-visible artifacts can be analyzed")
        if ref.kind not in INPUT_KINDS or ref.owner != ARTIFACT_OWNER:
            _fail("INVALID_REQUEST", "artifact kind is not an analyzable telemetry artifact")
        if self.artifacts.resolve(ref) is None:
            _fail("INVALID_REQUEST", "artifact ref was not issued by this lab store")
        return ref  # ArtifactStore.read re-verifies the persisted checksum

    def _load(self, envelope: Any, variables: Sequence[str]) -> _Series:
        ref = self._input_ref(envelope)
        try:
            records = self.artifacts.read(ref)
        except WorldError:
            raise
        except Exception as exc:
            raise WorldError("ARTIFACT_ERROR", "artifact could not be read") from exc
        if len(records) > self.limits.max_records:
            _fail("INVALID_REQUEST", "artifact exceeds the analysis record limit")
        if leakage_findings(records):
            _fail("ARTIFACT_ERROR", "artifact content failed the leakage screen")
        flat = ref.kind == "HistoryWindowArtifact"
        seconds, columns = [], {name: [] for name in variables}
        for record in records:
            if not isinstance(record, Mapping):
                _fail("ARTIFACT_ERROR", "artifact record is not an object")
            seconds.append(_seconds(record.get("simulation_time_hours"),
                                    "artifact timestamp", "SAMPLING_INCOMPATIBLE"))
            for name in variables:
                group = record if flat else record.get(
                    "measurements" if name.startswith("XMEAS(") else "manipulated_variables")
                value = group.get(name) if isinstance(group, Mapping) else None
                if value is None:
                    _fail("INVALID_REQUEST", "variable is missing from the artifact")
                if type(value) not in (int, float) or not math.isfinite(value):
                    _fail("ARTIFACT_ERROR", "artifact value is not a finite number")
                columns[name].append(float(value))
        if not seconds:
            _fail("ARTIFACT_ERROR", "artifact has no records")
        times = numpy.asarray(seconds, dtype=numpy.int64)
        if numpy.any(numpy.diff(times) <= 0):
            _fail("SAMPLING_INCOMPATIBLE", "artifact timestamps are not strictly increasing")
        return _Series(ref, times, {name: numpy.asarray(values, dtype=numpy.float64)
                                    for name, values in columns.items()})

    @staticmethod
    def _window(series: _Series, window: Mapping[str, Any], what: str) -> _Window:
        start = _seconds(window["start_hours"], f"{what} start")
        end = _seconds(window["end_hours"], f"{what} end")
        if start > end:
            _fail("INVALID_REQUEST", f"{what} start is after its end")
        if start < series.seconds[0] or end > series.seconds[-1]:
            _fail("INVALID_REQUEST", f"{what} extends beyond the artifact's time span")
        indices = numpy.nonzero((series.seconds >= start) & (series.seconds <= end))[0]
        if not len(indices):
            _fail("INVALID_REQUEST", f"{what} contains no samples")
        # Uniform sampling is required inside the selected window only, so a rollout
        # whose final record is off-grid (shutdown, non-multiple horizon) stays usable
        # before that record; a window containing it is rejected, never resampled.
        steps = numpy.diff(series.seconds[indices])
        if len(steps) and numpy.any(steps != steps[0]):
            _fail("SAMPLING_INCOMPATIBLE", f"{what} is not uniformly sampled")
        return _Window(start, end, indices, int(steps[0]) if len(steps) else None)

    @staticmethod
    def _inputs(*series: _Series) -> list[dict[str, Any]]:
        return [{"artifact_id": item.ref.ref_id, "kind": item.ref.kind,
                 "checksum": item.ref.checksum} for item in series]

    # -- compute_response_features ------------------------------------------------------
    def _prepare_features(self, arguments: Mapping[str, Any]):
        requested = [dict(item) for item in arguments["features"]]
        names = [item["feature"] for item in requested]
        if len(set(names)) != len(names):
            _fail("INVALID_REQUEST", "features must be distinct")
        for item in requested:
            name = item["feature"]
            if name in UNSUPPORTED_FEATURES:
                _fail("UNSUPPORTED_CAPABILITY", UNSUPPORTED_FEATURES[name])
            if name not in FEATURE_PARAMETERS:
                _fail("INVALID_REQUEST", "unknown feature")
            given, needed = set(item) - {"feature"}, FEATURE_PARAMETERS[Feature(name)]
            if given != needed:
                _fail("INVALID_REQUEST",
                      f"{name} requires exactly the parameters {sorted(needed) or 'none'}")
            for key in ("deadband", "threshold"):
                if key in item and (type(item[key]) not in (int, float)
                                    or not math.isfinite(item[key])):
                    _fail("INVALID_REQUEST", f"{key} must be a finite number")
        variables = self._variables(arguments["variables"])
        series = self._load(arguments["trajectory_ref"], variables)
        analysis = self._window(series, arguments["analysis_window"], "analysis window")
        baseline = None
        if any(name in BASELINE_FEATURES for name in names):
            if "baseline_window" not in arguments:
                _fail("INVALID_REQUEST", "a requested feature requires an explicit "
                                         "baseline_window")
            baseline = self._window(series, arguments["baseline_window"], "baseline window")
        elif "baseline_window" in arguments:
            _fail("INVALID_REQUEST", "baseline_window given but no requested feature uses it")
        if Feature.INTEGRATED_ERROR in names and len(analysis.indices) < 2:
            _fail("INVALID_REQUEST", "INTEGRATED_ERROR needs at least two analysis samples")
        return series, variables, analysis, baseline, requested

    def _features(self, plan, request, created_at):
        series, variables, analysis, baseline, requested = plan
        times = series.seconds[analysis.indices]
        rows = []
        for name in variables:
            column = series.values[name]
            samples = column[analysis.indices]
            base = float(numpy.mean(column[baseline.indices])) if baseline is not None else None
            unit = _unit(name)
            rows.append({"variable": name, "unit": unit, "baseline_mean": base,
                         "features": [self._feature(item, samples, times, analysis.start,
                                                    base, unit) for item in requested]})
        output = {"trajectory_ref": to_jsonable(series.ref),
                  "analysis_window": analysis.describe(),
                  "baseline_window": None if baseline is None else baseline.describe(),
                  "sampling_interval_seconds": analysis.interval, "variables": rows}
        provenance = {
            "configuration": {"features": requested,
                              "analysis_window": dict(request.arguments["analysis_window"]),
                              "baseline_window": (dict(request.arguments["baseline_window"])
                                                  if baseline is not None else None),
                              "baseline_statistic": "arithmetic_mean"},
            "inputs": self._inputs(series),
            "sampling": {"analysis_interval_seconds": analysis.interval,
                         "baseline_interval_seconds": (baseline.interval
                                                       if baseline is not None else None),
                         "resampled": False}}
        return output, (), provenance

    @staticmethod
    def _feature(item, samples, times, start, base, unit) -> dict[str, Any]:
        name = item["feature"]
        parameters = {key: value for key, value in item.items() if key != "feature"}
        found = {"feature": name, "status": "DEFINED", "value": None, "value_unit": unit,
                 "time_hours": None, "elapsed_seconds": None, "parameters": parameters}

        def at(index: int) -> None:
            found.update(time_hours=int(times[index]) / 3600,
                         elapsed_seconds=int(times[index]) - start)

        if name == Feature.DELTA:
            found["value"] = float(samples[-1] - base)
        elif name == Feature.DIRECTION:
            delta, band = float(samples[-1] - base), item["deadband"]
            found.update(value="INCREASE" if delta > band
                         else "DECREASE" if delta < -band else "UNCHANGED",
                         value_unit=None)
        elif name in (Feature.PEAK, Feature.MINIMUM):
            index = int(numpy.argmax(samples) if name == Feature.PEAK
                        else numpy.argmin(samples))  # first occurrence on ties
            found["value"] = float(samples[index])
            at(index)
        elif name == Feature.ONSET_TIME:
            hits = numpy.nonzero(numpy.abs(samples - base) > item["threshold"])[0]
            found["value_unit"] = "s"
            if len(hits):
                at(int(hits[0]))
                found["value"] = found["elapsed_seconds"]
            else:
                found["status"] = "NOT_DETECTED"
        elif name == Feature.STEADY_STATE_RANGE:
            found["value"] = float(numpy.max(samples) - numpy.min(samples))
        elif name == Feature.INTEGRATED_ERROR:
            error = samples - base
            if item["integrand"] == "ABSOLUTE":
                error = numpy.abs(error)
            widths = numpy.diff(times).astype(numpy.float64)
            found.update(value=float(numpy.sum((error[1:] + error[:-1]) / 2 * widths)),
                         value_unit=f"{unit or '1'}*s")
        return found

    # -- analyze_cross_correlation ------------------------------------------------------
    def _prepare_correlation(self, arguments: Mapping[str, Any]):
        x, y = arguments["x"], arguments["y"]
        if x == y:
            _fail("INVALID_REQUEST", "x and y must be different variables")
        self._variables([x, y])
        series = self._load(arguments["data_ref"], [x, y])
        window = self._window(series, arguments["window"], "window")
        interval = window.interval
        if interval is None:
            _fail("INVALID_REQUEST", "window needs at least min_overlap_samples samples")
        count = len(window.indices) - (arguments["preprocessing"] == "FIRST_DIFFERENCE")
        low = arguments["lag_range"]["min_lag_seconds"]
        high = arguments["lag_range"]["max_lag_seconds"]
        overlap = arguments["min_overlap_samples"]
        if low > high:
            _fail("INVALID_REQUEST", "min_lag_seconds must not exceed max_lag_seconds")
        if low % interval or high % interval:
            _fail("INVALID_REQUEST", "lags must be integral multiples of the "
                                     f"{interval} s sampling interval")
        low, high = low // interval, high // interval
        if high - low + 1 > self.limits.max_lags:
            _fail("INVALID_REQUEST", f"at most {self.limits.max_lags} lags per request")
        if overlap > count or count - max(abs(low), abs(high)) < overlap:
            _fail("INVALID_REQUEST", "lag range leaves fewer than min_overlap_samples "
                                     "overlapping samples")
        return series, window, x, y, low, high

    def _correlation(self, plan, request, created_at):
        series, window, x, y, low, high = plan
        interval = window.interval
        arguments = request.arguments
        preprocessing, selection = arguments["preprocessing"], arguments["selection"]
        times = series.seconds[window.indices].astype(numpy.float64)
        raw_x, raw_y = series.values[x][window.indices], series.values[y][window.indices]
        scale_x = float(numpy.max(numpy.abs(raw_x)))
        scale_y = float(numpy.max(numpy.abs(raw_y)))
        xs = _preprocess(raw_x, times, preprocessing)
        ys = _preprocess(raw_y, times, preprocessing)
        count = len(xs)
        constant = [label for label, values, scale in (("x", xs, scale_x), ("y", ys, scale_y))
                    if _degenerate(values, scale)]
        curve, scores = [], {}
        for lag in range(low, high + 1):
            a, b = (xs[:count - lag], ys[lag:]) if lag >= 0 else (xs[-lag:], ys[:count + lag])
            value = None
            if not (constant or _degenerate(a, scale_x) or _degenerate(b, scale_y)):
                value = _pearson(a, b)
            curve.append({"lag_seconds": lag * interval, "lag_samples": lag,
                          "correlation": value, "overlap_samples": int(len(a))})
            if value is not None:
                scores[lag] = value
        best, ties, reason = None, [], None
        if constant:
            reason = "CONSTANT_SIGNAL"
        elif not scores:
            reason = "NO_DEFINED_LAG"
        else:
            rank = {lag: round(abs(value) if selection == "MAX_ABS_CORRELATION" else value,
                               CORRELATION_TIE_DECIMALS) for lag, value in scores.items()}
            top = max(rank.values())
            tied = sorted(lag for lag, value in rank.items() if value == top)
            chosen = min(tied, key=lambda lag: (abs(lag), lag))
            ties = [lag * interval for lag in tied]
            best = {"lag_seconds": chosen * interval, "lag_samples": chosen,
                    "correlation": scores[chosen],
                    "overlap_samples": count - abs(chosen),
                    "lag_interpretation": ("X_LEADS_Y" if chosen > 0 else
                                           "Y_LEADS_X" if chosen < 0 else "ZERO_LAG")}
        artifact = self.artifacts.put_records(CURVE_KIND, curve, created_at)
        output = {
            "data_ref": to_jsonable(series.ref), "x": x, "y": y,
            "window": window.describe(), "sampling_interval_seconds": interval,
            "analyzed_samples": count, "preprocessing": preprocessing,
            "selection": selection, "lag_convention": LAG_CONVENTION, "lag_unit": "s",
            "outcome": "UNDEFINED" if reason else "DEFINED", "undefined_reason": reason,
            "constant_signals": constant, "best": best, "tied_lags_seconds": ties,
            "zero_lag_correlation": scores.get(0), "evaluated_lags": len(curve),
            "undefined_lags": len(curve) - len(scores),
            "interpretation_note": INTERPRETATION_NOTE,
            "curve_artifact_ref": to_jsonable(artifact)}
        provenance = {
            "configuration": {"window": dict(arguments["window"]),
                              "lag_range": dict(arguments["lag_range"]),
                              "min_overlap_samples": arguments["min_overlap_samples"],
                              "preprocessing": preprocessing, "selection": selection,
                              "correlation": "pearson_over_overlap",
                              "degeneracy_relative_tolerance": DEGENERACY_RELATIVE_TOLERANCE,
                              "tie_decimals": CORRELATION_TIE_DECIMALS,
                              "tie_break": "min(|lag|, lag)",
                              "lag_convention": LAG_CONVENTION},
            "inputs": self._inputs(series),
            "sampling": {"window_interval_seconds": interval, "resampled": False}}
        return output, (artifact,), provenance

    # -- compare_trajectories -----------------------------------------------------------
    def _prepare_comparison(self, arguments: Mapping[str, Any]):
        metrics = list(arguments["metrics"])
        if len(set(metrics)) != len(metrics):
            _fail("INVALID_REQUEST", "metrics must be distinct")
        variables = self._variables(arguments["variables"])
        policy = arguments["alignment_policy"]
        if policy == "EXACT_TIMESTAMPS" and "candidate_window" in arguments:
            _fail("INVALID_REQUEST", "EXACT_TIMESTAMPS uses reference_window for both")
        if policy == "ELAPSED_FROM_WINDOW_START" and "candidate_window" not in arguments:
            _fail("INVALID_REQUEST", "ELAPSED_FROM_WINDOW_START requires candidate_window")
        reference = self._load(arguments["reference_ref"], variables)
        candidate = self._load(arguments["candidate_ref"], variables)
        ref_window = self._window(reference, arguments["reference_window"], "reference window")
        cand_window = self._window(candidate, arguments.get("candidate_window",
                                                            arguments["reference_window"]),
                                   "candidate window")
        ref_times = reference.seconds[ref_window.indices]
        cand_times = candidate.seconds[cand_window.indices]
        if policy == "EXACT_TIMESTAMPS":
            if not numpy.array_equal(ref_times, cand_times):
                _fail("SAMPLING_INCOMPATIBLE", "selected timestamps differ; no resampling "
                                               "is performed")
        elif (ref_window.interval != cand_window.interval
              or len(ref_times) != len(cand_times)
              or ref_window.end - ref_window.start != cand_window.end - cand_window.start
              or ref_times[0] - ref_window.start != cand_times[0] - cand_window.start):
            _fail("SAMPLING_INCOMPATIBLE", "windows differ in duration, interval, sample "
                                           "count, or phase; no resampling is performed")
        return reference, candidate, ref_window, cand_window, variables, metrics

    def _comparison(self, plan, request, created_at):
        reference, candidate, ref_window, cand_window, variables, metrics = plan
        arguments = request.arguments
        preprocessing = arguments["preprocessing"]
        times = reference.seconds[ref_window.indices]
        rows = []
        for name in variables:
            r = reference.values[name][ref_window.indices]
            c = candidate.values[name][cand_window.indices]
            if preprocessing == "REMOVE_INITIAL_VALUE":
                r, c = r - r[0], c - c[0]
            error, unit = c - r, _unit(name)
            values = []
            for metric in metrics:
                time_hours = None
                if metric == "MEAN_ERROR":
                    value = numpy.mean(error)
                elif metric == "MEAN_ABSOLUTE_ERROR":
                    value = numpy.mean(numpy.abs(error))
                elif metric == "ROOT_MEAN_SQUARE_ERROR":
                    value = numpy.sqrt(numpy.mean(error * error))
                elif metric == "MAX_ABSOLUTE_ERROR":
                    index = int(numpy.argmax(numpy.abs(error)))  # earliest on ties
                    value, time_hours = numpy.abs(error[index]), int(times[index]) / 3600
                else:  # FINAL_ERROR
                    value = error[-1]
                values.append({"metric": metric, "value": float(value), "value_unit": unit,
                               "time_hours": time_hours})
            rows.append({"variable": name, "unit": unit, "metrics": values})
        policy = arguments["alignment_policy"]
        output = {
            "reference_ref": to_jsonable(reference.ref),
            "candidate_ref": to_jsonable(candidate.ref),
            "metric_contract_version": TRAJECTORY_METRICS_VERSION,
            "alignment": {"policy": policy, "reference_window": ref_window.describe(),
                          "candidate_window": cand_window.describe(),
                          "sampling_interval_seconds": ref_window.interval,
                          "aligned_samples": int(len(times)), "resampled": False},
            "preprocessing": preprocessing, "error_definition": "candidate - reference",
            "variables": rows}
        provenance = {
            "configuration": {"alignment_policy": policy,
                              "reference_window": dict(arguments["reference_window"]),
                              "candidate_window": (dict(arguments["candidate_window"])
                                                   if "candidate_window" in arguments
                                                   else None),
                              "preprocessing": preprocessing, "metrics": metrics,
                              "metric_contract_version": TRAJECTORY_METRICS_VERSION},
            "inputs": self._inputs(reference, candidate),
            "sampling": {"window_interval_seconds": {"reference": ref_window.interval,
                                                     "candidate": cand_window.interval},
                         "alignment": policy, "resampled": False}}
        return output, (), provenance


def _preprocess(values: numpy.ndarray, times: numpy.ndarray, policy: str) -> numpy.ndarray:
    if policy == "FIRST_DIFFERENCE":
        return numpy.diff(values)
    if policy == "LINEAR_DETREND":
        centered_t = times - numpy.mean(times)
        centered = values - numpy.mean(values)
        slope = numpy.sum(centered_t * centered) / numpy.sum(centered_t * centered_t)
        return centered - slope * centered_t
    return values


def _degenerate(values: numpy.ndarray, scale: float) -> bool:
    """Constant up to floating-point precision relative to the raw window scale."""
    return float(numpy.ptp(values)) <= DEGENERACY_RELATIVE_TOLERANCE * scale


def _pearson(a: numpy.ndarray, b: numpy.ndarray) -> float | None:
    a, b = a - numpy.mean(a), b - numpy.mean(b)
    denominator = math.sqrt(float(numpy.sum(a * a)) * float(numpy.sum(b * b)))
    numerator = float(numpy.sum(a * b))
    if denominator == 0 or not math.isfinite(denominator) or not math.isfinite(numerator):
        return None
    return min(1.0, max(-1.0, numerator / denominator))


class BridgedToolSurface:
    """One runtime registry: the unchanged C4 surface plus the C5 analysis bridge.

    Each hook routes by tool name; authority is the union of both catalogs with
    COMPUTE and the analysis tag added. No tool can be served by both.
    """

    def __init__(self, surface: BlindRcaToolSurface, bridge: AnalysisToolBridge) -> None:
        if not isinstance(surface, BlindRcaToolSurface) or not isinstance(
                bridge, AnalysisToolBridge):
            raise TypeError("BlindRcaToolSurface and AnalysisToolBridge required")
        if bridge.artifacts is not surface.artifacts:
            raise ValueError("bridge and surface must share one artifact store")
        if bridge._reference_revision != surface.reference_revision:
            # One reference truth: the bridge's stale-state guard is the surface's.
            raise ValueError("bridge reference guard must be surface.reference_revision")
        overlap = {spec.name for spec in surface.tool_specs()} & {
            spec.name for spec in bridge.tool_specs()}
        if overlap:
            raise ValueError(f"tool names served twice: {sorted(overlap)}")
        self.surface, self.bridge = surface, bridge
        self.registered_tool_set_version = "tool-set-" + checksum(self.tool_specs())

    def tool_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(sorted((*self.surface.tool_specs(), *self.bridge.tool_specs()),
                            key=lambda spec: spec.name))

    def gate_policy(self) -> GatePolicy:
        base = self.surface.gate_policy()
        return GatePolicy(BRIDGED_POLICY_VERSION,
                          base.granted_side_effect_classes | {SideEffectClass.COMPUTE},
                          base.granted_policy_tags | {ANALYSIS_POLICY_TAG},
                          base.simulation_dimensions,
                          tool_allowlist=frozenset(spec.name for spec in self.tool_specs()))

    def _owner(self, name: Any):
        return self.bridge if self.bridge.handles(name) else self.surface

    def reference_revision(self) -> str:
        return self.surface.reference_revision()

    def validate_request(self, request, spec, task, expected_state_revision, budget_usage):
        return self._owner(request.tool_name).validate_request(
            request, spec, task, expected_state_revision, budget_usage)

    def execute(self, request, spec):
        return self._owner(request.tool_name).execute(request, spec)

    def verify_result(self, result, request, spec, deltas, expected_state_revision):
        return self._owner(request.tool_name).verify_result(
            result, request, spec, deltas, expected_state_revision)

    def verify_finish(self, proposal, task, expected_state_revision):
        return self.surface.verify_finish(proposal, task, expected_state_revision)
