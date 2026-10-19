"""LOS-A-S1 generic Operational State evidence (LOS-A1/A2/A4/A5 subsets).

Owning spec: docs/specs/live-operational-state-v0.md. Every test uses opaque non-TEP
source/signal/unit ids over the real generic telemetry store and reader. The test
boundary below mimics only the public P0 telemetry query shapes; it is not a second
reader and has no domain vocabulary.
"""

import ast
from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path
import unittest

from tep_agent_lab import operational_state
from tep_agent_lab.operational_state import (
    ContextUnavailable, EngineeringSelection, Feature, FeatureStatus, Freshness,
    IncompatiblePolicy, InterpretationPolicy, InvalidOperationalStateRequest,
    NonfiniteDerivation, OperationalStateService, PlantContextRef, PolicyUnavailable,
    SignalDescriptor, Trend, ValueKind)
from tep_agent_lab.telemetry import (ClockDescriptor, ClockKind, ClockMismatch, InMemoryTimeSeriesStore,
                                     ResourceLimitExceeded, SampleIdentity, SnapshotUnavailable,
                                     SourceObservation, StoreRef, TelemetryReadSnapshot,
                                     TimePoint, TimeSeriesReader, UnknownBinding,
                                     parse_event_resolution, reduce_indices)

CLOCK = ClockDescriptor("clock-a", ClockKind.SIMULATION, Fraction(1), "origin-a")
INGEST = ClockDescriptor("ingest-a", ClockKind.SOURCE_DEFINED, Fraction(1))
SOURCE = "src-1"
CONTEXT = "ctx-ref-1"
NUM, FLAG, TEXT, ODD = "sig-num", "sig-flag", "sig-text", "sig-odd-unit"
DESCRIPTORS = {NUM: SignalDescriptor(ValueKind.NUMERIC, "u1"),
               FLAG: SignalDescriptor(ValueKind.BOOLEAN, None),
               TEXT: SignalDescriptor(ValueKind.STRING, None),
               ODD: SignalDescriptor(ValueKind.NUMERIC, "u-unpinned")}
POLICY = InterpretationPolicy(
    policy_id="policy-a", version="v0", default_window_ticks=10, max_window_ticks=100,
    max_input_points=50, max_gap_ticks=2, stale_after_ticks=3,
    trend_tolerance_per_second={"u1": 0.5}, max_response_bytes=262_144)
PLANT = PlantContextRef("INTERIM_GRAPH_SOURCE", CONTEXT)
EMPTY = EngineeringSelection.empty()


def snapshot_body(snapshot):
    return {**snapshot.as_json(), "snapshot_ref": snapshot.snapshot_ref}


def public_sample(sample):
    return {"source_id": sample.source_id, "signal_id": sample.signal_id,
            "sequence": sample.sequence, "event_ticks": sample.event_time.ticks,
            "value": sample.value, "quality": str(sample.quality)}


class Plant:
    """One opaque run: a real generic store plus public-shape snapshot-bound queries."""

    scope = "AGENT"

    def __init__(self, run_id="run-a", max_points_limit=100, *, source=SOURCE,
                 signals=(NUM, FLAG, TEXT, ODD), max_scan_records=500):
        self.run_id = run_id
        self.source = source
        self.store = InMemoryTimeSeriesStore(
            store_id="store-a", namespace=f"ns-{run_id}", context_ref=CONTEXT,
            visibility_policy_ref="vis-a", event_clock=CLOCK, ingest_clock=INGEST,
            bindings={source: signals}, incarnation=f"inc-{run_id}")
        self.reader = TimeSeriesReader(self.store, max_interval_ticks=1000,
                                       max_scan_records=max_scan_records,
                                       max_points_limit=max_points_limit)
        self.sequences = defaultdict(int)
        self.reads = []

    def add(self, signal, ticks, value, quality="GOOD"):
        sequence = self.sequences[signal]
        self.sequences[signal] += 1
        self.store.append(SourceObservation(
            signal_id=signal, source_id=self.source, event_time=TimePoint(CLOCK.clock_id, ticks),
            sequence=sequence, value=value, quality=quality),
            ingest_time=TimePoint(INGEST.clock_id, len(self.reads) + sequence))
        return SampleIdentity(self.source, signal, sequence)

    def series(self, signal, values, start=0):
        return [self.add(signal, start + index, value) for index, value in enumerate(values)]

    def snapshot(self, horizon, ingest_sequence=None):
        return snapshot_body(self.store.snapshot(TimePoint(CLOCK.clock_id, horizon),
                                                 ingest_sequence))

    def _resolve(self, body):
        clock = body["event_clock"]
        pinned = TelemetryReadSnapshot(
            StoreRef(**body["store"]), body["ingest_sequence"],
            TimePoint(**body["event_horizon"]),
            ClockDescriptor(clock["clock_id"], ClockKind(clock["kind"]),
                            parse_event_resolution(clock["resolution"]), clock["origin"]),
            body["context_ref"], body["visibility_policy_ref"])
        if pinned.snapshot_ref != body["snapshot_ref"]:
            raise SnapshotUnavailable("snapshot reference does not match its contents")
        return self.store.resolve(pinned)

    def _view(self, name, body):
        return {"view": name, "view_version": "test-views/v0", "scope": "AGENT",
                "run_id": self.run_id, **body}

    def telemetry_current(self, signal_id, *, snapshot):
        self.reads.append(("current", signal_id))
        pinned = self._resolve(snapshot)
        result = self.reader.current(source_id=self.source, signal_id=signal_id, snapshot=pinned)
        return self._view("CanonicalCurrentView", {
            "source": "test stream", "snapshot": snapshot_body(pinned),
            "source_id": self.source, "signal_id": signal_id, "status": str(result.status),
            "eligible_count": result.eligible_count,
            "sample": None if result.sample is None else public_sample(result.sample),
            "coverage": str(result.coverage)})

    def telemetry_history(self, signal_id, *, snapshot, event_start_ticks, event_end_ticks,
                          max_points):
        self.reads.append(("history", signal_id))
        pinned = self._resolve(snapshot)
        result = self.reader.history(
            source_id=self.source, signal_id=signal_id,
            event_start=TimePoint(CLOCK.clock_id, event_start_ticks),
            event_end=TimePoint(CLOCK.clock_id, event_end_ticks), max_points=max_points,
            snapshot=pinned)
        return self._view("CanonicalHistoryView", {
            "source": "test stream", "snapshot": snapshot_body(pinned), "source_id": self.source,
            "signal_id": signal_id, "event_start_ticks": event_start_ticks,
            "event_end_ticks": event_end_ticks, "max_points": result.max_points,
            "eligible_count": result.eligible_count, "returned_count": result.returned_count,
            "reduced": result.reduced, "reduction_policy": result.reduction_policy,
            "coverage": str(result.coverage), "status": str(result.status),
            "samples": [public_sample(sample) for sample in result.samples]})


class Boundary:
    """Opaque test composition of the public query boundary and pinned context."""

    def __init__(self, *plants):
        self.plants = {plant.run_id: plant for plant in plants}
        self.descriptors = dict(DESCRIPTORS)
        self.screened = []
        self.calls = []

    def queries(self, run_id):
        try:
            return self.plants[run_id]
        except KeyError:
            raise LookupError("unknown run") from None

    def signal_descriptor(self, queries, signal_id):
        self.calls.append("signal_descriptor")
        if signal_id not in self.descriptors:
            raise UnknownBinding("unregistered source/signal binding")
        return self.descriptors[signal_id]

    def plant_context(self, queries, ref, snapshot):
        self.calls.append("plant_context")
        if ref.kind != "INTERIM_GRAPH_SOURCE" or ref.ref != snapshot["context_ref"]:
            raise ContextUnavailable("plant context is unavailable")
        return {"kind": ref.kind, "context_ref": ref.ref, "source": {"source_id": "graph-a"}}

    def screen(self, result):
        self.screened.append(result)


class OperationalStateCase(unittest.TestCase):
    policy = POLICY

    def setUp(self):
        self.plant = Plant()
        self.boundary = Boundary(self.plant)
        self.service = OperationalStateService(self.boundary, (self.policy,))

    def read(self, signal=NUM, *, horizon=None, snapshot=None, start=None, end=None,
             policy=None, plant=PLANT, engineering=EMPTY, run_id="run-a", source_id=None):
        if snapshot is None:
            snapshot = self.plant.snapshot(horizon)
        return self.service.get_operational_state(
            run_id, signal, snapshot=snapshot, plant_context_ref=plant,
            engineering_selection=engineering,
            interpretation_policy_refs=((policy or self.policy).ref,),
            event_start_ticks=start, event_end_ticks=end, source_id=source_id)

    def feature(self, result, feature):
        return result.features[feature]

    def reasons(self, result, feature):
        return result.reason_samples(feature)

    def assertStatus(self, result, status, *features):
        for feature in features:
            with self.subTest(feature=feature):
                self.assertEqual(self.feature(result, feature).status, status)
                if status != FeatureStatus.AVAILABLE:
                    self.assertIsNone(self.feature(result, feature).value)


WINDOW_FEATURES = (Feature.RATE_OF_CHANGE, Feature.TREND, Feature.WINDOW_MIN, Feature.WINDOW_MAX)
SECANT = (Feature.RATE_OF_CHANGE, Feature.TREND)
EXTREMA = (Feature.WINDOW_MIN, Feature.WINDOW_MAX)


class FeatureCorrectnessTests(OperationalStateCase):
    """LOS-A1 subset: hand-computable sequences with exact refs."""

    def test_increasing_sequence_with_exact_sample_window_and_policy_refs(self):
        ids = self.plant.series(NUM, [0.0, 1.0, 2.0, 3.0, 4.0])
        result = self.read(horizon=4, start=0, end=4)
        rate, trend = self.feature(result, Feature.RATE_OF_CHANGE), self.feature(result, Feature.TREND)
        self.assertEqual((rate.status, rate.value), (FeatureStatus.AVAILABLE, 1.0))
        self.assertEqual(trend.value, Trend.RISING)
        self.assertEqual(rate.value_samples, (ids[0], ids[4]))
        self.assertEqual(trend.detail["tolerance_per_second"], 0.5)
        self.assertEqual(trend.detail["unit"], "u1")
        low, high = self.feature(result, Feature.WINDOW_MIN), self.feature(result, Feature.WINDOW_MAX)
        self.assertEqual((low.value, low.value_samples), (0.0, (ids[0],)))
        self.assertEqual((high.value, high.value_samples), (4.0, (ids[4],)))
        current = self.feature(result, Feature.CURRENT)
        self.assertEqual((current.value.identity, current.value.value), (ids[4], 4.0))
        stale = self.feature(result, Feature.STALENESS)
        self.assertEqual((stale.value, stale.detail["age_ticks"]), (Freshness.FRESH, 0))
        # Every window feature evaluates the complete window and cites its exact ref.
        self.assertEqual(result.window.sample_identities, tuple(ids))
        for feature in WINDOW_FEATURES:
            self.assertEqual(self.feature(result, feature).window_ref, result.window.window_ref)
        for feature in Feature:
            self.assertEqual(self.feature(result, feature).policy_ref, POLICY.ref.ref)
            self.assertIn(CONTEXT, self.feature(result, feature).citations)
        body = result.as_json()
        self.assertEqual(body["snapshot"], self.plant.snapshot(4))
        self.assertEqual(body["policy"]["checksum"], POLICY.ref.checksum)
        self.assertEqual(body["policy"]["parameters"], POLICY.parameters())
        self.assertEqual(body["window"]["selection_checksum"], result.window.selection_checksum)
        self.assertEqual(body["current_read"]["eligible_count"], 5)
        self.assertEqual(body["context"]["engineering_selection"], {"kind": "EXPLICIT_EMPTY",
                                                                   "refs": []})
        self.assertTrue(result.derivation_ref.startswith("operational-state:sha256:"))
        json.dumps(body, allow_nan=False)
        self.assertEqual(self.boundary.screened, [body])

    def test_falling_and_stable_follow_exact_tolerance_boundaries(self):
        cases = ((NUM, [10.0, 8.0, 6.0], -2.0, Trend.FALLING),
                 (NUM, [0.0, 0.5, 1.0], 0.5, Trend.STABLE),
                 (NUM, [0.0, 0.0, -1.0], -0.5, Trend.STABLE),
                 (NUM, [0.0, 0.0, 1.25], 0.625, Trend.RISING),
                 (NUM, [0.0, 0.0, -1.25], -0.625, Trend.FALLING),
                 (NUM, [5, 5, 5], 0.0, Trend.STABLE))
        for signal, values, rate, trend in cases:
            with self.subTest(values=values):
                self.setUp()
                self.plant.series(signal, values)
                result = self.read(horizon=2, start=0, end=2)
                self.assertEqual(self.feature(result, Feature.RATE_OF_CHANGE).value, rate)
                self.assertEqual(self.feature(result, Feature.TREND).value, trend)

    def test_equal_ticks_use_last_read_order_sample_and_retain_every_identity(self):
        first = self.plant.add(NUM, 0, 1.0)
        first_last = self.plant.add(NUM, 0, 5.0)
        last_first = self.plant.add(NUM, 2, 15.0)
        last = self.plant.add(NUM, 2, 25.0)
        result = self.read(horizon=2, start=0, end=2)
        rate = self.feature(result, Feature.RATE_OF_CHANGE)
        self.assertEqual(rate.value, 10.0)
        self.assertEqual(rate.value_samples, (first_last, last))
        self.assertEqual(result.window.sample_identities, (first, first_last, last_first, last))
        self.assertEqual(self.feature(result, Feature.CURRENT).value.identity, last)
        self.assertEqual(self.feature(result, Feature.WINDOW_MIN).value_samples, (first,))

    def test_one_point_and_same_tick_windows_are_insufficient_for_secant(self):
        only = self.plant.add(NUM, 3, 7.0)
        result = self.read(horizon=3, start=0, end=3)
        self.assertStatus(result, FeatureStatus.INSUFFICIENT_DATA, *SECANT)
        self.assertEqual(self.feature(result, Feature.WINDOW_MIN).value_samples, (only,))
        self.assertEqual(self.feature(result, Feature.WINDOW_MAX).value, 7.0)
        self.plant.add(NUM, 3, 9.0)
        result = self.read(horizon=3, start=0, end=3)
        self.assertStatus(result, FeatureStatus.INSUFFICIENT_DATA, *SECANT)
        self.assertEqual(result.window.diagnostics["distinct_ticks"], 1)

    def test_zero_point_window_and_empty_store(self):
        result = self.read(horizon=5, start=0, end=5)
        self.assertStatus(result, FeatureStatus.NO_DATA, Feature.CURRENT, Feature.STALENESS,
                          *WINDOW_FEATURES)
        self.assertIsNone(result.as_json()["current"])
        self.assertEqual(result.window.sample_identities, ())
        self.plant.add(NUM, 1, 3.0)
        result = self.read(horizon=9, start=5, end=9)
        self.assertStatus(result, FeatureStatus.NO_DATA, *WINDOW_FEATURES)
        self.assertEqual(self.feature(result, Feature.CURRENT).status, FeatureStatus.AVAILABLE)

    def test_ties_cite_the_first_sample_in_read_order(self):
        ids = self.plant.series(NUM, [3.0, 1.0, 1.0, 3.0, 2.0])
        result = self.read(horizon=4, start=0, end=4)
        self.assertEqual(self.feature(result, Feature.WINDOW_MIN).value_samples, (ids[1],))
        self.assertEqual(self.feature(result, Feature.WINDOW_MAX).value_samples, (ids[0],))

    def test_gap_above_policy_blocks_secant_but_not_exact_extrema(self):
        ticks = (0, 1, 5, 6)
        ids = [self.plant.add(NUM, tick, float(tick)) for tick in ticks]
        result = self.read(horizon=6, start=0, end=6)
        self.assertStatus(result, FeatureStatus.GAP_EXCEEDS_POLICY, *SECANT)
        self.assertEqual(self.reasons(result, Feature.RATE_OF_CHANGE), (ids[1], ids[2]))
        self.assertEqual(self.feature(result, Feature.TREND).detail["gaps"],
                         ({"kind": "INTERIOR", "from_event_ticks": 1, "to_event_ticks": 5,
                           "span_ticks": 4},))
        self.assertEqual(self.feature(result, Feature.WINDOW_MAX).value, 6.0)
        diagnostics = result.window.diagnostics
        self.assertEqual(diagnostics["max_adjacent_gap_ticks"], 4)
        self.assertEqual(diagnostics["max_gap_ticks"], 2)
        # A gap exactly at the declared bound is allowed.
        self.setUp()
        for tick in (0, 2, 4):
            self.plant.add(NUM, tick, float(tick))
        self.assertStatus(self.read(horizon=4, start=0, end=4), FeatureStatus.AVAILABLE, *SECANT)

    def test_leading_and_trailing_unobserved_ticks_are_disclosed(self):
        for tick in (3, 4, 5):
            self.plant.add(NUM, tick, 1.0)
        diagnostics = self.read(horizon=8, start=1, end=8).window.diagnostics
        self.assertEqual((diagnostics["observed_first_ticks"], diagnostics["observed_last_ticks"]),
                         (3, 5))
        self.assertEqual((diagnostics["leading_unobserved_ticks"],
                          diagnostics["trailing_unobserved_ticks"]), (2, 3))
        self.assertEqual(diagnostics["quality_counts"], {"GOOD": 3, "UNCERTAIN": 0, "BAD": 0})

    def test_leading_and_trailing_silence_gate_rate_and_trend(self):
        # max_gap_ticks is 2. The span from the window start to the first observed tick
        # and from the last observed tick to the window end are checked like gaps.
        cases = (
            # trailing silence: the signal stops at tick 5, window [0, 8]
            ((0, 1, 2, 3, 4, 5), (0, 8), "TRAILING", (5, 8)),
            # leading silence: the signal starts at tick 3, window [0, 8]
            ((3, 4, 5, 6, 7, 8), (0, 8), "LEADING", (0, 3)),
            # one tick over the bound at each end
            ((2, 3, 4, 5, 6), (0, 9), "TRAILING", (6, 9)),
            ((3, 4, 5, 6), (0, 8), "LEADING", (0, 3)),
        )
        for ticks, (start, end), kind, span in cases:
            with self.subTest(ticks=ticks, window=(start, end)):
                self.setUp()
                ids = [self.plant.add(NUM, tick, 1.0) for tick in ticks]
                result = self.read(horizon=end, start=start, end=end)
                self.assertStatus(result, FeatureStatus.GAP_EXCEEDS_POLICY, *SECANT)
                self.assertStatus(result, FeatureStatus.AVAILABLE, *EXTREMA)
                nearest = ids[-1] if kind == "TRAILING" else ids[0]
                self.assertEqual(self.reasons(result, Feature.TREND), (nearest,))
                self.assertEqual(self.feature(result, Feature.RATE_OF_CHANGE).detail["gaps"],
                                 ({"kind": kind, "from_event_ticks": span[0],
                                   "to_event_ticks": span[1], "span_ticks": span[1] - span[0]},))
        # Both ends exactly at the bound pass.
        self.setUp()
        for tick in (2, 3, 4, 5, 6):
            self.plant.add(NUM, tick, 1.0)
        result = self.read(horizon=8, start=0, end=8)
        self.assertStatus(result, FeatureStatus.AVAILABLE, *SECANT)
        self.assertEqual((result.window.diagnostics["leading_unobserved_ticks"],
                          result.window.diagnostics["trailing_unobserved_ticks"]), (2, 2))

    def test_status_precedence(self):
        # Declared kind applicability precedes no data; threshold too.
        result = self.read(FLAG, horizon=3, start=0, end=3)
        self.assertStatus(result, FeatureStatus.UNSUPPORTED_VALUE_KIND, *WINDOW_FEATURES,
                          Feature.THRESHOLD)
        self.assertEqual(self.feature(result, Feature.CURRENT).status, FeatureStatus.NO_DATA)
        # Canonical-limit availability precedes no data for a numeric threshold.
        self.assertStatus(self.read(horizon=3, start=0, end=3), FeatureStatus.NO_CANONICAL_LIMIT,
                          Feature.THRESHOLD)
        # Unusable quality/null precedes the observed value kind (spec order).
        bad = self.plant.add(NUM, 0, 1.0, quality="BAD")
        self.plant.add(NUM, 1, "not-numeric")
        result = self.read(horizon=1, start=0, end=1)
        self.assertStatus(result, FeatureStatus.QUALITY_UNUSABLE, *WINDOW_FEATURES)
        self.assertEqual(self.reasons(result, Feature.TREND), (bad,))
        # A GOOD, non-null, non-numeric sample in a numeric window is an unsupported kind.
        self.setUp()
        self.plant.add(NUM, 0, 1.0)
        odd = self.plant.add(NUM, 1, "not-numeric")
        result = self.read(horizon=1, start=0, end=1)
        self.assertStatus(result, FeatureStatus.UNSUPPORTED_VALUE_KIND, *WINDOW_FEATURES)
        self.assertEqual(self.reasons(result, Feature.WINDOW_MIN), (odd,))
        # Quality precedes insufficiency and gaps.
        self.setUp()
        bad = self.plant.add(NUM, 0, 1.0, quality="BAD")
        result = self.read(horizon=0, start=0, end=0)
        self.assertStatus(result, FeatureStatus.QUALITY_UNUSABLE, *WINDOW_FEATURES)
        self.assertEqual(self.reasons(result, Feature.TREND), (bad,))
        self.plant.add(NUM, 9, 2.0)
        self.assertStatus(self.read(horizon=9, start=0, end=9), FeatureStatus.QUALITY_UNUSABLE,
                          *WINDOW_FEATURES)
        # Booleans are not numeric values for rate/extrema, even under a numeric signal.
        self.setUp()
        self.plant.add(NUM, 0, True)
        self.plant.add(NUM, 1, False)
        self.assertStatus(self.read(horizon=1, start=0, end=1),
                          FeatureStatus.UNSUPPORTED_VALUE_KIND, *WINDOW_FEATURES)
        # A string signal still has an exact current value and freshness.
        self.plant.add(TEXT, 1, "running")
        result = self.read(TEXT, horizon=1, start=0, end=1)
        self.assertEqual(self.feature(result, Feature.CURRENT).value.value, "running")
        self.assertEqual(self.feature(result, Feature.STALENESS).value, Freshness.FRESH)

    def test_nonfinite_derived_arithmetic_fails_closed(self):
        self.plant.add(NUM, 0, -1.5e308)
        self.plant.add(NUM, 1, 1.5e308)
        with self.assertRaises(NonfiniteDerivation) as caught:
            self.read(horizon=1, start=0, end=1)
        self.assertNotIn("e+308", str(caught.exception))
        self.assertNotIn(NUM, str(caught.exception))

    def test_window_ending_before_horizon_discloses_its_own_endpoint(self):
        ids = self.plant.series(NUM, [0.0, 2.0, 4.0, 6.0, 8.0])
        late = self.plant.add(NUM, 10, 100.0)
        result = self.read(horizon=10, start=0, end=4)
        body = result.as_json()
        self.assertEqual(body["as_of_event_ticks"], 10)
        self.assertEqual((body["window"]["event_start_ticks"], body["window"]["event_end_ticks"]),
                         (0, 4))
        self.assertTrue(body["window"]["ends_before_horizon"])
        rate = self.feature(result, Feature.RATE_OF_CHANGE)
        self.assertEqual((rate.value, rate.detail["first_event_ticks"],
                          rate.detail["last_event_ticks"]), (2.0, 0, 4))
        self.assertEqual(rate.basis, "WINDOW")
        self.assertEqual(self.feature(result, Feature.WINDOW_MAX).value_samples, (ids[4],))
        current = self.feature(result, Feature.CURRENT)
        self.assertEqual((current.basis, current.value.identity), ("AS_OF_HORIZON", late))
        self.assertNotIn(late, result.window.sample_identities)

    def test_default_window_is_resolved_once_from_policy_and_disclosed(self):
        self.plant.series(NUM, [float(i) for i in range(25)])
        body = self.read(horizon=24).as_json()
        window = body["window"]
        self.assertEqual((window["event_start_ticks"], window["event_end_ticks"]), (14, 24))
        self.assertEqual((window["start_defaulted"], window["end_defaulted"]), (True, True))
        self.assertEqual((body["request"]["event_start_ticks"],
                          body["request"]["event_end_ticks"]), (None, None))
        early = self.read(horizon=4).as_json()["window"]
        self.assertEqual((early["event_start_ticks"], early["event_end_ticks"]), (0, 4))


class QualityAndFreshnessTests(OperationalStateCase):
    """LOS-A2."""

    def test_bad_uncertain_and_null_current_stay_current(self):
        for quality, value in (("BAD", 7.0), ("UNCERTAIN", 7.0), ("GOOD", None)):
            with self.subTest(quality=quality, value=value):
                self.setUp()
                self.plant.series(NUM, [1.0, 2.0])
                unusable = self.plant.add(NUM, 2, value, quality=quality)
                result = self.read(horizon=2, start=0, end=2)
                current = self.feature(result, Feature.CURRENT)
                self.assertEqual((current.status, current.value.identity), (FeatureStatus.AVAILABLE,
                                                                            unusable))
                self.assertEqual((current.value.quality, current.value.value), (quality, value))
                self.assertEqual(self.feature(result, Feature.STALENESS).value, Freshness.FRESH)
                self.assertStatus(result, FeatureStatus.QUALITY_UNUSABLE, *WINDOW_FEATURES)
                self.assertEqual(self.feature(result, Feature.THRESHOLD).status,
                                 FeatureStatus.NO_CANONICAL_LIMIT)

    def test_unusable_history_yields_no_trend(self):
        self.plant.add(NUM, 0, 0.0)
        uncertain = self.plant.add(NUM, 1, 50.0, quality="UNCERTAIN")
        self.plant.add(NUM, 2, 100.0)
        result = self.read(horizon=2, start=0, end=2)
        trend = self.feature(result, Feature.TREND)
        self.assertEqual((trend.status, trend.value, self.reasons(result, Feature.TREND)),
                         (FeatureStatus.QUALITY_UNUSABLE, None, (uncertain,)))
        self.assertEqual(result.window.diagnostics["quality_counts"],
                         {"GOOD": 2, "UNCERTAIN": 1, "BAD": 0})

    def test_staleness_flips_exactly_at_the_pinned_boundary(self):
        self.plant.add(NUM, 5, 1.0, quality="BAD")
        fresh = self.feature(self.read(horizon=8, start=0, end=8), Feature.STALENESS)
        stale = self.feature(self.read(horizon=9, start=0, end=9), Feature.STALENESS)
        self.assertEqual((fresh.value, fresh.detail["age_ticks"]), (Freshness.FRESH, 3))
        self.assertEqual((stale.value, stale.detail["age_ticks"]), (Freshness.STALE, 4))
        self.assertEqual(stale.detail["stale_after_ticks"], 3)

    def test_reduced_history_with_an_omitted_spike_fails(self):
        policy = InterpretationPolicy(
            policy_id="policy-small", version="v0", default_window_ticks=10, max_window_ticks=100,
            max_input_points=5, max_gap_ticks=2, stale_after_ticks=3,
            trend_tolerance_per_second={"u1": 0.5}, max_response_bytes=262_144)
        self.service = OperationalStateService(self.boundary, (policy,))
        values = [1.0] * 11
        values[3] = 1000.0
        self.plant.series(NUM, values)
        self.assertNotIn(3, reduce_indices(11, 5))
        with self.assertRaises(ResourceLimitExceeded):
            self.read(horizon=10, start=0, end=10, policy=policy)


class TimeConsistencyTests(OperationalStateCase):
    """LOS-A4."""

    def test_late_arrival_after_k_and_sample_after_t_change_nothing(self):
        self.plant.series(NUM, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        snapshot = self.plant.snapshot(5)
        before = self.read(snapshot=snapshot, start=0, end=5)
        self.plant.add(NUM, 2, 1e6)           # late arrival after K, earlier event tick
        self.plant.add(NUM, 9, -1e6)          # after T
        after = self.read(snapshot=snapshot, start=0, end=5)
        self.assertEqual(after.as_json(), before.as_json())
        self.assertEqual(after.derivation_ref, before.derivation_ref)
        newer = self.read(horizon=9, start=0, end=9)
        self.assertNotEqual(newer.derivation_ref, before.derivation_ref)
        self.assertEqual(newer.as_json()["current_read"]["eligible_count"], 8)

    def test_repeated_reads_at_one_snapshot_are_identical(self):
        self.plant.series(NUM, [3.0, 1.0, 4.0, 1.0, 5.0])
        snapshot = self.plant.snapshot(4)
        first, second = (self.read(snapshot=snapshot, start=0, end=4) for _ in range(2))
        self.assertEqual(first.as_json(), second.as_json())
        self.assertEqual(first.derivation_ref, second.derivation_ref)

    def test_changed_policy_is_a_distinct_derivation(self):
        other = InterpretationPolicy(
            policy_id="policy-a", version="v1", default_window_ticks=10, max_window_ticks=100,
            max_input_points=50, max_gap_ticks=2, stale_after_ticks=3,
            trend_tolerance_per_second={"u1": 5.0}, max_response_bytes=262_144)
        self.service = OperationalStateService(self.boundary, (POLICY, other))
        self.plant.series(NUM, [0.0, 1.0, 2.0])
        snapshot = self.plant.snapshot(2)
        first = self.read(snapshot=snapshot, start=0, end=2)
        second = self.read(snapshot=snapshot, start=0, end=2, policy=other)
        self.assertEqual(self.feature(first, Feature.TREND).value, Trend.RISING)
        self.assertEqual(self.feature(second, Feature.TREND).value, Trend.STABLE)
        self.assertNotEqual(first.derivation_ref, second.derivation_ref)
        self.assertNotEqual(POLICY.ref, other.ref)


class SelectionBoundsAndFailureTests(OperationalStateCase):
    """LOS-A5 subset: typed, redacted failures with opaque ids."""

    def assertRedacted(self, error, *secrets):
        text = str(error)
        for secret in ("sig-", "run-", "src-", "ctx-", "1e+", *secrets):
            self.assertNotIn(secret, text)

    def test_unknown_source_and_signal(self):
        self.plant.series(NUM, [1.0, 2.0])
        with self.assertRaises(UnknownBinding) as caught:
            self.read("sig-missing", horizon=1)
        self.assertRedacted(caught.exception)
        with self.assertRaises(UnknownBinding) as caught:
            self.read(horizon=1, source_id="src-missing")
        self.assertRedacted(caught.exception)
        self.assertEqual(self.plant.reads, [("current", NUM)])

    def test_wrong_run_incarnation_clock_context_and_future_k(self):
        self.plant.series(NUM, [1.0, 2.0])
        other = Plant("run-b")
        other.series(NUM, [1.0, 2.0])
        self.boundary.plants["run-b"] = other
        snapshot = self.plant.snapshot(1)

        def altered(**changes):
            body = json.loads(json.dumps(snapshot))
            for path, value in changes.items():
                target = body
                *parents, leaf = path.split(".")
                for name in parents:
                    target = target[name]
                target[leaf] = value
            stripped = {key: value for key, value in body.items() if key != "snapshot_ref"}
            clock = stripped["event_clock"]
            rebuilt = TelemetryReadSnapshot(
                StoreRef(**stripped["store"]), stripped["ingest_sequence"],
                TimePoint(**stripped["event_horizon"]),
                ClockDescriptor(clock["clock_id"], ClockKind(clock["kind"]),
                                parse_event_resolution(clock["resolution"]), clock["origin"]),
                stripped["context_ref"], stripped["visibility_policy_ref"])
            return {**stripped, "snapshot_ref": rebuilt.snapshot_ref}

        cases = (
            (SnapshotUnavailable, dict(snapshot=snapshot, run_id="run-b")),
            (SnapshotUnavailable, dict(snapshot=altered(**{"store.incarnation": "inc-other"}))),
            (ClockMismatch, dict(snapshot=altered(**{"event_clock.clock_id": "clock-b",
                                                    "event_horizon.clock_id": "clock-b"}))),
            (SnapshotUnavailable, dict(snapshot=altered(context_ref="ctx-other"),
                                       plant=PlantContextRef("INTERIM_GRAPH_SOURCE", "ctx-other"))),
            (ContextUnavailable, dict(snapshot=snapshot,
                                      plant=PlantContextRef("INTERIM_GRAPH_SOURCE", "ctx-other"))),
            (ContextUnavailable, dict(snapshot=snapshot,
                                      plant=PlantContextRef("PLANT_REVISION", CONTEXT))),
            (SnapshotUnavailable, dict(snapshot=altered(ingest_sequence=99))),
            (SnapshotUnavailable, dict(snapshot={**snapshot, "snapshot_ref": "telemetry-snapshot:x"})),
        )
        for error, options in cases:
            with self.subTest(error=error.__name__, options=sorted(options)):
                with self.assertRaises(error) as caught:
                    self.read(**options)
                self.assertRedacted(caught.exception, "inc-other", "clock-b")
        with self.assertRaises(LookupError) as caught:
            self.read(snapshot=snapshot, run_id="run-missing")
        self.assertRedacted(caught.exception)

    def test_bounds_and_request_shape_are_validated_before_any_read(self):
        self.plant.series(NUM, [1.0] * 5)
        snapshot = self.plant.snapshot(4)
        wrong = InterpretationPolicy(
            policy_id="policy-a", version="v0", default_window_ticks=10, max_window_ticks=100,
            max_input_points=50, max_gap_ticks=2, stale_after_ticks=4,
            trend_tolerance_per_second={"u1": 0.5}, max_response_bytes=262_144)
        cases = (
            (ResourceLimitExceeded, dict(start=0, end=200, snapshot=self.plant.snapshot(200))),
            (InvalidOperationalStateRequest, dict(start=0, end=5)),
            (InvalidOperationalStateRequest, dict(start=3, end=2)),
            (InvalidOperationalStateRequest, dict(start=-1, end=2)),
            (InvalidOperationalStateRequest, dict(start=True, end=2)),
            (InvalidOperationalStateRequest, dict(start=0.0, end=2)),
            (InvalidOperationalStateRequest, dict(snapshot="latest")),
            (InvalidOperationalStateRequest, dict(snapshot={"event_horizon": {"ticks": 4}})),
            (InvalidOperationalStateRequest, dict(engineering=None)),
            (InvalidOperationalStateRequest, dict(plant=None)),
            (ContextUnavailable, dict(engineering=EngineeringSelection(("rule-set-a@v1",)))),
            (PolicyUnavailable, dict(policy=wrong)),
            (IncompatiblePolicy, dict(signal=ODD)),
        )
        for error, options in cases:
            options.setdefault("snapshot", snapshot)
            with self.subTest(error=error.__name__, options=sorted(options)):
                self.plant.reads.clear()
                with self.assertRaises(error) as caught:
                    self.read(**options)
                self.assertRedacted(caught.exception)
                self.assertEqual(self.plant.reads, [])
        for refs in ((), (POLICY.ref, POLICY.ref), ("policy-a@v0",)):
            with self.subTest(refs=refs), self.assertRaises(InvalidOperationalStateRequest):
                self.service.get_operational_state(
                    "run-a", NUM, snapshot=snapshot, plant_context_ref=PLANT,
                    engineering_selection=EMPTY, interpretation_policy_refs=refs)
        self.assertEqual(self.plant.reads, [])

    def test_reader_bounds_still_apply_and_never_truncate(self):
        policy = InterpretationPolicy(
            policy_id="policy-wide", version="v0", default_window_ticks=10, max_window_ticks=100,
            max_input_points=500, max_gap_ticks=2, stale_after_ticks=3,
            trend_tolerance_per_second={"u1": 0.5}, max_response_bytes=1_048_576)
        self.service = OperationalStateService(self.boundary, (policy,))
        self.plant.series(NUM, [1.0, 2.0])
        with self.assertRaises(ResourceLimitExceeded):
            self.read(horizon=1, start=0, end=1, policy=policy)

    def test_response_bound_fails_instead_of_truncating(self):
        policy = InterpretationPolicy(
            policy_id="policy-tight", version="v0", default_window_ticks=10, max_window_ticks=100,
            max_input_points=50, max_gap_ticks=2, stale_after_ticks=3,
            trend_tolerance_per_second={"u1": 0.5}, max_response_bytes=4096)
        self.service = OperationalStateService(self.boundary, (policy,))
        self.plant.series(NUM, [float(i) for i in range(40)])
        with self.assertRaises(ResourceLimitExceeded):
            self.read(horizon=39, start=0, end=39, policy=policy)
        self.assertEqual(self.boundary.screened, [])
        # The bound applies to the exact canonical result, derivation ref included.
        def exact_policy(limit):
            return InterpretationPolicy(
                policy_id="policy-exact", version="v0", default_window_ticks=10,
                max_window_ticks=100, max_input_points=50, max_gap_ticks=2,
                stale_after_ticks=3, trend_tolerance_per_second={"u1": 0.5},
                max_response_bytes=limit)

        probe = exact_policy(99_999)   # same digit count as the measured size below
        self.service = OperationalStateService(self.boundary, (probe,))
        size = len(json.dumps(self.read(horizon=39, start=0, end=39, policy=probe).as_json(),
                              sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False).encode())
        self.assertTrue(10_001 <= size <= 99_999)
        for limit, fits in ((size, True), (size - 1, False)):
            exact = exact_policy(limit)
            self.service = OperationalStateService(self.boundary, (exact,))
            with self.subTest(limit=limit):
                if fits:
                    self.read(horizon=39, start=0, end=39, policy=exact)
                else:
                    with self.assertRaises(ResourceLimitExceeded):
                        self.read(horizon=39, start=0, end=39, policy=exact)

    def test_malformed_or_out_of_order_public_reads_are_server_faults(self):
        self.plant.series(NUM, [1.0, 2.0, 3.0])
        snapshot = self.plant.snapshot(2)
        history = self.plant.telemetry_history

        def tampered(change):
            def read(*args, **kwargs):
                view = history(*args, **kwargs)
                change(view)
                return view
            return read

        changes = (lambda view: view["samples"].reverse(),
                   # unreduced answers whose counts disagree are server faults
                   lambda view: view["samples"].pop(),
                   lambda view: view.update(eligible_count=view["eligible_count"] + 1),
                   lambda view: view["samples"][0].update(event_ticks=9),
                   lambda view: view["samples"][0].update(value=float("nan")),
                   lambda view: view["samples"][0].update(quality="MAYBE"),
                   lambda view: view["samples"][0].pop("value"),
                   lambda view: view.pop("coverage"),
                   lambda view: view.update(signal_id=TEXT))
        for change in changes:
            with self.subTest(change=change):
                self.plant.telemetry_history = tampered(change)
                with self.assertRaises(operational_state.InconsistentRead):
                    self.read(snapshot=snapshot, start=0, end=2)
        self.plant.telemetry_history = history
        self.read(snapshot=snapshot, start=0, end=2)

    def test_results_and_request_refs_are_immutable(self):
        self.plant.series(NUM, [1.0, 2.0])
        result = self.read(horizon=1, start=0, end=1)
        feature = result.features[Feature.TREND]
        for target, key in ((result.features, Feature.TREND), (result.body, "run_id"),
                            (feature.detail, "unit"), (result.window.body, "event_end_ticks"),
                            (result.body["snapshot"], "context_ref")):
            with self.subTest(key=key), self.assertRaises(TypeError):
                target[key] = "changed"
        with self.assertRaises(AttributeError):
            feature.value = Trend.FALLING
        selection = EngineeringSelection(["rule-set-a@v1"])
        self.assertEqual(selection.refs, ("rule-set-a@v1",))
        for bad in (lambda: EngineeringSelection("rule-set-a"),
                    lambda: EngineeringSelection((1,)),
                    lambda: PlantContextRef("INTERIM_GRAPH_SOURCE", 7)):
            with self.assertRaises(InvalidOperationalStateRequest):
                bad()

    def test_policy_record_is_validated_versioned_and_checksummed(self):
        base = dict(policy_id="policy-a", version="v0", default_window_ticks=10,
                    max_window_ticks=100, max_input_points=50, max_gap_ticks=2,
                    stale_after_ticks=3, trend_tolerance_per_second={"u1": 0.5},
                    max_response_bytes=262_144)
        for change in ({"trend_tolerance_per_second": {"u1": -0.1}},
                       {"trend_tolerance_per_second": {"u1": float("nan")}},
                       {"trend_tolerance_per_second": {"u1": True}},
                       {"trend_tolerance_per_second": {}},
                       {"trend_tolerance_per_second": {" u1": 0.5}},
                       {"default_window_ticks": 101}, {"max_window_ticks": -1},
                       {"max_input_points": 0}, {"max_gap_ticks": True},
                       {"stale_after_ticks": -1}, {"version": ""}, {"max_response_bytes": 0}):
            with self.subTest(change=change), self.assertRaises(IncompatiblePolicy):
                InterpretationPolicy(**{**base, **change})
        same = InterpretationPolicy(**base)
        self.assertEqual(same.ref, POLICY.ref)
        self.assertTrue(POLICY.ref.ref.startswith("interpretation-policy:policy-a@v0:sha256:"))
        changed = InterpretationPolicy(**{**base, "trend_tolerance_per_second": {"u1": 0.25}})
        self.assertNotEqual(changed.ref.checksum, POLICY.ref.checksum)
        with self.assertRaises(IncompatiblePolicy):
            OperationalStateService(self.boundary, (POLICY, same))
        tolerance = POLICY.parameters()["trend_tolerance_per_second"]
        self.assertEqual(tolerance, {"u1": 0.5})
        self.assertEqual(Fraction(POLICY.tolerance("u1")), Fraction(1, 2))
        # Hashable by content: equal policies hash equal and work as dict keys.
        self.assertEqual(same, POLICY)
        self.assertEqual(hash(same), hash(POLICY))
        self.assertEqual({POLICY: "a"}[same], "a")
        self.assertEqual(len({POLICY, same, changed}), 2)

    def test_maximum_unusable_window_is_a_typed_status_within_the_response_bound(self):
        source, signal = "src-" + "x" * 252, "sig-" + "y" * 252
        plant = Plant("run-wide", 1000, source=source, signals=(signal,), max_scan_records=5000)
        self.boundary.plants["run-wide"] = plant
        self.boundary.descriptors[signal] = SignalDescriptor(ValueKind.NUMERIC, "u1")
        policy = InterpretationPolicy(
            policy_id="policy-max", version="v0", default_window_ticks=120,
            max_window_ticks=999, max_input_points=1000, max_gap_ticks=1, stale_after_ticks=1,
            trend_tolerance_per_second={"u1": 0.5}, max_response_bytes=1_048_576)
        self.service = OperationalStateService(self.boundary, (policy,))
        self.plant = plant
        # 999 ticks, 1,000 samples: alternating BAD numeric and GOOD null, 256-char ids.
        ids = tuple(plant.add(signal, tick, None if tick % 2 else 1.0,
                              quality="GOOD" if tick % 2 else "BAD") for tick in range(1000))
        result = self.read(signal, horizon=999, start=0, end=999, policy=policy,
                           run_id="run-wide")
        self.assertStatus(result, FeatureStatus.QUALITY_UNUSABLE, *WINDOW_FEATURES)
        for feature in WINDOW_FEATURES:
            self.assertEqual(self.reasons(result, feature), ids)
        body = result.as_json()
        size = len(json.dumps(body, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False).encode())
        self.assertLessEqual(size, policy.max_response_bytes)
        # Full identities are emitted once, in the window; features cite positions into it.
        self.assertEqual(body["features"]["trend"]["reason_sample_positions"], list(range(1000)))
        self.assertNotIn("reason_samples", body["features"]["trend"])
        emitted = set()

        def collect(value):
            if isinstance(value, dict):
                if {"source_id", "signal_id", "sequence"} <= set(value):
                    emitted.add((value["source_id"], value["signal_id"], value["sequence"]))
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(body)
        self.assertLessEqual(len(emitted), policy.parameters()["max_inline_sample_refs"])


class GenericBoundaryTests(unittest.TestCase):
    def test_generic_contract_and_tests_have_no_domain_imports(self):
        allowed = {"__future__", "collections.abc", "dataclasses", "enum", "fractions",
                   "hashlib", "json", "math", "types", "typing", "telemetry"}
        for path in (Path(operational_state.__file__), Path(__file__)):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if path == Path(__file__):
                        self.assertNotRegex(module, r"tep_sim|tep_telemetry|tep_world|playground"
                                                    r"|run_telemetry|tep_operational_state")
                    else:
                        self.assertIn(module, allowed)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("tep", alias.name)
            text = path.read_text(encoding="utf-8")
            if path != Path(__file__):
                for word in ("XMEAS", "XMV", "IDV", "ControlMode", "tep_sim"):
                    self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
