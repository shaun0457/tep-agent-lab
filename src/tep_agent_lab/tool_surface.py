"""Blind-RCA TEP Tool Surface v0 (docs/specs/tool-surface-v0.md).

A narrow typed Agent-facing catalog over the tep-sim A1-A4 world contracts and the
runtime B1-B3 control contracts. One ``BlindRcaToolSurface`` supplies the trusted
runtime hooks for this surface:

- ``tool_specs``/``gate_policy``    -> explicit registry and execution authority;
- ``validate_request``              -> lab consumer pre-execution validation (after G0-G3);
- ``execute``                       -> the Executor adapter;
- ``verify_result``                 -> lab consumer post-execution invariants (inside B3);
- ``reference_revision``            -> the runtime ReferenceStateGuard.

Generic schema/budget/side-effect gating (B2) and generic result verification (B3)
stay in the runtime; result ingestion stays the C1 ``RcaResultIngestor``. Evaluator
bindings (``tep_sim.evaluator_bindings``) are never imported here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any

from industrial_agent_runtime import (
    GateDecision, GatePolicy, InformationRef, SideEffectClass, StateDelta, Task,
    ToolCallRequest, ToolResult, ToolSpec, Visibility, checksum, to_jsonable,
)
from tep_sim import (CAPABILITY_VERSION, REGISTRY, SAFETY_LIMITS, SAFETY_LIMITS_VERSION,
                     SCENARIO_MAPPING_VERSION, UPSTREAM_REVISION, AmbiguousScenario,
                     IncompatibleControlMode, InvalidEnvironmentState, InvalidIntervention,
                     InvalidScenario, MVConstraint, MVIntervention, ScenarioRejected,
                     ScenarioRequest, SimulationFailure, SnapshotFailure, SupportedScenario,
                     UnknownProcessEntity, UnsupportedCapability, UnsupportedScenario)
from tep_sim.snapshot import ENVIRONMENT_VERSION

from .tep_world import (REFERENCE, ArtifactStore, ReferenceWorld, SimulationSandbox,
                        WorldError, leakage_findings, read_telemetry, sanitize_observation)

TOOL_SURFACE_VERSION = "tep-agent-lab.tool-surface/v0"
POLICY_VERSION = "tep-agent-lab.blind-rca-policy/v0"
SANDBOX_POLICY_TAG = "tep.isolated_branch"
ISOLATION_GUARANTEE = ("tep-sim A2 fork: only a forked branch is stepped or intervened on; "
                       "the reference environment is never stepped, intervened on, or "
                       "rewritten")
SIMULATION_DIMENSIONS = ("simulation_snapshots", "simulation_branches",
                         "simulation_rollouts", "simulated_horizon_seconds")
SUCCESS = "SUCCESS"  # runtime RESULT_SUCCESS; the spec's "OK" outcome
FAILURE_CODES = frozenset({"INVALID_REQUEST", "UNSUPPORTED_CAPABILITY", "POLICY_DENIED",
                           "BUDGET_DENIED", "SIMULATION_FAILED", "ARTIFACT_ERROR",
                           "STALE_STATE"})
# Answer-leaking or high-authority tools that the default blind-RCA registry never holds.
BLIND_RCA_EXCLUDED_TOOLS = frozenset({
    "get_related_disturbances", "list_disturbances", "get_fault_bindings",
    "list_candidate_causes", "list_semantic_scenarios", "propose_intervention",
    "validate_intervention", "apply_validated_intervention",
})
INGESTIBLE_OPERATIONS = frozenset({"REGISTER_OBSERVATION", "REGISTER_ARTIFACT_REF"})
_ENABLED_CLASSES = frozenset({SideEffectClass.READ, SideEffectClass.SIMULATE})
_DEFAULT_PREVIEW = tuple(dict.fromkeys(limit.variable for limit in SAFETY_LIMITS))


@dataclass(frozen=True)
class ToolSurfaceLimits:
    """Model-visible bounds of the blind-RCA surface (lab policy, not physics)."""

    max_history_window_hours: float = 4.0
    max_variables: int = 8
    max_preview_points: int = 12
    max_metadata_ids: int = 16
    max_neighbor_depth: int = 3
    max_rollout_horizon_hours: float = 1.0
    max_scenarios_per_rollout: int = 2

    def __post_init__(self) -> None:
        for name in ("max_history_window_hours", "max_rollout_horizon_hours"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        for name in ("max_variables", "max_preview_points", "max_metadata_ids",
                     "max_neighbor_depth", "max_scenarios_per_rollout"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_preview_points < 2:
            raise ValueError("max_preview_points must allow a first and last point")


def tool_version(name: str) -> str:
    return f"{TOOL_SURFACE_VERSION}/{name}@1"


def simulation_quota(*, snapshots: int, branches: int, rollouts: int,
                     horizon_seconds: float) -> dict[str, float]:
    """Task ``Budget.extra_dimensions`` for the SIMULATE dimensions of this surface."""
    return {"simulation_snapshots": snapshots, "simulation_branches": branches,
            "simulation_rollouts": rollouts, "simulated_horizon_seconds": horizon_seconds}


class ResultInvariantError(ValueError):
    """A lab post-execution invariant failed; the result must not be ingested."""


# -- schemas (runtime G0 subset) ------------------------------------------------------
def _obj(properties: Mapping[str, Any], required: Sequence[str] = (),
         extra: Any = False) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": list(required),
            "additionalProperties": extra}


_TEXT = {"type": "string"}
_NUMBER = {"type": "number"}
_NUMBERS = {"type": "object", "additionalProperties": _NUMBER}
_VARIABLE = {"type": "string", "pattern": r"^(XMEAS|XMV)\([1-9][0-9]*\)$"}
_NODE_ID = {"type": "string", "pattern": r"^[a-z][a-z0-9_]*$", "maxLength": 64}
_BRANCH_ID = {"type": "string", "pattern": r"^branch-[0-9]{4,}$"}
_SNAPSHOT_ID = {"type": "string", "pattern": r"^snapshot-[0-9]{4,}$"}
_SCENARIO = _obj({"scenario_id": {"type": "string", "pattern": r"^[a-z][a-z0-9_]*$",
                                  "maxLength": 128},
                  "parameters": _NUMBERS}, ("scenario_id",))
_ARTIFACT_REF = _obj({"ref_id": _TEXT, "kind": _TEXT, "owner": _TEXT, "version": _TEXT,
                      "visibility": {"const": "AGENT"}, "created_at": _TEXT,
                      "checksum": _TEXT},
                     ("ref_id", "kind", "owner", "version", "visibility", "created_at",
                      "checksum"))
_ARTIFACT_REF_OR_NULL = {**_ARTIFACT_REF, "type": ["object", "null"]}
_PREVIEW = _obj({"simulation_time_hours": {"type": "array", "items": _NUMBER},
                 "values": {"type": "object",
                            "additionalProperties": {"type": "array", "items": _NUMBER}}},
                ("simulation_time_hours", "values"))
_SHAPE = _obj({"records": {"type": "integer", "minimum": 0},
               "variables": {"type": "integer", "minimum": 0}}, ("records", "variables"))
_NODE_SUMMARY = _obj({"node_id": _TEXT, "kind": _TEXT, "name": _TEXT}, ("node_id", "kind", "name"))
_BINDING = {"type": "object"}
_OBSERVATION = _obj({"simulation_time_hours": _NUMBER, "measurements": _NUMBERS,
                     "manipulated_variables": _NUMBERS, "shutdown_state": {"type": "boolean"},
                     "safety_margins": _NUMBERS},
                    ("simulation_time_hours", "measurements", "manipulated_variables",
                     "shutdown_state", "safety_margins"))
_SCENARIO_STATUS = _obj({
    "scenario_id": _TEXT,
    "status": {"enum": ["SUPPORTED", "UNSUPPORTED", "AMBIGUOUS", "INVALID"]},
    "reason": {"type": ["string", "null"]},
    "missing_capability": {"type": ["string", "null"]},
    "candidates": {"type": "array", "items": _TEXT},
    "control_mode": _TEXT,
    "mapping_version": _TEXT,
}, ("scenario_id", "status", "reason", "missing_capability", "candidates", "control_mode",
    "mapping_version"))


# -- per-tool definitions -------------------------------------------------------------
@dataclass(frozen=True)
class _Tool:
    spec: ToolSpec
    validate: Callable[[Mapping[str, Any]], None]
    run: Callable[[ToolCallRequest, str, dict[str, float]], tuple[Any, tuple[InformationRef, ...]]]
    check: Callable[[ToolResult, ToolCallRequest], None]
    provenance: Callable[[Mapping[str, Any]], Mapping[str, Any]] = field(
        default=lambda arguments: {})


def _fail(status: str, reason: str):
    raise WorldError(status, reason)


def _safe_reason(reason: str) -> str:
    """Failure text is model-adjacent: never echo hidden-truth vocabulary."""
    return reason if not leakage_findings(reason) else "request rejected by the world model"


def _world_error(exc: Exception) -> WorldError:
    """Map tep-sim's distinguishable errors onto the tool-surface failure codes."""
    if isinstance(exc, WorldError):
        return WorldError(exc.status, _safe_reason(exc.reason))
    if isinstance(exc, (UnsupportedCapability, IncompatibleControlMode)):
        status = "UNSUPPORTED_CAPABILITY"
    elif isinstance(exc, (SimulationFailure, SnapshotFailure)):
        status = "SIMULATION_FAILED"
    elif isinstance(exc, (InvalidIntervention, InvalidEnvironmentState, UnknownProcessEntity,
                          ScenarioRejected, ValueError)):
        status = "INVALID_REQUEST"
    elif isinstance(exc, OSError):
        status = "ARTIFACT_ERROR"
    else:
        raise exc
    return WorldError(status, _safe_reason(str(exc) or type(exc).__name__))


def _preview_indices(count: int, points: int) -> list[int]:
    if count <= points:
        return list(range(count))
    return sorted({round(index * (count - 1) / (points - 1)) for index in range(points)})


def _value(record: Mapping[str, Any], variable: str) -> float:
    group = "measurements" if variable.startswith("XMEAS(") else "manipulated_variables"
    return record[group][variable]


def _envelope(ref: InformationRef | None) -> Any:
    return None if ref is None else to_jsonable(ref)


class BlindRcaToolSurface:
    """Default blind-RCA tool registry plus its trusted runtime hooks."""

    def __init__(self, world: ReferenceWorld, artifacts: ArtifactStore, *,
                 limits: ToolSurfaceLimits | None = None,
                 finish_verifier: Callable[..., bool] | None = None,
                 clock: Callable[[], str] | None = None) -> None:
        if not isinstance(world, ReferenceWorld) or not isinstance(artifacts, ArtifactStore):
            raise TypeError("ReferenceWorld and ArtifactStore required")
        self.world, self.artifacts = world, artifacts
        self.limits = limits or ToolSurfaceLimits()
        self.sandbox = SimulationSandbox(world)
        self._finish_verifier = finish_verifier
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._audit: dict[str, tuple[str, str, str]] = {}
        graph = world.graph.provenance
        self._world_versions = {
            "environment_version": ENVIRONMENT_VERSION, "capability_version": CAPABILITY_VERSION,
            "scenario_mapping_version": SCENARIO_MAPPING_VERSION,
            "safety_limits_version": SAFETY_LIMITS_VERSION, "upstream_revision": UPSTREAM_REVISION,
            "process_graph": {"fixture_id": graph.fixture_id,
                              "fixture_version": graph.fixture_version,
                              "content_sha256": graph.content_sha256,
                              "review_status": graph.review_status}}
        self._tools = {tool.spec.name: tool for tool in self._build()}
        leaked = sorted(set(self._tools) & BLIND_RCA_EXCLUDED_TOOLS)
        if leaked:
            raise ValueError(f"excluded tools registered: {leaked}")
        self.registered_tool_set_version = "tool-set-" + checksum(self.tool_specs())

    # -- registry and authority ---------------------------------------------------------
    def tool_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name].spec for name in sorted(self._tools))

    def gate_policy(self) -> GatePolicy:
        """Blind RCA authority: READ plus isolated SIMULATE. No PROPOSE/MUTATE/ADMIN."""
        return GatePolicy(POLICY_VERSION, frozenset(_ENABLED_CLASSES),
                          frozenset({SANDBOX_POLICY_TAG}), frozenset(SIMULATION_DIMENSIONS),
                          tool_allowlist=frozenset(self._tools))

    def reference_revision(self) -> str:
        return self.world.revision()

    def _registered(self, name: str, spec: Any) -> _Tool | None:
        tool = self._tools.get(name)
        if tool is None or not isinstance(spec, ToolSpec) or checksum(spec) != checksum(tool.spec):
            return None
        return tool

    # -- consumer pre-execution validation (after runtime G0-G3) ------------------------
    def validate_request(self, request: ToolCallRequest, spec: ToolSpec, task: Task,
                         expected_state_revision: Any,
                         budget_usage: Mapping[str, Any]) -> GateDecision:
        def decide(decision: str, code: str, reason: str) -> GateDecision:
            return GateDecision(request.request_id, decision, "CONSUMER", code,
                                _safe_reason(reason), POLICY_VERSION,
                                expected_state_revision=expected_state_revision)

        tool = self._registered(request.tool_name, spec)
        if tool is None:
            return decide("DENY", "POLICY_DENIED",
                          "tool is not part of the registered blind-RCA surface")
        if spec.side_effect_class not in _ENABLED_CLASSES:
            return decide("DENY", "POLICY_DENIED",
                          "only READ and isolated SIMULATE are enabled in blind RCA")
        try:
            tool.validate(request.arguments)
        except Exception as exc:
            error = _world_error(exc)
            return decide("DENY", error.status, error.reason)
        return decide("ALLOW", "VALID", "request satisfies blind-RCA tool policy and world bounds")

    # -- executor -----------------------------------------------------------------------
    def execute(self, request: ToolCallRequest, spec: ToolSpec) -> ToolResult:
        created_at = self._clock()
        provenance: dict[str, Any] = {
            "tool_name": request.tool_name, "tool_version": tool_version(request.tool_name),
            "surface_version": TOOL_SURFACE_VERSION, "request_id": request.request_id,
            "created_at": created_at, "world": self._world_versions}
        tool = self._registered(request.tool_name, spec)
        usage = {name: 0 for name in (spec.declared_budget_draw if tool else ())}
        before = self.world.revision()
        output, refs, status, error = None, (), SUCCESS, None
        try:
            if tool is None:
                _fail("POLICY_DENIED", "tool is not part of the registered blind-RCA surface")
            tool.validate(request.arguments)  # state may have moved since authorization
            provenance.update(tool.provenance(request.arguments))
            output, refs = tool.run(request, created_at, usage)
        except Exception as exc:
            failure = _world_error(exc)
            status, error = failure.status, failure.reason
            output, refs = {"failure": status, "reason": error}, ()
        after = self.world.revision()
        if after != before and status == SUCCESS:
            status, error = "STALE_STATE", "reference world changed during execution"
            output, refs = {"failure": status, "reason": error}, ()
        result = ToolResult(request.request_id, status, output, usage, provenance,
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
            fail("result is not from a registered blind-RCA tool")
        audit = self._audit.get(request.request_id)
        if audit is None or audit[2] != checksum(result):
            fail("result was not produced unmodified by this surface's executor")
        before, after, _ = audit
        if before != after or after != self.world.revision():
            fail("reference world changed across execution")
        provenance = result.provenance
        for name, expected in (("tool_name", request.tool_name),
                               ("tool_version", tool_version(request.tool_name)),
                               ("surface_version", TOOL_SURFACE_VERSION),
                               ("request_id", request.request_id)):
            if provenance.get(name) != expected:
                fail(f"provenance {name} mismatch")
        if not isinstance(provenance.get("created_at"), str):
            fail("provenance created_at missing")
        findings = leakage_findings({"output": result.structured_output,
                                     "provenance": provenance, "error": result.error,
                                     "refs": [*result.artifact_refs, *result.information_refs]})
        if findings:
            fail("hidden evaluator vocabulary in Agent-visible result: " + findings[0])
        if result.information_refs:
            fail("the tool surface issues no information refs")
        for ref in result.artifact_refs:
            if ref.visibility != Visibility.AGENT or not self.artifacts.verify(ref):
                fail("artifact ref is unknown, hidden, or corrupted")
        embedded = (result.structured_output.get("artifact_ref")
                    if isinstance(result.structured_output, Mapping) else None)
        declared = {ref.ref_id for ref in result.artifact_refs}
        if declared != ({embedded["ref_id"]} if embedded else set()):
            fail("declared artifact refs differ from the output artifact_ref")
        operations = {delta.operation for delta in deltas}
        if not operations <= INGESTIBLE_OPERATIONS:
            fail("tool results register observations/artifacts only, never evidence")
        tool.check(result, request)

    def verify_result(self, result: ToolResult, request: ToolCallRequest, spec: ToolSpec,
                      deltas: Sequence[StateDelta], expected_state_revision: Any) -> bool:
        try:
            self.check_result(result, request, spec, deltas)
        except ResultInvariantError:
            return False
        return True

    def verify_finish(self, proposal: Any, task: Task, expected_state_revision: Any) -> bool:
        """Finish readiness is not a tool-surface concern; delegate or fail closed."""
        if self._finish_verifier is None:
            return False
        return self._finish_verifier(proposal, task, expected_state_revision) is True

    # -- tool catalog -------------------------------------------------------------------
    def _spec(self, name: str, description: str, input_schema: Mapping[str, Any],
              output_schema: Mapping[str, Any], cls: SideEffectClass = SideEffectClass.READ,
              declared: Mapping[str, Any] | None = None,
              maximum: Mapping[str, float] | None = None) -> ToolSpec:
        simulate = cls == SideEffectClass.SIMULATE
        return ToolSpec(
            name, description, input_schema, output_schema, cls,
            required_policy_tags=(SANDBOX_POLICY_TAG,) if simulate else (),
            declared_budget_draw=declared or {}, max_budget_draw=maximum or {},
            isolation_guarantee=ISOLATION_GUARANTEE if simulate else None,
            provider_metadata={"tool_version": tool_version(name),
                               "surface_version": TOOL_SURFACE_VERSION,
                               "owner": "tep-agent-lab"})

    def _build(self) -> list[_Tool]:
        limits = self.limits
        variables = {"type": "array", "items": _VARIABLE, "minItems": 1,
                     "maxItems": limits.max_variables}
        node_query = _obj({"node_id": _NODE_ID,
                           "include_incident_streams": {"type": "boolean"}}, ("node_id",))
        bindings_out = _obj({"node_id": _TEXT, "include_incident_streams": {"type": "boolean"},
                             "bindings": {"type": "array", "items": _BINDING}},
                            ("node_id", "include_incident_streams", "bindings"))
        scenario_query = _obj({"scenario": _SCENARIO, "branch_id": _BRANCH_ID}, ("scenario",))
        read_only = self._read_only
        return [
            _Tool(self._spec(
                "get_current_observation",
                "Current reference-plant measurements, manipulated variables, shutdown "
                "state, and safety margins; optionally restricted to listed variables.",
                _obj({"variables": variables}), _OBSERVATION),
                self._validate_variables_optional, self._current_observation, read_only),
            _Tool(self._spec(
                "get_history",
                "Bounded recent history window for up to "
                f"{limits.max_variables} variables: summary statistics and a downsampled "
                "preview; the full window is an artifact when it exceeds the preview.",
                _obj({"window_hours": {"type": "number", "exclusiveMinimum": 0,
                                       "maximum": limits.max_history_window_hours},
                      "variables": variables}, ("window_hours", "variables")),
                _obj({"window": _obj({"requested_hours": _NUMBER, "start_hours": _NUMBER,
                                      "end_hours": _NUMBER},
                                     ("requested_hours", "start_hours", "end_hours")),
                      "variables": {"type": "array", "items": _TEXT},
                      "schema": {"type": "object", "additionalProperties": _TEXT},
                      "shape": _SHAPE,
                      "summary": {"type": "object", "additionalProperties": _NUMBERS},
                      "preview": _PREVIEW, "preview_complete": {"type": "boolean"},
                      "artifact_ref": _ARTIFACT_REF_OR_NULL},
                     ("window", "variables", "schema", "shape", "summary", "preview",
                      "preview_complete", "artifact_ref"))),
                self._validate_history, self._history, read_only),
            _Tool(self._spec(
                "get_variable_metadata",
                "Name, unit, kind, and process-graph binding of XMEAS/XMV variables.",
                _obj({"ids": {**variables, "maxItems": limits.max_metadata_ids}}, ("ids",)),
                _obj({"variables": {"type": "array", "items": _obj(
                    {"variable_id": _TEXT, "kind": _TEXT, "name": _TEXT,
                     "unit": {"type": ["string", "null"]},
                     "binding": {"type": ["object", "null"]}},
                    ("variable_id", "kind", "name", "unit", "binding"))}}, ("variables",))),
                self._validate_metadata, self._metadata, read_only),
            _Tool(self._spec(
                "get_process_node",
                "Local process-graph view of one node: kind, incident streams, direct "
                "neighbors, and attached measurement/actuator bindings.",
                _obj({"node_id": _NODE_ID}, ("node_id",)),
                _obj({"graph": {"type": "object"}, "node": {"type": "object"},
                      "incident_edges": {"type": "array"}, "neighbors": {"type": "array"},
                      "bindings": {"type": "array"}},
                     ("graph", "node", "incident_edges", "neighbors", "bindings"))),
                self._validate_node, self._process_node, read_only,
                self._graph_provenance),
            _Tool(self._spec(
                "get_neighbors",
                "Upstream and/or downstream process nodes of a node along stream "
                f"direction, up to depth {limits.max_neighbor_depth}.",
                _obj({"node_id": _NODE_ID,
                      "direction": {"enum": ["upstream", "downstream", "both"]},
                      "max_depth": {"type": "integer", "minimum": 1,
                                    "maximum": limits.max_neighbor_depth}}, ("node_id",)),
                _obj({"node_id": _TEXT, "direction": _TEXT, "max_depth": {"type": "integer"},
                      "upstream": {"type": "array", "items": _NODE_SUMMARY},
                      "downstream": {"type": "array", "items": _NODE_SUMMARY}},
                     ("node_id", "direction", "max_depth", "upstream", "downstream"))),
                self._validate_node, self._neighbors, read_only, self._graph_provenance),
            _Tool(self._spec(
                "get_related_measurements",
                "Measurements (XMEAS) bound to a node, optionally including its "
                "incident streams.", node_query, bindings_out),
                self._validate_node, self._related("measurements"), read_only,
                self._graph_provenance),
            _Tool(self._spec(
                "get_related_actuators",
                "Manipulated variables (XMV) acting on a node, optionally including its "
                "incident streams.", node_query, bindings_out),
                self._validate_node, self._related("actuators"), read_only,
                self._graph_provenance),
            _Tool(self._spec(
                "get_safety_margins",
                "Current margins of the reference plant to its versioned shutdown limits.",
                _obj({}),
                _obj({"simulation_time_hours": _NUMBER, "shutdown_state": {"type": "boolean"},
                      "limits_version": _TEXT,
                      "limits": {"type": "array", "items": _obj(
                          {"limit_id": _TEXT, "variable": _TEXT, "direction": _TEXT,
                           "threshold": _NUMBER, "unit": _TEXT, "value": _NUMBER,
                           "margin": _NUMBER},
                          ("limit_id", "variable", "direction", "threshold", "unit", "value",
                           "margin"))}},
                     ("simulation_time_hours", "shutdown_state", "limits_version", "limits"))),
                self._validate_nothing, self._safety_margins, read_only),
            _Tool(self._spec(
                "get_capability_summary",
                "What the simulated world can and cannot represent: consequence domains, "
                "snapshot fidelity, control mode, and the bounds of this tool surface.",
                _obj({}),
                _obj({"capability_version": _TEXT, "backend": _TEXT, "control_mode": _TEXT,
                      "snapshot_fidelity": {"type": "array"},
                      "supported_consequence_domains": {"type": "array"},
                      "unsupported_consequence_domains": {"type": "array"},
                      "semantic_scenarios": {"type": "object"},
                      "rollout_interventions": _TEXT, "tool_limits": _NUMBERS},
                     ("capability_version", "backend", "control_mode", "snapshot_fidelity",
                      "supported_consequence_domains", "unsupported_consequence_domains",
                      "semantic_scenarios", "rollout_interventions", "tool_limits"))),
                self._validate_nothing, self._capability_summary, read_only),
            _Tool(self._spec(
                "check_scenario_capability",
                "Ask whether a named semantic scenario is simulatable here. Returns "
                "SUPPORTED, UNSUPPORTED, AMBIGUOUS (with candidates), or INVALID.",
                scenario_query, _SCENARIO_STATUS),
                self._validate_scenario_target, self._check_scenario, read_only),
            _Tool(self._spec(
                "compile_process_deviation",
                "Deterministically compile a supported semantic scenario into its typed "
                "intervention plan; unsupported or ambiguous scenarios fail explicitly.",
                scenario_query,
                _obj({"scenario_id": _TEXT, "status": {"const": "COMPILED"},
                      "parameters": _NUMBERS, "control_mode": _TEXT,
                      "state_precondition": {"type": ["object", "null"]},
                      "interventions": {"type": "array", "items": _obj(
                          {"kind": _TEXT, "target": {"type": ["string", "null"]},
                           "value": {"type": ["number", "null"]}},
                          ("kind", "target", "value"))},
                      "mapping_version": _TEXT},
                     ("scenario_id", "status", "parameters", "control_mode",
                      "state_precondition", "interventions", "mapping_version"))),
                self._validate_compilable, self._compile_runner, read_only),
            _Tool(self._spec(
                "snapshot_environment",
                "Exact snapshot of the reference world or of an isolated branch; never "
                "advances the source.",
                _obj({"source": {"type": "string",
                                 "pattern": r"^(reference|branch-[0-9]{4,})$"}}),
                _obj({"snapshot_id": _TEXT, "source": _TEXT, "simulation_time_hours": _NUMBER,
                      "fidelity": _TEXT, "randomness_policy": _TEXT},
                     ("snapshot_id", "source", "simulation_time_hours", "fidelity",
                      "randomness_policy")),
                SideEffectClass.SIMULATE, {"simulation_snapshots": 1},
                {"simulation_snapshots": 1}),
                self._validate_snapshot, self._snapshot, self._check_snapshot),
            _Tool(self._spec(
                "fork_environment",
                "Create an isolated simulation branch from a snapshot.",
                _obj({"snapshot_id": _SNAPSHOT_ID}, ("snapshot_id",)),
                _obj({"branch_id": _TEXT, "parent_snapshot_id": _TEXT,
                      "simulation_time_hours": _NUMBER, "isolated": {"const": True}},
                     ("branch_id", "parent_snapshot_id", "simulation_time_hours", "isolated")),
                SideEffectClass.SIMULATE, {"simulation_branches": 1},
                {"simulation_branches": 1}),
                self._validate_fork, self._fork, self._check_fork),
            _Tool(self._spec(
                "run_rollout",
                "Apply optional supported semantic scenarios to an isolated branch and "
                "roll it forward. Returns a safety summary, a downsampled preview, and "
                "the full sanitized telemetry as an artifact.",
                _obj({"branch_id": _BRANCH_ID,
                      "horizon_hours": {"type": "number", "exclusiveMinimum": 0,
                                        "maximum": limits.max_rollout_horizon_hours},
                      "scenarios": {"type": "array", "items": _SCENARIO,
                                    "maxItems": limits.max_scenarios_per_rollout},
                      "preview_variables": variables},
                     ("branch_id", "horizon_hours")),
                _obj({"branch_id": _TEXT, "termination_reason": _TEXT,
                      "requested_horizon_hours": _NUMBER,
                      "simulated_horizon_seconds": {"type": "integer", "minimum": 0},
                      "start_time_hours": _NUMBER, "end_time_hours": _NUMBER,
                      "applied_scenarios": {"type": "array"},
                      "final_state": {"type": "object"}, "safety": {"type": "object"},
                      "schema": {"type": "object"}, "shape": _SHAPE, "preview": _PREVIEW,
                      "artifact_ref": _ARTIFACT_REF},
                     ("branch_id", "termination_reason", "requested_horizon_hours",
                      "simulated_horizon_seconds", "start_time_hours", "end_time_hours",
                      "applied_scenarios", "final_state", "safety", "schema", "shape",
                      "preview", "artifact_ref")),
                SideEffectClass.SIMULATE,
                {"simulation_rollouts": 1,
                 "simulated_horizon_seconds": "round(horizon_hours * 3600)"},
                {"simulation_rollouts": 1,
                 "simulated_horizon_seconds": limits.max_rollout_horizon_hours * 3600}),
                self._validate_rollout, self._rollout, self._check_rollout,
                lambda arguments: {"branch_id": arguments["branch_id"]}),
        ]

    # -- validators (deterministic; tep-sim decides capability, bounds, control mode) --
    def _validate_nothing(self, arguments: Mapping[str, Any]) -> None:
        return None

    def _check_variables(self, names: Sequence[str]) -> None:
        if len(set(names)) != len(names):
            _fail("INVALID_REQUEST", "variables must be distinct")
        for name in names:
            variable = REGISTRY.get(name)
            if variable is None or not name.startswith(("XMEAS(", "XMV(")):
                _fail("INVALID_REQUEST", "unknown or non-visible variable")

    def _validate_variables_optional(self, arguments: Mapping[str, Any]) -> None:
        self._check_variables(arguments.get("variables", ()))

    def _validate_history(self, arguments: Mapping[str, Any]) -> None:
        self._check_variables(arguments["variables"])

    def _validate_metadata(self, arguments: Mapping[str, Any]) -> None:
        self._check_variables(arguments["ids"])

    def _validate_node(self, arguments: Mapping[str, Any]) -> None:
        self.world.graph.node(arguments["node_id"])

    def _scenario_target(self, arguments: Mapping[str, Any]):
        branch = arguments.get("branch_id")
        return self.world.environment if branch is None else self.sandbox.branch(branch)

    def _compile(self, arguments: Mapping[str, Any], environment=None):
        scenario = arguments["scenario"]
        environment = environment or self._scenario_target(arguments)
        request = ScenarioRequest(scenario["scenario_id"], dict(scenario.get("parameters", {})))
        return environment, environment.compile_scenario(request)

    def _validate_scenario_target(self, arguments: Mapping[str, Any]) -> None:
        self._scenario_target(arguments)

    @staticmethod
    def _require_supported(compiled: Any) -> SupportedScenario:
        if isinstance(compiled, SupportedScenario):
            return compiled
        if isinstance(compiled, UnsupportedScenario):
            _fail("UNSUPPORTED_CAPABILITY",
                  f"{compiled.reason} (missing {compiled.missing_capability})")
        if isinstance(compiled, AmbiguousScenario):
            _fail("INVALID_REQUEST", "ambiguous scenario; choose one of "
                  + ", ".join(compiled.candidates))
        _fail("INVALID_REQUEST", getattr(compiled, "reason", "scenario did not compile"))

    def _validate_compilable(self, arguments: Mapping[str, Any]) -> None:
        self._require_supported(self._compile(arguments)[1])

    def _validate_snapshot(self, arguments: Mapping[str, Any]) -> None:
        self.sandbox.source(arguments.get("source", REFERENCE))

    def _validate_fork(self, arguments: Mapping[str, Any]) -> None:
        if not self.sandbox.has_snapshot(arguments["snapshot_id"]):
            _fail("INVALID_REQUEST", "unknown snapshot")

    def _validate_rollout(self, arguments: Mapping[str, Any]) -> None:
        if arguments["branch_id"] == REFERENCE:
            _fail("POLICY_DENIED", "SIMULATE cannot target the reference world")
        branch = self.sandbox.branch(arguments["branch_id"])
        if branch.observe().shutdown_state:
            _fail("INVALID_REQUEST", "branch has shut down; fork a new branch")
        horizon = arguments["horizon_hours"]
        steps = round(horizon * 3600)
        if steps < 1 or not math.isclose(horizon * 3600, steps, rel_tol=0, abs_tol=1e-8):
            _fail("INVALID_REQUEST", "horizon must span an integral number of seconds")
        self._check_variables(arguments.get("preview_variables", ()))
        for scenario in arguments.get("scenarios", ()):
            self._require_supported(self._compile({"scenario": scenario}, branch)[1])

    # -- READ runners -------------------------------------------------------------------
    @staticmethod
    def _read_only(result: ToolResult, request: ToolCallRequest) -> None:
        if dict(result.actual_budget_draw):
            raise ResultInvariantError("READ tools draw no simulation budget")
        if result.artifact_refs and request.tool_name != "get_history":
            raise ResultInvariantError("only dense history READ results carry artifacts")

    def _graph_provenance(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"process_graph_review_status": self.world.graph.provenance.review_status}

    def _current_observation(self, request, created_at, usage):
        observation = self.world.observe()
        selected = request.arguments.get("variables")
        if selected:
            for group in ("measurements", "manipulated_variables"):
                observation[group] = {key: value for key, value in observation[group].items()
                                      if key in selected}
        return observation, ()

    def _history(self, request, created_at, usage):
        arguments = request.arguments
        window, names = arguments["window_hours"], list(arguments["variables"])
        history = self.world.history()
        end = history[-1]["simulation_time_hours"]
        start = end - window
        rows = [record for record in history
                if record["simulation_time_hours"] >= start - 1e-9]
        series = {name: [_value(record, name) for record in rows] for name in names}
        times = [record["simulation_time_hours"] for record in rows]
        indices = _preview_indices(len(rows), self.limits.max_preview_points)
        complete = len(indices) == len(rows)
        artifact = None
        if not complete:
            artifact = self.artifacts.put_records(
                "HistoryWindowArtifact",
                [{"simulation_time_hours": times[i], **{name: series[name][i] for name in names}}
                 for i in range(len(rows))], created_at)
        output = {
            "window": {"requested_hours": window, "start_hours": times[0], "end_hours": end},
            "variables": names,
            "schema": {"simulation_time_hours": "h",
                       **{name: REGISTRY[name].unit or "" for name in names}},
            "shape": {"records": len(rows), "variables": len(names)},
            "summary": {name: {"first": values[0], "last": values[-1], "min": min(values),
                               "max": max(values), "mean": sum(values) / len(values)}
                        for name, values in series.items()},
            "preview": {"simulation_time_hours": [times[i] for i in indices],
                        "values": {name: [series[name][i] for i in indices] for name in names}},
            "preview_complete": complete, "artifact_ref": _envelope(artifact)}
        return output, (artifact,) if artifact else ()

    def _metadata(self, request, created_at, usage):
        rows = []
        for name in request.arguments["ids"]:
            variable = REGISTRY[name]
            try:
                described = self.world.graph.binding(name).describe()
                binding = {key: described[key] for key in
                           ("semantic_entity_id", "attached_to", "relation", "quantity")}
            except UnknownProcessEntity:
                binding = None
            rows.append({"variable_id": name, "kind": name.split("(")[0], "name": variable.name,
                         "unit": variable.unit, "binding": binding})
        return {"variables": rows}, ()

    def _process_node(self, request, created_at, usage):
        return self.world.graph.project_local(request.arguments["node_id"]).as_dict(), ()

    def _neighbors(self, request, created_at, usage):
        arguments, graph = request.arguments, self.world.graph
        node_id, direction = arguments["node_id"], arguments.get("direction", "both")
        depth = arguments.get("max_depth", 1)

        def describe(ids):
            return [{"node_id": item.node_id, "kind": item.kind.value, "name": item.name}
                    for item in (graph.node(node) for node in ids)]

        upstream = graph.upstream(node_id, max_depth=depth) \
            if direction in ("upstream", "both") else ()
        downstream = graph.downstream(node_id, max_depth=depth) \
            if direction in ("downstream", "both") else ()
        return {"node_id": node_id, "direction": direction, "max_depth": depth,
                "upstream": describe(upstream), "downstream": describe(downstream)}, ()

    def _related(self, kind: str):
        def run(request, created_at, usage):
            arguments = request.arguments
            include = arguments.get("include_incident_streams", False)
            found = getattr(self.world.graph, kind)(arguments["node_id"],
                                                    include_incident_streams=include)
            return {"node_id": arguments["node_id"], "include_incident_streams": include,
                    "bindings": [binding.describe() for binding in found]}, ()
        return run

    def _safety_margins(self, request, created_at, usage):
        observation = self.world.observe()
        limits = [{"limit_id": limit.limit_id, "variable": limit.variable,
                   "direction": limit.direction, "threshold": limit.threshold,
                   "unit": limit.unit, "value": observation["measurements"][limit.variable],
                   "margin": observation["safety_margins"][limit.limit_id]}
                  for limit in SAFETY_LIMITS]
        return {"simulation_time_hours": observation["simulation_time_hours"],
                "shutdown_state": observation["shutdown_state"],
                "limits_version": SAFETY_LIMITS_VERSION, "limits": limits}, ()

    def _capability_summary(self, request, created_at, usage):
        registry = self.world.environment.capabilities()

        def domain(name):
            return [{"domain_id": entry.capability_id.split(":", 1)[1],
                     "description": entry.description} for entry in registry.domain(name)]

        limits = self.limits
        return {
            "capability_version": registry.version, "backend": registry.backend,
            "control_mode": self.world.control_mode.value,
            "snapshot_fidelity": [{"capability_id": entry.capability_id,
                                   "supported": entry.supported,
                                   "fidelity": entry.details["fidelity"]}
                                  for entry in registry.domain("snapshot_fidelity")],
            "supported_consequence_domains": domain("consequence_domains"),
            "unsupported_consequence_domains": domain("unsupported_domains"),
            # Enumerating tested scenarios would hand over a candidate-cause list.
            "semantic_scenarios": {"mapping_version": SCENARIO_MAPPING_VERSION,
                                   "enumeration": "NOT_EXPOSED_IN_BLIND_RCA",
                                   "query_tool": "check_scenario_capability"},
            "rollout_interventions": "SUPPORTED_SEMANTIC_SCENARIOS_ONLY",
            "tool_limits": {
                "max_history_window_hours": limits.max_history_window_hours,
                "max_variables": limits.max_variables,
                "max_preview_points": limits.max_preview_points,
                "max_rollout_horizon_hours": limits.max_rollout_horizon_hours,
                "max_scenarios_per_rollout": limits.max_scenarios_per_rollout},
        }, ()

    def _check_scenario(self, request, created_at, usage):
        environment, compiled = self._compile(request.arguments)
        output = {"scenario_id": request.arguments["scenario"]["scenario_id"],
                  "status": "SUPPORTED", "reason": None, "missing_capability": None,
                  "candidates": [], "control_mode": environment.config.control_mode.value,
                  "mapping_version": SCENARIO_MAPPING_VERSION}
        if isinstance(compiled, UnsupportedScenario):
            output.update(status="UNSUPPORTED", reason=compiled.reason,
                          missing_capability=compiled.missing_capability)
        elif isinstance(compiled, AmbiguousScenario):
            output.update(status="AMBIGUOUS", reason=compiled.reason,
                          candidates=list(compiled.candidates))
        elif isinstance(compiled, InvalidScenario) or not isinstance(compiled, SupportedScenario):
            output.update(status="INVALID",
                          reason=getattr(compiled, "reason", "scenario did not compile"))
        output["reason"] = None if output["reason"] is None else _safe_reason(output["reason"])
        return output, ()

    def _compile_runner(self, request, created_at, usage):
        environment, compiled = self._compile(request.arguments)
        supported = self._require_supported(compiled)
        interventions = []
        for item in supported.interventions:
            if isinstance(item, MVIntervention):
                interventions.append({"kind": "MANIPULATED_VARIABLE_SETPOINT",
                                      "target": item.target, "value": item.value})
            elif isinstance(item, MVConstraint):
                interventions.append({"kind": "MANIPULATED_VARIABLE_CONSTRAINT",
                                      "target": item.target, "value": None})
            else:  # process-input deviations stay semantic; runtime ids are withheld
                interventions.append({"kind": "PROCESS_DEVIATION", "target": None,
                                      "value": None})
        provenance = supported.provenance
        return {"scenario_id": supported.scenario_id, "status": "COMPILED",
                "parameters": dict(provenance["parameters"]),
                "control_mode": provenance["control_mode"],
                "state_precondition": to_jsonable(provenance["state_precondition"]),
                "interventions": interventions,
                "mapping_version": provenance["mapping_version"]}, ()

    # -- SIMULATE runners ---------------------------------------------------------------
    def _snapshot(self, request, created_at, usage):
        source = request.arguments.get("source", REFERENCE)
        handle, snapshot = self.sandbox.snapshot(source)
        usage["simulation_snapshots"] = 1
        return {"snapshot_id": handle, "source": source,
                "simulation_time_hours": float(snapshot.simulation_time),
                "fidelity": snapshot.fidelity.value,
                "randomness_policy": snapshot.random_state_metadata["fork_policy"]}, ()

    def _fork(self, request, created_at, usage):
        parent = request.arguments["snapshot_id"]
        handle, branch = self.sandbox.fork(parent)
        usage["simulation_branches"] = 1
        return {"branch_id": handle, "parent_snapshot_id": parent,
                "simulation_time_hours": float(branch.observe().simulation_time),
                "isolated": True}, ()

    def _rollout(self, request, created_at, usage):
        arguments = request.arguments
        handle, horizon = arguments["branch_id"], arguments["horizon_hours"]
        branch = self.sandbox.branch(handle)
        start = branch.observe().simulation_time
        applied = []
        for scenario in arguments.get("scenarios", ()):
            try:
                compiled = branch.apply_scenario(ScenarioRequest(
                    scenario["scenario_id"], dict(scenario.get("parameters", {}))))
            except Exception:
                self.sandbox.retire(handle)  # partially intervened: no longer a clean base
                raise
            applied.append({"scenario_id": compiled.scenario_id,
                            "parameters": dict(compiled.provenance["parameters"]),
                            "intervention_count": len(compiled.interventions)})
        usage["simulation_rollouts"] = 1
        try:
            rollout = branch.rollout(horizon)
        finally:
            usage["simulated_horizon_seconds"] = round(
                (branch.observe().simulation_time - start) * 3600)
        records = [sanitize_observation(record) for record in read_telemetry(rollout.telemetry)]
        safety = branch.evaluate_safety(rollout)
        artifact = self.artifacts.put_records("RolloutTelemetryArtifact", records, created_at)
        names = list(arguments.get("preview_variables") or _DEFAULT_PREVIEW)
        indices = _preview_indices(len(records), self.limits.max_preview_points)
        final = records[-1]
        return {
            "branch_id": handle, "termination_reason": rollout.termination_reason,
            "requested_horizon_hours": horizon,
            "simulated_horizon_seconds": usage["simulated_horizon_seconds"],
            "start_time_hours": float(start), "end_time_hours": final["simulation_time_hours"],
            "applied_scenarios": applied,
            "final_state": {"shutdown_state": final["shutdown_state"],
                            "safety_margins": final["safety_margins"],
                            "values": {name: _value(final, name) for name in names}},
            "safety": self._sanitize_safety(safety),
            "schema": {"record_fields": ["simulation_time_hours", "measurements",
                                         "manipulated_variables", "shutdown_state",
                                         "safety_margins"],
                       "time_unit": "h", "format": "jsonl"},
            "shape": {"records": len(records),
                      "variables": len(final["measurements"])
                      + len(final["manipulated_variables"])},
            "preview": {"simulation_time_hours": [records[i]["simulation_time_hours"]
                                                  for i in indices],
                        "values": {name: [_value(records[i], name) for i in indices]
                                   for name in names}},
            "artifact_ref": _envelope(artifact)}, (artifact,)

    @staticmethod
    def _sanitize_safety(evaluation: Any) -> dict[str, Any]:
        data = to_jsonable(evaluation)
        events = [event for event in data["relevant_process_events"]
                  if event["kind"] in ("limit_crossed", "limit_recovered", "shutdown")]
        return {"evaluation_version": data["evaluation_version"],
                "limits_version": data["limits_version"],
                "record_count": data["record_count"],
                "shutdown_occurred": data["shutdown_occurred"],
                "shutdown_time_hours": data["shutdown_time"],
                "shutdown_limit_ids": data["shutdown_limit_ids"],
                "limit_crossings": data["limit_crossings"],
                "minimum_safety_margins": data["minimum_safety_margins"],
                "unsafe_intervals": data["unsafe_intervals"],
                "process_events": events,
                "unsupported_consequence_domains": data["unsupported_consequence_domains"]}

    # -- SIMULATE result invariants -----------------------------------------------------
    def _check_snapshot(self, result: ToolResult, request: ToolCallRequest) -> None:
        if dict(result.actual_budget_draw) != {"simulation_snapshots": 1}:
            raise ResultInvariantError("snapshot must report exactly one snapshot")
        handle = result.structured_output["snapshot_id"]
        source = request.arguments.get("source", REFERENCE)
        if (not self.sandbox.has_snapshot(handle)
                or self.sandbox.snapshot_source(handle) != source
                or result.structured_output["source"] != source):
            raise ResultInvariantError("snapshot identity/source provenance mismatch")

    def _check_fork(self, result: ToolResult, request: ToolCallRequest) -> None:
        if dict(result.actual_budget_draw) != {"simulation_branches": 1}:
            raise ResultInvariantError("fork must report exactly one branch")
        handle = result.structured_output["branch_id"]
        if (handle == REFERENCE or not self.sandbox.has_branch(handle)
                or self.sandbox.branch_parent(handle) != request.arguments["snapshot_id"]):
            raise ResultInvariantError("branch identity/parent provenance mismatch")

    def _check_rollout(self, result: ToolResult, request: ToolCallRequest) -> None:
        draw, output = dict(result.actual_budget_draw), result.structured_output
        handle = request.arguments["branch_id"]
        if (handle == REFERENCE or output["branch_id"] != handle
                or result.provenance.get("branch_id") != handle
                or not self.sandbox.has_branch(handle)):
            raise ResultInvariantError("rollout branch provenance mismatch")
        requested = round(request.arguments["horizon_hours"] * 3600)
        seconds = draw.get("simulated_horizon_seconds")
        if (set(draw) != {"simulation_rollouts", "simulated_horizon_seconds"}
                or draw["simulation_rollouts"] != 1
                or seconds != output["simulated_horizon_seconds"]
                or not 0 < seconds <= requested
                or (seconds != requested and output["termination_reason"] != "shutdown")):
            raise ResultInvariantError("rollout must report its actual rollout and horizon use")
        if not result.artifact_refs:
            raise ResultInvariantError("dense rollout telemetry must be artifact-backed")

