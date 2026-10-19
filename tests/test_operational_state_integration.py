"""LOS-A-S1 integration: Operational State over a real P0 run and a growing live stream.

Authorized clean simulated data only (trusted setup advances the reference world with
no intervention); no evaluator fixtures, benchmark cases or scorer material.
"""

import ast
import contextlib
from fractions import Fraction
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from industrial_agent_runtime import Budget, to_jsonable

from tep_agent_lab import (application_transport, operational_state_demo, tool_bridge,
                           tool_surface)
from tep_agent_lab.application_views import ApplicationViewService
from tep_agent_lab.live_plant import LivePlantPolicyV2
from tep_agent_lab.operational_state import (ContextUnavailable, EngineeringSelection, Feature,
                                             FeatureStatus, Freshness,
                                             InvalidOperationalStateRequest, PlantContextRef,
                                             Trend, UnknownRun)
from tep_agent_lab.operational_state_demo import main as demo_main
from tep_agent_lab.playground import (ModelSpec, RunManager, RunRequest, SourceRevisions,
                                      WorldSpec, load_dependency_pins, pinned_tep_sim_sources)
from tep_agent_lab.telemetry import (ClockDescriptor, ClockKind, ClockMismatch,
                                     SnapshotUnavailable, StoreRef, TelemetryReadSnapshot,
                                     TimePoint, UnknownBinding, parse_event_resolution)
from tep_agent_lab.tep_operational_state import (INTERIM_PROCESS_GRAPH_SOURCE,
                                                 TEP_CLEAN_STREAM_POLICY,
                                                 P0OperationalStateBoundary,
                                                 interim_plant_context,
                                                 operational_state_service, served_signals)
from tep_agent_lab.tep_world import leakage_findings

PINS = load_dependency_pins(Path(__file__).parents[1] / "dependency-pins.json")
TEMPERATURE = "reactor.temperature_measurement"
PRESSURE = "reactor.pressure_measurement"
EMPTY = EngineeringSelection.empty()
POLICY_REF = TEP_CLEAN_STREAM_POLICY.ref


class NoModel:
    def generate(self, *args, **kwargs):
        raise AssertionError("operational state reads never call a model")


def plain(view):
    return json.loads(json.dumps(to_jsonable(view)))


def rebuilt(snapshot, **changes):
    """A self-consistent public snapshot object with top-level fields changed."""
    body = {**json.loads(json.dumps(snapshot)), **changes}
    clock = body["event_clock"]
    ref = TelemetryReadSnapshot(
        StoreRef(**body["store"]), body["ingest_sequence"], TimePoint(**body["event_horizon"]),
        ClockDescriptor(clock["clock_id"], ClockKind(clock["kind"]),
                        parse_event_resolution(clock["resolution"]), clock["origin"]),
        body["context_ref"], body["visibility_policy_ref"]).snapshot_ref
    return {**body, "snapshot_ref": ref}


def clean_setup(world):
    """Trusted pre-READY advance of the reference world; no intervention is applied."""
    world.advance(0.05)


class OperationalStateIntegrationCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.manager = RunManager(
            temp.name, dependency_pins=PINS, clock=lambda: "2026-10-19T00:00:00Z",
            revisions=SourceRevisions(PINS["tep-sim"], PINS["industrial-agent-runtime"],
                                      "a" * 40))
        self.sources = tuple(s for s in pinned_tep_sim_sources(PINS["tep-sim"])
                             if s.kind == "PROCESS_GRAPH")
        self.service = operational_state_service(self.manager)

    def request(self, interval, tools=None):
        options = {} if tools is None else {"allowed_tools": tools}
        return RunRequest("op-state", "clean stream", WorldSpec(17, record_interval=interval),
                          ModelSpec("unused", "unused", "v0", "v0"), Budget(0, 0, 0, 0, 0),
                          require_conclusion=False, **options)

    def snapshot(self, run_id, **options):
        return plain(self.manager.queries(run_id).telemetry_snapshot(**options))["snapshot"]

    def read(self, run_id, signal, snapshot, **options):
        options.setdefault("plant_context_ref", interim_plant_context(snapshot))
        options.setdefault("engineering_selection", EMPTY)
        return self.service.get_operational_state(
            run_id, signal, snapshot=snapshot,
            interpretation_policy_refs=(POLICY_REF,), **options)


class P0RunTests(OperationalStateIntegrationCase):
    def prepare(self, run_id, interval):
        self.manager.create(run_id, self.request(interval))
        self.manager.prepare(run_id, provider=NoModel(), context_sources=self.sources,
                             case_setup=clean_setup)
        self.addCleanup(lambda: self.manager._runs[run_id].session.close())

    def test_real_p0_run_matches_public_reads_and_exact_hand_computation(self):
        self.prepare("p0-fine", 1)
        queries = self.manager.queries("p0-fine")
        snapshot = self.snapshot("p0-fine")
        horizon = snapshot["event_horizon"]["ticks"]
        self.assertEqual(horizon, 180)
        result = self.read("p0-fine", TEMPERATURE, snapshot)
        history = plain(queries.telemetry_history(
            TEMPERATURE, snapshot=snapshot, event_start_ticks=60, event_end_ticks=180,
            max_points=1000))
        samples = history["samples"]
        self.assertEqual(len(samples), 121)
        self.assertEqual([(i.source_id, i.signal_id, i.sequence)
                          for i in result.window.sample_identities],
                         [(s["source_id"], s["signal_id"], s["sequence"]) for s in samples])
        exact = (Fraction(samples[-1]["value"]) - Fraction(samples[0]["value"])) / 120
        rate = result.features[Feature.RATE_OF_CHANGE]
        self.assertEqual(rate.value, float(exact))
        tolerance = Fraction(TEP_CLEAN_STREAM_POLICY.tolerance("deg C"))
        expected = (Trend.RISING if exact > tolerance else
                    Trend.FALLING if exact < -tolerance else Trend.STABLE)
        self.assertEqual(result.features[Feature.TREND].value, expected)
        values = [s["value"] for s in samples]
        self.assertEqual(result.features[Feature.WINDOW_MIN].value, min(values))
        self.assertEqual(result.features[Feature.WINDOW_MAX].value, max(values))
        current = plain(queries.telemetry_current(TEMPERATURE, snapshot=snapshot))
        body = result.as_json()
        self.assertEqual(body["current"], current["sample"])
        self.assertEqual(body["current_read"]["eligible_count"], current["eligible_count"])
        self.assertEqual(result.features[Feature.STALENESS].value, Freshness.FRESH)
        self.assertEqual(result.features[Feature.THRESHOLD].status,
                         FeatureStatus.NO_CANONICAL_LIMIT)
        plant = body["context"]["plant"]
        self.assertEqual(plant["kind"], INTERIM_PROCESS_GRAPH_SOURCE)
        self.assertEqual(plant["context_ref"], snapshot["context_ref"])
        graph = self.sources[0]
        self.assertEqual((plant["source"]["source_id"], plant["source"]["content_checksum"],
                          plant["source"]["git_revision"]),
                         (graph.source_id, graph.content_checksum, graph.git_revision))
        self.assertEqual(body["descriptor"], {"value_kind": "NUMERIC", "unit": "deg C"})
        self.assertEqual(leakage_findings(body), [])
        self.assertNotIn("ingest_time", json.dumps(body))

    def test_every_bound_signal_has_a_pinned_unit_tolerance(self):
        self.prepare("p0-units", 1)
        snapshot = self.snapshot("p0-units")
        queries = self.manager.queries("p0-units")
        signals = served_signals(queries)
        bound = {binding["semantic_entity_id"] for category in ("nodes", "edges")
                 for entity in plain(queries.process_graph())[category]
                 for binding in entity["bindings"]}
        self.assertEqual(set(signals), bound)
        self.assertEqual(len(signals), 53)
        app = ApplicationViewService(self.manager.queries)
        pinned = app.open_telemetry_snapshot("p0-units")
        for signal in signals:
            result = self.read("p0-units", signal, snapshot)
            self.assertEqual(result.features[Feature.TREND].status, FeatureStatus.AVAILABLE)
            # Same resolution as the application signal read.
            self.assertEqual(result.as_json()["descriptor"]["unit"],
                             app.get_signal("p0-units", signal, snapshot=pinned).unit)

    def test_signal_resolution_matches_the_application_read(self):
        boundary = P0OperationalStateBoundary(None)

        def graph(*bindings):
            class Queries:
                def process_graph(self):
                    return {"nodes": [{"node_id": "n1", "bindings": list(bindings)}],
                            "edges": []}
            return Queries()

        def binding(**changes):
            return {"semantic_entity_id": "s1", "runtime_variable_id": "r1",
                    "quantity": "temperature", "relation": "MEASURES", "unit": "deg C",
                    **changes}

        descriptor = boundary.signal_descriptor(graph(binding(), binding(runtime_variable_id="r2")),
                                                "s1")
        self.assertEqual(descriptor.unit, "deg C")
        for other in (binding(quantity="pressure"), binding(relation="ACTUATES"),
                      binding(unit="kPa")):
            with self.subTest(other=other), self.assertRaises(UnknownBinding) as caught:
                boundary.signal_descriptor(graph(binding(), other), "s1")
            self.assertNotIn("s1", str(caught.exception))
        with self.assertRaises(UnknownBinding):
            boundary.signal_descriptor(graph(binding()), "s2")
        self.assertEqual(TEP_CLEAN_STREAM_POLICY.parameters()["max_aggregate_candidates"], 50_000)

    def test_default_p0_cadence_reports_gaps_instead_of_a_trend(self):
        self.prepare("p0-coarse", 60)
        snapshot = self.snapshot("p0-coarse")
        result = self.read("p0-coarse", TEMPERATURE, snapshot)
        for feature in (Feature.RATE_OF_CHANGE, Feature.TREND):
            self.assertEqual(result.features[feature].status, FeatureStatus.GAP_EXCEEDS_POLICY)
        self.assertEqual(result.features[Feature.WINDOW_MAX].status, FeatureStatus.AVAILABLE)
        self.assertEqual(result.window.diagnostics["max_adjacent_gap_ticks"], 60)

    def test_typed_redacted_failures_through_public_p0_queries(self):
        self.prepare("p0-fail", 1)
        self.prepare("p0-other", 1)
        snapshot = self.snapshot("p0-fail")
        horizon_clock = json.loads(json.dumps(snapshot))
        horizon_clock["event_horizon"]["clock_id"] = "clock-secret"   # only the horizon clock
        other_context = rebuilt(snapshot, context_ref="context-source:other")
        cases = ((UnknownRun, dict(run_id="p0-missing")),
                 (InvalidOperationalStateRequest, dict(run_id="Not A Run")),
                 (UnknownBinding, dict(signal="reactor.secret_measurement")),
                 (ContextUnavailable, dict(plant_context_ref=PlantContextRef(
                     INTERIM_PROCESS_GRAPH_SOURCE, "context-source:other"))),
                 (SnapshotUnavailable, dict(run_id="p0-other")),
                 (ClockMismatch, dict(snapshot=horizon_clock)),
                 (UnknownBinding, dict(source_id="tep-reference:p0-fail:secret")),
                 (SnapshotUnavailable, dict(snapshot=rebuilt(snapshot, ingest_sequence=10**6))),
                 # A wrong context is caught by the public snapshot validation first.
                 (SnapshotUnavailable, dict(snapshot=other_context,
                                            plant_context_ref=interim_plant_context(other_context))))
        for error, options in cases:
            run_id = options.pop("run_id", "p0-fail")
            signal = options.pop("signal", TEMPERATURE)
            pinned = options.pop("snapshot", snapshot)
            with self.subTest(error=error.__name__, options=sorted(options)), \
                    self.assertRaises(error) as caught:
                self.read(run_id, signal, pinned, **options)
            text = str(caught.exception)
            for secret in (run_id, signal, "context-source:other", "secret",
                           snapshot["event_clock"]["clock_id"], "1000000"):
                self.assertNotIn(secret, text)
            self.assertIsNone(caught.exception.__cause__)

    def test_no_transport_method_api_version_or_agent_tool_change(self):
        self.assertEqual(application_transport.PROTOCOL_VERSION,
                         "tep-agent-lab.application-transport/v1")
        for module in (application_transport, tool_surface, tool_bridge):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("operational", source.lower())
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("operational_state", node.module or "")


class LiveStreamTests(OperationalStateIntegrationCase):
    def test_old_snapshot_answers_stay_identical_while_the_stream_grows(self):
        self.manager.create("op-live", self.request(1, tools=()))
        self.manager.prepare("op-live", provider=NoModel(), context_sources=self.sources,
                             live_policy=LivePlantPolicyV2(step_count=300))
        live = self.manager.live_session("op-live")
        self.addCleanup(self.manager.dispose_live, "op-live")
        self.addCleanup(live.wait)
        self.addCleanup(live.close_prepared)
        self.manager.start_live("op-live")
        live.wait_for_commit(121, timeout=60)
        old = self.snapshot("op-live", ingest_sequence=121)
        first = {signal: self.read("op-live", signal, old) for signal in (TEMPERATURE, PRESSURE)}
        self.assertEqual(first[TEMPERATURE].as_json()["window"]["event_start_ticks"], 0)
        # Reads during and after growth at the same snapshot are identical.
        deadline = time.monotonic() + 60
        while live.state.value == "ACTIVE" and time.monotonic() < deadline:
            again = self.read("op-live", TEMPERATURE, old)
            self.assertEqual(again.as_json(), first[TEMPERATURE].as_json())
        live.wait(timeout=60)
        newer = self.snapshot("op-live")
        self.assertEqual((newer["ingest_sequence"], newer["event_horizon"]["ticks"]), (301, 300))
        for signal, result in first.items():
            again = self.read("op-live", signal, old)
            self.assertEqual(again.as_json(), result.as_json())
            self.assertEqual(again.derivation_ref, result.derivation_ref)
            self.assertNotEqual(self.read("op-live", signal, newer).derivation_ref,
                                result.derivation_ref)
        self.manager.dispose_live("op-live")
        with self.assertRaises(SnapshotUnavailable):
            self.read("op-live", TEMPERATURE, old)

    def test_measurement_uses_public_reads_and_enforces_the_documented_margin(self):
        source = Path(operational_state_demo.__file__).read_text(encoding="utf-8")
        for private in (".telemetry.", "._runs", ".session."):
            self.assertFalse(private in source, private)
        self.assertEqual(operational_state_demo.MIN_MARGIN, Fraction(3, 2))
        self.assertTrue(operational_state_demo.margin_ok(0.375, Fraction(1, 4)))   # exactly 1.5x
        self.assertFalse(operational_state_demo.margin_ok(0.375, Fraction(1, 4) + Fraction(1, 10**9)))
        self.assertFalse(operational_state_demo.margin_ok(0.125, Fraction(1, 10)))  # 1.25x
        self.assertTrue(operational_state_demo.margin_ok(0.02, Fraction(0)))
        # A short clean stream: the measured set is the served set, every unit pinned.
        manager = operational_state_demo._manager(self.directory())
        live = operational_state_demo._prepare(manager, "op-measure", 11,
                                               LivePlantPolicyV2(step_count=3))
        self.addCleanup(manager.dispose_live, "op-measure")
        manager.start_live("op-measure")
        live.wait(timeout=60)
        series = operational_state_demo._histories(manager, "op-measure", 3)
        self.assertEqual(tuple(series), served_signals(manager.queries("op-measure")))
        self.assertEqual(len(series), 53)
        for unit, values in series.values():
            TEP_CLEAN_STREAM_POLICY.tolerance(unit)
            self.assertEqual(len(values), 4)

    def directory(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return temp.name

    def test_demo_prints_unchanged_old_snapshot(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(demo_main(["live"]), 0)
        text = output.getvalue()
        self.assertIn("snapshot A: T=120 K=121", text)
        self.assertIn("snapshot B: T=300 K=301", text)
        self.assertIn("identical=True", text)


if __name__ == "__main__":
    unittest.main()
