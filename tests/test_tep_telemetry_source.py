"""P1.1B acceptance tests: TEPSimulationSource -> canonical telemetry core."""

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest import mock

from tep_sim import (ControlMode, DisturbanceIntervention, EnvironmentConfig, MVIntervention,
                     Observation,
                     ProcessGraph, TEPEnvironment, UPSTREAM_REVISION, load_process_graph)
from tep_sim.bindings import BindingMethod, BindingProvenance, BindingRelation
from tep_sim.process import PINNED_FIXTURES

from tep_agent_lab import tep_telemetry, telemetry, tep_world
from tep_agent_lab.tep_telemetry import (SANITIZED_FIELDS, TICK_TOLERANCE_SECONDS,
                                         TEPSimulationSource, TEPSourceError,
                                         build_signal_bindings, simulation_ticks)
from tep_agent_lab.telemetry import (ClockDescriptor, ClockKind, IdentityConflict,
                                     InMemoryTimeSeriesStore, Quality, SourceObservation,
                                     TelemetryIngestor, TimePoint, TimeSeriesReader,
                                     UnknownBinding)
from tep_agent_lab.tep_world import (ReferenceWorld, leakage_findings, read_telemetry,
                                     sanitize_observation)

GRAPH = load_process_graph()
INGEST = ClockDescriptor("ingest-test", ClockKind.UTC, Fraction(1, 1000),
                         "1970-01-01T00:00:00Z/utc-smeared")
RUNTIME_ID = re.compile(r"XMEAS|XMV|IDV", re.IGNORECASE)
TEMPERATURE = "reactor.temperature_measurement"
COOLING = "reactor_cooling.flow_actuator"


def make_source(run: str = "run-1", graph: ProcessGraph = GRAPH) -> TEPSimulationSource:
    return TEPSimulationSource(graph, source_id=f"tep-sim/{run}", clock_id=f"tep-sim/{run}/sim",
                               clock_origin=f"tep-sim/{run}/reset-t0")


def make_store(source: TEPSimulationSource) -> InMemoryTimeSeriesStore:
    # context/visibility refs are caller-owned registration inputs, not the source's.
    return InMemoryTimeSeriesStore(
        store_id="p1-1b-test", namespace="tep-test", context_ref="context:test",
        visibility_policy_ref="visibility:test", event_clock=source.event_clock,
        ingest_clock=INGEST, bindings=source.store_bindings())


def synthetic_record(seconds: int = 360, offset: float = 0.0) -> dict:
    """Sanitized-shape record with a distinct value per runtime variable."""
    measurements = {f"XMEAS({i})": 100.0 + i + offset for i in range(1, 42)}
    manipulated = {f"XMV({i})": 200.0 + i + offset for i in range(1, 13)}
    return {"simulation_time_hours": seconds / 3600, "measurements": measurements,
            "manipulated_variables": manipulated, "shutdown_state": False,
            "safety_margins": {"reactor_pressure_kpa": 100.0}}


def ingest_at(ms: int) -> TimePoint:
    return TimePoint(INGEST.clock_id, ms)


def fake_graph(bindings) -> ProcessGraph:
    """Bypasses ProcessGraph validation to exercise the adapter's own fail-closed checks."""
    return ProcessGraph(GRAPH.provenance, {}, {}, bindings, ())


class BindingTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = make_source()

    def test_table_is_derived_from_process_graph_bindings(self) -> None:
        table = build_signal_bindings(GRAPH)
        self.assertEqual(table, self.source.bindings())
        self.assertEqual(tuple(sorted(item.signal_id for item in table)),
                         self.source.signal_ids)
        graph = {b.semantic_entity_id: b for b in GRAPH.bindings()}
        self.assertEqual(set(graph), set(self.source.signal_ids))
        for item in table:
            binding = graph[item.signal_id]
            self.assertEqual((binding.runtime_variable_id, binding.attached_to,
                              binding.relation, binding.quantity),
                             (item.runtime_variable_id, item.attached_to, item.relation,
                              item.quantity))
            self.assertEqual(binding.describe()["unit"], item.unit)

    def test_exact_visible_coverage(self) -> None:
        runtime = {item.runtime_variable_id for item in self.source.bindings()}
        self.assertEqual(runtime, {b.runtime_variable_id for b in GRAPH.bindings()})
        for runtime_id in runtime:
            self.assertEqual(GRAPH.binding(runtime_id).semantic_entity_id,
                             self.source.binding(runtime_id).signal_id)
        relations = [item.relation for item in self.source.bindings()]
        self.assertEqual(41, relations.count(BindingRelation.MEASURES))
        self.assertEqual(12, relations.count(BindingRelation.ACTUATES))
        self.assertEqual(53, len(self.source.signal_ids))
        self.assertEqual({f"XMEAS({i})" for i in range(1, 42)}
                         | {f"XMV({i})" for i in range(1, 13)}, runtime)

    def test_only_measures_and_actuates(self) -> None:
        self.assertEqual({BindingRelation.MEASURES, BindingRelation.ACTUATES},
                         {item.relation for item in self.source.bindings()})

    def test_canonical_ids_are_semantic_entity_ids(self) -> None:
        for signal_id in self.source.signal_ids:
            self.assertIsNone(RUNTIME_ID.search(signal_id), signal_id)
        (signals,) = self.source.store_bindings().values()
        self.assertFalse(any(RUNTIME_ID.search(signal) for signal in signals))

    def test_reactor_temperature_provenance(self) -> None:
        item = self.source.binding(TEMPERATURE)
        self.assertIs(item, self.source.binding("XMEAS(9)"))
        self.assertEqual(
            {"signal_id": TEMPERATURE, "runtime_variable_id": "XMEAS(9)",
             "attached_to": "reactor", "relation": "MEASURES", "quantity": "temperature",
             "unit": "deg C", "mapping_method": "HUMAN_VERIFIED_MAPPING",
             "graph": {"fixture_id": "tep-process-graph", "fixture_version": "0.2.0",
                       "content_sha256": PINNED_FIXTURES[("tep-process-graph", "0.2.0")]}},
            {key: value for key, value in item.describe().items()
             if key != "mapping_source_refs"})
        self.assertTrue(item.mapping_source_refs)

    def test_reactor_cooling_actuator_mapping(self) -> None:
        item = self.source.binding("XMV(10)")
        self.assertEqual(COOLING, item.signal_id)
        self.assertEqual(BindingRelation.ACTUATES, item.relation)
        self.assertEqual("reactor_cooling_water_in", item.attached_to)
        self.assertEqual(GRAPH.binding("XMV(10)").semantic_entity_id, item.signal_id)

    def test_unknown_binding_lookup(self) -> None:
        for identifier in ("IDV(4)", "nope", "XMEAS(99)"):
            with self.assertRaises(UnknownBinding):
                self.source.binding(identifier)

    def test_disturbs_relation_fails_closed(self) -> None:
        first = GRAPH.bindings()[0]
        disturbs = replace(first, semantic_entity_id="zz.cause", relation=BindingRelation.DISTURBS,
                           runtime_variable_id="IDV(4)", runtime_variable_kind="IDV")
        with self.assertRaisesRegex(TEPSourceError, "unsupported binding relation") as caught:
            build_signal_bindings(fake_graph([*GRAPH.bindings(), disturbs]))
        self.assertNotIn("zz.cause", str(caught.exception))

    def test_invalid_semantic_id_fails_at_construction(self) -> None:
        first = GRAPH.bindings()[0]
        for bad in (" padded", "x" * 300, "a\tb"):
            with self.assertRaises(TEPSourceError):
                build_signal_bindings(fake_graph([replace(first, semantic_entity_id=bad)]))

    def test_duplicate_signal_or_runtime_binding_fails_closed(self) -> None:
        a, b = GRAPH.bindings()[:2]
        with self.assertRaisesRegex(TEPSourceError, "duplicate canonical signal"):
            build_signal_bindings(fake_graph([a, replace(b, semantic_entity_id=a.semantic_entity_id)]))
        with self.assertRaisesRegex(TEPSourceError, "bound twice"):
            build_signal_bindings(fake_graph([a, replace(b, runtime_variable_id=a.runtime_variable_id)]))
        with self.assertRaisesRegex(TEPSourceError, "collides"):
            build_signal_bindings(fake_graph([a, replace(b, semantic_entity_id=a.runtime_variable_id)]))

    def test_unverified_graph_or_binding_fails_closed(self) -> None:
        first = GRAPH.bindings()[0]
        curated = replace(first, provenance=BindingProvenance(BindingMethod.CURATED_MAPPING,
                                                              first.provenance.source_refs))
        with self.assertRaisesRegex(TEPSourceError, "not human-verified"):
            build_signal_bindings(fake_graph([curated]))
        unpinned = ProcessGraph(replace(GRAPH.provenance, pinned=False), {}, {},
                                GRAPH.bindings(), ())
        with self.assertRaisesRegex(TEPSourceError, "pinned human-verified"):
            build_signal_bindings(unpinned)
        pending = ProcessGraph(replace(GRAPH.provenance, review_status="PENDING_HUMAN_REVIEW"),
                               {}, {}, GRAPH.bindings(), ())
        with self.assertRaises(TEPSourceError):
            build_signal_bindings(pending)
        with self.assertRaises(TEPSourceError):
            build_signal_bindings(fake_graph([]))
        with self.assertRaises(TEPSourceError):
            build_signal_bindings({"bindings": []})


class ClockTests(unittest.TestCase):
    def test_hours_to_exact_second_ticks(self) -> None:
        for hours, ticks in ((0.0, 0), (0, 0), (1 / 3600, 1), (60 / 3600, 60), (0.1, 360),
                             (1.0, 3600)):
            self.assertEqual(ticks, simulation_ticks(hours), hours)

    def test_accumulated_simulator_time_stays_on_grid(self) -> None:
        # tep-sim sums dt = 1/3600 h; the conversion must absorb that float drift.
        hours = 0.0
        for _ in range(7200):
            hours += 1.0 / 3600.0
        self.assertEqual(7200, simulation_ticks(hours))

    def test_off_grid_and_invalid_times_rejected(self) -> None:
        for hours in (0.5 / 3600, 1.5 / 3600, (360 + 2 * TICK_TOLERANCE_SECONDS) / 3600):
            with self.assertRaisesRegex(TEPSourceError, "integral simulator second"):
                simulation_ticks(hours)
        for hours in (-1 / 3600, float("nan"), float("inf"), True, "0.1", None, 1e306,
                      10**400):
            with self.assertRaises(TEPSourceError):
                simulation_ticks(hours)

    def test_simulation_clock_descriptor(self) -> None:
        clock = make_source().event_clock
        self.assertEqual(ClockKind.SIMULATION, clock.kind)
        self.assertEqual(Fraction(1), clock.resolution)
        self.assertEqual("tep-sim/run-1/reset-t0", clock.origin)
        self.assertEqual(TimePoint(clock.clock_id, 360), make_source().event_time(0.1))

    def test_source_identity_is_required(self) -> None:
        for kwargs in ({"source_id": ""}, {"clock_id": " x"}, {"clock_origin": None},
                       {"source_id": "a\nb"}, {"source_id": "s" * 300}):
            arguments = {"source_id": "s", "clock_id": "c", "clock_origin": "o", **kwargs}
            with self.assertRaises(TEPSourceError):
                TEPSimulationSource(GRAPH, **arguments)


class TranslationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = make_source()

    def test_accepted_fields_are_exactly_the_sanitizer_output(self) -> None:
        raw = Observation(0.0, synthetic_record()["measurements"],
                          synthetic_record()["manipulated_variables"], ("IDV(4)",), False,
                          {"reactor_pressure_kpa": 1.0})
        sanitized = sanitize_observation(raw)
        self.assertEqual(SANITIZED_FIELDS, set(sanitized))
        self.assertEqual(53, len(self.source.translate_sanitized(sanitized)))

    def test_one_record_is_one_complete_batch(self) -> None:
        batch = self.source.translate_sanitized(synthetic_record(360))
        self.assertEqual(53, len(batch))
        self.assertEqual(self.source.signal_ids, tuple(item.signal_id for item in batch))
        for item in batch:
            self.assertIs(SourceObservation, type(item))
            self.assertEqual(self.source.source_id, item.source_id)
            self.assertEqual(TimePoint(self.source.event_clock.clock_id, 360), item.event_time)
            self.assertEqual(0, item.sequence)
            self.assertEqual(Quality.GOOD, item.quality)
            self.assertIsNone(item.source_status_code)
            self.assertIsNone(item.source_metadata_ref)
            self.assertFalse(hasattr(item, "ingest_time"))

    def test_values_follow_binding_relation(self) -> None:
        record = synthetic_record(60)
        batch = {item.signal_id: item.value for item in self.source.translate_sanitized(record)}
        for item in self.source.bindings():
            field = ("measurements" if item.relation is BindingRelation.MEASURES
                     else "manipulated_variables")
            self.assertEqual(record[field][item.runtime_variable_id], batch[item.signal_id])
        self.assertEqual(record["measurements"]["XMEAS(9)"], batch[TEMPERATURE])
        self.assertEqual(record["manipulated_variables"]["XMV(10)"], batch[COOLING])

    def test_one_record_shares_one_sequence(self) -> None:
        for seconds in (0, 10, 10, 3600):
            batch = self.source.translate_sanitized(synthetic_record(seconds))
            self.assertEqual(len(GRAPH.bindings()), len(batch))
            self.assertEqual(1, len({item.sequence for item in batch}))
            self.assertEqual(1, len({item.event_time for item in batch}))

    def test_sequence_is_monotonic_and_independent_of_event_time(self) -> None:
        # Ticks go backwards, repeat and jump; sequences still count received records.
        ticks = (3600, 10, 10, 0, 7200)
        sequences = [self.source.translate_sanitized(synthetic_record(t))[0].sequence
                     for t in ticks]
        self.assertEqual([0, 1, 2, 3, 4], sequences)
        self.assertEqual(5, self.source.next_sequence)

    def test_identical_record_is_a_new_observation(self) -> None:
        record = synthetic_record(10)
        first = self.source.translate_sanitized(record)
        second = self.source.translate_sanitized(record)
        self.assertEqual([item.event_time for item in first],
                         [item.event_time for item in second])
        self.assertEqual([item.value for item in first], [item.value for item in second])
        self.assertEqual(({0}, {1}), ({item.sequence for item in first},
                                      {item.sequence for item in second}))

    def test_validation_failure_does_not_consume_a_sequence(self) -> None:
        self.assertEqual(0, self.source.translate_sanitized(synthetic_record(0))[0].sequence)
        bad = synthetic_record(1)
        bad["measurements"]["XMEAS(1)"] = float("nan")
        for invalid in (bad, {**synthetic_record(), "simulation_time_hours": 0.5 / 3600},
                        {**synthetic_record(), "active_disturbances": []}, ["x"]):
            with self.assertRaises(TEPSourceError):
                self.source.translate_sanitized(invalid)
            self.assertEqual(1, self.source.next_sequence)
        self.assertEqual(1, self.source.translate_sanitized(synthetic_record(2))[0].sequence)

    def test_fresh_source_replays_the_same_sequence_stream(self) -> None:
        stream = [synthetic_record(t, offset) for t, offset in
                  ((0, 0.0), (10, 0.0), (10, 1.0), (10, 1.0), (20, 0.0))]
        first, second = make_source(), make_source()
        replay_a = [first.translate_sanitized(record) for record in stream]
        replay_b = [second.translate_sanitized(record) for record in stream]
        self.assertEqual(replay_a, replay_b)
        self.assertEqual([0, 1, 2, 3, 4], [batch[0].sequence for batch in replay_a])
        self.assertNotEqual(replay_a[0], make_source("run-2").translate_sanitized(stream[0]))

    def test_concurrent_translation_never_duplicates_a_sequence(self) -> None:
        record = synthetic_record(10)
        workers, calls = 8, 50
        barrier = threading.Barrier(workers)
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # force frequent thread switches to provoke races
        self.addCleanup(sys.setswitchinterval, previous)

        def worker() -> list[set[int]]:
            barrier.wait()
            return [{item.sequence for item in self.source.translate_sanitized(record)}
                    for _ in range(calls)]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker) for _ in range(workers)]
            batches = [sequences for future in futures for sequences in future.result()]
        self.assertTrue(all(len(sequences) == 1 for sequences in batches))
        self.assertEqual(list(range(workers * calls)),
                         sorted(next(iter(sequences)) for sequences in batches))

    def test_batch_construction_failure_does_not_consume_a_sequence(self) -> None:
        self.source.translate_sanitized(synthetic_record(0))
        with mock.patch.object(tep_telemetry, "SourceObservation",
                               side_effect=TEPSourceError("boom")):
            with self.assertRaises(TEPSourceError):
                self.source.translate_sanitized(synthetic_record(1))
        self.assertEqual(1, self.source.next_sequence)
        self.assertEqual(1, self.source.translate_sanitized(synthetic_record(2))[0].sequence)

    def test_shutdown_and_safety_are_not_signals(self) -> None:
        record = synthetic_record()
        record["shutdown_state"] = True
        ids = {item.signal_id for item in self.source.translate_sanitized(record)}
        self.assertEqual(set(self.source.signal_ids), ids)
        self.assertFalse(any("shutdown" in i or "safety" in i or "margin" in i for i in ids))

    def assert_rejected(self, record, pattern: str = "") -> TEPSourceError:
        with self.assertRaisesRegex(TEPSourceError, pattern) as caught:
            self.source.translate_sanitized(record)
        return caught.exception

    def test_hidden_or_unexpected_fields_fail_closed(self) -> None:
        record = synthetic_record()
        record["active_disturbances"] = ["IDV(4)"]
        error = self.assert_rejected(record, "fields mismatch")
        self.assertEqual([], leakage_findings(str(error)))
        record = synthetic_record()
        record["run_id"] = "abc"
        self.assert_rejected(record, "fields mismatch")
        record = synthetic_record()
        del record["safety_margins"]
        self.assert_rejected(record, "missing")

    def test_raw_simulator_observation_rejected(self) -> None:
        raw = Observation(0.0, synthetic_record()["measurements"],
                          synthetic_record()["manipulated_variables"], ("IDV(4)",), False)
        self.assert_rejected(raw, "mapping required")
        data = {"simulation_time": 0.0, "measurements": dict(raw.measurements),
                "manipulated_variables": dict(raw.manipulated_variables),
                "active_disturbances": ["IDV(4)"], "shutdown_state": False,
                "safety_margins": {}}
        self.assert_rejected(data, "fields mismatch")

    def test_value_maps_must_match_bindings_exactly(self) -> None:
        record = synthetic_record()
        del record["measurements"]["XMEAS(9)"]
        self.assert_rejected(record, r"missing \['XMEAS\(9\)'\]")
        record = synthetic_record()
        record["measurements"]["IDV(4)"] = 1.0
        error = self.assert_rejected(record, "1 unbound")
        self.assertEqual([], leakage_findings(str(error)))
        record = synthetic_record()
        record["measurements"]["XMV(10)"] = record["manipulated_variables"].pop("XMV(10)")
        self.assert_rejected(record, "does not match visible bindings")
        record = synthetic_record()
        record["manipulated_variables"] = list(record["manipulated_variables"])
        self.assert_rejected(record, "must be a mapping")

    def test_invalid_values_and_time_fail_closed(self) -> None:
        for value in (float("nan"), float("inf"), 1, True, "1.0", None):
            record = synthetic_record()
            record["measurements"]["XMEAS(1)"] = value
            self.assert_rejected(record, "finite float")
        record = synthetic_record()
        record["simulation_time_hours"] = 0.5 / 3600
        self.assert_rejected(record, "integral simulator second")
        record = synthetic_record()
        record["shutdown_state"] = 0
        self.assert_rejected(record, "shutdown_state")
        for margins in ({"reactor_pressure_kpa": float("nan")}, {"reactor_pressure_kpa": 1},
                        {1: 1.0}):
            record = synthetic_record()
            record["safety_margins"] = margins
            self.assert_rejected(record, "safety_margins")
        self.assert_rejected(["not", "a", "mapping"], "mapping required")

    def test_no_hidden_state_in_translated_batch(self) -> None:
        batch = self.source.translate_sanitized(synthetic_record())
        dump = [{"signal_id": item.signal_id, "source_id": item.source_id,
                 "value": item.value, "quality": str(item.quality),
                 "status": item.source_status_code, "metadata": item.source_metadata_ref}
                for item in batch]
        self.assertEqual([], leakage_findings(dump))
        self.assertEqual([], leakage_findings([item.describe() for item in self.source.bindings()]))
        self.assertNotIn("IDV", json.dumps(dump).upper())


class StoreSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = make_source()
        self.store = make_store(self.source)
        self.ingestor = TelemetryIngestor(self.store)

    def test_record_is_one_atomic_commit(self) -> None:
        result = self.ingestor.ingest(self.source.translate_sanitized(synthetic_record(60)),
                                      ingest_time=ingest_at(5))
        self.assertEqual((1, 53), (result.ingest_sequence, result.new_count))
        self.assertEqual({1}, {record.commit_revision for record in result.records})
        result = self.ingestor.ingest(self.source.translate_sanitized(synthetic_record(120)),
                                      ingest_time=ingest_at(9))
        self.assertEqual({2}, {record.commit_revision for record in result.records})
        self.assertEqual({ingest_at(9)}, {record.sample.ingest_time for record in result.records})

    def test_same_tick_changed_observations_both_retained(self) -> None:
        first = self.ingestor.ingest(self.source.translate_sanitized(synthetic_record(10)),
                                     ingest_time=ingest_at(5))
        second = self.ingestor.ingest(self.source.translate_sanitized(synthetic_record(10, 0.5)),
                                      ingest_time=ingest_at(6))
        self.assertEqual((1, 2, 53), (first.ingest_sequence, second.ingest_sequence,
                                      second.new_count))
        clock = self.source.event_clock.clock_id
        snapshot = self.store.snapshot(TimePoint(clock, 10))
        reader = TimeSeriesReader(self.store, max_interval_ticks=60, max_scan_records=10)
        history = reader.history(source_id=self.source.source_id, signal_id=TEMPERATURE,
                                 event_start=TimePoint(clock, 0), event_end=TimePoint(clock, 10),
                                 max_points=10, snapshot=snapshot)
        self.assertEqual([0, 1], [sample.sequence for sample in history.samples])
        self.assertEqual([10, 10], [sample.event_time.ticks for sample in history.samples])
        record = synthetic_record(10)
        self.assertEqual([record["measurements"]["XMEAS(9)"],
                          record["measurements"]["XMEAS(9)"] + 0.5],
                         [sample.value for sample in history.samples])
        current = reader.current(source_id=self.source.source_id, signal_id=TEMPERATURE,
                                 snapshot=snapshot)
        self.assertEqual(1, current.sample.sequence)

    def test_identical_received_record_is_accepted_again(self) -> None:
        record = synthetic_record(60)
        first = self.ingestor.ingest(self.source.translate_sanitized(record),
                                     ingest_time=ingest_at(5))
        again = self.ingestor.ingest(self.source.translate_sanitized(record),
                                     ingest_time=ingest_at(50))
        self.assertEqual(53, again.new_count)
        self.assertEqual(first.ingest_sequence + 1, again.ingest_sequence)

    def test_store_identity_semantics_unchanged(self) -> None:
        # P1.1A semantics still apply to redelivery of one translated batch.
        batch = self.source.translate_sanitized(synthetic_record(60))
        first = self.ingestor.ingest(batch, ingest_time=ingest_at(5))
        again = self.ingestor.ingest(batch, ingest_time=ingest_at(50))
        self.assertEqual((0, first.records), (again.new_count, again.records))
        changed = tuple(replace(item, value=item.value + 1.0) for item in batch)
        with self.assertRaises(IdentityConflict):
            self.ingestor.ingest(changed, ingest_time=ingest_at(6))
        self.assertEqual(1, self.store.current_ingest_sequence)

    def test_reset_requires_new_source_incarnation(self) -> None:
        self.ingestor.ingest(self.source.translate_sanitized(synthetic_record(0)),
                             ingest_time=ingest_at(1))
        rerun = make_source("run-2")
        self.assertNotEqual(self.source.source_id, rerun.source_id)
        self.assertNotEqual(self.source.event_clock, rerun.event_clock)
        self.assertEqual(0, rerun.next_sequence)
        store = make_store(rerun)
        result = TelemetryIngestor(store).ingest(
            rerun.translate_sanitized(synthetic_record(0, 1.0)), ingest_time=ingest_at(2))
        self.assertEqual(53, result.new_count)
        self.assertEqual({0}, {record.sample.sequence for record in result.records})
        # The first store never registered the new incarnation (nor its clock).
        with self.assertRaises(UnknownBinding):
            self.ingestor.ingest(rerun.translate_sanitized(synthetic_record(0)),
                                 ingest_time=ingest_at(3))

    def test_source_does_not_own_store_or_ingest_time(self) -> None:
        for name in ("ingest_time", "store", "context_ref", "visibility_policy_ref"):
            self.assertFalse(hasattr(self.source, name), name)
        self.assertEqual({self.source.source_id: frozenset(self.source.signal_ids)},
                         self.source.store_bindings())


class RealTEPIntegrationTests(unittest.TestCase):
    """Pinned TEPEnvironment + ProcessGraph; the harness advances physics, not the source."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.environment = TEPEnvironment(EnvironmentConfig(
            seed=11, backend="python", control_mode=ControlMode.CLOSED_LOOP,
            record_interval=60, upstream_revision=UPSTREAM_REVISION,
            artifact_directory=Path(cls.directory.name) / "world"))
        cls.addClassCleanup(cls.environment.close)
        cls.initial = sanitize_observation(cls.environment.reset())
        cls.source = make_source("seed-11-reset-1")
        cls.store = make_store(cls.source)
        cls.ingestor = TelemetryIngestor(cls.store)
        cls.reader = TimeSeriesReader(cls.store, max_interval_ticks=3600, max_scan_records=100)
        cls.initial_batch = cls.source.translate_sanitized(cls.initial)
        cls.k_initial = cls.ingestor.ingest(cls.initial_batch,
                                            ingest_time=ingest_at(1_000)).ingest_sequence
        # Harness-owned hidden incident: the source must never see it.
        cls.environment.apply(DisturbanceIntervention("IDV(4)", 1))
        time_before = cls.environment.observe().simulation_time
        cls.rollout = cls.environment.rollout(0.1)
        cls.raw_records = read_telemetry(cls.rollout.telemetry)
        cls.records = [sanitize_observation(record) for record in cls.raw_records]
        cls.time_before = time_before
        # The rollout origin record is a new received observation: new sequence, new K.
        cls.origin = cls.ingestor.ingest(cls.source.translate_sanitized(cls.records[0]),
                                         ingest_time=ingest_at(2_000))
        cls.progressive = []
        for index, record in enumerate(cls.records[1:], start=1):
            cls.progressive.append(cls.ingestor.ingest(
                cls.source.translate_sanitized(record), ingest_time=ingest_at(2_000 + index)))
        # Every record this class ingested, in delivery order.
        cls.delivered = [cls.initial, *cls.records]

    def test_reset_observation_translates(self) -> None:
        self.assertEqual(SANITIZED_FIELDS, set(self.initial))
        self.assertEqual(1, self.k_initial)
        self.assertEqual({0}, {item.event_time.ticks for item in self.initial_batch})
        self.assertEqual({0}, {item.sequence for item in self.initial_batch})
        self.assertEqual(53, len(self.initial_batch))

    def test_rollout_records_on_second_grid(self) -> None:
        self.assertEqual(0.0, self.time_before)
        self.assertEqual([0, 60, 120, 180, 240, 300, 360],
                         [simulation_ticks(r["simulation_time_hours"]) for r in self.records])

    def test_rollout_origin_is_a_distinct_received_observation(self) -> None:
        self.assertEqual(53, self.origin.new_count)
        self.assertEqual(self.k_initial + 1, self.origin.ingest_sequence)
        self.assertEqual({0}, {r.sample.event_time.ticks for r in self.origin.records})
        self.assertEqual({1}, {r.sample.sequence for r in self.origin.records})

    def test_progressive_ingestion_one_commit_per_record(self) -> None:
        self.assertEqual(list(range(3, 3 + len(self.progressive))),
                         [result.ingest_sequence for result in self.progressive])
        self.assertEqual(list(range(2, 2 + len(self.progressive))),
                         [result.records[0].sample.sequence for result in self.progressive])
        for result in self.progressive:
            self.assertEqual(53, result.new_count)
            self.assertEqual({result.ingest_sequence},
                             {record.commit_revision for record in result.records})
            self.assertEqual(1, len({record.sample.event_time for record in result.records}))
            self.assertEqual(1, len({record.sample.sequence for record in result.records}))

    def test_canonical_values_equal_sanitized_values(self) -> None:
        horizon = TimePoint(self.source.event_clock.clock_id, 360)
        snapshot = self.store.snapshot(horizon)
        start = TimePoint(self.source.event_clock.clock_id, 0)
        for item in self.source.bindings():
            field = ("measurements" if item.relation is BindingRelation.MEASURES
                     else "manipulated_variables")
            history = self.reader.history(source_id=self.source.source_id,
                                          signal_id=item.signal_id, event_start=start,
                                          event_end=horizon, max_points=100, snapshot=snapshot)
            self.assertEqual([r[field][item.runtime_variable_id] for r in self.delivered],
                             [sample.value for sample in history.samples], item.signal_id)
            self.assertEqual([simulation_ticks(r["simulation_time_hours"])
                              for r in self.delivered],
                             [sample.event_time.ticks for sample in history.samples])
            self.assertEqual(list(range(len(self.delivered))),
                             [sample.sequence for sample in history.samples])

    def test_reactor_temperature_and_cooling_match_runtime_bindings(self) -> None:
        snapshot = self.store.snapshot(TimePoint(self.source.event_clock.clock_id, 360))
        temperature = self.reader.current(source_id=self.source.source_id,
                                          signal_id=TEMPERATURE, snapshot=snapshot)
        cooling = self.reader.current(source_id=self.source.source_id, signal_id=COOLING,
                                      snapshot=snapshot)
        self.assertEqual(self.records[-1]["measurements"]["XMEAS(9)"], temperature.sample.value)
        self.assertEqual(self.records[-1]["manipulated_variables"]["XMV(10)"],
                         cooling.sample.value)
        self.assertEqual(360, temperature.sample.event_time.ticks)
        self.assertEqual(len(self.delivered) - 1, temperature.sample.sequence)

    def test_earlier_snapshot_never_sees_later_record(self) -> None:
        clock = self.source.event_clock.clock_id
        snapshot = self.store.snapshot(TimePoint(clock, 360), self.progressive[-2].ingest_sequence)
        current = self.reader.current(source_id=self.source.source_id, signal_id=TEMPERATURE,
                                      snapshot=snapshot)
        self.assertEqual(300, current.sample.event_time.ticks)

    def test_hidden_disturbance_never_enters_canonical_samples(self) -> None:
        self.assertTrue(any(record["active_disturbances"] for record in self.raw_records))
        with self.assertRaisesRegex(TEPSourceError, "fields mismatch"):
            self.source.translate_sanitized(self.raw_records[-1])
        snapshot = self.store.snapshot(TimePoint(self.source.event_clock.clock_id, 360))
        dump = []
        for signal_id in self.source.signal_ids:
            history = self.reader.history(
                source_id=self.source.source_id, signal_id=signal_id,
                event_start=TimePoint(self.source.event_clock.clock_id, 0),
                event_end=snapshot.event_horizon, max_points=100, snapshot=snapshot)
            dump += [sample.as_json() for sample in history.samples]
        self.assertEqual(len(self.delivered) * 53, len(dump))
        self.assertEqual([], leakage_findings(dump))
        self.assertNotIn("IDV", json.dumps(dump).upper())

    def test_translation_does_not_advance_the_world(self) -> None:
        before = self.environment.observe()
        make_source("probe").translate_sanitized(sanitize_observation(before))
        self.assertEqual(before, self.environment.observe())

    def test_same_tick_mv_intervention_is_two_observations(self) -> None:
        with TemporaryDirectory() as directory:
            environment = TEPEnvironment(EnvironmentConfig(
                seed=11, backend="python", control_mode=ControlMode.MANUAL,
                record_interval=60, upstream_revision=UPSTREAM_REVISION,
                artifact_directory=Path(directory) / "world"))
            try:
                source = make_source("manual")
                store = make_store(source)
                ingestor = TelemetryIngestor(store)
                before = sanitize_observation(environment.reset())
                first = ingestor.ingest(source.translate_sanitized(before),
                                        ingest_time=ingest_at(1))
                value = before["manipulated_variables"]["XMV(10)"]
                target = value + 1.0 if value < 99 else 1.0
                environment.apply(MVIntervention("XMV(10)", target))  # no time advance
                after = sanitize_observation(environment.observe())
                self.assertEqual(before["simulation_time_hours"], after["simulation_time_hours"])
                self.assertNotEqual(before["manipulated_variables"]["XMV(10)"],
                                    after["manipulated_variables"]["XMV(10)"])
                second = ingestor.ingest(source.translate_sanitized(after),
                                         ingest_time=ingest_at(2))
                self.assertEqual(53, second.new_count)
                self.assertEqual(first.ingest_sequence + 1, second.ingest_sequence)
                clock = source.event_clock.clock_id
                snapshot = store.snapshot(TimePoint(clock, 0))
                history = TimeSeriesReader(store, max_interval_ticks=60, max_scan_records=10
                                           ).history(source_id=source.source_id,
                                                     signal_id=COOLING,
                                                     event_start=TimePoint(clock, 0),
                                                     event_end=TimePoint(clock, 0),
                                                     max_points=10, snapshot=snapshot)
                self.assertEqual([(0, 0), (0, 1)],
                                 [(s.event_time.ticks, s.sequence) for s in history.samples])
                self.assertEqual([before["manipulated_variables"]["XMV(10)"],
                                  after["manipulated_variables"]["XMV(10)"]],
                                 [s.value for s in history.samples])
            finally:
                environment.close()

    def test_reference_world_history_unchanged_and_translatable(self) -> None:
        with TemporaryDirectory() as directory:
            environment = TEPEnvironment(EnvironmentConfig(
                seed=11, backend="python", control_mode=ControlMode.CLOSED_LOOP,
                record_interval=60, upstream_revision=UPSTREAM_REVISION,
                artifact_directory=Path(directory) / "world"))
            environment.reset()
            try:
                world = ReferenceWorld(environment)
                world.advance(0.1)
                history = world.history()
                self.assertEqual(7, len(history))
                self.assertTrue(all(set(record) == SANITIZED_FIELDS for record in history))
                source = make_source("reference-world")
                store = make_store(source)
                for index, record in enumerate(history):
                    TelemetryIngestor(store).ingest(source.translate_sanitized(record),
                                                    ingest_time=ingest_at(index))
                self.assertEqual(7, store.current_ingest_sequence)
                self.assertEqual(history, world.history())
            finally:
                environment.close()


class DependencyDirectionTests(unittest.TestCase):
    ROOT = Path(tep_telemetry.__file__).parent

    @staticmethod
    def imports(path: Path) -> set[str]:
        found = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add("." * node.level + (node.module or ""))
        return found

    def test_adapter_imports_generic_core_and_public_tep_sim_only(self) -> None:
        found = self.imports(Path(tep_telemetry.__file__))
        self.assertIn(".telemetry", found)
        self.assertIn("tep_sim", found)
        self.assertFalse({name for name in found if "evaluator" in name or name == "tep"
                          or name.startswith("tep.")})

    def test_generic_core_does_not_import_adapter_or_tep_sim(self) -> None:
        found = self.imports(Path(telemetry.__file__))
        self.assertFalse({name for name in found if "tep" in name})

    def test_adapter_never_drives_the_simulator(self) -> None:
        forbidden = {"reset", "step", "rollout", "apply", "apply_scenario", "fork", "snapshot"}
        tree = ast.parse(Path(tep_telemetry.__file__).read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(called & forbidden)

    def test_existing_production_path_does_not_use_adapter(self) -> None:
        for path in self.ROOT.glob("*.py"):
            if path.name != "tep_telemetry.py":
                self.assertNotIn("tep_telemetry", path.read_text(encoding="utf-8"), path.name)
        self.assertIs(tep_world.sanitize_observation, sanitize_observation)


if __name__ == "__main__":
    unittest.main()
