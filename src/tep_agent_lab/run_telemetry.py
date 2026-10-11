"""Run-scoped canonical telemetry for one prepared P0 session (P1.1C).

docs/p1-1c-canonical-telemetry-integration.md::

    ReferenceWorld.history()                 (unchanged owner; harness advances it)
      -> RunTelemetry.sync_reference_history (once, inside RunManager.prepare)
      -> TEPSimulationSource.translate_sanitized   (one batch per record)
      -> TelemetryIngestor (trusted P0 clock supplies ingest_time)
      -> run-scoped InMemoryTimeSeriesStore -> TelemetryReadSnapshot -> TimeSeriesReader

Every prepare attempt draws an opaque incarnation token. Public identifiers (source,
clocks, namespace, incarnation) carry that token, never the attempt counter, the
session directory or a failure count, so failed prepares stay invisible to AGENT
readers. The token-to-attempt association exists only in this trusted in-process
object. Ingest times are diagnostic only and never public: they stay inside the store
and are sampled once per sync.

``seal()`` is called by P0 when the session is assembled, before READY. After that
``sync_reference_history`` raises. The only post-seal delivery path is the explicit
``_harness_translate``/``_harness_deliver`` pair, which production code never calls
(tested); acceptance tests use it to inject late, out-of-order, duplicate and same-tick
deliveries through a real prepared session.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import secrets
import threading
from typing import Any

from industrial_agent_runtime import checksum, to_jsonable
from tep_sim import ProcessGraph

from .canonical_context import ContextSourceRef
from .telemetry import (TELEMETRY_CONTRACT_VERSION, AppendResult, ClockDescriptor, ClockKind,
                        InMemoryTimeSeriesStore, SignalSample, SnapshotUnavailable,
                        SourceObservation, StoreRef, TelemetryError, TelemetryIngestor,
                        TelemetryReadSnapshot, TelemetryRequestError, TimePoint, TimeSeriesReader,
                        _checked_id, _checked_int, _coerce, parse_event_resolution)
from .tep_telemetry import TEPSimulationSource

TELEMETRY_STORE_ID = "p0-reference-telemetry"
CANONICAL_SOURCE = "P0 run-scoped canonical reference telemetry"
# Trusted acceptance clock: the P0 RunManager clock, in UTC milliseconds. Ingest time is
# diagnostic only; the store commit revision K orders knowledge.
INGEST_CLOCK = ClockDescriptor("p0-ingest-utc", ClockKind.UTC, Fraction(1, 1000),
                               "1970-01-01T00:00:00Z/utc")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SNAPSHOT_KEYS = frozenset({"contract", "store", "ingest_sequence", "event_horizon",
                           "event_clock", "context_ref", "visibility_policy_ref",
                           "snapshot_ref"})
_STORE_KEYS = frozenset({"store_id", "incarnation", "namespace"})
_HORIZON_KEYS = frozenset({"clock_id", "ticks"})
_CLOCK_KEYS = frozenset({"clock_id", "kind", "resolution", "origin"})


@dataclass(frozen=True)
class TelemetryReadPolicy:
    """P0 reader service policy (not contract): returned points, interval, window scan."""

    max_points_limit: int = 1000
    max_interval_ticks: int = 2_592_000      # 30 simulated days at 1 s/tick
    max_scan_records: int = 50_000

    def __post_init__(self) -> None:
        _checked_int("max_points_limit", self.max_points_limit, minimum=1)
        _checked_int("max_interval_ticks", self.max_interval_ticks, minimum=0)
        _checked_int("max_scan_records", self.max_scan_records, minimum=1)


def context_ref_for(graph_source: ContextSourceRef) -> str:
    """Exact ref of the attested AGENT PROCESS_GRAPH source the bindings come from.

    Not a PlantContextRevision; P1.3 maps it through this recorded source ref.
    """
    if not isinstance(graph_source, ContextSourceRef) or graph_source.kind != "PROCESS_GRAPH":
        raise TelemetryError("context_ref requires the PROCESS_GRAPH ContextSourceRef")
    return f"context-source:{graph_source.source_id}:sha256:{checksum(to_jsonable(graph_source))}"


def snapshot_json(snapshot: TelemetryReadSnapshot) -> dict[str, Any]:
    """The public snapshot object: the core snapshot body plus its ``snapshot_ref``."""
    return {**snapshot.as_json(), "snapshot_ref": snapshot.snapshot_ref}


def sample_json(sample: SignalSample) -> dict[str, Any]:
    """Public sample shape. ``ingest_time`` is deliberately absent: it is diagnostic
    acceptance-clock data and would expose prepare timing to AGENT readers."""
    return {"source_id": sample.source_id, "signal_id": sample.signal_id,
            "sequence": sample.sequence, "event_ticks": sample.event_time.ticks,
            "value": sample.value, "quality": str(sample.quality)}


def utc_ticks(timestamp: Any) -> int:
    """Milliseconds since the UTC epoch of a trusted ISO-8601 timestamp with offset."""
    if not isinstance(timestamp, str):
        raise TelemetryError("trusted clock must return an ISO-8601 timestamp")
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise TelemetryError("trusted clock must return an ISO-8601 timestamp") from None
    if moment.utcoffset() is None:
        raise TelemetryError("trusted clock timestamp must carry a UTC offset")
    return (moment - _EPOCH) // timedelta(milliseconds=1)


def _exact(value: Any, keys: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise TelemetryRequestError(f"snapshot {name} has an unexpected shape")
    return value


def _request_int(name: str, value: Any) -> int:
    try:
        return _checked_int(name, value, minimum=0)
    except TelemetryError:
        raise TelemetryRequestError(f"{name} must be a non-negative integer") from None


class TelemetrySealed(TelemetryError):
    """Ingestion was attempted after the run telemetry was sealed at READY."""


class RunTelemetry:
    """Source, store, ingestor and reader of one prepared P0 session (one incarnation)."""

    def __init__(self, *, run_id: str, graph: ProcessGraph, graph_source: ContextSourceRef,
                 visibility_policy_ref: str, clock: Callable[[], str],
                 policy: TelemetryReadPolicy | None = None) -> None:
        # The trusted host (RunManager) validates the policy type.
        policy = policy or TelemetryReadPolicy()
        self._token = secrets.token_hex(16)   # opaque; never the attempt number
        token = self._token
        self.policy = policy
        self.source = TEPSimulationSource(graph, source_id=f"tep-reference:{run_id}:{token}",
                                          clock_id=f"tep-sim:{run_id}:{token}",
                                          clock_origin=f"tep-reset:{run_id}:{token}")
        self.store = InMemoryTimeSeriesStore(
            store_id=TELEMETRY_STORE_ID, namespace=f"p0-run:{run_id}:{token}",
            context_ref=context_ref_for(graph_source),
            visibility_policy_ref=visibility_policy_ref, event_clock=self.source.event_clock,
            ingest_clock=INGEST_CLOCK, bindings=self.source.store_bindings(),
            incarnation=f"inc-{token}")
        self.reader = TimeSeriesReader(self.store, max_interval_ticks=policy.max_interval_ticks,
                                       max_scan_records=policy.max_scan_records,
                                       max_points_limit=policy.max_points_limit)
        self._ingestor = TelemetryIngestor(self.store)
        self._clock = clock
        self._lock = threading.Lock()
        self._cursor = 0
        self._sealed = False

    @property
    def source_id(self) -> str:
        return self.source.source_id

    @property
    def sealed(self) -> bool:
        with self._lock:
            return self._sealed

    # -- ingestion (trusted, pre-READY) -------------------------------------------------
    def _ingest_time(self) -> TimePoint:
        return TimePoint(INGEST_CLOCK.clock_id, utc_ticks(self._clock()))

    def _commit(self, batch: Sequence[SourceObservation], ingest_time: TimePoint
                ) -> AppendResult:
        return self._ingestor.ingest(batch, ingest_time=ingest_time)

    def sync_reference_history(self, history: Sequence[Mapping[str, Any]]) -> int:
        """Translate and ingest ``history[cursor:]`` in order; return the committed K.

        One trusted clock sample serves the whole sync, so ingest times carry no
        per-record timing. Raises ``TelemetrySealed`` after ``seal()``.
        """
        with self._lock:
            if self._sealed:
                raise TelemetrySealed("run telemetry is sealed")
            if len(history) < self._cursor:
                raise TelemetryError("reference history shrank below the ingested prefix")
            ingest_time = self._ingest_time()
            for record in history[self._cursor:]:
                self._commit(self.source.translate_sanitized(record), ingest_time)
                self._cursor += 1
            return self.store.current_ingest_sequence

    def seal(self) -> None:
        """Close ingestion; P0 calls this when the session is assembled, before READY."""
        with self._lock:
            self._sealed = True

    # -- harness-only seam (acceptance tests; never production) --------------------------
    def _harness_translate(self, record: Mapping[str, Any]) -> tuple[SourceObservation, ...]:
        """Trusted test harness: translate a sanitized record with the session's source."""
        return self.source.translate_sanitized(record)

    def _harness_deliver(self, batch: Sequence[SourceObservation]) -> AppendResult:
        """Trusted test harness: commit one batch even after seal (dirty-delivery tests)."""
        with self._lock:
            return self._commit(batch, self._ingest_time())

    # -- snapshots ----------------------------------------------------------------------
    def snapshot(self, *, event_horizon_ticks: int | None = None,
                 ingest_sequence: int | None = None) -> TelemetryReadSnapshot:
        """Freeze (T, K) once. K defaults to now; T to the K frontier, or tick 0 if empty."""
        if ingest_sequence is None:
            ingest_sequence = self.store.current_ingest_sequence
        _request_int("ingest_sequence", ingest_sequence)
        if event_horizon_ticks is None:
            frontier = self.store.event_frontier(ingest_sequence)
            event_horizon_ticks = 0 if frontier is None else frontier.ticks
        _request_int("event_horizon_ticks", event_horizon_ticks)
        return self.store.snapshot(TimePoint(self.store.event_clock.clock_id,
                                             event_horizon_ticks), ingest_sequence)

    def resolve_snapshot(self, value: Any) -> TelemetryReadSnapshot:
        """Rebuild an exact public snapshot object, check its ref, resolve it here."""
        body = _exact(value, SNAPSHOT_KEYS, "object")
        if body["contract"] != TELEMETRY_CONTRACT_VERSION:
            raise SnapshotUnavailable("unsupported snapshot contract")
        store = _exact(body["store"], _STORE_KEYS, "store")
        horizon = _exact(body["event_horizon"], _HORIZON_KEYS, "event_horizon")
        clock = _exact(body["event_clock"], _CLOCK_KEYS, "event_clock")
        resolution = parse_event_resolution(clock["resolution"])
        # Validate client fields explicitly. Constructor/store invariant failures below
        # are server faults and must never be reclassified by a broad base-class catch.
        try:
            for name in _STORE_KEYS:
                _checked_id(name, store[name])
            for name, value in (("clock_id", horizon["clock_id"]),
                                ("clock_id", clock["clock_id"]),
                                ("context_ref", body["context_ref"]),
                                ("visibility_policy_ref", body["visibility_policy_ref"])):
                _checked_id(name, value)
            if not isinstance(clock["kind"], str):
                raise TelemetryRequestError("snapshot clock kind must be a string")
            kind = _coerce(ClockKind, "kind", clock["kind"])
            if clock["origin"] is not None:
                _checked_id("origin", clock["origin"])
            elif kind in (ClockKind.SIMULATION, ClockKind.UTC):
                raise TelemetryRequestError("snapshot clock requires an origin")
            _checked_int("ingest_sequence", body["ingest_sequence"], minimum=0)
            _checked_int("event_horizon_ticks", horizon["ticks"], minimum=0)
        except TelemetryError:
            raise TelemetryRequestError("invalid snapshot arguments") from None
        pinned = TelemetryReadSnapshot(
            StoreRef(store["store_id"], store["incarnation"], store["namespace"]),
            body["ingest_sequence"], TimePoint(horizon["clock_id"], horizon["ticks"]),
            ClockDescriptor(clock["clock_id"], kind, resolution, clock["origin"]),
            body["context_ref"], body["visibility_policy_ref"])
        if body["snapshot_ref"] != pinned.snapshot_ref:
            raise SnapshotUnavailable("snapshot reference does not match its contents")
        return self.store.resolve(pinned)

    def history_bounds(self, snapshot: TelemetryReadSnapshot, event_start_ticks: Any,
                       event_end_ticks: Any) -> tuple[int, int]:
        """Default end = T and start = max(0, end - max_interval_ticks), resolved once."""
        end = (snapshot.event_horizon.ticks if event_end_ticks is None
               else _request_int("event_end_ticks", event_end_ticks))
        start = (max(0, end - self.policy.max_interval_ticks) if event_start_ticks is None
                 else _request_int("event_start_ticks", event_start_ticks))
        return start, end
