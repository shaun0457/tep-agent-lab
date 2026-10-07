"""P1.1A acceptance tests for the generic telemetry core (plant-telemetry-contract-v0)."""

import ast
from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path
import re
import unittest

from tep_agent_lab import telemetry
from tep_agent_lab.telemetry import (
    ClockDescriptor, ClockKind, ClockMismatch, IdentityConflict,
    InMemoryTimeSeriesStore, Quality, ReadStatus, ResourceLimitExceeded,
    SampleIdentity, SignalSample, SnapshotUnavailable, SourceObservation,
    TelemetryError, TelemetryIngestor, TelemetryReadSnapshot, TimePoint,
    TimeSeriesReader, UnknownBinding, reduce_indices,
)

# One tick = one simulated second; minutes are expressed as tick multiples.
SIM = ClockDescriptor("sim-run-1", ClockKind.SIMULATION, Fraction(1), "model-t0")
INGEST = ClockDescriptor("ingest-utc", ClockKind.UTC, Fraction(1, 1000),
                         "1970-01-01T00:00:00Z/utc-smeared")
MINUTE = 60


def at(minutes: float) -> TimePoint:
    return TimePoint(SIM.clock_id, int(minutes * MINUTE))


def ingest_at(ms: int) -> TimePoint:
    return TimePoint(INGEST.clock_id, ms)


def obs(sequence: int, minutes: float, value=1.0, quality=Quality.GOOD,
        signal_id: str = "sig-a", source_id: str = "src-1", **extra) -> SourceObservation:
    return SourceObservation(signal_id=signal_id, source_id=source_id,
                             event_time=at(minutes), sequence=sequence, value=value,
                             quality=quality, **extra)


def make_store(namespace: str = "reference", incarnation: str = "inc-1",
               **changes) -> InMemoryTimeSeriesStore:
    options = dict(store_id="store-1", namespace=namespace, context_ref="context:rev-1",
                   event_clock=SIM, ingest_clock=INGEST,
                   bindings={"src-1": ("sig-a", "sig-b"), "src-2": ("sig-a",)},
                   incarnation=incarnation)
    options.update(changes)
    return InMemoryTimeSeriesStore(**options)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.store = make_store()
        self.ingestor = TelemetryIngestor(self.store)
        self.reader = TimeSeriesReader(self.store, max_interval_ticks=24 * 60 * MINUTE,
                                       max_points_limit=500)
        self._clock = 0

    def ingest(self, *observations: SourceObservation):
        self._clock += 1000
        return self.ingestor.ingest(observations, ingest_time=ingest_at(self._clock))

    def history(self, snapshot, start=0, end=None, max_points=100, **binding):
        binding = {"source_id": "src-1", "signal_id": "sig-a", **binding}
        return self.reader.history(event_start=at(start),
                                   event_end=end or snapshot.event_horizon,
                                   max_points=max_points, snapshot=snapshot, **binding)


class RecordValidationTests(Fixture):
    def test_typed_record_validation(self) -> None:
        for bad in ({"sequence": -1}, {"sequence": True}, {"sequence": 1.0},
                    {"signal_id": ""}, {"source_id": " src"}, {"quality": "PERFECT"},
                    {"value": [1.0]}, {"event_time": 600}):
            with self.subTest(bad=bad), self.assertRaises(TelemetryError):
                SourceObservation(**{"signal_id": "sig-a", "source_id": "src-1",
                                     "event_time": at(1), "sequence": 0, "value": 1.0,
                                     "quality": Quality.GOOD, **bad})
        with self.assertRaises(TelemetryError):
            TimePoint(SIM.clock_id, 1.5)
        with self.assertRaises(TelemetryError):
            TimePoint(SIM.clock_id, True)
        for value in (None, True, 3, 2.5, "OPEN"):
            self.assertEqual(obs(0, 1, value=value).value, value)
        sample = self.ingest(obs(0, 1)).records[0].sample
        with self.assertRaises(FrozenInstanceError):
            sample.value = 2.0  # type: ignore[misc]

    def test_nonfinite_values_rejected(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(TelemetryError):
                obs(0, 1, value=value)

    def test_clock_descriptor_is_exact_and_explicit(self) -> None:
        with self.assertRaises(TelemetryError):
            ClockDescriptor("c", ClockKind.SIMULATION, 0.1, "t0")  # float resolution
        with self.assertRaises(TelemetryError):
            ClockDescriptor("c", ClockKind.SIMULATION, Fraction(0), "t0")
        with self.assertRaises(TelemetryError):
            ClockDescriptor("c", ClockKind.UTC, Fraction(1))  # UTC needs an epoch
        self.assertIsNone(ClockDescriptor("c", ClockKind.SOURCE_DEFINED, 1).origin)
        with self.assertRaises(TelemetryError):
            ClockDescriptor("c", "WALL", 1, "t0")
        with self.assertRaises(TelemetryError):
            ClockDescriptor("c", ClockKind.SIMULATION, True, "t0")

    def test_store_registration_fails_closed(self) -> None:
        for bindings in ({}, {"src-1": ()}, {"src-1": "sig-a"}, {"": ("sig-a",)}):
            with self.subTest(bindings=bindings), self.assertRaises(TelemetryError):
                make_store(bindings=bindings)
        with self.assertRaises(TelemetryError):
            make_store(event_clock="sim-run-1")

    def test_value_subclasses_rejected(self) -> None:
        for value in (Quality.GOOD, ClockKind.UTC):  # str subclasses
            with self.subTest(value=value), self.assertRaises(TelemetryError):
                obs(0, 1, value=value)

    def test_clock_mismatch_rejected(self) -> None:
        utc_event = SourceObservation(signal_id="sig-a", source_id="src-1",
                                      event_time=TimePoint(INGEST.clock_id, 600),
                                      sequence=0, value=1.0, quality=Quality.GOOD)
        with self.assertRaises(ClockMismatch):
            self.ingestor.ingest((utc_event,), ingest_time=ingest_at(1))
        with self.assertRaises(ClockMismatch):  # simulation seconds are not ingest UTC
            self.ingestor.ingest((obs(0, 1),), ingest_time=at(1))
        with self.assertRaises(ClockMismatch):
            self.store.snapshot(TimePoint(INGEST.clock_id, 600))
        self.ingest(obs(0, 1))
        snapshot = self.store.snapshot(at(10))
        with self.assertRaises(ClockMismatch):
            self.reader.history(source_id="src-1", signal_id="sig-a",
                                event_start=TimePoint(INGEST.clock_id, 0), event_end=at(10),
                                max_points=10, snapshot=snapshot)
        self.assertEqual(self.store.current_ingest_sequence, 1)

    def test_source_cannot_supply_ingest_time(self) -> None:
        self.assertNotIn("ingest_time", SourceObservation.__dataclass_fields__)
        backdated = SignalSample(signal_id="sig-a", source_id="src-1", event_time=at(1),
                                 sequence=0, value=1.0, quality=Quality.GOOD,
                                 ingest_time=ingest_at(-5))
        with self.assertRaises(TelemetryError):
            self.ingestor.ingest((backdated,), ingest_time=ingest_at(1))
        accepted = self.ingest(obs(0, 1)).records[0].sample
        self.assertEqual(accepted.ingest_time, ingest_at(1000))

    def test_unregistered_binding_fails_closed(self) -> None:
        with self.assertRaises(UnknownBinding):
            self.ingest(obs(0, 1, signal_id="sig-unknown"))
        with self.assertRaises(UnknownBinding):
            self.ingest(obs(0, 1, source_id="src-2", signal_id="sig-b"))
        self.ingest(obs(0, 1))
        with self.assertRaises(UnknownBinding):
            self.reader.current(source_id="src-9", signal_id="sig-a",
                                snapshot=self.store.snapshot(at(10)))


class AppendSemanticsTests(Fixture):
    def test_first_append_advances_revision(self) -> None:
        self.assertEqual(self.store.current_ingest_sequence, 0)
        result = self.ingest(obs(0, 1))
        self.assertEqual((result.ingest_sequence, result.new_count), (1, 1))
        self.assertEqual(result.records[0].commit_revision, 1)
        self.assertEqual(result.records[0].store, self.store.ref)
        self.assertEqual(result.records[0].context_ref, "context:rev-1")
        self.assertEqual(self.store.current_ingest_sequence, 1)

    def test_batch_shares_one_atomic_revision(self) -> None:
        self.ingest(obs(0, 1))
        result = self.ingest(obs(1, 2), obs(2, 3), obs(0, 2, source_id="src-2"))
        self.assertEqual(result.ingest_sequence, 2)
        self.assertEqual({r.commit_revision for r in result.records}, {2})
        with self.assertRaises(UnknownBinding):  # one invalid record publishes none
            self.ingest(obs(3, 4), obs(0, 4, signal_id="sig-unknown"))
        self.assertEqual(self.store.current_ingest_sequence, 2)
        snapshot = self.store.snapshot(at(10))
        self.assertEqual(self.history(snapshot).eligible_count, 3)
        self.assertEqual(self.store.append(SignalSample(
            signal_id="sig-b", source_id="src-1", event_time=at(1), sequence=0,
            value="OPEN", quality=Quality.GOOD, ingest_time=ingest_at(1))).ingest_sequence, 3)

    def test_all_duplicate_batch_is_noop(self) -> None:
        first = self.ingest(obs(0, 1), obs(1, 2))
        again = self.ingest(obs(0, 1), obs(1, 2))
        self.assertEqual(again.ingest_sequence, 1)
        self.assertEqual(again.new_count, 0)
        self.assertFalse(again.committed)
        self.assertEqual(again.records, first.records)
        self.assertEqual(self.store.current_ingest_sequence, 1)

    def test_duplicate_retry_keeps_original_ingest_time(self) -> None:
        original = self.ingestor.ingest((obs(0, 1),), ingest_time=ingest_at(100))
        retry = self.ingestor.ingest((obs(0, 1),), ingest_time=ingest_at(999))
        self.assertIs(retry.records[0], original.records[0])
        self.assertEqual(retry.records[0].sample.ingest_time, ingest_at(100))
        self.assertEqual(self.store.current_ingest_sequence, 1)
        mixed = self.ingestor.ingest((obs(0, 1), obs(1, 2)), ingest_time=ingest_at(2000))
        self.assertEqual(mixed.new_count, 1)
        self.assertEqual(mixed.records[0].sample.ingest_time, ingest_at(100))
        self.assertEqual(mixed.records[1].commit_revision, 2)
        snapshot = self.store.snapshot(at(10))
        self.assertEqual(self.history(snapshot).eligible_count, 2)

    def test_identity_conflict_fails_batch_closed(self) -> None:
        self.ingest(obs(0, 1, value=1.0))
        for changed in ({"value": 2.0}, {"value": 1}, {"value": True},
                        {"quality": Quality.BAD}, {"minutes": 2},
                        {"source_status_code": "0x80000000"}):
            minutes = changed.pop("minutes", 1)
            with self.subTest(changed=changed), self.assertRaises(IdentityConflict):
                self.ingest(obs(5, 3), obs(0, minutes, **{"value": 1.0, **changed}))
        with self.assertRaises(IdentityConflict):  # conflict inside one batch
            self.ingest(obs(7, 3, value=1.0), obs(7, 3, value=2.0))
        self.assertEqual(self.store.current_ingest_sequence, 1)
        current = self.reader.current(source_id="src-1", signal_id="sig-a",
                                      snapshot=self.store.snapshot(at(10)))
        self.assertEqual((current.sample.sequence, current.sample.value), (0, 1.0))
        self.assertEqual(current.eligible_count, 1)

    def test_negative_zero_is_distinct_content(self) -> None:
        self.ingest(obs(0, 1, value=0.0))
        with self.assertRaises(IdentityConflict):
            self.ingest(obs(0, 1, value=-0.0))

    def test_same_event_time_distinct_sequences_preserved(self) -> None:
        self.ingest(obs(3, 5, value=3.0), obs(1, 5, value=1.0), obs(2, 5, value=2.0))
        result = self.history(self.store.snapshot(at(10)))
        self.assertEqual([s.sequence for s in result.samples], [1, 2, 3])
        self.assertEqual(result.eligible_count, 3)


class SnapshotTests(Fixture):
    def test_late_arrival_excluded_from_old_k(self) -> None:
        """Central P1.1A invariant: late data never rewrites an earlier snapshot."""
        self.ingest(obs(0, 2), obs(1, 4))
        self.ingest(obs(2, 6))
        snapshot = self.store.snapshot(at(10))  # T = 10 min, K = 2
        self.assertEqual((snapshot.ingest_sequence, snapshot.event_horizon), (2, at(10)))
        before = (self.reader.current(source_id="src-1", signal_id="sig-a",
                                      snapshot=snapshot), self.history(snapshot))
        late = self.ingest(obs(3, 8, value=99.0))  # event 8 min, committed at K = 3
        self.assertEqual(late.ingest_sequence, 3)
        after = (self.reader.current(source_id="src-1", signal_id="sig-a",
                                     snapshot=snapshot), self.history(snapshot))
        self.assertEqual(after, before)
        self.assertEqual(repr(after), repr(before))
        self.assertEqual(after[0].sample.sequence, 2)
        self.assertNotIn(SampleIdentity("src-1", "sig-a", 3), after[1].sample_identities)
        self.assertEqual(after[1].eligible_count, 3)
        newer = self.store.snapshot(at(10))
        self.assertEqual(self.reader.current(source_id="src-1", signal_id="sig-a",
                                             snapshot=newer).sample.value, 99.0)

    def test_future_event_excluded_by_old_t(self) -> None:
        self.ingest(obs(0, 2), obs(1, 15, value=15.0))
        self.ingest(obs(2, 20, value=20.0))
        for k in (1, 2):
            snapshot = self.store.snapshot(at(10), ingest_sequence=k)
            current = self.reader.current(source_id="src-1", signal_id="sig-a",
                                          snapshot=snapshot)
            history = self.history(snapshot)
            self.assertEqual(current.sample.sequence, 0)
            self.assertEqual((current.eligible_count, history.eligible_count), (1, 1))
            self.assertEqual([s.sequence for s in history.samples], [0])
        with self.assertRaises(TelemetryError):  # history cannot reach beyond T
            self.history(self.store.snapshot(at(10)), end=at(15))

    def test_old_snapshot_deterministic_after_later_appends(self) -> None:
        self.ingest(*(obs(i, i) for i in range(5)))
        snapshot = self.store.snapshot(at(30))
        first = self.history(snapshot, max_points=3)
        self.ingest(*(obs(i, i - 2) for i in range(5, 30)))
        self.ingest(obs(0, 0, source_id="src-2"))
        self.assertEqual(self.history(snapshot, max_points=3), first)
        self.assertEqual(self.history(snapshot, max_points=3).snapshot_ref,
                         first.snapshot_ref)
        # A historical K stays resolvable after later commits.
        rebuilt = self.store.snapshot(at(30), ingest_sequence=1)
        self.assertEqual(rebuilt, snapshot)
        self.assertEqual(rebuilt.snapshot_ref, snapshot.snapshot_ref)

    def test_snapshot_from_other_incarnation_or_namespace_rejected(self) -> None:
        self.ingest(obs(0, 1))
        other_incarnation = make_store(incarnation="inc-2")
        replay = make_store(namespace="replay")
        TelemetryIngestor(other_incarnation).ingest((obs(0, 1),), ingest_time=ingest_at(1))
        TelemetryIngestor(replay).ingest((obs(0, 1),), ingest_time=ingest_at(1))
        for foreign in (other_incarnation, replay):
            with self.subTest(store=foreign.ref), self.assertRaises(SnapshotUnavailable):
                self.reader.current(source_id="src-1", signal_id="sig-a",
                                    snapshot=foreign.snapshot(at(10)))
        forged = TelemetryReadSnapshot(self.store.ref, 1, at(10), SIM, "context:rev-2")
        with self.assertRaises(SnapshotUnavailable):
            self.reader.current(source_id="src-1", signal_id="sig-a", snapshot=forged)
        # Same clock_id, different descriptor (resolution or origin): not the same domain.
        for clock in (ClockDescriptor(SIM.clock_id, ClockKind.SIMULATION, Fraction(1, 10),
                                      "model-t0"),
                      ClockDescriptor(SIM.clock_id, ClockKind.SIMULATION, Fraction(1),
                                      "model-t1")):
            relabeled = TelemetryReadSnapshot(self.store.ref, 1, at(10), clock,
                                              "context:rev-1")
            with self.subTest(clock=clock), self.assertRaises(ClockMismatch):
                self.reader.current(source_id="src-1", signal_id="sig-a",
                                    snapshot=relabeled)
        self.assertNotEqual(make_store(incarnation=None).ref.incarnation,
                            make_store(incarnation=None).ref.incarnation)

    def test_future_k_rejected(self) -> None:
        self.ingest(obs(0, 1))
        with self.assertRaises(SnapshotUnavailable):
            self.store.snapshot(at(10), ingest_sequence=2)
        future = TelemetryReadSnapshot(self.store.ref, 5, at(10), SIM, "context:rev-1")
        with self.assertRaises(SnapshotUnavailable):
            self.history(future)
        with self.assertRaises(TelemetryError):
            self.store.snapshot(at(10), ingest_sequence=-1)

    def test_snapshot_is_not_reused_from_other_revision_concepts(self) -> None:
        with self.assertRaises(TelemetryError):
            self.store.snapshot(at(10), ingest_sequence="rev-abc")  # type: ignore[arg-type]
        empty = self.store.snapshot(at(10))
        self.assertEqual(empty.ingest_sequence, 0)
        self.assertEqual(self.reader.current(source_id="src-1", signal_id="sig-a",
                                             snapshot=empty).status, ReadStatus.NO_DATA)


class ReadSemanticsTests(Fixture):
    def test_out_of_order_arrival_sorted_deterministically(self) -> None:
        arrivals = [obs(4, 4), obs(0, 0), obs(3, 3), obs(1, 1), obs(2, 2)]
        for order in (arrivals, list(reversed(arrivals))):
            with self.subTest(order=[o.sequence for o in order]):
                store = make_store()
                ingestor = TelemetryIngestor(store)
                for i, observation in enumerate(order):
                    ingestor.ingest((observation,), ingest_time=ingest_at(i))
                reader = TimeSeriesReader(store, max_interval_ticks=3600)
                result = reader.history(source_id="src-1", signal_id="sig-a",
                                        event_start=at(0), event_end=at(10),
                                        max_points=10, snapshot=store.snapshot(at(10)))
                self.assertEqual([s.sequence for s in result.samples], [0, 1, 2, 3, 4])
                current = reader.current(source_id="src-1", signal_id="sig-a",
                                         snapshot=store.snapshot(at(10)))
                self.assertEqual(current.sample.sequence, 4)

    def test_bad_current_not_replaced_by_older_good(self) -> None:
        self.ingest(obs(0, 1, value=5.0, quality=Quality.GOOD))
        self.ingest(obs(1, 2, value=None, quality=Quality.BAD, source_status_code="comm-loss"))
        current = self.reader.current(source_id="src-1", signal_id="sig-a",
                                      snapshot=self.store.snapshot(at(10)))
        self.assertEqual((current.sample.quality, current.sample.value), (Quality.BAD, None))
        self.ingest(obs(2, 3, value=6.0, quality=Quality.UNCERTAIN))
        current = self.reader.current(source_id="src-1", signal_id="sig-a",
                                      snapshot=self.store.snapshot(at(10)))
        self.assertEqual(current.sample.quality, Quality.UNCERTAIN)
        history = self.history(self.store.snapshot(at(10)))
        self.assertEqual(history.qualities, (Quality.GOOD, Quality.BAD, Quality.UNCERTAIN))

    def test_empty_valid_interval_is_no_data(self) -> None:
        self.ingest(obs(0, 1), obs(1, 9))
        snapshot = self.store.snapshot(at(10))
        result = self.history(snapshot, start=3, end=at(7))
        self.assertEqual((result.status, result.eligible_count, result.returned_count),
                         (ReadStatus.NO_DATA, 0, 0))
        self.assertEqual(result.samples, ())
        none = self.reader.current(source_id="src-1", signal_id="sig-b", snapshot=snapshot)
        self.assertEqual((none.status, none.sample, none.eligible_count),
                         (ReadStatus.NO_DATA, None, 0))

    def test_history_bounds_are_required(self) -> None:
        self.ingest(obs(0, 1))
        snapshot = self.store.snapshot(at(10))
        for kwargs, error in (({"start": 5, "end": at(4)}, TelemetryError),
                              ({"max_points": 0}, TelemetryError),
                              ({"max_points": True}, TelemetryError),
                              ({"max_points": 501}, ResourceLimitExceeded)):
            with self.subTest(kwargs=kwargs), self.assertRaises(error):
                self.history(snapshot, **kwargs)
        narrow = TimeSeriesReader(self.store, max_interval_ticks=5 * MINUTE)
        with self.assertRaises(ResourceLimitExceeded):
            narrow.history(source_id="src-1", signal_id="sig-a", event_start=at(0),
                           event_end=at(10), max_points=10, snapshot=snapshot)
        point = self.history(snapshot, start=1, end=at(1))  # inclusive single instant
        self.assertEqual(point.eligible_count, 1)

    def test_deterministic_history_reduction(self) -> None:
        self.assertEqual(reduce_indices(10, 4), (0, 3, 6, 9))
        self.assertEqual(reduce_indices(5, 2), (0, 4))
        self.assertEqual(reduce_indices(3, 5), (0, 1, 2))
        self.assertEqual(reduce_indices(7, 7), tuple(range(7)))
        self.ingest(*(obs(i, i, value=float(i)) for i in range(10)))
        result = self.history(self.store.snapshot(at(10)), max_points=4)
        self.assertEqual([s.sequence for s in result.samples], [0, 3, 6, 9])
        self.assertEqual((result.eligible_count, result.returned_count, result.reduced),
                         (10, 4, True))
        self.assertEqual(result.reduction_policy, telemetry.REDUCTION_POLICY)
        self.assertEqual(result, self.history(self.store.snapshot(at(10)), max_points=4))

    def test_m_equals_one_returns_last_eligible_point(self) -> None:
        self.ingest(*(obs(i, i) for i in range(6)))
        result = self.history(self.store.snapshot(at(10)), max_points=1)
        self.assertEqual(result.sample_identities, (SampleIdentity("src-1", "sig-a", 5),))
        self.assertEqual(result.eligible_count, 6)

    def test_filtering_happens_before_reduction(self) -> None:
        # K=1: events 0..4 min plus beyond-T events 7, 8 min (excluded only by T).
        self.ingest(*(obs(i, i) for i in range(5)), obs(20, 7), obs(21, 8))
        self.ingest(obs(5, 5))                                  # K=2: event 5 min
        snapshot = self.store.snapshot(at(6))                   # T=6 min, K=2
        # Within-T events committed after K (excluded only by K).
        self.ingest(*(obs(i, i - 10 + 0.5) for i in range(10, 15)))
        self.ingest(obs(0, 1, source_id="src-2"))               # other source
        result = self.history(snapshot, end=at(6), max_points=2)
        self.assertEqual(result.eligible_count, 6)
        self.assertEqual([s.sequence for s in result.samples], [0, 5])
        last = self.history(snapshot, end=at(6), max_points=1)
        self.assertEqual(last.sample_identities, (SampleIdentity("src-1", "sig-a", 5),))
        current = self.reader.current(source_id="src-1", signal_id="sig-a", snapshot=snapshot)
        self.assertEqual((current.sample.sequence, current.eligible_count), (5, 6))

    def test_cutoff_boundaries_are_inclusive(self) -> None:
        horizon = at(10)
        edge = TimePoint(SIM.clock_id, horizon.ticks)
        beyond = TimePoint(SIM.clock_id, horizon.ticks + 1)
        self.ingest(SourceObservation(signal_id="sig-a", source_id="src-1", event_time=edge,
                                      sequence=0, value=1.0, quality=Quality.GOOD),
                    SourceObservation(signal_id="sig-a", source_id="src-1",
                                      event_time=beyond, sequence=1, value=2.0,
                                      quality=Quality.GOOD))
        self.ingest(obs(2, 9))                                  # committed at K+1
        snapshot = self.store.snapshot(horizon, ingest_sequence=1)
        current = self.reader.current(source_id="src-1", signal_id="sig-a", snapshot=snapshot)
        self.assertEqual((current.sample.sequence, current.eligible_count), (0, 1))
        self.assertEqual(self.history(snapshot).sample_identities,
                         (SampleIdentity("src-1", "sig-a", 0),))
        at_k2 = self.store.snapshot(horizon, ingest_sequence=2)
        self.assertEqual(self.history(at_k2).eligible_count, 2)

    def test_concurrent_batches_get_unique_contiguous_revisions(self) -> None:
        import threading

        results, errors = [], []

        def worker(index: int) -> None:
            try:
                batch = [obs(index * 10 + j, index) for j in range(5)]
                results.append(self.ingestor.ingest(batch, ingest_time=ingest_at(index)))
            except Exception as error:  # pragma: no cover - surfaced below
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        revisions = sorted(r.ingest_sequence for r in results)
        self.assertEqual(revisions, list(range(1, 17)))
        for result in results:
            self.assertEqual({r.commit_revision for r in result.records},
                             {result.ingest_sequence})
            k = result.ingest_sequence
            visible = self.history(self.store.snapshot(at(20), ingest_sequence=k),
                                   max_points=500).eligible_count
            hidden = self.history(self.store.snapshot(at(20), ingest_sequence=k - 1),
                                  max_points=500).eligible_count
            self.assertEqual(visible - hidden, 5)

    def test_read_result_provenance(self) -> None:
        self.ingest(obs(0, 1), obs(1, 2, quality=Quality.UNCERTAIN))
        snapshot = self.store.snapshot(at(10))
        result = self.history(snapshot, start=0, end=at(10), max_points=5)
        self.assertEqual(result.snapshot, snapshot)
        self.assertTrue(result.snapshot_ref.startswith("telemetry-snapshot:sha256:"))
        self.assertEqual((result.source_id, result.signal_id), ("src-1", "sig-a"))
        self.assertEqual((result.event_start, result.event_end), (at(0), at(10)))
        self.assertEqual(result.sample_identities,
                         (SampleIdentity("src-1", "sig-a", 0),
                          SampleIdentity("src-1", "sig-a", 1)))
        self.assertEqual(result.qualities, (Quality.GOOD, Quality.UNCERTAIN))
        self.assertEqual(snapshot.store, self.store.ref)
        self.assertEqual(snapshot.context_ref, "context:rev-1")
        self.assertEqual(snapshot.as_json()["event_clock"]["resolution"], "1/1")
        other = self.store.snapshot(at(10), ingest_sequence=0)
        self.assertNotEqual(other.snapshot_ref, snapshot.snapshot_ref)


class BoundaryTests(unittest.TestCase):
    SOURCE = Path(telemetry.__file__).read_text(encoding="utf-8")
    ALLOWED_IMPORTS = {"__future__", "bisect", "collections.abc", "dataclasses", "enum",
                       "fractions", "hashlib", "json", "math", "secrets", "threading",
                       "typing"}

    def test_generic_module_imports_stdlib_only(self) -> None:
        imported = set()
        for node in ast.walk(ast.parse(self.SOURCE)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "no package-relative imports")
                imported.add(node.module)
        self.assertLessEqual(imported, self.ALLOWED_IMPORTS)

    def test_no_simulator_specific_vocabulary(self) -> None:
        forbidden = re.compile(
            r"xmeas|xmv|\bidv|control_?mode|tep_sim|tepenvironment|processgraph"
            r"|referenceworld|simulationsandbox|rcastate", re.IGNORECASE)
        self.assertEqual(forbidden.findall(self.SOURCE), [])


if __name__ == "__main__":
    unittest.main()
