"""E0 TEP Environment Observatory: a developer/research view of one real P0 run.

Developer demo only (docs/e0-environment-observatory.md). It is not a production
frontend, a transport adapter, a D0 benchmark, or a new source of truth:

- the run is a real ``RunManager`` run: Coordinator -> B2 gates -> lab consumer ->
  Executor -> B3 verification -> deterministic ingestion, driven by a local scripted
  ``FakeProvider`` that only emits typed ``ModelTurn``s;
- the report is built from the AGENT-scoped P0 read projections
  (``manager.queries(run_id)``) and exact-ref ``get_artifact`` reads only;
- the known developer-demo cause is trusted harness metadata, kept in a structurally
  separate block that is never merged into the AGENT payload.

Usage::

    py examples/e0_environment_observatory.py --output-root runs/e0-demo --open

Output is a self-contained HTML file (inline CSS/SVG/JavaScript, no network).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import webbrowser
import xml.etree.ElementTree as ET

from industrial_agent_runtime import (Action, Budget, FakeProvider, FinishProposal,
                                      InformationRef, ModelTurn, ToolCallRequest,
                                      to_jsonable)
from tep_sim import ControlMode, DisturbanceIntervention

from tep_agent_lab.canonical_context import ProjectionScope
from tep_agent_lab.playground import (LifecycleError, ModelSpec, PrepareError, RunManager,
                                      RunOutcome, RunRequest, RunStatus, SourceRevisions,
                                      WorldSpec, load_dependency_pins,
                                      pinned_tep_sim_sources)
# P0's write-once publication (temp + fsync + hard link), reused rather than re-implemented.
from tep_agent_lab.playground import _write_once
from tep_agent_lab.playground_views import MAX_TELEMETRY_RECORDS, RunQueries
from tep_agent_lab.tep_world import ReferenceWorld, leakage_findings
from tep_agent_lab.tool_surface import simulation_quota

E0_VERSION = "tep-agent-lab.e0-environment-observatory/v0"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "e0-observatory"
VARIABLES = ("XMEAS(9)", "XMEAS(21)")
SEED = 11
RECORD_INTERVAL_SECONDS = 60
HISTORY_WINDOW_HOURS = 0.3
ROLLOUT_HORIZON_HOURS = 0.2
COMPARISON_WINDOW = {"start_hours": 0.1, "end_hours": 0.3}
COMPARISON_METRICS = ("MEAN_ABSOLUTE_ERROR", "ROOT_MEAN_SQUARE_ERROR", "FINAL_ERROR")
# The model's ContextProjection carries observation refs, not their summaries, so a
# scripted provider cannot read the fork result. The branch handle is the world's next
# sequential handle after the harness baseline; ``lineage_findings`` checks it after the
# run against the Agent-visible capability/fork/rollout observations and BranchTreeView.
EXPECTED_BRANCH = "branch-0002"
SCHEMATIC_LABEL = "TEP process schematic — P&ID-style, not authoritative P&ID geometry"
SCHEMATIC_ASSET = ROOT / "examples" / "assets" / "e0" / "tep_process_schematic.svg"

# -- trusted developer-demo setup: never Agent-visible ----------------------------------
DEMO_PRE_INCIDENT_HOURS = 0.1
DEMO_POST_INCIDENT_HOURS = 0.2
DEMO_INJECTED_CAUSE = "IDV(4)"
DEVELOPER_LABEL = "DEVELOPER / EVALUATOR ONLY — not Agent-visible context"


class BaselineHandle:
    """The one harness output the scripted provider may use: an opaque snapshot id.

    The provider holds only this cell, never the harness (which knows the cause).
    """

    def __init__(self) -> None:
        self.value: str | None = None


class DemoHarness:
    """Trusted harness ``case_setup``: baseline, then the known developer-demo cause.

    Runs inside ``RunManager.prepare`` on the reference world, exactly like a benchmark
    harness; it is never an Agent tool. What it records stays developer-only.
    """

    def __init__(self) -> None:
        self.baseline = BaselineHandle()
        self.baseline_time_hours: float | None = None
        self.final_time_hours: float | None = None

    @property
    def baseline_handle(self) -> str | None:
        return self.baseline.value

    def __call__(self, world: ReferenceWorld) -> None:
        world.advance(DEMO_PRE_INCIDENT_HOURS)
        self.baseline.value = world.designate_baseline()
        self.baseline_time_hours = world.observe()["simulation_time_hours"]
        world.environment.apply(DisturbanceIntervention(DEMO_INJECTED_CAUSE, 1))
        world.advance(DEMO_POST_INCIDENT_HOURS)
        self.final_time_hours = world.observe()["simulation_time_hours"]

    def developer_setup(self) -> dict[str, Any]:
        return {"label": DEVELOPER_LABEL,
                "known_injected_cause": DEMO_INJECTED_CAUSE,
                "injection_time_hours": self.baseline_time_hours,
                "pre_incident_baseline_snapshot": self.baseline_handle,
                "reference_end_time_hours": self.final_time_hours,
                "harness_sequence": [
                    "reset",
                    f"advance {DEMO_PRE_INCIDENT_HOURS} h",
                    f"designate pre-incident baseline ({self.baseline_handle})",
                    f"apply {DEMO_INJECTED_CAUSE} (developer-demo setup)",
                    f"advance {DEMO_POST_INCIDENT_HOURS} h"],
                "note": ("Trusted demo setup metadata. Not a D0 benchmark case and not "
                         "part of any AGENT view, ContextProjection, or artifact.")}


# -- scripted provider: typed turns only, through the normal Coordinator path -----------
def _artifact(state: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    for ref in state["artifact_refs"]:
        if ref["kind"] == kind:
            return ref
    raise LookupError(f"no {kind} in the projected task state; an earlier step failed")


def scripted_provider(baseline: BaselineHandle) -> FakeProvider:
    """A deterministic investigation script; every call goes through runtime authority."""
    requests: list[Callable[[Mapping[str, Any]], ToolCallRequest]] = [
        lambda state: ToolCallRequest("e0-1-capabilities", "get_capability_summary", {}),
        lambda state: ToolCallRequest("e0-2-history", "get_history", {
            "window_hours": HISTORY_WINDOW_HOURS, "variables": list(VARIABLES)}),
        lambda state: ToolCallRequest("e0-3-fork", "fork_environment", {
            "snapshot_id": baseline.value}),
        lambda state: ToolCallRequest("e0-4-rollout", "run_rollout", {
            "branch_id": EXPECTED_BRANCH, "horizon_hours": ROLLOUT_HORIZON_HOURS,
            "preview_variables": list(VARIABLES)}),
        lambda state: ToolCallRequest("e0-5-compare", "compare_trajectories", {
            "reference_ref": _artifact(state, "HistoryWindowArtifact"),
            "candidate_ref": _artifact(state, "RolloutTelemetryArtifact"),
            "variables": list(VARIABLES), "alignment_policy": "EXACT_TIMESTAMPS",
            "reference_window": dict(COMPARISON_WINDOW),
            "metrics": list(COMPARISON_METRICS), "preprocessing": "NONE"}),
    ]

    def turn(make: Callable[[Mapping[str, Any]], ToolCallRequest] | None
             ) -> Callable[[Any, Mapping[str, Any]], ModelTurn]:
        def build(projection: Any, limits: Mapping[str, Any]) -> ModelTurn:
            turn_id = f"e0-turn-{limits['budget_usage']['model_calls']}"
            if make is None:
                return ModelTurn(turn_id, limits["context_projection_ref"],
                                 projection.base_revision, Action.FINISH_PROPOSAL,
                                 finish_proposal=FinishProposal(
                                     {"summary": "E0 scripted observation sequence complete; "
                                                 "no causal conclusion is claimed"}))
            return ModelTurn(turn_id, limits["context_projection_ref"],
                             projection.base_revision, Action.TOOL_REQUEST,
                             tool_request=make(projection.content["task_state"]))
        return build

    return FakeProvider([turn(make) for make in requests] + [turn(None)])


EXPECTED_TOOLS = ("get_capability_summary", "get_history", "fork_environment",
                  "run_rollout", "compare_trajectories")


def run_request() -> RunRequest:
    return RunRequest(
        investigation_id="e0-environment-observatory",
        goal=("Developer demo: observe the reference trajectory and a baseline-descended "
              "counterfactual rollout of the TEP world"),
        world=WorldSpec(seed=SEED, control_mode=ControlMode.CLOSED_LOOP,
                        record_interval=RECORD_INTERVAL_SECONDS),
        model=ModelSpec("fake", "e0-scripted-investigation", "v0", "e0-script/v0",
                        config={"scripted": True}),
        budget=Budget(max_model_calls=8, max_tool_calls=8, max_subagents=0,
                      max_subagent_depth=0, max_steps=30,
                      extra_dimensions=simulation_quota(snapshots=0, branches=1, rollouts=1,
                                                        horizon_seconds=3600)),
        require_conclusion=False)


# -- run --------------------------------------------------------------------------------
def git_revision(path: Path) -> str:
    """The lab checkout revision (the trusted host attests SourceRevisions).

    ``safe.directory`` is scoped to this one checkout, as in the lab tests, so a
    checkout owned by another user (CI, containers) can still report its revision.
    """
    posix = path.as_posix()
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={posix}", "-C", posix, "rev-parse", "HEAD"],
        text=True, stderr=subprocess.DEVNULL).strip()


def lab_checkout_dirty(path: Path) -> bool:
    """Uncommitted tracked or untracked changes (git-ignored run output excluded)."""
    posix = path.as_posix()
    try:
        status = subprocess.check_output(
            ["git", "-c", f"safe.directory={posix}", "-C", posix, "status", "--porcelain"],
            text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(status.strip())


@dataclass(frozen=True)
class DemoRun:
    manager: RunManager
    run_id: str
    outcome: RunOutcome
    harness: DemoHarness


def p0_root(output_root: Path) -> Path:
    return Path(output_root) / "p0-runs"


def report_path(output_root: Path, run_id: str) -> Path:
    return Path(output_root) / "observatory" / run_id / "observatory.html"


def run_demo(output_root: Path, run_id: str = DEFAULT_RUN_ID, *,
             lab_revision: str | None = None,
             clock: Callable[[], str] | None = None) -> DemoRun:
    """One real P0 run: create -> prepare (trusted harness) -> start (Coordinator)."""
    pins = load_dependency_pins(ROOT / "dependency-pins.json")
    revisions = SourceRevisions(pins["tep-sim"], pins["industrial-agent-runtime"],
                                lab_revision or git_revision(ROOT))
    manager = RunManager(p0_root(output_root), revisions=revisions, dependency_pins=pins,
                         clock=clock)
    harness = DemoHarness()
    manager.create(run_id, run_request())  # LifecycleError if the run already exists
    manager.prepare(run_id, provider=scripted_provider(harness.baseline),
                    context_sources=pinned_tep_sim_sources(revisions.tep_sim),
                    case_setup=harness)
    outcome = manager.start(run_id)
    return DemoRun(manager, run_id, outcome, harness)


# -- AGENT report payload ---------------------------------------------------------------
def _plain(value: Any) -> Any:
    return json.loads(json.dumps(to_jsonable(value), allow_nan=False))  # fail early


def _observations(investigation: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in investigation["observations"]:
        record = item["record"] or {}
        rows.append({"observation_id": item["ref"]["ref_id"],
                     "request_id": record.get("producer_request_ref", {}).get("ref_id"),
                     "tool": record.get("tool_or_service_ref", {}).get("ref_id"),
                     "summary": record.get("summary"),
                     "artifact_refs": record.get("artifact_refs", [])})
    return rows


def _model_turns(observations: Sequence[Mapping[str, Any]],
                 events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group the AGENT event feed by model turn and attach what each turn produced.

    AGENT trace items carry no request payloads, so grouping uses feed order only: a
    turn runs from one runtime ``MODEL_TURN`` to the next. Observation refs are appended
    in state-revision order, one per ``REGISTER_OBSERVATION`` operation of the lab
    state-update events (the lab log records accepted updates only), so the k-th such
    operation registered the k-th observation. Each registration must directly follow
    its anchoring ACCEPTED ``RESULT_INGESTION`` trace item (P0 places unanchored lab
    events at the feed's end); otherwise, or on any count mismatch, this fails closed.
    """
    turns: list[dict[str, Any]] = []
    counts: list[int] = []
    previous: Mapping[str, Any] = {}
    for event in events:
        if "REGISTER_OBSERVATION" in event.get("operations", ()) and (
                previous.get("source"), previous.get("type"), previous.get("status")) != (
                "runtime_trace", "RESULT_INGESTION", "ACCEPTED"):
            raise ValueError("an observation registration has no anchoring ingestion event")
        previous = event
        if event["source"] == "runtime_trace" and event["type"] == "MODEL_TURN":
            turns.append({"turn": len(turns) + 1, "trace": [], "observations": []})
            counts.append(0)
        if not turns or event["source"] == "application_lifecycle":
            continue
        if event["source"] == "runtime_trace":
            turns[-1]["trace"].append({"type": event["type"], "status": event["status"],
                                       "position": event["position"]})
        else:
            counts[-1] += list(event.get("operations", ())).count("REGISTER_OBSERVATION")
    if sum(counts) != len(observations):
        raise ValueError("event feed and RcaState disagree on the number of observations")
    pending = iter(observations)
    for turn, count in zip(turns, counts):
        turn["observations"] = [next(pending) for _ in range(count)]
    return turns


def _tool_calls(turns: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"turn": turn["turn"], "request_id": observation["request_id"],
             "tool": observation["tool"], "trace": turn["trace"],
             "observation_id": observation["observation_id"],
             "artifacts": [ref["kind"] for ref in observation["artifact_refs"]]}
            for turn in turns for observation in turn["observations"]]


def _series(history: Sequence[Mapping[str, Any]], rollout: Sequence[Mapping[str, Any]],
            history_ref: str, rollout_ref: str) -> dict[str, Any]:
    """Chart data copied verbatim from the two Agent-visible telemetry artifacts."""
    series = {}
    for name in VARIABLES:
        group = "measurements" if name.startswith("XMEAS(") else "manipulated_variables"
        series[name] = {
            "reference": {"artifact_id": history_ref,
                          "points": [[row["simulation_time_hours"], row[name]]
                                     for row in history]},
            "counterfactual": {"artifact_id": rollout_ref,
                               "points": [[row["simulation_time_hours"], row[group][name]]
                                          for row in rollout]}}
    return series


def _topology(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Node neighborhoods from the view's edges plus a deterministic schematic layout.

    Upstream/downstream are edge-direction neighbours over every ProcessGraph edge
    (material, utility and recycle streams alike). Coordinates are a presentation aid
    (layered by stream direction), not P&ID geometry.
    """
    ids = sorted(node["node_id"] for node in graph["nodes"])
    edges = sorted(graph["edges"], key=lambda edge: edge["edge_id"])
    successors = {node: sorted({edge["target_node"] for edge in edges
                                if edge["source_node"] == node}) for node in ids}
    predecessors = {node: sorted({edge["source_node"] for edge in edges
                                  if edge["target_node"] == node}) for node in ids}
    # Depth-first from sources: back edges (recycle loops) are ignored for layering only.
    state: dict[str, int] = {}
    back: set[tuple[str, str]] = set()

    def visit(node: str) -> None:
        state[node] = 1
        for child in successors[node]:
            if state.get(child) == 1:
                back.add((node, child))
            elif child not in state:
                visit(child)
        state[node] = 2

    kinds = {node["node_id"]: node["kind"] for node in graph["nodes"]}
    # Feeds first, so the main material path (not a utility loop) sets the reading order.
    roots = sorted((node for node in ids if not predecessors[node]),
                   key=lambda node: (kinds[node] != "FEED_SOURCE", node))
    for node in roots + ids:
        if node not in state:
            visit(node)
    layer = {node: 0 for node in ids}
    for _ in ids:  # longest path over the acyclic forward edges
        for edge in edges:
            pair = (edge["source_node"], edge["target_node"])
            if pair not in back:
                layer[pair[1]] = max(layer[pair[1]], layer[pair[0]] + 1)
    for node in roots:  # draw each source next to the unit it feeds
        if successors[node]:
            layer[node] = max(0, min(layer[child] for child in successors[node]) - 1)
    columns: dict[int, list[str]] = {}
    for node in ids:
        columns.setdefault(layer[node], []).append(node)
    # Within a column: process units, then feeds/products, then utilities (bottom).
    band = {"FEED_SOURCE": 1, "PRODUCT_SINK": 1, "UTILITY_SOURCE": 2, "UTILITY_SINK": 2}
    rows: dict[str, float] = {}
    for index in sorted(columns):
        nodes = columns[index]
        nodes.sort(key=lambda node: (  # then one barycenter pass over placed predecessors
            band.get(kinds[node], 0),
            sum(rows[p] for p in predecessors[node] if p in rows)
            / max(1, len([p for p in predecessors[node] if p in rows])), node))
        for position, node in enumerate(nodes):
            rows[node] = position
    bindings = {node["node_id"]: node["bindings"] for node in graph["nodes"]}
    neighborhoods = {}
    for node in ids:
        incident = [edge for edge in edges
                    if node in (edge["source_node"], edge["target_node"])]
        stream_bindings = [binding for edge in incident for binding in edge["bindings"]]
        neighborhoods[node] = {
            "upstream": predecessors[node], "downstream": successors[node],
            "inlets": [edge["edge_id"] for edge in edges if edge["target_node"] == node],
            "outlets": [edge["edge_id"] for edge in edges if edge["source_node"] == node],
            "measurements": [binding["runtime_variable_id"] for binding in bindings[node]
                             if binding["runtime_variable_kind"] == "XMEAS"],
            "actuators": [binding["runtime_variable_id"] for binding in bindings[node]
                          if binding["runtime_variable_kind"] == "XMV"],
            "stream_measurements": sorted({binding["runtime_variable_id"]
                                           for binding in stream_bindings
                                           if binding["runtime_variable_kind"] == "XMEAS"}),
            "stream_actuators": sorted({binding["runtime_variable_id"]
                                        for binding in stream_bindings
                                        if binding["runtime_variable_kind"] == "XMV"})}
    return {"label": SCHEMATIC_LABEL, "layout": {node: [layer[node], rows[node]]
                                                 for node in ids},
            "layout_note": ("columns follow stream direction (recycle edges ignored for "
                            "layering); positions carry no geometry"),
            "neighborhoods": neighborhoods}


def validate_schematic(svg: str, graph: Mapping[str, Any]) -> dict[str, list[str]]:
    """Fail closed on unknown/duplicate identities and nonlocal or executable SVG."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("invalid schematic SVG XML") from exc
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("schematic root must be an SVG element")
    allowed = {"svg", "g", "path", "rect", "circle", "ellipse", "text", "title",
               "defs", "marker"}
    declared: dict[str, list[str]] = {"nodes": [], "edges": []}
    known = {"nodes": {n["node_id"] for n in graph["nodes"]},
             "edges": {e["edge_id"] for e in graph["edges"]}}
    dom_ids: set[str] = set()
    for element in root.iter():
        if element.tag.removeprefix("{http://www.w3.org/2000/svg}") not in allowed:
            raise ValueError("unsupported schematic SVG element")
        if all(key in element.attrib for key in ("data-process-node-id", "data-process-edge-id")):
            raise ValueError("schematic element must have only one semantic identity")
        for key, value in element.attrib.items():
            if key.lower().startswith("on") or "href" in key or "url(" in value.lower():
                raise ValueError("schematic must contain only local presentation geometry")
        if "id" in element.attrib:
            dom_id = element.attrib["id"]
            if dom_id in dom_ids:
                raise ValueError("duplicate schematic DOM id: " + dom_id)
            dom_ids.add(dom_id)
        for category, attribute in (("nodes", "data-process-node-id"),
                                    ("edges", "data-process-edge-id")):
            if attribute not in element.attrib:
                continue
            identity = element.attrib[attribute]
            if identity not in known[category] or identity in declared[category]:
                raise ValueError("unknown or duplicate schematic semantic id: " + identity)
            declared[category].append(identity)
    if leakage_findings(svg):
        raise ValueError("schematic failed the AGENT leakage screen")
    return {key: sorted(value) for key, value in declared.items()}


def process_overlay(graph: Mapping[str, Any], telemetry: Mapping[str, Any],
                    plotted: Sequence[str]) -> dict[str, Any]:
    """Derived display data only: graph bindings + current P0 projection, never world reads.

    Animation eligibility requires an observed, positive XMEAS MEASURES flow binding.
    Fixed dash speed signifies availability/direction only, never physical flow rate.
    """
    current = telemetry["current"]
    values = {**current["measurements"], **current["manipulated_variables"]}
    entities = {"nodes": {}, "edges": {}}
    signals: dict[str, dict[str, list[str]]] = {}
    animated: dict[str, list[str]] = {}
    for category, identity_key in (("nodes", "node_id"), ("edges", "edge_id")):
        for entity in graph[category]:
            identity = entity[identity_key]
            rows = []
            for binding in entity["bindings"]:
                variable = binding["runtime_variable_id"]
                targets = signals.setdefault(variable, {"nodes": [], "edges": []})
                if identity not in targets[category]:
                    targets[category].append(identity)
                present = variable in values
                rows.append({"binding": binding, "plottable": variable in plotted,
                             "telemetry_present": present,
                             "current_value": values.get(variable),
                             "simulation_time_hours": current["simulation_time_hours"]
                             if present else None})
                if (category == "edges" and present and values[variable] > 0
                        and binding["runtime_variable_kind"] == "XMEAS"
                        and binding["relation"] == "MEASURES"
                        and binding["quantity"] == "flow"):
                    animated.setdefault(identity, []).append(variable)
            entities[category][identity] = {"entity": entity, "signals": rows}
    return {"entities": entities, "signal_entities": signals, "animated_edges": animated,
            "telemetry_source": "P0 TelemetryView.current (reference world)",
            "animation_note": "Fixed-speed directional dash: observed positive flow measurement; "
                              "speed does not encode physical flow rate."}


def build_agent_report(queries: RunQueries) -> dict[str, Any]:
    """The complete AGENT-derived report payload; fails closed on any leakage finding."""
    if queries.scope != ProjectionScope.AGENT:
        raise ValueError("the E0 report payload is built from AGENT-scoped queries only")
    views = {"run_summary": queries.run_summary(), "manifest": queries.manifest_view(),
             "context_inventory": queries.context_inventory(),
             "process_graph": queries.process_graph(),
             "telemetry": queries.telemetry(variables=VARIABLES,
                                            max_records=MAX_TELEMETRY_RECORDS),
             "investigation": queries.investigation(), "branch_tree": queries.branch_tree(),
             "budget": queries.budget(), "events": queries.events(),
             "artifacts": queries.artifacts()}
    views = {name: _plain(view) for name, view in views.items()}
    contents = {}
    for envelope in views["artifacts"]["artifacts"]:
        ref = InformationRef(**envelope)  # the exact issued ref; checksum verified by P0
        contents[ref.ref_id] = _plain(queries.get_artifact(ref))

    def only(kind: str) -> Mapping[str, Any]:
        found = [ref for ref in views["artifacts"]["artifacts"] if ref["kind"] == kind]
        if len(found) != 1:
            raise ValueError(f"expected exactly one {kind}, found {len(found)}")
        return found[0]

    history, rollout = only("HistoryWindowArtifact"), only("RolloutTelemetryArtifact")
    observations = _observations(views["investigation"])
    by_tool = {row["tool"]: row for row in observations}
    turns = _model_turns(observations, views["events"]["events"])
    manifest = views["manifest"]["manifest"]
    summary = views["run_summary"]
    payload = {
        "e0_version": E0_VERSION, "scope": "AGENT",
        "source_note": ("Every value below is copied from AGENT-scoped P0 read projections "
                        "or exact-ref artifact reads of one completed run."),
        "variables": list(VARIABLES),
        "world": {
            "run_id": summary["run_id"], "run_status": summary["run_status"],
            "run_status_owner": summary["status_owner"],
            "task_status": summary.get("runtime_task_status", {}).get("status"),
            "task_status_owner": summary.get("runtime_task_status", {}).get("owner"),
            "simulation_time_hours": views["telemetry"]["current"]["simulation_time_hours"],
            "environment_config": manifest["world"]["environment_config"],
            "environment_version": manifest["world"]["environment_version"],
            "process_semantics": manifest["process_semantics"],
            "manifest_checksum": summary["manifest"]["checksum"],
            "manifest_checksum_of": summary["manifest"]["checksum_of"],
            "runtime_policy": {key: manifest["runtime_policy"][key] for key in (
                "tool_set_version", "gate_policy_version", "tool_surface_version",
                "bridge_version", "verifier_version", "allowed_tools")}},
        "model_turns": turns, "tool_calls": _tool_calls(turns),
        "series": _series(contents[history["ref_id"]]["content"],
                          contents[rollout["ref_id"]]["content"],
                          history["ref_id"], rollout["ref_id"]),
        "fork": by_tool.get("fork_environment", {}).get("summary"),
        "rollout": by_tool.get("run_rollout", {}).get("summary"),
        "comparison": by_tool.get("compare_trajectories", {}).get("summary"),
        "baseline_snapshots": (by_tool.get("get_capability_summary", {}).get("summary")
                               or {}).get("baseline_snapshots", []),
        "topology": _topology(views["process_graph"]),
        "process_overlay": process_overlay(views["process_graph"], views["telemetry"], VARIABLES),
        "views": views, "artifact_contents": contents}
    findings = leakage_findings(payload)
    if findings:
        raise ValueError("AGENT report payload failed the leakage screen: " + findings[0])
    return payload


# -- HTML -------------------------------------------------------------------------------
def _script_json(value: Any) -> str:
    """JSON safe inside a <script> element (no tag or entity can be closed early)."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


_PLACEHOLDERS = re.compile(r"__(AGENT_JSON|DEVELOPER_JSON|SCHEMATIC_SVG)__")


def render_html(agent_payload: Mapping[str, Any], developer: Mapping[str, Any]) -> str:
    """Self-contained page: the AGENT payload and the developer block stay separate.

    One substitution pass over the template only (inserted text is never rescanned), so
    run data that contains a placeholder token cannot pull one block into the other.
    """
    svg = SCHEMATIC_ASSET.read_text(encoding="utf-8")
    validate_schematic(svg, agent_payload["views"]["process_graph"])
    if leakage_findings(agent_payload):
        raise ValueError("AGENT report payload failed the leakage screen")
    blocks = {"AGENT_JSON": _script_json(agent_payload),
              "DEVELOPER_JSON": _script_json(developer), "SCHEMATIC_SVG": svg}
    return _PLACEHOLDERS.sub(lambda match: blocks[match.group(1)], _PAGE)


def write_report(path: Path, html: str) -> None:
    """Publish once with P0's write-once helper (temp + fsync + hard link, no overwrite)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_once(path, html.encode("utf-8"))


def lineage_findings(payload: Mapping[str, Any], baseline: str | None) -> list[str]:
    """Post-run check of the scripted handles against Agent-visible results and lineage."""
    found = []
    listed = [item["snapshot_id"] for item in payload["baseline_snapshots"]]
    if baseline is None or listed != [baseline]:
        found.append(f"capability summary lists baselines {listed}, harness designated "
                     f"{baseline}")
    fork, rollout = payload["fork"] or {}, payload["rollout"] or {}
    if (fork.get("branch_id"), fork.get("parent_snapshot_id")) != (EXPECTED_BRANCH, baseline):
        found.append("fork observation does not match the scripted branch/baseline")
    if rollout.get("branch_id") != EXPECTED_BRANCH:
        found.append("rollout observation does not name the scripted branch")
    nodes = {node["handle"]: node for node in payload["views"]["branch_tree"]["nodes"]}
    branch = nodes.get(EXPECTED_BRANCH, {})
    if (branch.get("kind"), branch.get("parent"), branch.get("lineage")) != (
            "BRANCH", baseline, "baseline"):
        found.append("BranchTreeView has no baseline-lineage branch from the baseline")
    return found


def terminal_summary(payload: Mapping[str, Any], path: Path) -> str:
    world, branches = payload["world"], payload["views"]["branch_tree"]["nodes"]
    refs = {ref["kind"]: ref["ref_id"] for ref in payload["views"]["artifacts"]["artifacts"]}
    snapshots = [node["handle"] for node in branches if node["kind"] == "SNAPSHOT"]
    forks = [node["handle"] for node in branches if node["kind"] == "BRANCH"]
    return "\n".join([
        "E0 Environment Observatory", "",
        f"RunStatus: {world['run_status']}", f"TaskStatus: {world['task_status']}",
        f"History artifact: {refs.get('HistoryWindowArtifact')}",
        f"Rollout artifact: {refs.get('RolloutTelemetryArtifact')}",
        f"Snapshot: {', '.join(snapshots) or '-'}", f"Branch: {', '.join(forks) or '-'}",
        f"Tools: {' -> '.join(str(call['tool']) for call in payload['tool_calls'])}",
        f"Report: {path.resolve()}"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E0 TEP Environment Observatory demo")
    parser.add_argument("--output-root", type=Path, default=Path("runs/e0-demo"))
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--lab-revision", default=None,
                        help="exact 40-hex lab revision (default: git rev-parse HEAD)")
    parser.add_argument("--open", action="store_true", help="open the report in a browser")
    args = parser.parse_args(argv)
    path = report_path(args.output_root, args.run_id)
    if path.exists():
        print(f"refusing to overwrite an existing report: {path}", file=sys.stderr)
        return 2
    revision = args.lab_revision
    if revision is None:
        try:
            revision = git_revision(ROOT)
        except (OSError, subprocess.CalledProcessError):
            print("cannot read the lab git revision; pass --lab-revision", file=sys.stderr)
            return 2
        if lab_checkout_dirty(ROOT):
            print(f"warning: the lab checkout has uncommitted changes; the manifest records "
                  f"{revision}, not the code that actually ran", file=sys.stderr)
    try:
        demo = run_demo(args.output_root, args.run_id, lab_revision=revision)
    except LifecycleError as exc:
        print(f"refusing to reuse run records ({exc}); choose another --run-id or "
              "--output-root", file=sys.stderr)
        return 2
    except PrepareError as exc:  # atomic: no manifest was published
        print(f"run preparation failed: {exc}", file=sys.stderr)
        return 1
    except (ValueError, KeyError) as exc:  # run id, lab revision, or dependency pins
        print("invalid run configuration (--run-id, --lab-revision, or "
              f"dependency-pins.json): {exc!r}", file=sys.stderr)
        return 2
    records = p0_root(args.output_root) / demo.run_id
    try:
        payload = build_agent_report(demo.manager.queries(demo.run_id))
        write_report(path, render_html(payload, demo.harness.developer_setup()))
    except (ValueError, LookupError, RuntimeError, OSError) as exc:  # incl. VisibilityViolation
        print(f"run {demo.run_id} ended {demo.outcome.terminal_status.value} but no report "
              f"was written ({exc}); P0 records: {records}", file=sys.stderr)
        return 1
    print(terminal_summary(payload, path))
    problems = lineage_findings(payload, demo.harness.baseline_handle)
    for problem in problems:
        print(f"lineage check: {problem}", file=sys.stderr)
    if args.open:
        webbrowser.open(path.resolve().as_uri())
    executed = [call["tool"] for call in payload["tool_calls"]]
    ok = (demo.outcome.terminal_status == RunStatus.COMPLETED and not problems
          and payload["world"]["task_status"] == "DONE" and tuple(executed) == EXPECTED_TOOLS)
    return 0 if ok else 1


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>E0 Environment Observatory</title>
<style>
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d2329;--muted:#5d6670;--line:#d9dde1;
--ref:#1f6fb2;--cf:#c2561a;--mark:#6b5ca5;--dev:#a3261b;--devbg:#fdf0ee;
--ok:#2e7d4f;--chip:#eef1f4;--node:#f2f5f8;--hl:#ffe9a8}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15181b;
--panel:#1d2125;--ink:#e6e8ea;--muted:#9aa3ab;--line:#353b41;--ref:#5aa6e6;
--cf:#f08a4b;--mark:#a99be0;--dev:#f08070;--devbg:#2c1b19;--ok:#6cc690;--chip:#272c31;
--node:#242a30;--hl:#5a4a12}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,
"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 2px}h2{font-size:17px;margin:0 0 10px}
h3{font-size:14px;margin:14px 0 6px}
.sub{color:var(--muted);margin:0 0 16px}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:16px;margin:0 0 16px;overflow-x:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}
code,.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;word-break:break-all}
.chip{display:inline-block;background:var(--chip);border-radius:10px;padding:0 8px;
margin:1px 2px;font-size:12px}
.status{font-weight:700}.ok{color:var(--ok)}.warn{color:var(--dev)}
.note{color:var(--muted);font-size:12px}
.flow{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.flow div{border:1px solid var(--line);border-radius:6px;padding:6px 10px;background:var(--node)}
.flow span{color:var(--muted)}
.dev{border:2px solid var(--dev);background:var(--devbg)}
.dev h2{color:var(--dev)}
select{font:inherit;padding:2px 6px;background:var(--panel);color:var(--ink);
border:1px solid var(--line);border-radius:4px}
svg text{fill:var(--ink);font-size:11px}
.axis line,.axis path{stroke:var(--muted)}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;margin:6px 0}
.sw{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:6px}
details{margin:6px 0}summary{cursor:pointer;color:var(--muted)}
pre{max-height:360px;overflow:auto;background:var(--chip);padding:8px;border-radius:6px;
font-size:12px}
.tree{list-style:none;padding-left:0}.tree ul{list-style:none;padding-left:22px;
border-left:2px solid var(--line);margin-left:8px}
.tree li{margin:4px 0}
.src-application_lifecycle{background:#dfe9f5;color:#1b3a5c}
.src-runtime_trace{background:#e7e2f3;color:#3a2a63}
.src-lab_run_log{background:#e1f0e6;color:#1d4d30}
@media (prefers-color-scheme: dark){.src-application_lifecycle{background:#21354a;color:#cfe0f3}
.src-runtime_trace{background:#2f2846;color:#ddd3f6}.src-lab_run_log{background:#1f3a2a;color:#cdebd8}}
#tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--line);
border-radius:6px;padding:6px 8px;font-size:12px;display:none;box-shadow:0 2px 8px #0003}
#graph{min-width:850px;display:block}
#graph .equipment{fill:var(--node);stroke:var(--muted);stroke-width:1.8}
#graph .symbol{fill:none;stroke:var(--muted);stroke-width:1.4;pointer-events:none}
#graph .pipe{stroke:var(--muted);fill:none;stroke-width:2.5;pointer-events:stroke}
#graph .utility .pipe{stroke-dasharray:6 4}
#graph [data-process-node-id],#graph [data-process-edge-id]{cursor:pointer;outline:none}
#graph [data-process-node-id] text{text-anchor:middle;font-size:11px}
#graph [data-process-edge-id] text{font-size:10px;fill:var(--muted)}
#graph text{paint-order:stroke;stroke:var(--panel);stroke-width:3;stroke-linejoin:round}
#graph .hl .equipment{fill:var(--hl);stroke:var(--mark);stroke-width:3}
#graph .hl .pipe{stroke:var(--mark);stroke-width:4}
#graph .sel .equipment,#graph .sel .pipe,#graph g:focus .equipment,#graph g:focus .pipe{
stroke:var(--ref);stroke-width:4}
#graph .has-telemetry text{font-weight:700}
#graph .zone-label{fill:var(--muted);font-size:11px;letter-spacing:1px}
#graph .animated .pipe{stroke-dasharray:8 6;animation:process-dash 2s linear infinite}
@keyframes process-dash{to{stroke-dashoffset:-28}}
@media(prefers-reduced-motion:reduce){#graph .animated .pipe{animation:none}}
button.plot{font:inherit;color:var(--ref);background:var(--panel);border:1px solid var(--line);
border-radius:4px;cursor:pointer;padding:2px 6px}
.process-scroll{overflow-x:auto}
</style>
</head>
<body>
<main>
<h1>E0 — TEP Environment Observatory</h1>
<p class="sub">Developer/research view of one real P0 run. Not a production UI, not a
benchmark, and not a source of truth: process data outside the red developer block
comes from AGENT-scoped P0 projections of the run. SVG geometry is presentation only.</p>

<section id="a"><h2>A · Run / World summary</h2><div id="a-body"></div></section>

<section id="b"><h2>B · Data flow (documentation only)</h2>
<div class="flow"><div>TEP simulator<br><span class="note">tep-sim TEPEnvironment</span></div>
<span>→</span><div>telemetry<br><span class="note">rollout() JSONL artifacts</span></div>
<span>→</span><div>ReferenceWorld<br><span class="note">harness reference history</span></div>
<span>→</span><div>Agent tools<br><span class="note">C4 surface + C5 bridge</span></div>
<span>→</span><div>Coordinator<br><span class="note">B2 gates · Executor · B3 verify · ingestion</span></div>
<span>→</span><div>P0 projections<br><span class="note">RunManager / RunQueries (AGENT)</span></div>
<span>→</span><div>E0<br><span class="note">this page (owns no truth)</span></div></div>
<p class="note">E0 adds no runtime path: it reads the finished run's projections and
exact-ref artifacts. Telemetry already exists today as TEPEnvironment.rollout() →
telemetry JSONL → ReferenceWorld.history() → C4 get_history → P0 TelemetryView.</p>
</section>

<section id="c"><h2>C · Time series</h2>
<label>Variable <select id="var"></select></label>
<div class="legend"><span><i class="sw" style="background:var(--ref)"></i>
<b id="ref-label"></b></span><span><i class="sw" style="background:var(--cf)"></i>
<b id="cf-label"></b></span><span><i class="sw" style="background:var(--mark)"></i>
branch origin (from the Agent-visible fork result)</span><span><i class="sw"
style="background:var(--dev)"></i>incident injection (developer-only block)</span></div>
<div style="position:relative"><svg id="chart" viewBox="0 0 900 360" width="100%"
role="img" aria-label="time series chart"></svg><div id="tip"></div></div>
<p class="note" id="series-src"></p>
<h3>C5 compare_trajectories (descriptive metrics, not scoring)</h3><div id="cmp"></div>
</section>

<section id="d"><h2>D · Process telemetry overlay</h2>
<p class="note"><b id="schem"></b>. <span id="graph-prov"></span></p>
<div class="process-scroll">__SCHEMATIC_SVG__</div>
<p class="note">Purple / pale fill = exact selected signal binding; blue outline = selected
entity; bold label = current telemetry available. Dashed connections = utility streams.
Arrows show ProcessGraph direction. Colors do not indicate process health. Line crossings
are not extra junctions. Click equipment or a stream (or focus and press Enter) for bindings.</p>
<p class="note" id="overlay-status"></p>
<div id="node-detail" aria-live="polite"></div>
<details><summary>All nodes</summary><div id="node-table"></div></details>
</section>

<section id="e"><h2>E · Snapshot / branch tree</h2><div id="tree"></div>
<p class="note">From BranchTreeView (SimulationSandbox lineage records). A snapshot is an
exact saved world state; a fork is an isolated branch environment created from it; a
counterfactual rollout steps only that branch — the reference world is never stepped
by Agent tools.</p></section>

<section id="f"><h2>F · Runtime event timeline</h2>
<p class="note">Trace ≠ Observation ≠ Evidence. This feed is a view of control-plane and
state-log records; none of it is evidence. Observations are registered by deterministic
ingestion; evidence exists only through an explicit evidence link (there are none here).</p>
<div id="calls"></div>
<label><input type="checkbox" id="only-major" checked> hide routine GATE ALLOW / RECONCILIATION</label>
<div id="events"></div></section>

<section id="g"><h2>G · Investigation / artifacts / budgets</h2><div id="g-body"></div>
<h3>Raw P0 views (AGENT scope)</h3><div id="raw"></div></section>

<section class="dev" id="dev"><h2 id="dev-title"></h2><div id="dev-body"></div></section>
</main>
<script type="application/json" id="agent-payload">__AGENT_JSON__</script>
<script type="application/json" id="developer-setup">__DEVELOPER_JSON__</script>
<script>
"use strict";
const A = JSON.parse(document.getElementById("agent-payload").textContent);
const DEV = JSON.parse(document.getElementById("developer-setup").textContent);
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs, ...kids){const n=document.createElement(tag);
 for(const[k,v]of Object.entries(attrs||{})){if(k==="class")n.className=v;else n.setAttribute(k,v);}
 for(const k of kids){if(k==null)continue;n.append(k instanceof Node?k:String(k));}return n;}
function sv(tag, attrs, text){const n=document.createElementNS(NS,tag);
 for(const[k,v]of Object.entries(attrs||{}))n.setAttribute(k,v);if(text!=null)n.textContent=text;return n;}
function table(head, rows){const t=el("table");t.append(el("tr",{},...head.map(h=>el("th",{},h))));
 for(const r of rows)t.append(el("tr",{},...r.map(c=>el("td",{},c))));return t;}
function code(x){return el("code",{},x==null?"—":String(x));}
function fmt(x,d=4){return typeof x==="number"?Number(x.toPrecision(d+2)).toString():String(x);}
const V = A.views, W = A.world;
const SOURCES = new Set(["application_lifecycle","runtime_trace","lab_run_log"]);

// A — run / world summary
(function(){const cfg=W.environment_config, ps=W.process_semantics, rp=W.runtime_policy;
 const box=document.getElementById("a-body");
 box.append(el("div",{class:"grid"},
  table(["Status domain","Value","Owner"],[
   ["Application RunStatus",el("span",{class:"status"+(W.run_status==="COMPLETED"?" ok":" warn")},W.run_status),W.run_status_owner],
   ["Runtime TaskStatus",el("span",{class:"status"+(W.task_status==="DONE"?" ok":" warn")},W.task_status||"—"),W.task_status_owner||"—"]]),
  table(["World","Value"],[["run id",code(W.run_id)],["simulation time",fmt(W.simulation_time_hours)+" h"],
   ["seed",cfg.seed],["control mode",cfg.control_mode],["record interval",cfg.record_interval+" s"],
   ["backend",cfg.backend],["environment",code(W.environment_version)]])));
 box.append(el("p",{class:"note"},"RunStatus is the application-hosting lifecycle (COMPLETED = the Coordinator "+
  "returned a RuntimeResult). TaskStatus is the runtime task outcome; P0 never mirrors one into the other."));
 box.append(table(["Identity","Value"],[
  ["ProcessGraph",ps.fixture_id+" "+ps.fixture_version+" · "+ps.review_status],
  ["ProcessGraph sha256",code(ps.fixture_checksum)],
  ["manifest checksum",el("span",{},code(W.manifest_checksum)," ",el("span",{class:"note"},"("+W.manifest_checksum_of+")"))],
  ["tool-set version",code(rp.tool_set_version)],["gate policy",code(rp.gate_policy_version)],
  ["tool surface / bridge",code(rp.tool_surface_version+" · "+rp.bridge_version)],
  ["verifier",code(rp.verifier_version)],
  ["allowed tools",el("span",{},...rp.allowed_tools.map(t=>el("span",{class:"chip"},t)))]]));
})();

// C — time series
const sel=document.getElementById("var");
for(const v of A.variables)sel.append(el("option",{value:v},v));
function ticks(lo,hi,n){const span=hi-lo||Math.abs(hi)||1,step0=span/n,mag=Math.pow(10,Math.floor(Math.log10(step0)));
 const step=[1,2,5,10].map(m=>m*mag).find(s=>span/s<=n)||10*mag;const out=[];
 for(let t=Math.ceil(lo/step)*step;t<=hi+step*1e-9;t+=step)out.push(+t.toPrecision(12));return out;}
function drawChart(name){
 const s=A.series[name], svg=document.getElementById("chart");svg.replaceChildren();
 document.getElementById("ref-label").textContent="Reference / incident trajectory";
 document.getElementById("cf-label").textContent="Counterfactual baseline-descended rollout";
 const unit=(V.process_graph.nodes.concat(V.process_graph.edges).flatMap(n=>n.bindings)
  .find(b=>b.runtime_variable_id===name)||{}).unit||"";
 const pts=s.reference.points.concat(s.counterfactual.points);
 const xs=pts.map(p=>p[0]), ys=pts.map(p=>p[1]);
 let x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
 const pad=(y1-y0)*0.08||Math.abs(y1)*0.01||1;y0-=pad;y1+=pad;
 const L=70,R=20,T=16,B=40,Wd=900,Ht=360;
 const X=t=>L+(t-x0)/(x1-x0||1)*(Wd-L-R), Y=v=>T+(y1-v)/(y1-y0)*(Ht-T-B);
 const ax=sv("g",{class:"axis"});
 for(const t of ticks(y0,y1,6)){ax.append(sv("line",{x1:L,x2:Wd-R,y1:Y(t),y2:Y(t),stroke:"var(--line)"}));
  ax.append(sv("text",{x:L-6,y:Y(t)+4,"text-anchor":"end"},fmt(t,4)));}
 for(const t of ticks(x0,x1,8)){ax.append(sv("line",{x1:X(t),x2:X(t),y1:Ht-B,y2:Ht-B+5}));
  ax.append(sv("text",{x:X(t),y:Ht-B+18,"text-anchor":"middle"},fmt(t,3)));}
 ax.append(sv("line",{x1:L,x2:Wd-R,y1:Ht-B,y2:Ht-B}));
 ax.append(sv("text",{x:(L+Wd-R)/2,y:Ht-6,"text-anchor":"middle"},"simulation time [h]"));
 ax.append(sv("text",{x:14,y:(T+Ht-B)/2,transform:`rotate(-90 14 ${(T+Ht-B)/2})`,"text-anchor":"middle"},
  name+(unit?" ["+unit+"]":"")+" (raw engineering value)"));
 svg.append(ax);
 const marks=[];
 if(A.fork&&typeof A.fork.simulation_time_hours==="number")
  marks.push([A.fork.simulation_time_hours,"var(--mark)","branch origin "+fmt(A.fork.simulation_time_hours,3)+" h",T+12]);
 if(typeof DEV.injection_time_hours==="number")
  marks.push([DEV.injection_time_hours,"var(--dev)","injection (developer-only)",T+26]);
 for(const[t,c,label,y]of marks){svg.append(sv("line",{x1:X(t),x2:X(t),y1:T,y2:Ht-B,stroke:c,"stroke-dasharray":"5 4","stroke-width":1.5}));
  svg.append(sv("text",{x:X(t)+5,y:y,style:"fill:"+c},label));}
 for(const[key,c]of[["reference","var(--ref)"],["counterfactual","var(--cf)"]]){
  const p=s[key].points;svg.append(sv("polyline",{points:p.map(q=>X(q[0])+","+Y(q[1])).join(" "),fill:"none",stroke:c,"stroke-width":2}));
  for(const q of p)svg.append(sv("circle",{cx:X(q[0]),cy:Y(q[1]),r:2.4,fill:c}));}
 const cursor=sv("line",{y1:T,y2:Ht-B,stroke:"var(--muted)",visibility:"hidden"});svg.append(cursor);
 const tip=document.getElementById("tip");
 svg.onmousemove=e=>{const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)*Wd/r.width;
  const t=x0+(px-L)/(Wd-L-R)*(x1-x0);if(t<x0-1e-9||t>x1+1e-9){svg.onmouseleave();return;}
  const near=p=>p.reduce((a,b)=>Math.abs(b[0]-t)<Math.abs(a[0]-t)?b:a);
  const rp=near(s.reference.points),parts=["t = "+fmt(rp[0],4)+" h","reference: "+fmt(rp[1],6)];
  const cp=s.counterfactual.points.find(p=>Math.abs(p[0]-rp[0])<1e-9);
  parts.push("counterfactual: "+(cp?fmt(cp[1],6):"— (branch not yet forked)"));
  cursor.setAttribute("x1",X(rp[0]));cursor.setAttribute("x2",X(rp[0]));cursor.setAttribute("visibility","visible");
  tip.replaceChildren(...parts.flatMap((p,i)=>i?[el("br"),p]:[p]));tip.style.display="block";
  tip.style.left=Math.min(r.width-190,(X(rp[0])*r.width/Wd)+12)+"px";tip.style.top="8px";};
 svg.onmouseleave=()=>{tip.style.display="none";cursor.setAttribute("visibility","hidden");};
 document.getElementById("series-src").textContent="Reference: "+s.reference.points.length+
  " records from "+s.reference.artifact_id+" (HistoryWindowArtifact, C4 get_history). Counterfactual: "+
  s.counterfactual.points.length+" records from "+s.counterfactual.artifact_id+
  " (RolloutTelemetryArtifact, C4 run_rollout). Values are not normalized or resampled.";
 highlightGraph(name);}
(function(){const c=A.comparison,box=document.getElementById("cmp");
 if(!c){box.append("No comparison observation.");return;}const al=c.alignment;
 box.append(el("p",{class:"note"},"error = "+c.error_definition+" · alignment "+al.policy+
  " · window "+fmt(al.reference_window.start_hours,3)+"–"+fmt(al.reference_window.end_hours,3)+" h · "+
  al.aligned_samples+" aligned samples @ "+al.sampling_interval_seconds+" s · resampled: "+al.resampled+
  " · preprocessing "+c.preprocessing+" · "+c.metric_contract_version));
 const metrics=c.variables[0].metrics.map(m=>m.metric);
 box.append(table(["variable","unit",...metrics],c.variables.map(v=>[v.variable,v.unit||"—",
  ...v.metrics.map(m=>fmt(m.value,6))])));})();

// D — presentation geometry resolves canonical IDs through the P0-derived overlay.
const O=A.process_overlay, graphElements={nodes:{},edges:{}};
function selectSignal(name){if(!Object.hasOwn(A.series,name))return;sel.value=name;drawChart(name);}
function signalTable(rows){return table(["signal / plot","quantity / relation","current reference telemetry"],
 rows.map(r=>{const b=r.binding,name=b.runtime_variable_id;
 const label=r.plottable?el("button",{class:"plot",type:"button"},name+" [plot]"):code(name);
 if(r.plottable)label.onclick=()=>selectSignal(name);
 return[label,b.quantity+" · "+b.relation,r.telemetry_present?
  fmt(r.current_value,6)+" "+b.unit+" @ "+fmt(r.simulation_time_hours)+" h":
  "telemetry not present in this E0 projection"];}));}
function showEntity(category,id){const record=O.entities[category][id],n=record.entity;
 for(const[k,list]of Object.entries(graphElements))for(const[key,g]of Object.entries(list)){
  const selected=k===category&&key===id;g.classList.toggle("sel",selected);g.setAttribute("aria-pressed",String(selected));}
 const box=document.getElementById("node-detail");
 box.replaceChildren(el("h3",{},n.name+" · "+n.kind+(n.tag?" · tag "+n.tag:"")),
  el("p",{class:"note"},category.slice(0,-1)+" id: "+id+" · "+O.telemetry_source));
 if(category==="nodes"){
  const h=A.topology.neighborhoods[id];
  box.append(table(["ProcessGraph neighborhood","canonical IDs"],[
   ["upstream",h.upstream.join(", ")||"—"],["downstream",h.downstream.join(", ")||"—"],
   ["inlets",h.inlets.join(", ")||"—"],["outlets",h.outlets.join(", ")||"—"]]));
 }else box.append(table(["ProcessGraph stream","value"],[
  ["source",n.source_node],["target",n.target_node],["stream number",n.stream_number??"—"]]));
 for(const[kind,label]of[["XMEAS","Measurements"],["XMV","Actuators"]]){
  const rows=record.signals.filter(r=>r.binding.runtime_variable_kind===kind);
  box.append(el("h3",{},label),rows.length?signalTable(rows):el("p",{class:"note"},"No direct bindings."));}
 if(category==="nodes"){
  box.append(el("h3",{},"Incident stream bindings"));
  for(const edge of V.process_graph.edges.filter(e=>e.source_node===id||e.target_node===id)){
   const button=el("button",{class:"plot",type:"button"},edge.edge_id+" · "+edge.name);
   button.onclick=()=>showEntity("edges",edge.edge_id);
   box.append(el("p",{},button));const rows=O.entities.edges[edge.edge_id].signals;
   box.append(rows.length?signalTable(rows):el("p",{class:"note"},"No direct bindings."));}}
 box.append(el("details",{},el("summary",{},"ProcessGraph entity (raw P0 view)"),
  el("pre",{},JSON.stringify(n,null,1))));}
function highlightGraph(name){const targets=O.signal_entities[name]||{nodes:[],edges:[]};
 for(const[category,list]of Object.entries(graphElements))for(const[id,g]of Object.entries(list))
  g.classList.toggle("hl",targets[category].includes(id));
 const row=[...Object.values(O.entities.nodes),...Object.values(O.entities.edges)]
  .flatMap(r=>r.signals).find(r=>r.binding.runtime_variable_id===name);
 document.getElementById("overlay-status").textContent="Selected signal: "+name+" · "+
  (row&&row.telemetry_present?fmt(row.current_value,6)+" "+row.binding.unit+" (current reference @ "+
   fmt(row.simulation_time_hours)+" h)":"telemetry not present in this E0 projection")+
  " · Bound nodes: "+(targets.nodes.join(", ")||"none")+" · Bound streams: "+(targets.edges.join(", ")||"none")+
  " · "+(Object.keys(O.animated_edges).length?O.animation_note:
   "Static connections only: no available bound positive flow measurement.");}
(function(){const g=V.process_graph,svg=document.getElementById("graph");
 document.getElementById("schem").textContent=A.topology.label;
 document.getElementById("graph-prov").textContent=g.provenance.fixture_id+" "+g.provenance.fixture_version+
  " · "+g.provenance.review_status+" · "+g.nodes.length+" nodes, "+g.edges.length+" edges";
 for(const[category,attr]of[["nodes","data-process-node-id"],["edges","data-process-edge-id"]]){
  for(const element of svg.querySelectorAll("["+attr+"]")){
   const id=element.getAttribute(attr),record=O.entities[category][id],n=record.entity;
   graphElements[category][id]=element;
   element.setAttribute("tabindex","0");element.setAttribute("role","button");
   element.setAttribute("aria-label",n.name+" ("+id+")");
   element.append(sv("title",{},n.name+" · "+n.kind+" · "+id));
   const text=element.querySelector("text"),label=category==="edges"&&n.stream_number!=null?
    String(n.stream_number)+" · "+n.name:n.name;
   const words=label.split(" "),lines=[],limit=category==="nodes"?23:21;
   let line="";
   for(const word of words){if(line&&(line+" "+word).length>limit){lines.push(line);line=word;}
    else line=(line+" "+word).trim();}if(line)lines.push(line);
   const x=text.getAttribute("x")||0;
   lines.forEach((line,i)=>text.append(sv("tspan",{x,dy:i?12:-(lines.length-1)*6},line)));
   if(category==="edges"){
    element.querySelector(".pipe").setAttribute("marker-end","url(#process-arrow)");
    element.classList.toggle("utility",n.kind==="UTILITY_STREAM");
    element.classList.toggle("animated",Object.hasOwn(O.animated_edges,id));}
   element.classList.toggle("has-telemetry",record.signals.some(r=>r.telemetry_present));
   element.onclick=()=>showEntity(category,id);
   element.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();showEntity(category,id);}};
  }}
 document.getElementById("node-table").append(table(["node","kind","name / tag"],
  g.nodes.map(n=>[code(n.node_id),n.kind,n.name+(n.tag?" ("+n.tag+")":"")])));
 const first=Object.keys(graphElements.nodes)[0];if(first)showEntity("nodes",first);
})();

// E — branch tree from BranchTreeView parent links
(function(){const bt=V.branch_tree,box=document.getElementById("tree");
 const kids=p=>bt.nodes.filter(n=>n.parent===p);
 const rollout=A.rollout;
 function item(label,detail,children){const li=el("li",{},el("b",{},label)," ",el("span",{class:"note"},detail));
  if(children.length)li.append(el("ul",{},...children));return li;}
 function node(n){const ch=kids(n.handle).map(node);
  if(n.kind==="BRANCH"&&rollout&&rollout.branch_id===n.handle)
   ch.push(item("rollout",fmt(rollout.start_time_hours,3)+" → "+fmt(rollout.end_time_hours,3)+" h · "+
    rollout.termination_reason+" · "+rollout.shape.records+" records → "+rollout.artifact_ref.ref_id+
    " ("+rollout.artifact_ref.kind+")",[]));
  const what=n.kind==="SNAPSHOT"?"snapshot":"counterfactual branch";
  return item(what+" "+n.handle,"kind "+n.kind+" · lineage "+n.lineage+" · status "+n.status+" · parent "+n.parent,ch);}
 box.append(el("ul",{class:"tree"},item("reference","kind "+bt.root.kind+" · status "+bt.root.status+
  " (harness-owned ReferenceWorld; never stepped by Agent tools)",kids(bt.root.handle).map(node))));
 box.append(el("p",{class:"note"},"Agent-visible baseline listing (get_capability_summary): "+
  (A.baseline_snapshots.map(s=>s.snapshot_id).join(", ")||"none")+(A.fork?" · fork result: branch "+A.fork.branch_id+
  " from "+A.fork.parent_snapshot_id+" at "+fmt(A.fork.simulation_time_hours,3)+" h, isolated = "+A.fork.isolated:"")));})();

// F — tool calls and event timeline
(function(){document.getElementById("calls").append(el("h3",{},"What the Agent/runtime actually called"),
 table(["turn","request","tool","runtime trace of the turn (AGENT feed)","observation","artifacts"],
  A.model_turns.map(t=>{const os=t.observations,list=f=>os.length?el("span",{},...os.map(o=>el("div",{},f(o)))):"—";
   return[t.turn,list(o=>code(o.request_id)),
   os.length?list(o=>el("b",{},o.tool)):el("i",{},"no observation registered (finish, or call not ingested)"),
   el("span",{},...t.trace.map(x=>el("span",{class:"chip"},x.type+":"+x.status))),
   list(o=>code(o.observation_id)),list(o=>o.artifact_refs.map(r=>r.kind).join(", ")||"—")];})),
 el("p",{class:"note"},"Turns are delimited by runtime MODEL_TURN events; each observation is matched to the "+
  "turn whose RESULT_INGESTION registered it (state-revision order). AGENT trace items carry no request payloads."));
 // Hide only routine control-plane noise; every other event (failures included) stays.
 const routine=e=>e.source==="runtime_trace"&&(e.type==="RECONCILIATION"||(e.type==="GATE"&&e.status==="ALLOW"));
 const box=document.getElementById("events"),cb=document.getElementById("only-major");
 function render(){const ev=V.events.events.filter(e=>!cb.checked||!routine(e));
  box.replaceChildren(el("p",{class:"note"},V.events.semantics+" · "+ev.length+" of "+V.events.events.length+" events"),
  table(["pos","source","type","status","request / revision","timestamp"],ev.map(e=>[e.position,
   el("span",{class:"chip "+(SOURCES.has(e.source)?"src-"+e.source:"")},e.source),e.type,e.status||"—",
   e.request_id?code(e.request_id):(e.resulting_revision!=null?"revision "+e.expected_revision+" → "+e.resulting_revision+
    (e.operations?" · "+[].concat(e.operations).map(o=>typeof o==="string"?o:(o.operation||JSON.stringify(o))).join(", "):""):"—"),
   el("span",{class:"note"},e.timestamp||"—")])));}
 cb.onchange=render;render();})();

// G — investigation, artifacts, budgets, raw views
(function(){const inv=V.investigation,box=document.getElementById("g-body"),b=V.budget;
 box.append(el("div",{class:"grid"},
  table(["RcaState (revision "+inv.state_revision+")","count"],[["observations",inv.observations.length],
   ["evidence links",inv.evidence_links.length],["hypotheses",inv.hypotheses.length],
   ["completed experiments",inv.completed_experiments.length],["runtime TaskStatus",inv.runtime_task_status]]),
  table(["budget dimension","limit","used"],Object.entries(Object.assign({},b.limits,b.limits.extra_dimensions||{}))
   .filter(([k,v])=>k!=="extra_dimensions"&&v!=null).map(([k,v])=>[k,v,b.usage[k.replace(/^max_/,"")]||0]))));
 box.append(el("p",{class:"note"},"Observations are automatically registered tool results; evidence links exist only "+
  "through an explicit model state update. This demo proposes none, so evidence links = "+inv.evidence_links.length+"."));
 box.append(el("h3",{},"Issued artifacts (exact refs; content read through get_artifact with checksum verification)"),
  table(["ref","kind","sha256","records"],V.artifacts.artifacts.map(r=>[code(r.ref_id),r.kind,code(r.checksum),
   (A.artifact_contents[r.ref_id]||{content:[]}).content.length])));
 box.append(el("h3",{},"Observations"),table(["observation","tool","summary keys"],inv.observations.map(o=>[
  code(o.ref.ref_id),(o.record&&o.record.tool_or_service_ref.ref_id)||"—",
  el("details",{},el("summary",{},Object.keys((o.record&&o.record.summary)||{}).join(", ")),
   el("pre",{},JSON.stringify(o.record&&o.record.summary,null,1)))])));
 const raw=document.getElementById("raw");
 for(const[k,v]of Object.entries(V))raw.append(el("details",{},el("summary",{},k+" · "+v.view),el("pre",{},JSON.stringify(v,null,1))));
 for(const[k,v]of Object.entries(A.artifact_contents))raw.append(el("details",{},el("summary",{},"artifact "+k),el("pre",{},JSON.stringify(v,null,1))));})();

// Developer-only block (separate JSON source)
(function(){document.getElementById("dev-title").textContent="Trusted demo setup — "+DEV.label;
 document.getElementById("dev-body").append(table(["",""],[["known injected cause",el("b",{},DEV.known_injected_cause)],
  ["injection time",fmt(DEV.injection_time_hours,3)+" h"],["pre-incident baseline",code(DEV.pre_incident_baseline_snapshot)],
  ["reference end time",fmt(DEV.reference_end_time_hours,3)+" h"],["harness sequence",DEV.harness_sequence.join(" → ")]]),
  el("p",{class:"note"},DEV.note));})();

sel.onchange=()=>drawChart(sel.value);drawChart(A.variables[0]);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
