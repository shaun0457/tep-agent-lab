"""Generic telemetry core: canonical samples, append store and snapshot-bound reads.

Implements the P1.1A subset of ``docs/specs/plant-telemetry-contract-v0.md``::

    SourceObservation -> TelemetryIngestor (assigns ingest_time)
      -> InMemoryTimeSeriesStore (immutable records, store-wide commit revision K)
      -> TelemetryReadSnapshot (event horizon T + ingest revision K)
      -> TimeSeriesReader (bounded deterministic current/history)

Hard invariant: a read pinned to (T, K) never observes a record whose commit
revision exceeds K or whose event time exceeds T. Both cutoffs are applied before
ordering, reduction or any count is computed, so late arrivals never rewrite an
earlier snapshot.

This module is source-independent application/domain infrastructure. It imports
the standard library only; simulator adapters translate into these contracts, never
the other way round. It does not replace any existing world/history owner, and
store revisions are unrelated to simulator snapshots or investigation-state revisions.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from enum import StrEnum
from fractions import Fraction
import hashlib
import json
import math
import secrets
import threading
from typing import Any

TELEMETRY_CONTRACT_VERSION = "tep-agent-lab.telemetry/v0"
REDUCTION_POLICY = "telemetry.reduction.endpoint-index/v0"

_MAX_ID_LENGTH = 256

Scalar = bool | int | float | str | None


class TelemetryError(ValueError):
    """Invalid telemetry record, request or registration (fails closed)."""


class ClockMismatch(TelemetryError):
    """Comparison across clock domains without an explicit mapping."""


class IdentityConflict(TelemetryError):
    """Different source content proposed under an already accepted identity."""


class UnknownBinding(LookupError):
    """Source/signal binding not registered with the store."""


class SnapshotUnavailable(LookupError):
    """Snapshot does not resolve against this store incarnation/namespace/revision."""


class ResourceLimitExceeded(TelemetryError):
    """Request exceeds the reader's declared service-policy bounds."""


def _checked_id(name: str, value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > _MAX_ID_LENGTH
            or value != value.strip() or not value.isprintable()):
        raise TelemetryError(f"{name} must be a non-empty printable identifier")
    return value


def _coerce(enum: type[StrEnum], name: str, value: Any) -> Any:
    try:
        return enum(value)
    except ValueError:
        raise TelemetryError(f"{name} must be one of {[str(member) for member in enum]}") from None


def _checked_int(name: str, value: Any, *, minimum: int | None = None) -> int:
    # bool is an int subclass; True must never pass as tick 1 or sequence 1.
    if type(value) is not int:
        raise TelemetryError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise TelemetryError(f"{name} must be >= {minimum}")
    return value


class ClockKind(StrEnum):
    SIMULATION = "SIMULATION"
    UTC = "UTC"
    SOURCE_DEFINED = "SOURCE_DEFINED"


@dataclass(frozen=True)
class ClockDescriptor:
    """Immutable clock domain: exact rational seconds per tick and explicit origin.

    SIMULATION and UTC clocks require an origin (model origin, or UTC epoch plus
    leap-second convention). Simulation time is never presented as UTC; cross-clock
    mapping is not implemented in P1.1A and any such comparison is rejected.
    """

    clock_id: str
    kind: ClockKind
    resolution: Fraction
    origin: str | None = None

    def __post_init__(self) -> None:
        _checked_id("clock_id", self.clock_id)
        object.__setattr__(self, "kind", _coerce(ClockKind, "kind", self.kind))
        # Floats are rejected: resolution must stay exact for deterministic ticks.
        if type(self.resolution) is int:
            object.__setattr__(self, "resolution", Fraction(self.resolution))
        if not isinstance(self.resolution, Fraction) or self.resolution <= 0:
            raise TelemetryError("resolution must be a positive exact Fraction/int")
        if self.origin is not None:
            _checked_id("origin", self.origin)
        elif self.kind in (ClockKind.SIMULATION, ClockKind.UTC):
            raise TelemetryError(f"{self.kind} clock requires an explicit origin")

    def as_json(self) -> dict[str, Any]:
        return {"clock_id": self.clock_id, "kind": str(self.kind),
                "resolution": f"{self.resolution.numerator}/{self.resolution.denominator}",
                "origin": self.origin}


@dataclass(frozen=True)
class TimePoint:
    """Integer ticks in one clock domain. Deliberately not orderable across clocks."""

    clock_id: str
    ticks: int

    def __post_init__(self) -> None:
        _checked_id("clock_id", self.clock_id)
        _checked_int("ticks", self.ticks)

    def as_json(self) -> dict[str, Any]:
        return {"clock_id": self.clock_id, "ticks": self.ticks}


def require_clock(point: Any, clock: ClockDescriptor, name: str) -> TimePoint:
    if not isinstance(point, TimePoint):
        raise TelemetryError(f"{name} must be a TimePoint")
    if point.clock_id != clock.clock_id:
        raise ClockMismatch(
            f"{name} clock {point.clock_id!r} is not {clock.clock_id!r}; "
            "cross-clock comparison requires an explicit mapping")
    return point


class Quality(StrEnum):
    GOOD = "GOOD"
    UNCERTAIN = "UNCERTAIN"
    BAD = "BAD"


@dataclass(frozen=True, order=True)
class SampleIdentity:
    source_id: str
    signal_id: str
    sequence: int


def _checked_value(value: Any) -> Scalar:
    # Exact types only: subclasses (enum members, numpy scalars) would blur identity.
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise TelemetryError("numeric value must be finite")
        return value
    raise TelemetryError("value must be a numeric, boolean or string scalar, or None")


def _value_key(value: Scalar) -> tuple[str, Any]:
    # Type-strict, sign-preserving equality: 1, 1.0 and True are different content.
    if type(value) is float:
        return ("float", value.hex())
    return (type(value).__name__, value)


@dataclass(frozen=True, kw_only=True, eq=False)
class SourceObservation:
    """Source event fields before acceptance; a source cannot supply ingest_time.

    Equality and hashing are type-strict on ``value`` (1, 1.0 and True differ; so
    do 0.0 and -0.0), matching duplicate/conflict identity semantics.
    """

    signal_id: str
    source_id: str
    event_time: TimePoint
    sequence: int
    value: Scalar
    quality: Quality
    source_status_code: str | None = None
    source_metadata_ref: str | None = None

    def __post_init__(self) -> None:
        _checked_id("signal_id", self.signal_id)
        _checked_id("source_id", self.source_id)
        if not isinstance(self.event_time, TimePoint):
            raise TelemetryError("event_time must be a TimePoint")
        _checked_int("sequence", self.sequence, minimum=0)
        _checked_value(self.value)
        object.__setattr__(self, "quality", _coerce(Quality, "quality", self.quality))
        for name in ("source_status_code", "source_metadata_ref"):
            if getattr(self, name) is not None:
                _checked_id(name, getattr(self, name))

    @property
    def identity(self) -> SampleIdentity:
        return SampleIdentity(self.source_id, self.signal_id, self.sequence)

    def source_content(self) -> tuple[Any, ...]:
        """Canonical source/fidelity fields used for duplicate detection.

        Excludes ``ingest_time``: a retried delivery proposes a new acceptance time
        but carries the same source content.
        """
        return (self.signal_id, self.source_id, self.event_time, self.sequence,
                _value_key(self.value), self.quality, self.source_status_code,
                self.source_metadata_ref)

    def _key(self) -> tuple[Any, ...]:
        return self.source_content()

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self._key() == other._key()  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash(self._key())


@dataclass(frozen=True, kw_only=True, eq=False)
class SignalSample(SourceObservation):
    """Canonical immutable sample; ``ingest_time`` is assigned at acceptance."""

    ingest_time: TimePoint

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.ingest_time, TimePoint):
            raise TelemetryError("ingest_time must be a TimePoint")

    def _key(self) -> tuple[Any, ...]:
        return (*self.source_content(), self.ingest_time)

    def as_json(self) -> dict[str, Any]:
        return {"signal_id": self.signal_id, "source_id": self.source_id,
                "event_time": self.event_time.as_json(),
                "ingest_time": self.ingest_time.as_json(), "sequence": self.sequence,
                "value": self.value, "quality": str(self.quality),
                "source_status_code": self.source_status_code,
                "source_metadata_ref": self.source_metadata_ref}


@dataclass(frozen=True)
class StoreRef:
    """Store identity: one incarnation within one isolated telemetry namespace."""

    store_id: str
    incarnation: str
    namespace: str

    def __post_init__(self) -> None:
        for name in ("store_id", "incarnation", "namespace"):
            _checked_id(name, getattr(self, name))

    def as_json(self) -> dict[str, Any]:
        return {"store_id": self.store_id, "incarnation": self.incarnation,
                "namespace": self.namespace}


@dataclass(frozen=True)
class AcceptedRecord:
    """Store envelope; commit data never enlarges the SignalSample schema."""

    sample: SignalSample
    commit_revision: int
    store: StoreRef
    context_ref: str


@dataclass(frozen=True)
class AppendResult:
    """``records`` aligns with the input; duplicates resolve to the original record."""

    ingest_sequence: int
    records: tuple[AcceptedRecord, ...]
    new_count: int

    @property
    def committed(self) -> bool:
        return self.new_count > 0


@dataclass(frozen=True)
class TelemetryReadSnapshot:
    """Immutable read cutoff: event horizon T (inclusive) and committed revision K.

    A read capability/reference only; it is not a simulator snapshot and grants no
    visibility beyond the reader's own checks. ``visibility_policy_ref`` is an
    opaque immutable policy identifier pinned into the snapshot identity; P1.1A
    does not evaluate it (caller authorization is later application integration).
    ``mapping_ref`` is absent because cross-clock mapping is unsupported.
    """

    store: StoreRef
    ingest_sequence: int
    event_horizon: TimePoint
    event_clock: ClockDescriptor
    context_ref: str
    visibility_policy_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.store, StoreRef):
            raise TelemetryError("store must be a StoreRef")
        _checked_int("ingest_sequence", self.ingest_sequence, minimum=0)
        if not isinstance(self.event_clock, ClockDescriptor):
            raise TelemetryError("event_clock must be a ClockDescriptor")
        require_clock(self.event_horizon, self.event_clock, "event_horizon")
        _checked_id("context_ref", self.context_ref)
        _checked_id("visibility_policy_ref", self.visibility_policy_ref)

    def as_json(self) -> dict[str, Any]:
        return {"contract": TELEMETRY_CONTRACT_VERSION, "store": self.store.as_json(),
                "ingest_sequence": self.ingest_sequence,
                "event_horizon": self.event_horizon.as_json(),
                "event_clock": self.event_clock.as_json(), "context_ref": self.context_ref,
                "visibility_policy_ref": self.visibility_policy_ref}

    @cached_property
    def snapshot_ref(self) -> str:
        # Same canonical JSON settings as persistence.canonical_json.
        body = json.dumps(self.as_json(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
        return "telemetry-snapshot:sha256:" + hashlib.sha256(body.encode()).hexdigest()


class InMemoryTimeSeriesStore:
    """Deterministic append-only store for one namespace and one event clock domain.

    Registration (identity, clocks, context, visibility policy ref, source/signal
    bindings) is frozen at construction. Records are never replaced or evicted; the
    incarnation exists only for the life of this process object and is not durable
    across restart.
    """

    def __init__(self, *, store_id: str, namespace: str, context_ref: str,
                 visibility_policy_ref: str, event_clock: ClockDescriptor,
                 ingest_clock: ClockDescriptor, bindings: Mapping[str, Iterable[str]],
                 incarnation: str | None = None) -> None:
        if incarnation is None:
            incarnation = f"inc-{secrets.token_hex(8)}"
        self.ref = StoreRef(store_id, incarnation, namespace)
        self.context_ref = _checked_id("context_ref", context_ref)
        self.visibility_policy_ref = _checked_id("visibility_policy_ref",
                                                 visibility_policy_ref)
        if not (isinstance(event_clock, ClockDescriptor)
                and isinstance(ingest_clock, ClockDescriptor)):
            raise TelemetryError("event_clock and ingest_clock must be ClockDescriptors")
        self.event_clock, self.ingest_clock = event_clock, ingest_clock
        frozen: dict[str, frozenset[str]] = {}
        for source_id, signals in dict(bindings).items():
            if isinstance(signals, (str, bytes)):
                raise TelemetryError("signals must be a collection of signal ids")
            signal_ids = frozenset(_checked_id("signal_id", s) for s in signals)
            if not signal_ids:
                raise TelemetryError("each registered source needs at least one signal")
            frozen[_checked_id("source_id", source_id)] = signal_ids
        if not frozen:
            raise TelemetryError("store requires at least one source binding")
        self._bindings = frozen
        self._lock = threading.Lock()
        self._revision = 0
        self._by_identity: dict[SampleIdentity, AcceptedRecord] = {}
        # Per binding, in commit order; commit revisions are therefore nondecreasing.
        self._streams: dict[tuple[str, str], list[AcceptedRecord]] = {}

    @property
    def bindings(self) -> Mapping[str, frozenset[str]]:
        return dict(self._bindings)

    @property
    def current_ingest_sequence(self) -> int:
        with self._lock:
            return self._revision

    def require_binding(self, source_id: str, signal_id: str) -> None:
        if not (isinstance(source_id, str) and isinstance(signal_id, str)):
            raise TelemetryError("source_id and signal_id must be strings")
        if signal_id not in self._bindings.get(source_id, frozenset()):
            raise UnknownBinding("unregistered source/signal binding")

    def _accept(self, observation: Any, ingest_time: TimePoint) -> SignalSample:
        # Exact type: a prebuilt SignalSample would smuggle in its own ingest_time.
        if type(observation) is not SourceObservation:
            raise TelemetryError("store accepts SourceObservation records only")
        self.require_binding(observation.source_id, observation.signal_id)
        require_clock(observation.event_time, self.event_clock, "event_time")
        fields = {name: getattr(observation, name)
                  for name in SourceObservation.__dataclass_fields__}
        return SignalSample(**fields, ingest_time=ingest_time)

    def append(self, observation: SourceObservation, *,
               ingest_time: TimePoint) -> AppendResult:
        return self.append_batch((observation,), ingest_time=ingest_time)

    def append_batch(self, observations: Sequence[SourceObservation], *,
                     ingest_time: TimePoint) -> AppendResult:
        """Accept a batch at one trusted ``ingest_time`` under one commit revision.

        The store builds every SignalSample itself. Validation completes before
        visibility. Exact duplicates (ignoring the newly proposed ingest_time)
        resolve to the accepted record; any identity conflict rejects the whole
        batch; an all-duplicate batch leaves the revision unchanged.
        """
        if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
            raise TelemetryError("observations must be a sequence of SourceObservation")
        require_clock(ingest_time, self.ingest_clock, "ingest_time")
        batch = tuple(self._accept(observation, ingest_time) for observation in observations)
        if not batch:
            raise TelemetryError("batch must contain at least one sample")
        with self._lock:
            revision = self._revision + 1
            pending: dict[SampleIdentity, AcceptedRecord] = {}
            records: list[AcceptedRecord] = []
            for sample in batch:
                prior = self._by_identity.get(sample.identity) or pending.get(sample.identity)
                if prior is not None:
                    if prior.sample.source_content() != sample.source_content():
                        raise IdentityConflict(
                            f"identity {sample.identity} already accepted with different content")
                    records.append(prior)
                    continue
                record = AcceptedRecord(sample, revision, self.ref, self.context_ref)
                pending[sample.identity] = record
                records.append(record)
            if pending:
                for identity, record in pending.items():
                    self._by_identity[identity] = record
                    self._streams.setdefault(
                        (identity.source_id, identity.signal_id), []).append(record)
                self._revision = revision
            return AppendResult(self._revision, tuple(records), len(pending))

    def snapshot(self, event_horizon: TimePoint,
                 ingest_sequence: int | None = None) -> TelemetryReadSnapshot:
        """Freeze T and K together; K defaults to the current committed revision."""
        with self._lock:
            revision = self._revision if ingest_sequence is None else ingest_sequence
        snapshot = TelemetryReadSnapshot(self.ref, revision, event_horizon,
                                         self.event_clock, self.context_ref,
                                         self.visibility_policy_ref)
        self.resolve(snapshot)
        return snapshot

    def resolve(self, snapshot: Any) -> TelemetryReadSnapshot:
        """Reject snapshots of another incarnation/namespace/context/visibility policy,
        another clock, or a future K."""
        if not isinstance(snapshot, TelemetryReadSnapshot):
            raise TelemetryError("snapshot must be a TelemetryReadSnapshot")
        if (snapshot.store != self.ref or snapshot.context_ref != self.context_ref
                or snapshot.visibility_policy_ref != self.visibility_policy_ref):
            raise SnapshotUnavailable("snapshot does not belong to this store incarnation")
        if snapshot.event_clock != self.event_clock:
            raise ClockMismatch("snapshot clock domain differs from the store clock")
        with self._lock:
            if snapshot.ingest_sequence > self._revision:
                raise SnapshotUnavailable("snapshot ingest_sequence is not yet committed")
        return snapshot

    def _eligible(self, source_id: str, signal_id: str, snapshot: TelemetryReadSnapshot,
                  start_ticks: int | None, end_ticks: int,
                  max_scan_records: int) -> list[SignalSample]:
        """Records with commit <= K and start <= event <= end (<= T), unordered.

        Reader-internal: the reader resolves the snapshot and validates bounds and
        limits first. Both cutoffs apply here, before any sort, reduction or count.

        The candidate set is the binding's K-committed prefix, sized by bisection
        before any record is read. If it exceeds ``max_scan_records`` the read fails
        without scanning anything. The prefix is fixed by (binding, K), so a given
        snapshot passes or fails deterministically regardless of later appends.
        """
        self.require_binding(source_id, signal_id)
        with self._lock:
            stream = self._streams.get((source_id, signal_id), [])
            # Commit order makes the K-visible records an exact prefix.
            visible = bisect_right(stream, snapshot.ingest_sequence,
                                   key=lambda record: record.commit_revision)
            if visible > max_scan_records:
                # No counts or values in the message: nothing about the candidates leaks.
                raise ResourceLimitExceeded("candidate scan exceeds the reader limit")
            prefix = stream[:visible]
        return [record.sample for record in prefix
                if record.sample.event_time.ticks <= end_ticks
                and (start_ticks is None or record.sample.event_time.ticks >= start_ticks)]


def read_order(sample: SignalSample) -> tuple[int, str, int]:
    """Total read order within one clock; independent of ingest arrival order."""
    return (sample.event_time.ticks, sample.source_id, sample.sequence)


class TelemetryIngestor:
    """Source-facing acceptance boundary.

    Sources hand over SourceObservations only; the trusted caller supplies
    ``ingest_time``. The store builds every SignalSample itself, so no caller can
    insert a prebuilt sample carrying a backdated acceptance time.
    """

    def __init__(self, store: InMemoryTimeSeriesStore) -> None:
        self._store = store

    def ingest(self, observations: Sequence[SourceObservation], *,
               ingest_time: TimePoint) -> AppendResult:
        return self._store.append_batch(observations, ingest_time=ingest_time)


class ReadStatus(StrEnum):
    DATA = "DATA"
    NO_DATA = "NO_DATA"


class RetentionCoverage(StrEnum):
    """What the store retained for the read, never a claim about the source stream.

    COMPLETE_FOR_STORE_INCARNATION: every record this store incarnation accepted
    (committed at or before K) is retained and was considered. It does NOT claim
    the physical/source stream had no missing deliveries, gaps or late data.
    """

    COMPLETE_FOR_STORE_INCARNATION = "COMPLETE_FOR_STORE_INCARNATION"


@dataclass(frozen=True)
class CurrentResult:
    snapshot: TelemetryReadSnapshot
    source_id: str
    signal_id: str
    eligible_count: int
    sample: SignalSample | None
    coverage: RetentionCoverage = RetentionCoverage.COMPLETE_FOR_STORE_INCARNATION

    @property
    def status(self) -> ReadStatus:
        return ReadStatus.NO_DATA if self.sample is None else ReadStatus.DATA

    @property
    def snapshot_ref(self) -> str:
        return self.snapshot.snapshot_ref


@dataclass(frozen=True)
class HistoryResult:
    snapshot: TelemetryReadSnapshot
    source_id: str
    signal_id: str
    event_start: TimePoint
    event_end: TimePoint
    max_points: int
    eligible_count: int
    samples: tuple[SignalSample, ...]
    reduction_policy: str = field(default=REDUCTION_POLICY)
    coverage: RetentionCoverage = RetentionCoverage.COMPLETE_FOR_STORE_INCARNATION

    @property
    def status(self) -> ReadStatus:
        return ReadStatus.DATA if self.samples else ReadStatus.NO_DATA

    @property
    def returned_count(self) -> int:
        return len(self.samples)

    @property
    def reduced(self) -> bool:
        return self.returned_count < self.eligible_count

    @property
    def sample_identities(self) -> tuple[SampleIdentity, ...]:
        return tuple(sample.identity for sample in self.samples)

    @property
    def qualities(self) -> tuple[Quality, ...]:
        return tuple(sample.quality for sample in self.samples)

    @property
    def snapshot_ref(self) -> str:
        return self.snapshot.snapshot_ref


def reduce_indices(n: int, m: int) -> tuple[int, ...]:
    """Frozen v0 reduction: all if n <= m; last if m == 1; else floor(i(n-1)/(m-1))."""
    if n <= m:
        return tuple(range(n))
    if m == 1:
        return (n - 1,)
    return tuple(i * (n - 1) // (m - 1) for i in range(m))


class TimeSeriesReader:
    """Bounded deterministic reads; every request pins one snapshot and one binding.

    Service-policy bounds: returned points, event interval and candidate records
    scanned (``max_scan_records``). Exceeding any of them raises
    ResourceLimitExceeded; there is no partial scan or silent truncation.
    """

    def __init__(self, store: InMemoryTimeSeriesStore, *, max_interval_ticks: int,
                 max_scan_records: int, max_points_limit: int = 1000) -> None:
        self._store = store
        self.max_interval_ticks = _checked_int("max_interval_ticks", max_interval_ticks,
                                               minimum=0)
        self.max_scan_records = _checked_int("max_scan_records", max_scan_records,
                                             minimum=1)
        self.max_points_limit = _checked_int("max_points_limit", max_points_limit, minimum=1)

    def current(self, *, source_id: str, signal_id: str,
                snapshot: TelemetryReadSnapshot) -> CurrentResult:
        """Last eligible sample at or before T, whatever its quality, or NO_DATA."""
        self._store.resolve(snapshot)
        samples = self._store._eligible(source_id, signal_id, snapshot, None,
                                        snapshot.event_horizon.ticks,
                                        self.max_scan_records)
        return CurrentResult(snapshot, source_id, signal_id, len(samples),
                             max(samples, key=read_order) if samples else None)

    def history(self, *, source_id: str, signal_id: str, event_start: TimePoint,
                event_end: TimePoint, max_points: int,
                snapshot: TelemetryReadSnapshot) -> HistoryResult:
        clock = self._store.event_clock
        require_clock(event_start, clock, "event_start")
        require_clock(event_end, clock, "event_end")
        _checked_int("max_points", max_points, minimum=1)
        if event_start.ticks > event_end.ticks:
            raise TelemetryError("event_start must not be after event_end")
        self._store.resolve(snapshot)
        if event_end.ticks > snapshot.event_horizon.ticks:
            raise TelemetryError("event_end must not exceed the snapshot event horizon")
        if max_points > self.max_points_limit:
            raise ResourceLimitExceeded("max_points exceeds the reader limit")
        if event_end.ticks - event_start.ticks > self.max_interval_ticks:
            raise ResourceLimitExceeded("event interval exceeds the reader limit")
        eligible = sorted(self._store._eligible(source_id, signal_id, snapshot,
                                                event_start.ticks, event_end.ticks,
                                                self.max_scan_records),
                          key=read_order)
        selected = tuple(eligible[i] for i in reduce_indices(len(eligible), max_points))
        return HistoryResult(snapshot, source_id, signal_id, event_start, event_end,
                             max_points, len(eligible), selected)
