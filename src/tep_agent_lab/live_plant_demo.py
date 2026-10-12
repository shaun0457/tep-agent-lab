"""Reproducible LOS-B-S1 CLI over public application telemetry queries."""

import argparse
from pathlib import Path
import subprocess
import tempfile

from industrial_agent_runtime import Budget

from .application_views import ApplicationViewService
from .canonical_context import ProjectionScope, inventory_checksum
from .live_plant import LivePlantPolicy, LivePlantState
from .playground import (ModelSpec, RunManager, RunRequest, SourceRevisions, WorldSpec,
                         load_dependency_pins, pinned_tep_sim_sources)


class _UnusedProvider:
    def generate(self, *args, **kwargs):
        raise RuntimeError("the live demonstration has no model turns")


def main() -> int:
    parser = argparse.ArgumentParser(description="clean deterministic simulated stream")
    parser.add_argument("mode", choices=("live",))
    parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    pins = load_dependency_pins(root / "dependency-pins.json")
    lab_revision = subprocess.check_output(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root),
         "rev-parse", "HEAD"], text=True).strip()
    policy = LivePlantPolicy()
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
        print(f"manifest before: {manifest.checksum()}")
        manager.start_live("live-demo")
        try:
            # Supervise committed milestones and open exact accepted prefixes. No
            # read/printing grants permission to step; the producer never waits on us.
            for k in (1, 101, 201, 301):
                live.wait_for_commit(k)
                snapshot = app.open_telemetry_snapshot("live-demo", ingest_sequence=k)
                values = [app.get_signal("live-demo", signal, snapshot=snapshot).current.value
                          for signal in signals]
                print(f"T={snapshot.event_horizon_ticks:3d} K={snapshot.ingest_sequence:3d} "
                      + " ".join(f"{signal}={value:.8f}" for signal, value in zip(signals, values)))
        finally:
            live.wait()
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
