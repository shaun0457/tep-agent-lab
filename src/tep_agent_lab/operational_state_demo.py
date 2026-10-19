"""Operational State on a growing clean live stream (LOS-A-S1 demonstration).

``live`` prepares a clean deterministic simulated stream, pins an early snapshot while
the stream grows, and prints the operational state of reactor signals at that snapshot
and at the final one; the early answer is re-read and shown unchanged.
``measure`` reproduces the OD-2 trend-tolerance measurement through public reads.
Temporary run artifacts are removed on exit; nothing is written to the repository.
"""

import argparse
from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path
import statistics
import sys
import tempfile

from industrial_agent_runtime import Budget, to_jsonable

from .application_views import ApplicationViewService
from .e0_demo import git_revision
from .live_plant import LivePlantPolicyV2
from .live_plant_demo import cleanup_live
from .operational_state import EngineeringSelection, Feature
from .playground import (ModelSpec, RunManager, RunRequest, SourceRevisions, WorldSpec,
                         load_dependency_pins, pinned_tep_sim_sources)
from .run_telemetry import TelemetryReadPolicy
from .sidecar_resources import dependency_pins_path, packaged_lab_revision
from .tep_operational_state import (TEP_CLEAN_STREAM_POLICY, interim_plant_context,
                                    operational_state_service, served_signals)

RUN_ID = "operational-demo"
SIGNALS = ("reactor.temperature_measurement", "reactor.pressure_measurement",
           "reactor_cooling.flow_actuator")
_COMMIT_TIMEOUT = 120.0
# Documented OD-2 rule: each tolerance is at least 1.5x the clean maximum |rate|.
MIN_MARGIN = Fraction(3, 2)


class _UnusedProvider:
    def generate(self, *args, **kwargs):
        raise RuntimeError("the operational state demonstration has no model turns")


def _manager(directory: str, policy: TelemetryReadPolicy | None = None) -> RunManager:
    root = Path(__file__).resolve().parents[2]
    pins = load_dependency_pins(dependency_pins_path())
    revision = packaged_lab_revision() or git_revision(root)
    return RunManager(directory, dependency_pins=pins, telemetry_policy=policy,
                      revisions=SourceRevisions(pins["tep-sim"], pins["industrial-agent-runtime"],
                                                revision))


def _prepare(manager: RunManager, run_id: str, seed: int, policy: LivePlantPolicyV2):
    pins = load_dependency_pins(dependency_pins_path())
    manager.create(run_id, RunRequest(
        run_id, "clean simulated telemetry", WorldSpec(seed, record_interval=1),
        ModelSpec("unused", "unused", "v0", "v0"), Budget(0, 0, 0, 0, 0),
        allowed_tools=(), require_conclusion=False))
    sources = tuple(s for s in pinned_tep_sim_sources(pins["tep-sim"])
                    if s.kind == "PROCESS_GRAPH")
    manager.prepare(run_id, provider=_UnusedProvider(), context_sources=sources,
                    live_policy=policy)
    return manager.live_session(run_id)


def _snapshot(manager: RunManager, run_id: str, **options) -> dict:
    view = manager.queries(run_id).telemetry_snapshot(**options)
    return json.loads(json.dumps(to_jsonable(view)))["snapshot"]


def _state(service, run_id: str, signal: str, snapshot: dict):
    return service.get_operational_state(
        run_id, signal, snapshot=snapshot, plant_context_ref=interim_plant_context(snapshot),
        engineering_selection=EngineeringSelection.empty(),
        interpretation_policy_refs=(TEP_CLEAN_STREAM_POLICY.ref,))


def _line(result) -> str:
    features = result.features
    current = features[Feature.CURRENT].value
    if current is None or features[Feature.WINDOW_MIN].value is None:
        return (f"  {result.signal_id}: current={features[Feature.CURRENT].status.value}; "
                f"window={features[Feature.WINDOW_MIN].status.value}")
    rate, trend = features[Feature.RATE_OF_CHANGE], features[Feature.TREND]
    low, high = features[Feature.WINDOW_MIN], features[Feature.WINDOW_MAX]
    window = result.window.as_json()
    staleness = features[Feature.STALENESS]
    rate_text = (f"{rate.value:+.6f} {rate.detail['rate_unit']}" if rate.value is not None
                 else rate.status.value)
    return (f"  {result.signal_id}: current={current.value:.6f} {current.quality} "
            f"(seq {current.identity.sequence}, tick {current.event_ticks}); "
            f"{staleness.value} age={staleness.detail['age_ticks']}; "
            f"window=[{window['event_start_ticks']},{window['event_end_ticks']}] "
            f"n={len(result.window.sample_identities)}; rate={rate_text}; "
            f"trend={trend.value or trend.status.value}; "
            f"min={low.value:.6f} max={high.value:.6f}; "
            f"threshold={features[Feature.THRESHOLD].status.value}")


def _live(steps: int) -> int:
    with tempfile.TemporaryDirectory(prefix="operational-state-demo-") as directory:
        manager = _manager(directory)
        live = _prepare(manager, RUN_ID, 17, LivePlantPolicyV2(step_count=steps))
        service = operational_state_service(manager)
        print("source: clean deterministic simulated stream (seed 17, 1 s cadence)")
        print(f"policy: {TEP_CLEAN_STREAM_POLICY.ref.ref}")
        early_k = min(121, steps + 1)
        try:
            manager.start_live(RUN_ID)
            live.wait_for_commit(early_k, timeout=_COMMIT_TIMEOUT)
            early = _snapshot(manager, RUN_ID, ingest_sequence=early_k)
            first = {signal: _state(service, RUN_ID, signal, early) for signal in SIGNALS}
            print(f"snapshot A: T={early['event_horizon']['ticks']} K={early['ingest_sequence']} "
                  f"{early['snapshot_ref']}")
            for signal in SIGNALS:
                print(_line(first[signal]))
            live.wait(timeout=_COMMIT_TIMEOUT)
            final = _snapshot(manager, RUN_ID)
            print(f"stream grew: session={live.state.value}; snapshot B: "
                  f"T={final['event_horizon']['ticks']} K={final['ingest_sequence']}")
            for signal in SIGNALS:
                print(_line(_state(service, RUN_ID, signal, final)))
            again = {signal: _state(service, RUN_ID, signal, early) for signal in SIGNALS}
            unchanged = all(again[s].as_json() == first[s].as_json()
                            and again[s].derivation_ref == first[s].derivation_ref
                            for s in SIGNALS)
            print(f"snapshot A re-read after growth: identical={unchanged}")
            for signal in SIGNALS:
                print(f"  {signal}: {first[signal].derivation_ref}")
            return 0 if unchanged else 1
        finally:
            try:
                cleanup_live(live, suppress_errors=sys.exc_info()[0] is not None)
            finally:
                manager.dispose_live(RUN_ID)


def _histories(manager: RunManager, run_id: str, steps: int):
    """Complete unreduced per-signal histories via public application reads (<=1000/read)."""
    app = ApplicationViewService(manager.queries)
    snapshot = app.open_telemetry_snapshot(run_id)
    queries = manager.queries(run_id)
    graph = queries.process_graph()
    bound = sorted({binding["semantic_entity_id"] for category in ("nodes", "edges")
                    for entity in graph[category] for binding in entity["bindings"]})
    signals = served_signals(queries)
    if list(signals) != bound:
        raise RuntimeError("measurement requires every bound signal to be served")
    series = {}
    for signal in signals:
        unit = app.get_signal(run_id, signal, snapshot=snapshot).unit
        TEP_CLEAN_STREAM_POLICY.tolerance(unit)   # every served unit has a pinned tolerance
        values = []
        for start in range(0, steps + 1, 1000):
            history = app.get_signal_history(run_id, signal, snapshot=snapshot,
                                              event_start_ticks=start,
                                              event_end_ticks=min(start + 999, steps),
                                              max_points=1000)
            if history.reduced or history.returned_count != history.eligible_count:
                raise RuntimeError("measurement requires complete windows")
            values += [(point.event_ticks, point.value, point.quality) for point in history.points]
        if [tick for tick, _, _ in values] != list(range(steps + 1)) or \
                any(quality != "GOOD" for _, _, quality in values):
            raise RuntimeError("measurement requires one GOOD sample per tick")
        series[signal] = (unit, [value for _, value, _ in values])
    return series


def margin_ok(tolerance: float, worst: Fraction) -> bool:
    """Whether a tolerance keeps the documented margin over the clean maximum |rate|."""
    return worst == 0 or Fraction(tolerance) >= MIN_MARGIN * worst


def _measure(steps: int, seeds: list[int]) -> int:
    window = TEP_CLEAN_STREAM_POLICY.default_window_ticks
    signals = 64   # generous per-batch bound; the pinned graph binds 53 signals
    commits = steps + 1
    reader = TelemetryReadPolicy(max_points_limit=max(1000, commits),
                                 max_scan_records=max(50_000, commits * signals))
    policy = LivePlantPolicyV2(step_count=steps, commit_cap=commits,
                               retained_sample_cap=commits * signals,
                               index_record_cap=commits * signals,
                               retained_byte_cap=commits * signals * 1024,
                               commit_metadata_byte_cap=commits * 1024)
    worst: dict[str, Fraction] = defaultdict(Fraction)
    noise: dict[str, float] = defaultdict(float)
    print(f"measurement: clean stream, {steps} steps at 1 s, seeds {seeds}, "
          f"max |endpoint secant| over every {window}-tick window, public application reads")
    with tempfile.TemporaryDirectory(prefix="operational-state-measure-") as directory:
        manager = _manager(directory, reader)
        for seed in seeds:
            run_id = f"measure-seed-{seed}"
            live = _prepare(manager, run_id, seed, policy)
            try:
                manager.start_live(run_id)
                live.wait(timeout=_COMMIT_TIMEOUT + steps)
                for unit, values in _histories(manager, run_id, steps).values():
                    steps_1s = [b - a for a, b in zip(values, values[1:])]
                    noise[unit] = max(noise[unit], statistics.pstdev(steps_1s))
                    for index in range(len(values) - window):
                        rate = abs(Fraction(values[index + window]) - Fraction(values[index]))
                        worst[unit] = max(worst[unit], rate / window)
            finally:
                try:
                    cleanup_live(live, suppress_errors=sys.exc_info()[0] is not None)
                finally:
                    manager.dispose_live(run_id)
    print(f"{'unit':>6} {'1 s step std':>13} {'max |rate| /s':>14} {'tolerance /s':>13} "
          f"{'margin':>7}")
    passed = True
    for unit in sorted(worst):
        tolerance = TEP_CLEAN_STREAM_POLICY.tolerance(unit)
        margin = (Fraction(tolerance) / worst[unit]) if worst[unit] else None
        passed = passed and margin_ok(tolerance, worst[unit])
        print(f"{unit:>6} {noise[unit]:>13.4g} {float(worst[unit]):>14.6g} {tolerance:>13g} "
              f"{'n/a' if margin is None else f'{float(margin):.2f}x':>7}")
    print(f"required margin: >= {float(MIN_MARGIN)}x; {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operational State over the clean live stream")
    parser.add_argument("mode", nargs="?", choices=("live", "measure"), default="live")
    parser.add_argument("--steps", type=int, default=None,
                        help="live: 300 (default); measure: 3600 (default)")
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 17])
    args = parser.parse_args(argv)
    steps = args.steps if args.steps is not None else (300 if args.mode == "live" else 3600)
    if steps < 1 or (args.mode == "measure"
                     and steps <= TEP_CLEAN_STREAM_POLICY.default_window_ticks):
        parser.error("steps must be positive (measure: longer than the default window)")
    return _live(steps) if args.mode == "live" else _measure(steps, args.seeds)


if __name__ == "__main__":
    raise SystemExit(main())
