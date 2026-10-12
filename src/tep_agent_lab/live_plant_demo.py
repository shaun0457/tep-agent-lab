"""Reproducible live stream/lifecycle CLI over public application telemetry queries."""

import argparse
from pathlib import Path
import tempfile

from industrial_agent_runtime import Budget

from .application_views import ApplicationViewService
from .canonical_context import ProjectionScope, inventory_checksum
from .e0_demo import git_revision
from .live_plant import (LIVE_POLICY_V1, LivePacingMode, LivePacingPolicy,
                         LivePlantPolicy, LivePlantState)
from .playground import (ModelSpec, RunManager, RunRequest, SourceRevisions, WorldSpec,
                         load_dependency_pins, pinned_tep_sim_sources)
from .sidecar_resources import dependency_pins_path, packaged_lab_revision


class _UnusedProvider:
    def generate(self, *args, **kwargs):
        raise RuntimeError("the live demonstration has no model turns")


def main() -> int:
    parser = argparse.ArgumentParser(description="clean deterministic simulated stream")
    parser.add_argument("mode", choices=("live", "lifecycle"))
    parser.add_argument("--pacing", choices=tuple(m.value for m in LivePacingMode))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    pins = load_dependency_pins(dependency_pins_path())
    lab_revision = packaged_lab_revision() or git_revision(root)
    pacing = LivePacingPolicy(mode=LivePacingMode(args.pacing or (
        "ACCELERATED" if args.mode == "lifecycle" else "DETERMINISTIC")))
    policy = (LivePlantPolicy(version=LIVE_POLICY_V1, session_event_cap=35, pacing=pacing)
              if args.mode == "lifecycle" else LivePlantPolicy(pacing=pacing))
    # Temporary run artifacts are removed on exit; nothing is logged to the repo.
    with tempfile.TemporaryDirectory(prefix="live-plant-demo-") as directory:
        manager = RunManager(directory, dependency_pins=pins,
                             revisions=SourceRevisions(pins["tep-sim"],
                                                       pins["industrial-agent-runtime"], lab_revision))
        manager.create("live-demo", RunRequest(
            "live-demo", "clean simulated telemetry", WorldSpec(17, record_interval=1),
            ModelSpec("unused", "unused", "v0", "v0"), Budget(0, 0, 0, 0, 0),
            allowed_tools=(), require_conclusion=False))
        sources = tuple(s for s in pinned_tep_sim_sources(pins["tep-sim"])
                        if s.kind == "PROCESS_GRAPH")
        manifest = manager.prepare("live-demo", provider=_UnusedProvider(),
                                   context_sources=sources, live_policy=policy)
        live = manager.live_session("live-demo")
        app = ApplicationViewService(manager.queries)
        signals = ("reactor.temperature_measurement", "reactor.pressure_measurement",
                   "reactor_cooling.flow_actuator")
        print("source: clean deterministic simulated stream")
        print(f"policy: {policy.version}; cadence={policy.cadence_ticks}s; steps={policy.step_count}")
        print(f"pacing: {pacing.mode.value}; wall seconds/sim second={pacing.seconds_per_tick:g}")
        print(f"manifest before: {manifest.checksum()}")
        owner = manager.start_live("live-demo")
        try:
            # Supervise committed milestones and open exact accepted prefixes. No
            # read/printing grants permission to step; the producer never waits on us.
            for k in ((1, 11) if args.mode == "lifecycle" else (1, 101, 201, 301)):
                live.wait_for_commit(k)
                snapshot = app.open_telemetry_snapshot("live-demo", ingest_sequence=k)
                values = [app.get_signal("live-demo", signal, snapshot=snapshot).current.value
                          for signal in signals]
                print(f"T={snapshot.event_horizon_ticks:3d} K={snapshot.ingest_sequence:3d} "
                      + " ".join(f"{signal}={value:.8f}" for signal, value in zip(signals, values)))
            if args.mode == "lifecycle":
                manager.pause_live("live-demo", owner=owner)
                first = app.open_telemetry_snapshot("live-demo")
                second = app.open_telemetry_snapshot("live-demo")
                print(f"pause read 1: T={first.event_horizon_ticks} K={first.ingest_sequence}")
                print(f"pause read 2: T={second.event_horizon_ticks} K={second.ingest_sequence}")
                print(f"paused T/K frozen: {first == second}")
                manager.resume_live("live-demo", owner=owner)
                live.wait_for_commit(first.ingest_sequence + 10)
                resumed = app.open_telemetry_snapshot("live-demo")
                print(f"resumed: T={resumed.event_horizon_ticks} K={resumed.ingest_sequence}")
                manager.stop_live("live-demo", owner=owner)
                retained = app.open_telemetry_snapshot("live-demo")
                print(f"stopped retained: T={retained.event_horizon_ticks} K={retained.ingest_sequence}")
                print(f"stop reason: {live.events[-1]['reason']}")
        finally:
            if args.mode == "lifecycle" and live.owner is not None:
                manager.stop_live("live-demo", owner=owner)
            live.wait(timeout=30 + policy.step_count * pacing.seconds_per_tick)
        after = manager.manifest("live-demo")
        inventory = inventory_checksum(manager.queries("live-demo").registry()
                                       .inventory(ProjectionScope.EVALUATOR))
        print(f"session: {live.state.value}; owner={live.owner}; physics_closed={live.physics_closed}")
        print(f"manifest after:  {after.checksum()}")
        print(f"manifest unchanged: {manifest.checksum() == after.checksum()}")
        print(f"inventory unchanged: {manifest.context_inventory_checksum == inventory}")
        print(f"P0 status: {manager.get('live-demo').status.value}; outcome=None")
        return 0 if live.state == LivePlantState.STOPPED else 1


if __name__ == "__main__":
    raise SystemExit(main())
