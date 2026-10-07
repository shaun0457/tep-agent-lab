"""D0 frozen benchmark-case contract (docs/specs/benchmark-case-v0.md).

Benchmark-domain contracts, the deterministic Agent projection, the trusted harness,
the leakage audit, and the deterministic metric-vector scorer. Everything here is
trusted evaluator/application code; nothing is an Agent tool.

P0 stays the only run/provenance/lifecycle owner: the harness builds an ordinary
``RunRequest``, a bound ``BenchmarkRefs`` and a trusted ``case_setup`` returning a
``CaseSetupAttestation``, and execution is ``RunManager.create -> prepare -> start``.
There is no second manifest, scheduler, TaskStateStore or world here.

Hidden setup and ground truth live in two separate EVALUATOR-only canonical sources.
``project_case`` never receives ground truth; the scorer reads saved records only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any

from industrial_agent_runtime import (Action, Budget, FakeProvider, FinishProposal,
                                      InformationRef, ModelTurn, ToolCallRequest, Visibility,
                                      checksum, to_jsonable)
from industrial_agent_runtime.serialization import freeze_json
from tep_sim import REGISTRY, ControlMode, DisturbanceIntervention

from .canonical_context import (CANONICAL_JSON_SHA256, ContextSourceRef,
                                PackageSourceMaterializer, ProjectionScope, SourceMaterializer,
                                content_checksum)
from .persistence import canonical_json
from .playground import (CASE_SETUP_ATTESTATION_VERSION, LAB_REPOSITORY, TEP_SIM_REPOSITORY,
                         BenchmarkPartition, BenchmarkRefs, CaseSetupAttestation, ModelSpec,
                         RunManager, RunManifest, RunRequest, WorldSpec,
                         pinned_tep_sim_sources)
from .tep_world import ReferenceWorld, hidden_vocabulary, names_disturbance_id
from .tool_surface import SIMULATION_DIMENSIONS

BENCHMARK_CASE_SCHEMA = "tep-agent-lab.benchmark-case/v0"
HIDDEN_SETUP_SCHEMA = "tep-agent-lab.benchmark-setup/v0"
GROUND_TRUTH_SCHEMA = "tep-agent-lab.benchmark-ground-truth/v0"
AGENT_PROJECTION_SCHEMA = "tep-agent-lab.benchmark-agent-projection/v0"
SCORING_SCHEMA = "tep-agent-lab.benchmark-scoring/v0"
LEAKAGE_AUDIT_SCHEMA = "tep-agent-lab.benchmark-leakage-audit/v0"
SUBMISSION_SCHEMA = "tep-agent-lab.benchmark-submission/v0"
SCORE_SCHEMA = "tep-agent-lab.benchmark-score/v0"

SCORER_VERSION = "tep-agent-lab.benchmark-scorer/v0"
SETUP_POLICY_VERSION = "tep-agent-lab.benchmark-setup-policy/v0"
LEAKAGE_POLICY_VERSION = "tep-agent-lab.benchmark-leakage-policy/v0"
TASK_FAMILY = "RCA"
ORCHESTRATION_CONDITIONS = frozenset({"O0", "O1", "O2", "O3", "O4", "O5"})
BENCHMARK_CASE_KIND = "BENCHMARK_CASE"
GROUND_TRUTH_KIND = "EVALUATOR_GROUND_TRUTH"

_OPAQUE_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_CASE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_SIGNAL_ID = re.compile(r"(XMEAS|XMV)\([1-9][0-9]*\)")
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SURFACE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SAFE_KEY = re.compile(r"[A-Za-z0-9_.-]{1,64}")
# Equal float sums (0.1 + 0.2 vs 0.3) are compared at simulator time resolution.
_TIME_TOLERANCE_HOURS = 1e-9


class BenchmarkContractError(ValueError):
    """A benchmark fixture, truth, submission or binding violates the frozen contract."""


class CausalMechanism(StrEnum):
    """Frozen v0 mechanism vocabulary; no IDV-specific values."""

    FLOW_DISTURBANCE = "FLOW_DISTURBANCE"
    TEMPERATURE_DISTURBANCE = "TEMPERATURE_DISTURBANCE"
    COMPOSITION_DISTURBANCE = "COMPOSITION_DISTURBANCE"
    VALVE_STICKING = "VALVE_STICKING"
    CONTROL_ACTION_OR_LOOP = "CONTROL_ACTION_OR_LOOP"
    REACTION_OR_PROCESS_DYNAMICS = "REACTION_OR_PROCESS_DYNAMICS"
    MEASUREMENT_OR_SENSOR = "MEASUREMENT_OR_SENSOR"
    MULTIPLE_OR_INTERACTING_CAUSES = "MULTIPLE_OR_INTERACTING_CAUSES"
    OTHER_SUPPORTED_MECHANISM = "OTHER_SUPPORTED_MECHANISM"
    NO_ABNORMAL_CAUSE = "NO_ABNORMAL_CAUSE"


class EvidenceRefStatus(StrEnum):
    """Frozen v0 evidence-reference statuses; no free-form classifications."""

    VALID_RELEVANT_REF = "VALID_RELEVANT_REF"
    VALID_BUT_IRRELEVANT_REF = "VALID_BUT_IRRELEVANT_REF"
    UNUSED_OBSERVATION = "UNUSED_OBSERVATION"
    MISSING_REF = "MISSING_REF"
    HIDDEN_REF_VIOLATION = "HIDDEN_REF_VIOLATION"
    UNSUPPORTED_NARRATIVE_CLAIM = "UNSUPPORTED_NARRATIVE_CLAIM"


class MetricStatus(StrEnum):
    """Every metric states explicitly whether it was computed; nothing is guessed."""

    AVAILABLE = "AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # excluded by the frozen scoring configuration
    NOT_AVAILABLE = "NOT_AVAILABLE"  # the saved scoring input carries no such data


class SubmissionSource(StrEnum):
    SYNTHETIC = "SYNTHETIC"  # evaluator-side scorer fixture, never injected into a run
    RUN_OUTCOME = "RUN_OUTCOME"  # derived from a saved P0 run through public reads


class LeakageCategory(StrEnum):
    HIDDEN_DISTURBANCE_ID = "HIDDEN_DISTURBANCE_ID"
    HIDDEN_VOCABULARY = "HIDDEN_VOCABULARY"
    HIDDEN_TRUTH_LABEL = "HIDDEN_TRUTH_LABEL"
    HIDDEN_SETUP_CHECKSUM = "HIDDEN_SETUP_CHECKSUM"
    HIDDEN_SOURCE_REF = "HIDDEN_SOURCE_REF"
    EVALUATOR_STRUCTURE = "EVALUATOR_STRUCTURE"


CLAIM_FIELDS = ("entity_id", "mechanism", "variable_or_actuator_id", "fault_family",
                "direction_or_mode")
_CLAIM_METRICS = {"mechanism": "causal_mechanism_match", "entity_id": "entity_match",
                  "variable_or_actuator_id": "variable_or_actuator_match",
                  "fault_family": "fault_family_match",
                  "direction_or_mode": "direction_or_mode_match"}
METRIC_NAMES = ("top1_causal_claim_exact", "causal_mechanism_match", "entity_match",
                "variable_or_actuator_match", "fault_family_match", "direction_or_mode_match",
                "healthy_no_abnormal_correct", "evidence_ref_status_counts",
                "hidden_ref_violation_count", "unsupported_narrative_claim_count",
                "model_calls", "tool_calls", "rollout_count", "simulated_horizon_seconds",
                "terminal_task_status")


# -- strict field validation ------------------------------------------------------------
def _object(value: Any, name: str, required: Sequence[str],
            optional: Sequence[str] = ()) -> Mapping[str, Any]:
    """Exact field set: unknown fields are rejected, never silently ignored."""
    if not isinstance(value, Mapping):
        raise BenchmarkContractError(f"{name} must be an object")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        raise BenchmarkContractError(f"{name} has unknown fields {unknown}")
    missing = sorted(set(required) - set(value))
    if missing:
        raise BenchmarkContractError(f"{name} is missing fields {missing}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise BenchmarkContractError(f"{name} must be a nonempty trimmed string")
    return value


def _agent_text(value: Any, name: str) -> str:
    """Agent-visible text: no hidden-truth vocabulary or disturbance id spelling."""
    if hidden_vocabulary(_text(value, name)):
        raise BenchmarkContractError(f"{name} carries hidden-truth vocabulary")
    return value


def _opaque_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _OPAQUE_ID.fullmatch(value):
        raise BenchmarkContractError(f"{name} must match [a-z0-9][a-z0-9-]{{2,63}}")
    return _agent_text(value, name)


def _number(value: Any, name: str) -> float:
    """A finite nonnegative number, normalized to float so 1 and 1.0 hash alike."""
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise BenchmarkContractError(f"{name} must be a finite nonnegative number")
    try:
        number = float(value) + 0.0  # + 0.0 also normalizes -0.0
    except OverflowError as exc:
        raise BenchmarkContractError(f"{name} must be a finite nonnegative number") from exc
    if number < 0:
        raise BenchmarkContractError(f"{name} must be a finite nonnegative number")
    return number


def _sequence(value: Any, name: str) -> tuple[Any, ...]:
    """A list/tuple; a bare string is never exploded into characters."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise BenchmarkContractError(f"{name} must be a list")
    return tuple(value)


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise BenchmarkContractError(f"{name} must be a nonnegative integer")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise BenchmarkContractError(f"{name} must be a boolean")
    return value


def _optional_text(value: Any, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _strings(value: Any, name: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or not value:
        raise BenchmarkContractError(f"{name} must be a nonempty list")
    items = tuple(value)
    for item in items:
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise BenchmarkContractError(f"{name} has an invalid entry")
    if len(set(items)) != len(items):
        raise BenchmarkContractError(f"{name} has duplicate entries")
    return items


def _enum(kind: type[StrEnum], value: Any, name: str) -> Any:
    try:
        return kind(value)
    except ValueError as exc:
        raise BenchmarkContractError(f"{name} is not a frozen {kind.__name__} value") from exc


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=0.0, abs_tol=_TIME_TOLERANCE_HOURS)


def strict_json(data: bytes) -> Any:
    """Duplicate keys and non-finite constants are contract violations, not defaults."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for key, item in items:
            if key in found:
                raise BenchmarkContractError(f"duplicate JSON field {key!r}")
            found[key] = item
        return found

    def constant(name: str) -> Any:
        raise BenchmarkContractError(f"non-finite JSON number {name}")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=constant)
    except BenchmarkContractError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise BenchmarkContractError("fixture is not strict UTF-8 JSON") from exc


# -- BenchmarkCase ----------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class DisturbanceSetup:
    kind: str
    disturbance_id: str
    value: int

    def __post_init__(self) -> None:
        if self.kind != "DISTURBANCE":
            raise BenchmarkContractError("hidden intervention kind must be DISTURBANCE")
        # tep-sim applies IDV values as integers; a float spelling fails at apply time,
        # so it is rejected here rather than normalized.
        if _integer(self.value, "hidden_setup.intervention.value") == 0:
            raise BenchmarkContractError("a D0 hidden intervention must activate its disturbance")
        if (not isinstance(self.disturbance_id, str) or not self.disturbance_id.startswith("IDV(")
                or self.disturbance_id not in REGISTRY):
            raise BenchmarkContractError("hidden intervention is not a pinned tep-sim disturbance")
        try:
            DisturbanceIntervention(self.disturbance_id, self.value)
        except Exception as exc:
            raise BenchmarkContractError("hidden intervention is not supported by tep-sim") from exc

    @classmethod
    def from_record(cls, record: Any) -> "DisturbanceSetup":
        if isinstance(record, (list, tuple)):
            raise BenchmarkContractError("D0 v0 permits exactly one hidden intervention")
        return cls(**_object(record, "hidden_setup.intervention",
                             ("kind", "disturbance_id", "value")))

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, kw_only=True)
class HiddenSetup:
    """EVALUATOR-only; never copied into Agent-visible records."""

    schema_version: str
    pre_incident_hours: float
    intervention: DisturbanceSetup
    post_incident_hours: float

    def __post_init__(self) -> None:
        if self.schema_version != HIDDEN_SETUP_SCHEMA:
            raise BenchmarkContractError(f"hidden_setup schema_version must be {HIDDEN_SETUP_SCHEMA}")
        for name in ("pre_incident_hours", "post_incident_hours"):
            object.__setattr__(self, name, _number(getattr(self, name), f"hidden_setup.{name}"))
        if type(self.intervention) is not DisturbanceSetup:
            raise BenchmarkContractError("hidden_setup.intervention must be typed")

    @classmethod
    def from_record(cls, record: Any) -> "HiddenSetup":
        record = _object(record, "hidden_setup", ("schema_version", "pre_incident_hours",
                                                  "intervention", "post_incident_hours"))
        return cls(**{**record, "intervention": DisturbanceSetup.from_record(
            record["intervention"])})

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)

    def checksum(self) -> str:
        return checksum(self.record())


@dataclass(frozen=True, kw_only=True)
class AgentProjectionSpec:
    """The explicitly Agent-visible fixture fields; nothing else is projected."""

    incident_id: str
    goal: str
    trigger_signal_ids: tuple[str, ...]
    initial_time_hours: float
    projection_policy_version: str

    def __post_init__(self) -> None:
        _opaque_id(self.incident_id, "agent_projection.incident_id")
        _agent_text(self.goal, "agent_projection.goal")
        object.__setattr__(self, "trigger_signal_ids", _strings(
            self.trigger_signal_ids, "agent_projection.trigger_signal_ids", _SIGNAL_ID))
        if any(signal not in REGISTRY for signal in self.trigger_signal_ids):
            raise BenchmarkContractError("trigger signals must be pinned tep-sim variables")
        object.__setattr__(self, "initial_time_hours", _number(
            self.initial_time_hours, "agent_projection.initial_time_hours"))
        _agent_text(self.projection_policy_version, "agent_projection.projection_policy_version")

    @classmethod
    def from_record(cls, record: Any) -> "AgentProjectionSpec":
        return cls(**_object(record, "agent_projection", (
            "incident_id", "goal", "trigger_signal_ids", "initial_time_hours",
            "projection_policy_version")))


@dataclass(frozen=True, kw_only=True)
class ToolPolicy:
    policy_version: str
    allowed_tools: tuple[str, ...]

    def __post_init__(self) -> None:
        _agent_text(self.policy_version, "tool_policy.policy_version")
        object.__setattr__(self, "allowed_tools", _strings(
            self.allowed_tools, "tool_policy.allowed_tools", _TOOL_NAME))
        for name in self.allowed_tools:
            _agent_text(name, "tool_policy.allowed_tools")

    @classmethod
    def from_record(cls, record: Any) -> "ToolPolicy":
        return cls(**_object(record, "tool_policy", ("policy_version", "allowed_tools")))


@dataclass(frozen=True, kw_only=True)
class OrchestrationPolicy:
    condition: str
    policy_version: str

    def __post_init__(self) -> None:
        if self.condition not in ORCHESTRATION_CONDITIONS:
            raise BenchmarkContractError("orchestration condition must be one of O0-O5")
        _text(self.policy_version, "orchestration_policy.policy_version")

    @classmethod
    def from_record(cls, record: Any) -> "OrchestrationPolicy":
        return cls(**_object(record, "orchestration_policy", ("condition", "policy_version")))


@dataclass(frozen=True, kw_only=True)
class SubagentPolicy:
    enabled: bool
    max_count: int
    max_depth: int

    def __post_init__(self) -> None:
        _boolean(self.enabled, "subagent_policy.enabled")
        _integer(self.max_count, "subagent_policy.max_count")
        _integer(self.max_depth, "subagent_policy.max_depth")
        if not self.enabled and (self.max_count or self.max_depth):
            raise BenchmarkContractError("disabled subagents must have zero count and depth")

    @classmethod
    def from_record(cls, record: Any) -> "SubagentPolicy":
        return cls(**_object(record, "subagent_policy", ("enabled", "max_count", "max_depth")))


@dataclass(frozen=True, kw_only=True)
class ScoringConfig:
    schema_version: str
    scorer_version: str
    causal_claim_match_fields: tuple[str, ...]
    healthy_outcome_enabled: bool
    evidence_ref_policy_version: str
    authority_policy_version: str

    def __post_init__(self) -> None:
        if self.schema_version != SCORING_SCHEMA:
            raise BenchmarkContractError(f"scoring schema_version must be {SCORING_SCHEMA}")
        for name in ("scorer_version", "evidence_ref_policy_version", "authority_policy_version"):
            _text(getattr(self, name), f"scoring.{name}")
        fields = _strings(self.causal_claim_match_fields, "scoring.causal_claim_match_fields",
                          re.compile("|".join(CLAIM_FIELDS)))
        if "mechanism" not in fields:
            raise BenchmarkContractError("causal claim matching always includes mechanism")
        object.__setattr__(self, "causal_claim_match_fields", fields)
        _boolean(self.healthy_outcome_enabled, "scoring.healthy_outcome_enabled")

    @classmethod
    def from_record(cls, record: Any) -> "ScoringConfig":
        return cls(**_object(record, "scoring", (
            "schema_version", "scorer_version", "causal_claim_match_fields",
            "healthy_outcome_enabled", "evidence_ref_policy_version",
            "authority_policy_version")))

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)


def _world(record: Any) -> WorldSpec:
    record = _object(record, "world", ("seed", "backend", "control_mode", "record_interval"))
    if type(record["seed"]) is not int:
        raise BenchmarkContractError("world.seed must be an integer")
    if type(record["record_interval"]) is not int or record["record_interval"] <= 0:
        raise BenchmarkContractError("world.record_interval must be a positive integer")
    return WorldSpec(seed=record["seed"], backend=_text(record["backend"], "world.backend"),
                     control_mode=_enum(ControlMode, record["control_mode"],
                                        "world.control_mode"),
                     record_interval=record["record_interval"])


def _budget(record: Any) -> Budget:
    """Explicit runtime Budget; simulation quotas name exactly this surface's dimensions."""
    record = _object(record, "budget", ("max_model_calls", "max_tool_calls", "max_subagents",
                                        "max_subagent_depth", "max_steps", "extra_dimensions"),
                     ("max_total_tokens", "max_parallel_width"))
    if record.get("max_total_tokens") is not None:
        raise BenchmarkContractError("token-metered benchmark budgets are not supported in v0")
    extra = _object(record["extra_dimensions"], "budget.extra_dimensions", SIMULATION_DIMENSIONS)
    for name in ("max_model_calls", "max_tool_calls", "max_subagents", "max_subagent_depth",
                 "max_steps"):
        _integer(record[name], f"budget.{name}")
    try:
        return Budget(**{**record, "extra_dimensions": {
            name: _number(value, f"budget.extra_dimensions.{name}")
            for name, value in extra.items()}})
    except ValueError as exc:
        raise BenchmarkContractError(f"invalid budget: {exc}") from exc


@dataclass(frozen=True, kw_only=True)
class BenchmarkCase:
    """The complete canonical fixture. EVALUATOR-only: the Agent never resolves it."""

    schema_version: str
    benchmark_version: str
    case_id: str
    case_version: str
    scenario_family_id: str
    partition: BenchmarkPartition
    task_family: str
    world: WorldSpec
    agent_projection: AgentProjectionSpec
    tool_policy: ToolPolicy
    orchestration_policy: OrchestrationPolicy
    subagent_policy: SubagentPolicy
    budget: Budget
    hidden_setup: HiddenSetup
    scoring: ScoringConfig

    def __post_init__(self) -> None:
        if self.schema_version != BENCHMARK_CASE_SCHEMA:
            raise BenchmarkContractError(f"schema_version must be {BENCHMARK_CASE_SCHEMA}")
        _agent_text(self.benchmark_version, "benchmark_version")
        _opaque_id(self.case_id, "case_id")
        if not isinstance(self.case_version, str) or not _CASE_VERSION.fullmatch(self.case_version):
            raise BenchmarkContractError("case_version must be a short version string")
        _text(self.scenario_family_id, "scenario_family_id")
        object.__setattr__(self, "partition",
                           _enum(BenchmarkPartition, self.partition, "partition"))
        if self.task_family != TASK_FAMILY:
            raise BenchmarkContractError("task_family must be RCA")
        for name, kind in (("world", WorldSpec), ("agent_projection", AgentProjectionSpec),
                           ("tool_policy", ToolPolicy), ("orchestration_policy",
                                                         OrchestrationPolicy),
                           ("subagent_policy", SubagentPolicy), ("budget", Budget),
                           ("hidden_setup", HiddenSetup), ("scoring", ScoringConfig)):
            if type(getattr(self, name)) is not kind:
                raise BenchmarkContractError(f"{name} must be a typed {kind.__name__}")
        family = self.scenario_family_id.lower()
        for name in ("case_id", "benchmark_version"):
            if family in getattr(self, name).lower():
                raise BenchmarkContractError(f"{name} must not encode the scenario family")
        if (self.budget.max_subagents != self.subagent_policy.max_count
                or self.budget.max_subagent_depth != self.subagent_policy.max_depth):
            raise BenchmarkContractError("budget subagent limits differ from subagent_policy")
        setup = self.hidden_setup
        if not _close(self.agent_projection.initial_time_hours,
                      setup.pre_incident_hours + setup.post_incident_hours):
            raise BenchmarkContractError("initial_time_hours differs from the hidden timeline")

    @classmethod
    def from_record(cls, record: Any) -> "BenchmarkCase":
        record = _object(record, "benchmark case", (
            "schema_version", "benchmark_version", "case_id", "case_version",
            "scenario_family_id", "partition", "task_family", "world", "agent_projection",
            "tool_policy", "orchestration_policy", "subagent_policy", "budget",
            "hidden_setup", "scoring"))
        return cls(**{
            **record, "world": _world(record["world"]),
            "agent_projection": AgentProjectionSpec.from_record(record["agent_projection"]),
            "tool_policy": ToolPolicy.from_record(record["tool_policy"]),
            "orchestration_policy": OrchestrationPolicy.from_record(
                record["orchestration_policy"]),
            "subagent_policy": SubagentPolicy.from_record(record["subagent_policy"]),
            "budget": _budget(record["budget"]),
            "hidden_setup": HiddenSetup.from_record(record["hidden_setup"]),
            "scoring": ScoringConfig.from_record(record["scoring"])})

    def identity(self) -> tuple[str, str, str]:
        return self.benchmark_version, self.case_id, self.case_version


# -- EvaluatorGroundTruth ---------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class CausalClaim:
    """Structured causal semantics; free text is never canonical scoring truth."""

    mechanism: CausalMechanism
    entity_id: str | None = None
    variable_or_actuator_id: str | None = None
    fault_family: str | None = None
    direction_or_mode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mechanism",
                           _enum(CausalMechanism, self.mechanism, "causal_claim.mechanism"))
        for name in CLAIM_FIELDS:
            if name != "mechanism":
                _optional_text(getattr(self, name), f"causal_claim.{name}")
        if self.mechanism == CausalMechanism.NO_ABNORMAL_CAUSE and any(
                getattr(self, name) is not None for name in CLAIM_FIELDS if name != "mechanism"):
            raise BenchmarkContractError("NO_ABNORMAL_CAUSE carries no cause fields")

    @classmethod
    def from_record(cls, record: Any) -> "CausalClaim":
        return cls(**_object(record, "causal_claim", ("mechanism",),
                             tuple(name for name in CLAIM_FIELDS if name != "mechanism")))

    def record(self) -> dict[str, Any]:
        return {name: to_jsonable(getattr(self, name)) for name in CLAIM_FIELDS}


@dataclass(frozen=True, kw_only=True)
class EvaluatorGroundTruth:
    """Separate EVALUATOR-only canonical source; never projected or given to the model."""

    schema_version: str
    benchmark_version: str
    case_id: str
    case_version: str
    causal_claim: CausalClaim

    def __post_init__(self) -> None:
        if self.schema_version != GROUND_TRUTH_SCHEMA:
            raise BenchmarkContractError(f"ground truth schema_version must be {GROUND_TRUTH_SCHEMA}")
        for name in ("benchmark_version", "case_id", "case_version"):
            _text(getattr(self, name), name)
        if type(self.causal_claim) is not CausalClaim:
            raise BenchmarkContractError("causal_claim must be a typed CausalClaim")

    @classmethod
    def from_record(cls, record: Any) -> "EvaluatorGroundTruth":
        record = _object(record, "ground truth", ("schema_version", "benchmark_version",
                                                  "case_id", "case_version", "causal_claim"))
        return cls(**{**record,
                      "causal_claim": CausalClaim.from_record(record["causal_claim"])})

    def identity(self) -> tuple[str, str, str]:
        return self.benchmark_version, self.case_id, self.case_version

    def record(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version,
                "benchmark_version": self.benchmark_version, "case_id": self.case_id,
                "case_version": self.case_version, "causal_claim": self.causal_claim.record()}


# -- AgentCaseProjection ----------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class AgentCaseProjection:
    schema_version: str
    benchmark_version: str
    case_id: str
    case_version: str
    incident_id: str
    goal: str
    trigger_signal_ids: tuple[str, ...]
    initial_time_hours: float
    allowed_tools: tuple[str, ...]
    budget: Budget
    projection_policy_version: str

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_PROJECTION_SCHEMA:
            raise BenchmarkContractError(f"schema_version must be {AGENT_PROJECTION_SCHEMA}")
        if type(self.budget) is not Budget:
            raise BenchmarkContractError("budget must be a typed runtime Budget")
        for name in ("trigger_signal_ids", "allowed_tools"):
            object.__setattr__(self, name, _sequence(getattr(self, name), name))
        object.__setattr__(self, "initial_time_hours",
                           _number(self.initial_time_hours, "initial_time_hours"))
        if hidden_vocabulary(canonical_json(self.record()).decode("utf-8")):
            raise BenchmarkContractError("Agent projection carries hidden-truth vocabulary")

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)

    def checksum(self) -> str:
        return checksum(self.record())


def project_case(case: BenchmarkCase) -> AgentCaseProjection:
    """Pure deterministic projection of explicitly Agent-visible fixture fields.

    It cannot read ground truth (it is never given it) and ignores hidden setup,
    scoring and scenario-family metadata entirely.
    """
    if type(case) is not BenchmarkCase:
        raise TypeError("BenchmarkCase required")
    visible = case.agent_projection
    return AgentCaseProjection(
        schema_version=AGENT_PROJECTION_SCHEMA, benchmark_version=case.benchmark_version,
        case_id=case.case_id, case_version=case.case_version,
        incident_id=visible.incident_id, goal=visible.goal,
        trigger_signal_ids=visible.trigger_signal_ids,
        initial_time_hours=visible.initial_time_hours,
        allowed_tools=tuple(sorted(case.tool_policy.allowed_tools)), budget=case.budget,
        projection_policy_version=visible.projection_policy_version)


def task_goal(projection: AgentCaseProjection) -> str:
    """The Agent-visible task text: the projected goal plus its trigger and time."""
    return (f"{projection.goal} Trigger signals: {', '.join(projection.trigger_signal_ids)}. "
            f"Incident observed at simulation time {projection.initial_time_hours!r} h.")


# -- canonical EVALUATOR-only sources ----------------------------------------------------
@dataclass(frozen=True)
class PackagedFixture:
    """A lab-packaged case/ground-truth pair with exact frozen content checksums.

    The checksums are frozen here, not recomputed from the bytes being attested.
    File names carry only the opaque case id.
    """

    benchmark_version: str
    case_id: str
    case_version: str
    case_path: str
    case_checksum: str
    ground_truth_path: str
    ground_truth_checksum: str

    @property
    def case_source_id(self) -> str:
        return f"tep-agent-lab.benchmark-case.{self.case_id}.v{self.case_version}"

    @property
    def ground_truth_source_id(self) -> str:
        return f"tep-agent-lab.benchmark-ground-truth.{self.case_id}.v{self.case_version}"


FIXTURE_DIRECTORY = "src/tep_agent_lab/fixtures/benchmarks"


def _packaged(case_id: str, case_checksum: str, ground_truth_checksum: str) -> PackagedFixture:
    return PackagedFixture(
        benchmark_version="tep-rca-benchmark/v0", case_id=case_id, case_version="1",
        case_path=f"{FIXTURE_DIRECTORY}/{case_id}.case.json", case_checksum=case_checksum,
        ground_truth_path=f"{FIXTURE_DIRECTORY}/{case_id}.ground-truth.json",
        ground_truth_checksum=ground_truth_checksum)


# The explicit EVALUATOR-only canonical fixture registry. It is never scanned from a
# directory and never reachable from an Agent tool or application view.
BENCHMARK_FIXTURES: Mapping[tuple[str, str], PackagedFixture] = MappingProxyType({
    (fixture.case_id, fixture.case_version): fixture for fixture in (
        _packaged("rca-dev-001",
                  "e3305b5cd4ba1e2c59c625e5e067ceb067c90a9b9db3631d4d38bc80c088e176",
                  "2feb22f1657624cbd739aeada44033758eceaa2555c540de1adaf381a91155f7"),
        _packaged("rca-dev-002",
                  "ea571876db66c11e40dfb554849b60d5819edd74f8044eabcf7046ad9c76caf5",
                  "7f6d3bb9621a72f56252db028633a60da7e09c4fa5f641b0846ee73c91a28ec0"),
        _packaged("rca-dev-003",
                  "f2ed7acf8fdd568e0832535d47db74820a00bef6db4120cbe6c31b98ad502a7d",
                  "09aa3063514ec22499494092d3fdc7eb35a87b46bf144daaf614ad6ebf8455a0"),
    )})
D0_FIXTURE = BENCHMARK_FIXTURES[("rca-dev-001", "1")]  # the D0.1 fixture, unchanged


def benchmark_context_sources(lab_revision: str, fixture: PackagedFixture = D0_FIXTURE
                              ) -> tuple[ContextSourceRef, ContextSourceRef]:
    """The two separate EVALUATOR-only sources: the case fixture and its ground truth."""
    identity = {"benchmark_version": fixture.benchmark_version, "case_id": fixture.case_id,
                "case_version": fixture.case_version}
    return (
        ContextSourceRef(
            source_id=fixture.case_source_id, repository=LAB_REPOSITORY,
            git_revision=lab_revision, path_or_ref=fixture.case_path,
            content_checksum=fixture.case_checksum, kind=BENCHMARK_CASE_KIND,
            schema_version=BENCHMARK_CASE_SCHEMA, visibility=Visibility.EVALUATOR,
            provenance=identity),
        ContextSourceRef(
            source_id=fixture.ground_truth_source_id, repository=LAB_REPOSITORY,
            git_revision=lab_revision, path_or_ref=fixture.ground_truth_path,
            content_checksum=fixture.ground_truth_checksum, kind=GROUND_TRUTH_KIND,
            schema_version=GROUND_TRUTH_SCHEMA, visibility=Visibility.EVALUATOR,
            provenance=identity),
    )


def benchmark_materializers() -> dict[str, SourceMaterializer]:
    """Pinned tep-sim sources plus the lab's own packaged benchmark fixtures."""
    return {TEP_SIM_REPOSITORY: PackageSourceMaterializer("tep_sim"),
            LAB_REPOSITORY: PackageSourceMaterializer("tep_agent_lab")}


def verified_fixture(data: bytes, expected_checksum: str) -> Any:
    """Bytes must equal the frozen checksum before they are parsed at all."""
    try:
        actual = content_checksum(data, CANONICAL_JSON_SHA256)
    except ValueError as exc:
        raise BenchmarkContractError("fixture is not JSON") from exc
    if actual != expected_checksum:
        raise BenchmarkContractError("fixture content differs from its frozen checksum")
    return strict_json(data)


# -- trusted setup ----------------------------------------------------------------------
class BenchmarkCaseSetup:
    """Trusted ``case_setup``: exactly the frozen hidden setup, then a typed attestation.

    Runs inside ``RunManager.prepare`` on P0's own reference world; never an Agent tool.
    """

    def __init__(self, harness: "BenchmarkHarness") -> None:
        case = harness.case
        self._setup = case.hidden_setup
        self._identity = case.identity()
        self._case_checksum = harness.fixture.case_checksum
        self._truth_checksum = harness.fixture.ground_truth_checksum
        self._world_checksum = checksum(case.world.record())

    def __call__(self, world: ReferenceWorld) -> CaseSetupAttestation:
        setup, operations = self._setup, 0
        if setup.pre_incident_hours > 0:
            world.advance(setup.pre_incident_hours)
            operations += 1
        world.environment.apply(DisturbanceIntervention(setup.intervention.disturbance_id,
                                                        setup.intervention.value))
        operations += 1
        if setup.post_incident_hours > 0:
            world.advance(setup.post_incident_hours)
            operations += 1
        observation = world.observe()  # sanitized Agent-visible observation only
        benchmark_version, case_id, case_version = self._identity
        return CaseSetupAttestation(
            schema_version=CASE_SETUP_ATTESTATION_VERSION, benchmark_version=benchmark_version,
            case_id=case_id, case_version=case_version,
            setup_policy_version=SETUP_POLICY_VERSION,
            case_source_checksum=self._case_checksum,
            ground_truth_source_checksum=self._truth_checksum,
            world_config_checksum=self._world_checksum,
            hidden_setup_checksum=setup.checksum(), operation_count=operations,
            final_simulation_time_hours=observation["simulation_time_hours"],
            final_agent_observation_checksum=checksum(observation))


# -- model-input capture and the D0 scripted blind provider -----------------------------
class ModelInputRecorder:
    """Evaluator-side capture of exactly what each model turn received.

    Wraps the provider; the capture is never fed back into any model input.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.turns: list[Any] = []

    def generate(self, context_projection: Any, tool_specs: Sequence[Any],
                 output_schema: Mapping[str, Any], limits: Mapping[str, Any]) -> ModelTurn:
        self.turns.append(freeze_json({
            "context_projection": context_projection, "tool_specs": list(tool_specs),
            "output_schema": output_schema, "limits": limits}))
        return self._provider.generate(context_projection, tool_specs, output_schema, limits)


_GOAL_SIGNAL = re.compile(r"\b(?:XMEAS|XMV)\([1-9][0-9]*\)")
_GOAL_TIME = re.compile(r"simulation time ([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?) h")
SCRIPTED_HISTORY_MAX_HOURS = 4.0


def scripted_blind_provider() -> FakeProvider:
    """D0 plumbing provider: capability summary, trigger-signal history, finish.

    It takes no harness/case/truth input. Signal ids and the window are read from
    the Agent-visible task goal in each ContextProjection. It claims no diagnosis.
    """
    def bind(projection: Any, limits: Mapping[str, Any], action: Action, **turn: Any
             ) -> ModelTurn:
        return ModelTurn(f"d0-turn-{limits['budget_usage']['model_calls']}",
                         limits["context_projection_ref"], projection.base_revision, action,
                         **turn)

    def capabilities(projection: Any, limits: Mapping[str, Any]) -> ModelTurn:
        return bind(projection, limits, Action.TOOL_REQUEST, tool_request=ToolCallRequest(
            "d0-1-capabilities", "get_capability_summary", {}))

    def history(projection: Any, limits: Mapping[str, Any]) -> ModelTurn:
        goal = projection.content["task_state"]["goal"]
        signals = list(dict.fromkeys(_GOAL_SIGNAL.findall(goal)))
        time = _GOAL_TIME.search(goal)
        elapsed = float(time.group(1)) if time else 0.0
        window = min(elapsed, SCRIPTED_HISTORY_MAX_HOURS) if elapsed > 0 else 1.0
        return bind(projection, limits, Action.TOOL_REQUEST, tool_request=ToolCallRequest(
            "d0-2-history", "get_history", {"window_hours": window, "variables": signals}))

    def finish(projection: Any, limits: Mapping[str, Any]) -> ModelTurn:
        return bind(projection, limits, Action.FINISH_PROPOSAL, finish_proposal=FinishProposal(
            {"summary": "D0 scripted blind observation sequence complete; "
                        "no causal conclusion is claimed"}))

    return FakeProvider([capabilities, history, finish])


D0_MODEL = ModelSpec("fake", "d0-scripted-blind", "v0", "d0-script/v0",
                     config={"scripted": True})


# -- harness ----------------------------------------------------------------------------
class BenchmarkHarness:
    """Trusted application/evaluator assembly of one benchmark case for P0.

    It builds inputs for the existing ``RunManager`` lifecycle and never calls Agent
    tools, schedules work, touches private run state, or creates a world itself.
    """

    def __init__(self, case: BenchmarkCase, truth: EvaluatorGroundTruth, *,
                 lab_revision: str, fixture: PackagedFixture = D0_FIXTURE,
                 read: Callable[[str], bytes] | None = None) -> None:
        if type(case) is not BenchmarkCase or type(truth) is not EvaluatorGroundTruth:
            raise TypeError("typed BenchmarkCase and EvaluatorGroundTruth required")
        if case.identity() != truth.identity():
            raise BenchmarkContractError("ground truth identity differs from the case")
        if case.identity() != (fixture.benchmark_version, fixture.case_id, fixture.case_version):
            raise BenchmarkContractError("case identity differs from its packaged fixture")
        if case.scoring.scorer_version != SCORER_VERSION:
            raise BenchmarkContractError("case scorer_version is not this scorer")
        # The attestation reports the fixture's frozen checksums, so the objects this
        # harness applies and scores must be exactly the frozen fixture content.
        frozen_case, frozen_truth = _read_fixture(fixture, read)
        if case != frozen_case or truth != frozen_truth:
            raise BenchmarkContractError("case or ground truth differs from its frozen fixture")
        self.case, self.truth, self.fixture = case, truth, fixture
        self.lab_revision = lab_revision
        self.projection = project_case(case)
        visible = canonical_json(self.projection.record()).decode("utf-8").lower()
        for label in _truth_labels(case, truth):
            if label in visible:
                raise BenchmarkContractError("Agent projection carries an evaluator truth label")
        self.projection_checksum = self.projection.checksum()

    @classmethod
    def load(cls, lab_revision: str, fixture: PackagedFixture = D0_FIXTURE, *,
             read: Callable[[str], bytes] | None = None) -> "BenchmarkHarness":
        case, truth = _read_fixture(fixture, read)
        return cls(case, truth, lab_revision=lab_revision, fixture=fixture, read=read)

    def run_request(self, model: ModelSpec = D0_MODEL, *,
                    require_conclusion: bool = False) -> RunRequest:
        projection = self.projection
        return RunRequest(investigation_id=projection.incident_id, goal=task_goal(projection),
                          world=self.case.world, model=model, budget=projection.budget,
                          allowed_tools=projection.allowed_tools,
                          require_conclusion=require_conclusion)

    def benchmark_refs(self) -> BenchmarkRefs:
        case = self.case
        return BenchmarkRefs(
            benchmark_version=case.benchmark_version, case_id=case.case_id,
            case_version=case.case_version, partition=case.partition,
            benchmark_case_source_id=self.fixture.case_source_id,
            evaluator_ground_truth_source_id=self.fixture.ground_truth_source_id,
            agent_projection_checksum=self.projection_checksum,
            scorer_version=case.scoring.scorer_version,
            setup_policy_version=SETUP_POLICY_VERSION,
            leakage_policy_version=LEAKAGE_POLICY_VERSION)

    def case_setup(self) -> BenchmarkCaseSetup:
        return BenchmarkCaseSetup(self)

    def context_sources(self, tep_sim_revision: str) -> tuple[ContextSourceRef, ...]:
        return (*pinned_tep_sim_sources(tep_sim_revision),
                *benchmark_context_sources(self.lab_revision, self.fixture))

    def hidden_sources(self, tep_sim_revision: str) -> tuple[ContextSourceRef, ...]:
        return tuple(source for source in self.context_sources(tep_sim_revision)
                     if source.visibility != Visibility.AGENT)

    def create_and_prepare(self, manager: RunManager, run_id: str, provider: Any, *,
                           model: ModelSpec = D0_MODEL) -> RunManifest:
        """The ordinary P0 path: ``create`` then ``prepare`` with trusted setup.

        ``binding_findings`` is the separate EVALUATOR acceptance check of the result.
        """
        manager.create(run_id, self.run_request(model))
        return manager.prepare(
            run_id, provider=provider,
            context_sources=self.context_sources(manager.revisions.tep_sim),
            case_setup=self.case_setup(), benchmark=self.benchmark_refs())

    def binding_findings(self, manifest: RunManifest) -> list[str]:
        """EVALUATOR check that a prepared run hosts exactly this case's Agent projection.

        P0 cannot interpret ``agent_projection_checksum`` (it does not own the
        projection schema) and only checks its shape; this harness-side check closes
        that gap for any manifest, however the run was prepared.
        """
        expected = self.run_request()  # the model identity is not part of the projection
        task, policy = manifest.task, manifest.runtime_policy
        checks = {
            "benchmark": dict(manifest.benchmark).get("agent_projection_checksum")
            == self.projection_checksum,
            "investigation_id": task["investigation_id"] == expected.investigation_id,
            "goal": task["goal"] == expected.goal,
            "budget": task["budget"] == freeze_json(to_jsonable(expected.budget)),
            "allowed_tools": tuple(policy["allowed_tools"]) == tuple(sorted(
                expected.allowed_tools)),
            "world": manifest.world["environment_config"] == freeze_json(
                expected.world.record()),
        }
        return sorted(name for name, ok in checks.items() if not ok)

    def audit(self, surfaces: Mapping[str, Any], *, tep_sim_revision: str) -> "LeakageAudit":
        return audit_agent_surfaces(
            case=self.case, truth=self.truth, agent_projection_checksum=self.projection_checksum,
            hidden_sources=self.hidden_sources(tep_sim_revision), surfaces=surfaces)


def _read_fixture(fixture: PackagedFixture, read: Callable[[str], bytes] | None
                  ) -> tuple[BenchmarkCase, EvaluatorGroundTruth]:
    read = read or PackageSourceMaterializer("tep_agent_lab").read_bytes
    case = BenchmarkCase.from_record(verified_fixture(read(fixture.case_path),
                                                      fixture.case_checksum))
    truth = EvaluatorGroundTruth.from_record(verified_fixture(
        read(fixture.ground_truth_path), fixture.ground_truth_checksum))
    return case, truth


def load_fixture(case_id: str, case_version: str, *, lab_revision: str,
                 read: Callable[[str], bytes] | None = None) -> BenchmarkHarness:
    """EVALUATOR: the harness for one registered fixture; unknown identities fail closed."""
    fixture = BENCHMARK_FIXTURES.get((case_id, case_version))
    if fixture is None:
        raise BenchmarkContractError("no registered benchmark fixture has this identity")
    return BenchmarkHarness.load(lab_revision, fixture, read=read)


def iter_development_fixtures(lab_revision: str, *, read: Callable[[str], bytes] | None = None
                              ) -> Iterator[BenchmarkHarness]:
    """EVALUATOR: harnesses for every registered DEVELOPMENT fixture, in registry order."""
    for fixture in BENCHMARK_FIXTURES.values():
        harness = BenchmarkHarness.load(lab_revision, fixture, read=read)
        if harness.case.partition == BenchmarkPartition.DEVELOPMENT:
            yield harness


def _truth_labels(case: BenchmarkCase, truth: EvaluatorGroundTruth) -> tuple[str, ...]:
    """Evaluator labels whose appearance in Agent data is a leak.

    ``entity_id`` is excluded on purpose: it names a ProcessGraph entity that blind
    topology already shows. ``direction_or_mode`` is a generic word (STEP).
    """
    claim = truth.causal_claim
    labels = [case.hidden_setup.intervention.disturbance_id, case.scenario_family_id,
              claim.mechanism.value, claim.fault_family]
    return tuple(label.lower() for label in labels if label)


# -- LeakageAudit -----------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class LeakageFinding:
    """Where and what kind of leak; never the hidden payload itself."""

    surface: str
    location: str
    category: LeakageCategory

    def __post_init__(self) -> None:
        if not isinstance(self.surface, str) or not _SURFACE.fullmatch(self.surface):
            raise BenchmarkContractError("finding surface must be a short identifier")
        _text(self.location, "finding location")
        object.__setattr__(self, "category",
                           _enum(LeakageCategory, self.category, "finding category"))


@dataclass(frozen=True, kw_only=True)
class LeakageAudit:
    """EVALUATOR output. Any finding fails the audit."""

    schema_version: str
    leakage_policy_version: str
    benchmark_version: str
    case_id: str
    case_version: str
    agent_projection_checksum: str
    findings: tuple[LeakageFinding, ...]
    passed: bool

    def __post_init__(self) -> None:
        if self.schema_version != LEAKAGE_AUDIT_SCHEMA:
            raise BenchmarkContractError(f"schema_version must be {LEAKAGE_AUDIT_SCHEMA}")
        findings = tuple(item if type(item) is LeakageFinding else LeakageFinding(**item)
                         for item in _sequence(self.findings, "findings"))
        object.__setattr__(self, "findings", findings)
        if self.passed is not (not findings):
            raise BenchmarkContractError("an audit passes exactly when it has no findings")

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)


def _walk(value: Any, path: str, hidden: Callable[[str], bool]
          ) -> Iterator[tuple[str, Any]]:
    """(location, item) for every node and every object key.

    A key is echoed into a location only when it is a plain identifier with no
    hidden vocabulary; otherwise it is addressed by position.
    """
    yield path, value
    if isinstance(value, dict):
        for index, key in enumerate(sorted(value)):
            label = f"{path}.<key {index}>"
            yield label, key
            clean = _SAFE_KEY.fullmatch(key) and not hidden(key)
            yield from _walk(value[key], f"{path}.{key}" if clean else label, hidden)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]", hidden)


def audit_agent_surfaces(*, case: BenchmarkCase, truth: EvaluatorGroundTruth,
                         agent_projection_checksum: str,
                         hidden_sources: Sequence[ContextSourceRef],
                         surfaces: Mapping[str, Any]) -> LeakageAudit:
    """Deterministic hard-fail audit of Agent-visible surfaces for evaluator truth.

    Checks every key and string for disturbance-id spellings and hidden-truth
    vocabulary, exact evaluator labels/source refs/setup checksum, and structurally
    for EVALUATOR-visibility refs or hidden source kinds. Findings carry location and
    category only.
    """
    tokens: list[tuple[str, LeakageCategory]] = [
        (label, LeakageCategory.HIDDEN_TRUTH_LABEL) for label in _truth_labels(case, truth)]
    tokens.append((case.hidden_setup.checksum(), LeakageCategory.HIDDEN_SETUP_CHECKSUM))
    for source in hidden_sources:
        for token in (source.source_id, source.path_or_ref,
                      PurePosixPath(source.path_or_ref).name, source.content_checksum):
            tokens.append((token.lower(), LeakageCategory.HIDDEN_SOURCE_REF))
    hidden_kinds = {source.kind for source in hidden_sources}

    def hidden_key(key: str) -> bool:
        lowered = key.lower()
        return hidden_vocabulary(key) or any(token in lowered for token, _ in tokens)

    found: set[tuple[str, str, LeakageCategory]] = set()
    for surface in sorted(surfaces):
        if not isinstance(surface, str) or not _SURFACE.fullmatch(surface):
            raise BenchmarkContractError("surface names must be short identifiers")
        for location, item in _walk(to_jsonable(surfaces[surface]), "$", hidden_key):
            if isinstance(item, dict):
                kind = item.get("kind")
                if item.get("visibility") == Visibility.EVALUATOR.value or (
                        isinstance(kind, str) and kind in hidden_kinds):
                    found.add((surface, location, LeakageCategory.EVALUATOR_STRUCTURE))
                continue
            if not isinstance(item, str):
                continue
            if names_disturbance_id(item):
                found.add((surface, location, LeakageCategory.HIDDEN_DISTURBANCE_ID))
            elif hidden_vocabulary(item):
                found.add((surface, location, LeakageCategory.HIDDEN_VOCABULARY))
            text = item.lower()
            for token, category in tokens:
                if token in text:
                    found.add((surface, location, category))
    findings = tuple(LeakageFinding(surface=surface, location=location, category=category)
                     for surface, location, category in sorted(found))
    return LeakageAudit(
        schema_version=LEAKAGE_AUDIT_SCHEMA, leakage_policy_version=LEAKAGE_POLICY_VERSION,
        benchmark_version=case.benchmark_version, case_id=case.case_id,
        case_version=case.case_version, agent_projection_checksum=agent_projection_checksum,
        findings=findings, passed=not findings)


def collect_agent_surfaces(manager: RunManager, run_id: str, *,
                           projection: AgentCaseProjection,
                           model_inputs: Sequence[Any] | None = None) -> dict[str, Any]:
    """AGENT-scoped public reads of one in-process run plus the exact model inputs.

    Model inputs are the ContextProjections the runtime persisted for every turn,
    read through the trusted EVALUATOR artifact view; an optional live
    ``ModelInputRecorder`` capture (tool specs, limits) is audited as well.
    """
    queries = manager.queries(run_id, ProjectionScope.AGENT)
    evaluator = manager.queries(run_id, ProjectionScope.EVALUATOR)
    artifacts = queries.artifacts()
    persisted = [evaluator.get_artifact(InformationRef(**ref))["content"]
                 for ref in evaluator.artifacts()["artifacts"]
                 if ref["kind"] == "ContextProjection"]
    surfaces = {
        "agent_case_projection": projection.record(),
        "agent_manifest": queries.manifest_view(),
        "agent_context_inventory": queries.context_inventory(),
        "agent_run_summary": queries.run_summary(),
        "agent_process_graph": queries.process_graph(),
        "agent_investigation": queries.investigation(),
        "agent_events": queries.events(),
        "agent_budget": queries.budget(),
        "agent_artifacts": artifacts,
        "agent_artifact_contents": [queries.get_artifact(InformationRef(**ref))
                                    for ref in artifacts["artifacts"]],
        "agent_telemetry": queries.telemetry(),
        "agent_branch_tree": queries.branch_tree(),
        "model_context_projections": persisted,
    }
    if model_inputs is not None:
        surfaces["model_inputs"] = list(model_inputs)
    return surfaces


# -- deterministic scoring --------------------------------------------------------------
@dataclass(frozen=True)
class Metric:
    status: MetricStatus
    value: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(MetricStatus, self.status, "metric status"))
        if (self.status == MetricStatus.AVAILABLE) == (self.value is None):
            raise BenchmarkContractError("only an AVAILABLE metric carries a value")
        object.__setattr__(self, "value", freeze_json(self.value))


_NOT_APPLICABLE = Metric(MetricStatus.NOT_APPLICABLE)
_NOT_AVAILABLE = Metric(MetricStatus.NOT_AVAILABLE)


def _available(value: Any) -> Metric:
    return _NOT_AVAILABLE if value is None else Metric(MetricStatus.AVAILABLE, value)


@dataclass(frozen=True, kw_only=True)
class SubmittedRef:
    ref_id: str
    visibility: Visibility

    def __post_init__(self) -> None:
        _text(self.ref_id, "ref_id")
        object.__setattr__(self, "visibility", _enum(Visibility, self.visibility, "visibility"))


@dataclass(frozen=True, kw_only=True)
class SubmissionResources:
    """Resource usage from saved records; None means not recorded, never zero."""

    model_calls: int | None
    tool_calls: int | None
    rollout_count: int | None
    simulated_horizon_seconds: float | None

    def __post_init__(self) -> None:
        for name in ("model_calls", "tool_calls", "rollout_count"):
            if getattr(self, name) is not None:
                _integer(getattr(self, name), name)
        if self.simulated_horizon_seconds is not None:
            object.__setattr__(self, "simulated_horizon_seconds", _number(
                self.simulated_horizon_seconds, "simulated_horizon_seconds"))


@dataclass(frozen=True, kw_only=True)
class BenchmarkSubmission:
    """Saved evaluator-side scoring input; independent of live model/simulator state."""

    schema_version: str
    benchmark_version: str
    case_id: str
    case_version: str
    source: SubmissionSource
    run_id: str | None
    causal_claim: CausalClaim | None
    cited_evidence_refs: tuple[SubmittedRef, ...]
    available_observation_ref_ids: tuple[str, ...]
    resources: SubmissionResources
    terminal_task_status: str | None

    def __post_init__(self) -> None:
        if self.schema_version != SUBMISSION_SCHEMA:
            raise BenchmarkContractError(f"schema_version must be {SUBMISSION_SCHEMA}")
        for name in ("benchmark_version", "case_id", "case_version"):
            _text(getattr(self, name), name)
        object.__setattr__(self, "source", _enum(SubmissionSource, self.source, "source"))
        _optional_text(self.run_id, "run_id")
        _optional_text(self.terminal_task_status, "terminal_task_status")
        if self.causal_claim is not None and type(self.causal_claim) is not CausalClaim:
            raise BenchmarkContractError("causal_claim must be a typed CausalClaim or null")
        cited = _sequence(self.cited_evidence_refs, "cited_evidence_refs")
        if any(type(ref) is not SubmittedRef for ref in cited):
            raise BenchmarkContractError("cited_evidence_refs must be typed SubmittedRefs")
        if len({ref.ref_id for ref in cited}) != len(cited):
            raise BenchmarkContractError("cited_evidence_refs has duplicates")
        available = _sequence(self.available_observation_ref_ids,
                              "available_observation_ref_ids")
        if len(set(available)) != len(available):
            raise BenchmarkContractError("available_observation_ref_ids has duplicates")
        for ref_id in available:
            _text(ref_id, "available_observation_ref_ids")
        if type(self.resources) is not SubmissionResources:
            raise BenchmarkContractError("resources must be typed SubmissionResources")
        object.__setattr__(self, "cited_evidence_refs", cited)
        object.__setattr__(self, "available_observation_ref_ids", available)

    def identity(self) -> tuple[str, str, str]:
        return self.benchmark_version, self.case_id, self.case_version

    def record(self) -> dict[str, Any]:
        record = to_jsonable(self)
        record["causal_claim"] = (None if self.causal_claim is None
                                  else self.causal_claim.record())
        return record

    def checksum(self) -> str:
        return checksum(self.record())

    @classmethod
    def from_record(cls, record: Any) -> "BenchmarkSubmission":
        record = _object(record, "submission", (
            "schema_version", "benchmark_version", "case_id", "case_version", "source",
            "run_id", "causal_claim", "cited_evidence_refs", "available_observation_ref_ids",
            "resources", "terminal_task_status"))
        refs = record["cited_evidence_refs"]
        if not isinstance(refs, list):
            raise BenchmarkContractError("cited_evidence_refs must be a list")
        claim = record["causal_claim"]
        return cls(**{
            **record,
            "causal_claim": None if claim is None else CausalClaim.from_record(claim),
            "cited_evidence_refs": tuple(SubmittedRef(**_object(
                ref, "cited evidence ref", ("ref_id", "visibility"))) for ref in refs),
            "available_observation_ref_ids": _sequence(record["available_observation_ref_ids"],
                                                       "available_observation_ref_ids"),
            "resources": SubmissionResources(**_object(record["resources"], "resources", (
                "model_calls", "tool_calls", "rollout_count", "simulated_horizon_seconds")))})


@dataclass(frozen=True, kw_only=True)
class BenchmarkScore:
    """Deterministic metric vector. There is deliberately no aggregate scalar."""

    schema_version: str
    scorer_version: str
    benchmark_version: str
    case_id: str
    case_version: str
    submission_checksum: str
    ground_truth_checksum: str
    scoring_config_checksum: str
    metrics: Mapping[str, Metric]

    def __post_init__(self) -> None:
        if tuple(self.metrics) != METRIC_NAMES or any(
                type(metric) is not Metric for metric in self.metrics.values()):
            raise BenchmarkContractError("score metrics must be exactly the frozen vector")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.record())


def score_submission(submission: BenchmarkSubmission, truth: EvaluatorGroundTruth,
                     config: ScoringConfig) -> BenchmarkScore:
    """Pure function of saved records: no model, simulator, UI, or clock."""
    if type(submission) is not BenchmarkSubmission or type(truth) is not EvaluatorGroundTruth \
            or type(config) is not ScoringConfig:
        raise TypeError("typed submission, ground truth, and scoring config required")
    if submission.identity() != truth.identity():
        raise BenchmarkContractError("submission identity differs from the ground truth")
    if config.scorer_version != SCORER_VERSION:
        raise BenchmarkContractError("scoring config names a different scorer version")
    claim, expected = submission.causal_claim, truth.causal_claim
    metrics: dict[str, Metric] = {}
    matches = []
    for name in CLAIM_FIELDS:
        if name not in config.causal_claim_match_fields:
            metrics[_CLAIM_METRICS[name]] = _NOT_APPLICABLE
        elif claim is None:
            metrics[_CLAIM_METRICS[name]] = _NOT_AVAILABLE
        else:
            matches.append(getattr(claim, name) == getattr(expected, name))
            metrics[_CLAIM_METRICS[name]] = _available(matches[-1])
    top1 = _NOT_AVAILABLE if claim is None else _available(all(matches))
    healthy_truth = expected.mechanism == CausalMechanism.NO_ABNORMAL_CAUSE
    if not config.healthy_outcome_enabled:
        healthy = _NOT_APPLICABLE
    elif claim is None:
        healthy = _NOT_AVAILABLE
    else:
        healthy = _available((claim.mechanism == CausalMechanism.NO_ABNORMAL_CAUSE)
                             == healthy_truth)
    evidence = _evidence_counts(submission)
    counts = evidence["counts"]
    resources = submission.resources
    metrics = {
        "top1_causal_claim_exact": top1,
        **{name: metrics[name] for name in METRIC_NAMES[1:6]},
        "healthy_no_abnormal_correct": healthy,
        "evidence_ref_status_counts": _available(evidence),
        "hidden_ref_violation_count": _available(
            counts[EvidenceRefStatus.HIDDEN_REF_VIOLATION.value]),
        "unsupported_narrative_claim_count": _available(
            counts[EvidenceRefStatus.UNSUPPORTED_NARRATIVE_CLAIM.value]),
        "model_calls": _available(resources.model_calls),
        "tool_calls": _available(resources.tool_calls),
        "rollout_count": _available(resources.rollout_count),
        "simulated_horizon_seconds": _available(resources.simulated_horizon_seconds),
        "terminal_task_status": _available(submission.terminal_task_status),
    }
    return BenchmarkScore(
        schema_version=SCORE_SCHEMA, scorer_version=SCORER_VERSION,
        benchmark_version=truth.benchmark_version, case_id=truth.case_id,
        case_version=truth.case_version, submission_checksum=submission.checksum(),
        ground_truth_checksum=checksum(truth.record()),
        scoring_config_checksum=checksum(config.record()), metrics=metrics)


def _evidence_counts(submission: BenchmarkSubmission) -> dict[str, Any]:
    """Structural classification only.

    Scorer v0 has no frozen evidence-relevance configuration, so a valid cited ref is
    not classified relevant/irrelevant: those two counts are null and the number of
    such refs is reported separately rather than guessed.
    """
    available = set(submission.available_observation_ref_ids)
    counts: dict[str, int | None] = {status.value: 0 for status in EvidenceRefStatus}
    counts[EvidenceRefStatus.VALID_RELEVANT_REF.value] = None
    counts[EvidenceRefStatus.VALID_BUT_IRRELEVANT_REF.value] = None
    valid = 0
    for ref in submission.cited_evidence_refs:
        if ref.visibility != Visibility.AGENT:
            counts[EvidenceRefStatus.HIDDEN_REF_VIOLATION.value] += 1
        elif ref.ref_id not in available:
            counts[EvidenceRefStatus.MISSING_REF.value] += 1
        else:
            valid += 1
    cited = {ref.ref_id for ref in submission.cited_evidence_refs}
    counts[EvidenceRefStatus.UNUSED_OBSERVATION.value] = len(available - cited)
    claim = submission.causal_claim
    if (claim is not None and claim.mechanism != CausalMechanism.NO_ABNORMAL_CAUSE
            and valid == 0):
        counts[EvidenceRefStatus.UNSUPPORTED_NARRATIVE_CLAIM.value] = 1
    return {"counts": counts, "valid_refs_relevance_unclassified": valid}


def _whole(value: Any, name: str) -> int:
    """An integral usage count; a fractional draw is an error, never truncated."""
    number = _number(value, name)
    if not number.is_integer():
        raise BenchmarkContractError(f"{name} must be a whole number")
    return int(number)


def submission_from_run(manager: RunManager, run_id: str, *,
                        projection: AgentCaseProjection) -> BenchmarkSubmission:
    """Saved scoring input from a finished P0 run, via trusted/EVALUATOR public reads.

    D0 has no structured RcaResult boundary yet: the run's causal claim and cited
    evidence are recorded as absent (null/empty) rather than inferred from text.
    """
    outcome = manager.get(run_id).outcome
    if outcome is None:
        raise BenchmarkContractError("run has no terminal outcome to score")
    evaluator = manager.queries(run_id, ProjectionScope.EVALUATOR)
    observations = tuple(item["ref"]["ref_id"]
                         for item in evaluator.investigation()["observations"])
    runtime = outcome.runtime_result
    usage = {} if runtime is None else runtime["budget_usage"]
    budgeted = manager.manifest(run_id).task["budget"]["extra_dimensions"]

    def drawn(dimension: str) -> Any:
        # A budgeted dimension with no recorded draw was not drawn; an unbudgeted or
        # unrecorded one is unknown.
        if runtime is None or dimension not in budgeted:
            return None
        return usage.get(dimension, 0)

    rollouts = drawn("simulation_rollouts")
    return BenchmarkSubmission(
        schema_version=SUBMISSION_SCHEMA, benchmark_version=projection.benchmark_version,
        case_id=projection.case_id, case_version=projection.case_version,
        source=SubmissionSource.RUN_OUTCOME, run_id=run_id, causal_claim=None,
        cited_evidence_refs=(), available_observation_ref_ids=observations,
        resources=SubmissionResources(
            model_calls=usage.get("model_calls"), tool_calls=usage.get("tool_calls"),
            rollout_count=None if rollouts is None else _whole(rollouts, "simulation_rollouts"),
            simulated_horizon_seconds=drawn("simulated_horizon_seconds")),
        terminal_task_status=None if runtime is None else runtime["task_status"])
