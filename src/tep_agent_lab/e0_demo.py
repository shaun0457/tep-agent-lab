"""Shared deterministic E0 developer bootstrap; hidden setup is never a client view."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

from industrial_agent_runtime import (Action, Budget, FakeProvider, FinishProposal,
                                      ModelTurn, ToolCallRequest)
from tep_sim import ControlMode, DisturbanceIntervention

from .playground import (ModelSpec, RunManager, RunOutcome, RunRequest, SourceRevisions,
                         WorldSpec, load_dependency_pins, pinned_tep_sim_sources)
from .tep_world import ReferenceWorld
from .tool_surface import simulation_quota

ROOT = Path(__file__).resolve().parents[2]
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


def git_revision(path: Path) -> str:
    """The lab checkout revision (the trusted host attests SourceRevisions).

    ``safe.directory`` is scoped to this one checkout, as in the lab tests, so a
    checkout owned by another user (CI, containers) can still report its revision.
    """
    posix = path.as_posix()
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={posix}", "-C", posix, "rev-parse", "HEAD"],
        text=True, stderr=subprocess.DEVNULL).strip()


@dataclass(frozen=True)
class DemoRun:
    manager: RunManager
    run_id: str
    outcome: RunOutcome
    harness: DemoHarness


def p0_root(output_root: Path) -> Path:
    return Path(output_root) / "p0-runs"


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
