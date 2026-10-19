"""Generic Operational State: snapshot-bound current, freshness, endpoint trend and extrema.

LOS-A-S1 of ``docs/specs/live-operational-state-v0.md`` (§Operational State contracts,
§Window selection and deterministic policy, §Initial Q2 policies (OD-1), §Quality,
gaps and freshness, §Serving and time consistency (OD-5, direct service first))::

    caller -- exact snapshot + finite window + policy/context refs --> OperationalStateService
    OperationalStateService -- public current/history queries --> P0 query boundary
    P0 query boundary -- unreduced complete window --> OD-1 interpreter (pure)
    OD-1 interpreter -- typed features + exact sample/window/policy refs --> SignalOperationalState

The service reads only through an injected boundary that exposes the public P0
telemetry queries and the pinned context; it never opens a store, private world state
or a cache. Every request pins one TelemetryReadSnapshot (T, K); no read resolves
"latest" and no default reaches past T. Source, signal and unit ids are opaque. This
module imports the standard library and the generic telemetry core only; process-pack
policy values and graph-derived signal descriptors are injected.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Protocol, TypeVar

from .telemetry import (ClockMismatch, Quality, ResourceLimitExceeded, SampleIdentity,
                        SnapshotUnavailable, TelemetryError, TelemetryRequestError,
                        UnknownBinding, parse_event_resolution)

OPERATIONAL_STATE_VERSION = "tep-agent-lab.operational-state/v0"
OD1_ALGORITHM = "operational-state.od1.endpoint-secant-strict-good/v0"
# Exact rational arithmetic over unrounded inputs and the exact clock resolution; one
# final IEEE-754 binary64 rounding of the rate; nonfinite results fail closed. Values
# serialize as shortest round-trip JSON numbers; classification uses the exact rational.
NUMERIC_METHOD = "operational-state.numeric.exact-rational-binary64/v0"
WINDOW_READ_POLICY = "operational-state.window.unreduced-complete/v0"
EXPLICIT_EMPTY = "EXPLICIT_EMPTY"
_ID_LIMIT = 256
# Telemetry reads one request performs: the current read and the complete-window history.
_READS_PER_REQUEST = 2
_SAMPLE_KEYS = frozenset({"source_id", "signal_id", "sequence", "event_ticks", "value",
                          "quality"})


# -- typed failures (messages are fixed; they never echo ids, values or exceptions) ------
class InvalidOperationalStateRequest(TelemetryRequestError):
    """Caller-supplied arguments are malformed or out of the pinned snapshot's range."""


class UnknownRun(LookupError):
    """No run with this id is available to the caller."""


class ContextUnavailable(LookupError):
    """A pinned plant/engineering context ref is unavailable or inconsistent."""


class PolicyUnavailable(LookupError):
    """The exact interpretation policy ref is not registered with this service."""


class IncompatiblePolicy(ValueError):
    """Policy configuration is invalid or does not apply to the selected signal."""


class NonfiniteDerivation(ArithmeticError):
    """A derived value is not finite; no value is served (fails closed)."""


class InconsistentRead(RuntimeError):
    """A public read answered another snapshot, binding or window, or a boundary failed
    outside the service families (internal fault)."""


class VisibilityViolation(RuntimeError):
    """The AGENT scope or the result leakage screen rejected the request."""


# Boundary and public-read failures keep their family and lose their text: messages never
# echo ids, values, hidden counts, paths or exception text, and the original exception
# is not chained. Anything outside these families is an internal fault.
_PUBLIC_FAILURES: tuple[tuple[type[Exception], type[Exception], str], ...] = (
    (TelemetryRequestError, InvalidOperationalStateRequest, "invalid operational state request"),
    (UnknownRun, UnknownRun, "unknown run"),
    (UnknownBinding, UnknownBinding, "unknown or ambiguous signal binding"),
    (ClockMismatch, ClockMismatch, "incompatible event clock"),
    (SnapshotUnavailable, SnapshotUnavailable, "telemetry snapshot is unavailable"),
    (ContextUnavailable, ContextUnavailable, "plant context is unavailable"),
    (PolicyUnavailable, PolicyUnavailable, "interpretation policy is unavailable"),
    (IncompatiblePolicy, IncompatiblePolicy, "interpretation policy does not apply"),
    (VisibilityViolation, VisibilityViolation, "AGENT visibility violation"),
    (ResourceLimitExceeded, ResourceLimitExceeded, "request exceeds a declared resource bound"),
)
_T = TypeVar("_T")


def _guarded(call: Callable[[], _T]) -> _T:
    """Run one boundary or public-read call; re-raise failures redacted by family."""
    try:
        return call()
    except Exception as error:
        failure = error
    # Raised outside the handler so the original is neither __cause__ nor __context__.
    for family, public, message in _PUBLIC_FAILURES:
        if isinstance(failure, family):
            raise public(message)
    raise InconsistentRead("internal fault in a boundary or public read")


# -- vocabulary ------------------------------------------------------------------------
class FeatureStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NO_DATA = "NO_DATA"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    QUALITY_UNUSABLE = "QUALITY_UNUSABLE"
    GAP_EXCEEDS_POLICY = "GAP_EXCEEDS_POLICY"
    UNSUPPORTED_VALUE_KIND = "UNSUPPORTED_VALUE_KIND"
    NO_CANONICAL_LIMIT = "NO_CANONICAL_LIMIT"
    UNSUPPORTED_LIMIT_PREDICATE = "UNSUPPORTED_LIMIT_PREDICATE"


class Feature(StrEnum):
    CURRENT = "current"
    STALENESS = "staleness"
    RATE_OF_CHANGE = "rate_of_change"
    TREND = "trend"
    WINDOW_MIN = "window_min"
    WINDOW_MAX = "window_max"
    THRESHOLD = "threshold"


class Freshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"


class Trend(StrEnum):
    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"


class ValueKind(StrEnum):
    NUMERIC = "NUMERIC"
    BOOLEAN = "BOOLEAN"
    STRING = "STRING"


AS_OF_HORIZON = "AS_OF_HORIZON"
WINDOW = "WINDOW"


def _canonical(value: Any) -> bytes:
    # Same settings as persistence.canonical_json and TelemetryReadSnapshot.snapshot_ref.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(name: str, value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > _ID_LIMIT
            or value != value.strip() or not value.isprintable()):
        raise IncompatiblePolicy(f"{name} must be a non-empty printable identifier")
    return value


def _count(name: str, value: Any, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise IncompatiblePolicy(f"{name} must be an integer >= {minimum}")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


# -- policy ----------------------------------------------------------------------------
@dataclass(frozen=True)
class InterpretationPolicyRef:
    """Exact immutable policy identity: id, version and parameter-content checksum."""

    policy_id: str
    version: str
    checksum: str

    @property
    def ref(self) -> str:
        return f"interpretation-policy:{self.policy_id}@{self.version}:sha256:{self.checksum}"


@dataclass(frozen=True)
class InterpretationPolicy:
    """Versioned OD-1 policy record with OD-2 parameters (values come from a domain pack).

    ``trend_tolerance_per_second`` maps an exact unit marker to a non-negative finite
    tolerance in that unit per second. A unit without an entry cannot borrow one.
    Bounds: ``max_window_ticks`` (inclusive span end - start), ``max_input_points``
    (complete-window points requested from the reader), ``max_aggregate_candidates``
    (candidate records across every read of one request, as the public reads report
    them: the current prefix plus the history window eligible counts),
    ``max_index_work_per_read`` (index probes and tail checks one public read may make;
    the host reader's own bound must not exceed it) and ``max_aggregate_index_work``
    (that host bound times the reads of one request), ``max_response_bytes`` (one
    serialized result). S1 serves one signal per request
    with no peers or transitions.
    """

    policy_id: str
    version: str
    default_window_ticks: int
    max_window_ticks: int
    max_input_points: int
    max_aggregate_candidates: int
    max_index_work_per_read: int
    max_aggregate_index_work: int
    max_gap_ticks: int
    stale_after_ticks: int
    trend_tolerance_per_second: Mapping[str, float]
    max_response_bytes: int
    value_kinds: tuple[ValueKind, ...] = (ValueKind.NUMERIC,)
    max_signals_per_request: int = 1
    max_peers: int = 0
    max_transitions: int = 0
    algorithm: str = OD1_ALGORITHM
    numeric_method: str = NUMERIC_METHOD
    _checksum: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _text("policy_id", self.policy_id)
        _text("version", self.version)
        for name, minimum in (("default_window_ticks", 0), ("max_window_ticks", 0),
                              ("max_input_points", 1), ("max_aggregate_candidates", 2),
                              ("max_index_work_per_read", 1), ("max_aggregate_index_work", 2),
                              ("max_gap_ticks", 1), ("stale_after_ticks", 0),
                              ("max_response_bytes", 1), ("max_signals_per_request", 1),
                              ("max_peers", 0), ("max_transitions", 0)):
            # Strict int: a bool or float equal to a valid value would change the
            # checksum (and hash) without changing equality.
            _count(name, getattr(self, name), minimum)
        if self.default_window_ticks > self.max_window_ticks:
            raise IncompatiblePolicy("default window exceeds the maximum window")
        if self.max_aggregate_candidates < 2 * self.max_input_points:
            # A complete window's samples are also in the current prefix at or before T.
            raise IncompatiblePolicy("aggregate candidate bound cannot serve a complete window")
        if self.max_aggregate_index_work < _READS_PER_REQUEST * self.max_index_work_per_read:
            raise IncompatiblePolicy("aggregate index-work bound cannot serve one request")
        if (self.max_signals_per_request != 1 or self.max_peers != 0
                or self.max_transitions != 0 or self.algorithm != OD1_ALGORITHM
                or self.numeric_method != NUMERIC_METHOD
                or tuple(self.value_kinds) != (ValueKind.NUMERIC,)):
            raise IncompatiblePolicy("unsupported interpretation policy configuration")
        if not isinstance(self.trend_tolerance_per_second, Mapping) \
                or not self.trend_tolerance_per_second:
            raise IncompatiblePolicy("trend tolerances must name at least one unit")
        tolerances = {}
        for unit, tolerance in self.trend_tolerance_per_second.items():
            _text("unit", unit)
            if (type(tolerance) not in (int, float) or not math.isfinite(tolerance)
                    or tolerance < 0):
                raise IncompatiblePolicy("trend tolerance must be a non-negative finite number")
            # One canonical number per value (int -> float, -0.0 -> 0.0), so equal
            # policies have one checksum and one hash.
            tolerances[unit] = float(tolerance) + 0.0
        object.__setattr__(self, "trend_tolerance_per_second",
                           MappingProxyType(dict(sorted(tolerances.items()))))
        object.__setattr__(self, "value_kinds", tuple(self.value_kinds))
        object.__setattr__(self, "_checksum", _sha256(self.parameters()))

    def __hash__(self) -> int:
        # Equal policies have equal parameter checksums; the tolerance mapping is not hashed.
        return hash(self._checksum)

    @property
    def max_inline_sample_refs(self) -> int:
        """Distinct full sample identities one result may emit: the window plus current."""
        return self.max_input_points + 1

    def parameters(self) -> dict[str, Any]:
        """Typed parameters, applicable value kinds/units and method versions (JSON)."""
        return {"policy_id": self.policy_id, "version": self.version,
                "algorithm": self.algorithm, "numeric_method": self.numeric_method,
                "window_read_policy": WINDOW_READ_POLICY,
                "default_window_ticks": self.default_window_ticks,
                "max_window_ticks": self.max_window_ticks,
                "max_input_points": self.max_input_points,
                "max_aggregate_candidates": self.max_aggregate_candidates,
                "max_index_work_per_read": self.max_index_work_per_read,
                "max_aggregate_index_work": self.max_aggregate_index_work,
                "max_gap_ticks": self.max_gap_ticks,
                "stale_after_ticks": self.stale_after_ticks,
                "trend_tolerance_per_second": dict(self.trend_tolerance_per_second),
                "applicable_value_kinds": [str(kind) for kind in self.value_kinds],
                "applicable_units": sorted(self.trend_tolerance_per_second),
                "max_signals_per_request": self.max_signals_per_request,
                "max_peers": self.max_peers, "max_transitions": self.max_transitions,
                "max_inline_sample_refs": self.max_inline_sample_refs,
                "max_response_bytes": self.max_response_bytes}

    @property
    def ref(self) -> InterpretationPolicyRef:
        return InterpretationPolicyRef(self.policy_id, self.version, self._checksum)

    def tolerance(self, unit: str | None) -> float:
        if unit is None or unit not in self.trend_tolerance_per_second:
            raise IncompatiblePolicy("policy has no trend tolerance for the signal unit")
        return self.trend_tolerance_per_second[unit]


# -- request refs ----------------------------------------------------------------------
@dataclass(frozen=True)
class SignalDescriptor:
    """Declared value kind and exact unit marker of one opaque signal (from a domain pack)."""

    value_kind: ValueKind
    unit: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_kind", ValueKind(self.value_kind))
        if self.unit is not None:
            _text("unit", self.unit)


@dataclass(frozen=True)
class PlantContextRef:
    """Exact pinned plant context: an opaque kind and immutable ref (never "latest")."""

    kind: str
    ref: str

    def __post_init__(self) -> None:
        for name in ("kind", "ref"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise InvalidOperationalStateRequest("plant context ref fields must be strings")

    def as_json(self) -> dict[str, str]:
        return {"kind": self.kind, "ref": self.ref}


@dataclass(frozen=True)
class EngineeringSelection:
    """Bounded exact engineering/rule-set refs, or the explicit empty selection."""

    refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if (isinstance(self.refs, (str, bytes)) or not isinstance(self.refs, Iterable)):
            raise InvalidOperationalStateRequest("engineering refs must be a collection")
        refs = tuple(self.refs)
        if not all(isinstance(ref, str) and ref for ref in refs):
            raise InvalidOperationalStateRequest("engineering refs must be strings")
        object.__setattr__(self, "refs", refs)

    @classmethod
    def empty(cls) -> "EngineeringSelection":
        return cls(())

    def as_json(self) -> dict[str, Any]:
        return {"kind": EXPLICIT_EMPTY if not self.refs else "EXACT_REFS",
                "refs": list(self.refs)}


# -- results ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PublicSample:
    """Public canonical sample fields; diagnostic ingest time is never present."""

    identity: SampleIdentity
    event_ticks: int
    value: Any
    quality: Quality

    def as_json(self) -> dict[str, Any]:
        return {"source_id": self.identity.source_id, "signal_id": self.identity.signal_id,
                "sequence": self.identity.sequence, "event_ticks": self.event_ticks,
                "value": self.value, "quality": str(self.quality)}


def _identity_json(identity: SampleIdentity) -> dict[str, Any]:
    return {"source_id": identity.source_id, "signal_id": identity.signal_id,
            "sequence": identity.sequence}


@dataclass(frozen=True)
class FeatureResult:
    """One typed feature: a value only when AVAILABLE, with exact refs either way.

    ``value_samples`` are the samples whose values define the value. ``reason_positions``
    index the window's ``sample_identities`` (the single list of full identities) and
    cite the samples that made the feature unavailable. Window features evaluate every
    sample of ``window_ref``; AS_OF_HORIZON features describe T.
    """

    feature: Feature
    status: FeatureStatus
    basis: str
    value: Any
    value_samples: tuple[SampleIdentity, ...]
    reason_positions: tuple[int, ...]
    detail: Mapping[str, Any]
    policy_ref: str
    window_ref: str | None
    citations: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        value = self.value.as_json() if isinstance(self.value, PublicSample) else (
            str(self.value) if isinstance(self.value, StrEnum) else self.value)
        return {"feature": str(self.feature), "status": str(self.status), "basis": self.basis,
                "value": value,
                "value_samples": [_identity_json(item) for item in self.value_samples],
                "reason_sample_positions": list(self.reason_positions),
                "detail": _thaw(self.detail), "policy_ref": self.policy_ref,
                "window_ref": self.window_ref, "citations": list(self.citations)}


@dataclass(frozen=True)
class WindowSelection:
    """Immutable query provenance of the complete feature window (not a context assembly)."""

    window_ref: str
    selection_checksum: str
    sample_identities: tuple[SampleIdentity, ...]
    body: Mapping[str, Any]
    diagnostics: Mapping[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {**_thaw(self.body), "window_ref": self.window_ref,
                "selection_checksum": self.selection_checksum,
                "sample_identities": [_identity_json(item) for item in self.sample_identities],
                "diagnostics": _thaw(self.diagnostics)}


@dataclass(frozen=True)
class SignalOperationalState:
    """Deterministic derivation record for one signal at one snapshot/window/policy."""

    run_id: str
    source_id: str
    signal_id: str
    features: Mapping[Feature, FeatureResult]
    window: WindowSelection
    body: Mapping[str, Any]
    derivation_ref: str

    def reason_samples(self, feature: Feature) -> tuple[SampleIdentity, ...]:
        """Exact identities a feature cites as unusable, resolved from the window list."""
        return tuple(self.window.sample_identities[position]
                     for position in self.features[feature].reason_positions)

    def as_json(self) -> dict[str, Any]:
        return {**_thaw(self.body), "window": self.window.as_json(),
                "features": {str(name): result.as_json()
                             for name, result in self.features.items()},
                "derivation_ref": self.derivation_ref}


# -- injected public boundary ----------------------------------------------------------
@dataclass(frozen=True)
class HostReadLimits:
    """Per-read limits of the host reader behind the public reads (host configuration).

    ``max_points``: points one history read may return; ``max_interval_ticks``: the
    largest ``end - start`` of one history read; ``max_index_work``: index probes and
    tail checks one read may make. The service validates the values before any read.
    """

    max_points: int
    max_interval_ticks: int
    max_index_work: int


class TelemetryQueries(Protocol):
    """The public snapshot-bound P0 telemetry queries (AGENT scope)."""

    def telemetry_current(self, signal_id: str, *,
                          snapshot: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def telemetry_history(self, signal_id: str, *, snapshot: Mapping[str, Any],
                          event_start_ticks: int, event_end_ticks: int,
                          max_points: int) -> Mapping[str, Any]: ...


class OperationalStateBoundary(Protocol):
    """Composition seam: public queries, pinned plant context and the AGENT screen.

    ``queries`` is the object this boundary returned from ``queries``; its other public
    projections (graph, inventory) are boundary-specific, hence typed ``Any`` here.
    Failures should use the service families; the service replaces every boundary or
    read message with a fixed one, and maps any other exception to an internal fault.
    """

    def host_read_limits(self) -> HostReadLimits:
        """Host configuration: the per-read limits of the reader behind the reads."""

    def queries(self, run_id: str) -> TelemetryQueries: ...

    def signal_descriptor(self, queries: Any, signal_id: str) -> SignalDescriptor: ...

    def plant_context(self, queries: Any, ref: PlantContextRef,
                      snapshot: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def screen(self, result: Mapping[str, Any]) -> None: ...


# -- OD-1 interpretation (pure) ---------------------------------------------------------
def _public_sample(value: Any) -> PublicSample:
    """Strict public sample shape; anything else is a server fault, never interpreted."""
    if not isinstance(value, Mapping) or set(value) != _SAMPLE_KEYS:
        raise InconsistentRead("public sample has an unexpected shape")
    scalar = value["value"]
    if (not all(isinstance(value[name], str) for name in ("source_id", "signal_id"))
            or any(type(value[name]) is not int or value[name] < 0
                   for name in ("sequence", "event_ticks"))
            or not (scalar is None or type(scalar) in (bool, int, str)
                    or (type(scalar) is float and math.isfinite(scalar)))
            or value["quality"] not in tuple(Quality)):
        raise InconsistentRead("public sample has an unexpected shape")
    return PublicSample(SampleIdentity(value["source_id"], value["signal_id"], value["sequence"]),
                        value["event_ticks"], scalar, Quality(value["quality"]))


def _is_number(value: Any) -> bool:
    return type(value) in (int, float)


@dataclass(frozen=True)
class _Context:
    policy: InterpretationPolicy
    descriptor: SignalDescriptor
    resolution: Fraction
    window_ref: str
    citations: tuple[str, ...]

    def result(self, feature: Feature, status: FeatureStatus, *, value: Any = None,
               value_samples: Sequence[SampleIdentity] = (),
               reason_positions: Sequence[int] = (),
               detail: Mapping[str, Any] | None = None, basis: str = WINDOW,
               citations: Sequence[str] = ()) -> FeatureResult:
        return FeatureResult(feature, status, basis, value, tuple(value_samples),
                             tuple(reason_positions), _freeze(detail or {}),
                             self.policy.ref.ref,
                             self.window_ref if basis == WINDOW else None,
                             (*self.citations, *citations))


def _window_status(samples: Sequence[PublicSample], kind: ValueKind
                   ) -> tuple[FeatureStatus, tuple[int, ...]] | None:
    """OD-1 precedence before sample-count checks: declared kind, no data, unusable
    quality/null, then the observed kind of the remaining GOOD non-null samples."""
    if kind != ValueKind.NUMERIC:
        return FeatureStatus.UNSUPPORTED_VALUE_KIND, ()
    if not samples:
        return FeatureStatus.NO_DATA, ()
    unusable = tuple(position for position, s in enumerate(samples)
                     if s.quality != Quality.GOOD or s.value is None)
    if unusable:
        return FeatureStatus.QUALITY_UNUSABLE, unusable
    wrong = tuple(position for position, s in enumerate(samples) if not _is_number(s.value))
    if wrong:
        return FeatureStatus.UNSUPPORTED_VALUE_KIND, wrong
    return None


def _spans(samples: Sequence[PublicSample], start: int, end: int, max_gap: int
           ) -> tuple[list[dict[str, Any]], tuple[int, ...]]:
    """Unobserved spans above ``max_gap`` over the full window, with bounding positions.

    Leading (window start to first observed tick) and trailing (last observed tick to
    window end) spans count like adjacent gaps; their window boundary is in the detail.
    """
    ticks = sorted({sample.event_ticks for sample in samples})
    edges = [("LEADING", start, ticks[0]),
             *(("INTERIOR", left, right) for left, right in zip(ticks, ticks[1:])),
             ("TRAILING", ticks[-1], end)]
    gaps, positions = [], []
    for kind, left, right in edges:
        if right - left <= max_gap:
            continue
        gaps.append({"kind": kind, "from_event_ticks": left, "to_event_ticks": right,
                     "span_ticks": right - left})
        if kind != "LEADING":   # last read-order sample at the left tick
            positions.append(max(i for i, s in enumerate(samples) if s.event_ticks == left))
        if kind != "TRAILING":  # first read-order sample at the right tick
            positions.append(min(i for i, s in enumerate(samples) if s.event_ticks == right))
    return gaps, tuple(sorted(set(positions)))


def _secant(samples: Sequence[PublicSample], start: int, end: int,
            context: _Context) -> list[FeatureResult]:
    blocked = _window_status(samples, context.descriptor.value_kind)
    detail: dict[str, Any] = {}
    if blocked is None:
        if len({sample.event_ticks for sample in samples}) < 2:
            blocked = FeatureStatus.INSUFFICIENT_DATA, ()
        else:
            gaps, bounding = _spans(samples, start, end, context.policy.max_gap_ticks)
            if gaps:
                blocked = FeatureStatus.GAP_EXCEEDS_POLICY, bounding
                detail = {"gaps": gaps, "max_gap_ticks": context.policy.max_gap_ticks}
    if blocked is not None:
        status, reasons = blocked
        return [context.result(feature, status, reason_positions=reasons, detail=detail)
                for feature in (Feature.RATE_OF_CHANGE, Feature.TREND)]
    first_tick, last_tick = samples[0].event_ticks, samples[-1].event_ticks
    # Last canonical read-order sample at each endpoint tick.
    first = [s for s in samples if s.event_ticks == first_tick][-1]
    last = samples[-1]
    elapsed = (last_tick - first_tick) * context.resolution
    exact = (Fraction(last.value) - Fraction(first.value)) / elapsed
    try:
        rate = float(exact)
    except OverflowError:
        raise NonfiniteDerivation("derived rate is not finite") from None
    if not math.isfinite(rate):
        raise NonfiniteDerivation("derived rate is not finite")
    unit = context.descriptor.unit
    tolerance = context.policy.tolerance(unit)
    bound = Fraction(tolerance)
    trend = (Trend.RISING if exact > bound else Trend.FALLING if exact < -bound
             else Trend.STABLE)
    endpoints = {"first_event_ticks": first_tick, "last_event_ticks": last_tick,
                 "elapsed_seconds": f"{elapsed.numerator}/{elapsed.denominator}",
                 "unit": unit}
    pair = (first.identity, last.identity)
    return [context.result(Feature.RATE_OF_CHANGE, FeatureStatus.AVAILABLE, value=rate,
                           value_samples=pair,
                           detail={**endpoints, "rate_unit": f"{unit}/s"}),
            context.result(Feature.TREND, FeatureStatus.AVAILABLE, value=trend,
                           value_samples=pair,
                           detail={**endpoints, "rate": rate,
                                   "tolerance_per_second": tolerance,
                                   "semantics": "endpoint secant over the stated window"})]


def _extrema(samples: Sequence[PublicSample], context: _Context) -> list[FeatureResult]:
    blocked = _window_status(samples, context.descriptor.value_kind)
    if blocked is not None:
        status, reasons = blocked
        return [context.result(feature, status, reason_positions=reasons)
                for feature in (Feature.WINDOW_MIN, Feature.WINDOW_MAX)]
    results = []
    for feature, better in ((Feature.WINDOW_MIN, lambda a, b: a < b),
                            (Feature.WINDOW_MAX, lambda a, b: a > b)):
        best = samples[0]
        for sample in samples[1:]:
            if better(sample.value, best.value):   # ties keep the first in read order
                best = sample
        results.append(context.result(feature, FeatureStatus.AVAILABLE, value=best.value,
                                      value_samples=(best.identity,),
                                      detail={"event_ticks": best.event_ticks,
                                              "unit": context.descriptor.unit}))
    return results


def _diagnostics(samples: Sequence[PublicSample], start: int, end: int,
                 policy: InterpretationPolicy) -> dict[str, Any]:
    ticks = sorted({sample.event_ticks for sample in samples})
    gaps = [right - left for left, right in zip(ticks, ticks[1:])]
    counts = {str(quality): 0 for quality in Quality}
    for sample in samples:
        counts[str(sample.quality)] += 1
    return {"sample_count": len(samples), "distinct_ticks": len(ticks),
            "observed_first_ticks": ticks[0] if ticks else None,
            "observed_last_ticks": ticks[-1] if ticks else None,
            "leading_unobserved_ticks": ticks[0] - start if ticks else None,
            "trailing_unobserved_ticks": end - ticks[-1] if ticks else None,
            "max_adjacent_gap_ticks": max(gaps) if gaps else None,
            "max_gap_ticks": policy.max_gap_ticks,
            "quality_counts": counts,
            "null_count": sum(1 for sample in samples if sample.value is None),
            "coverage_semantics": "retained accepted records; not proof of a gap-free stream"}


# -- service ---------------------------------------------------------------------------
def _request_ticks(name: str, value: Any) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise InvalidOperationalStateRequest(f"{name} must be a non-negative integer or None")
    return value


def _snapshot_coordinates(snapshot: Any) -> tuple[int, Fraction, str]:
    """(T, exact resolution, context ref) of a public snapshot object; shape only."""
    invalid = InvalidOperationalStateRequest("a public telemetry snapshot object is required")
    if not isinstance(snapshot, Mapping):
        raise invalid
    try:
        horizon = snapshot["event_horizon"]["ticks"]
        resolution = parse_event_resolution(snapshot["event_clock"]["resolution"])
        texts = (snapshot["context_ref"], snapshot["snapshot_ref"],
                 snapshot["visibility_policy_ref"], snapshot["event_clock"]["clock_id"])
    except (TypeError, KeyError, TelemetryError):
        raise invalid from None
    if type(horizon) is not int or horizon < 0 or not all(isinstance(t, str) for t in texts):
        raise invalid
    return horizon, resolution, texts[0]


class OperationalStateService:
    """Direct transport-neutral ``get_operational_state`` (OD-5); no caching, no writes."""

    def __init__(self, boundary: OperationalStateBoundary,
                 policies: Iterable[InterpretationPolicy]) -> None:
        self._boundary = boundary
        registered: dict[tuple[str, str], InterpretationPolicy] = {}
        for policy in policies:
            if not isinstance(policy, InterpretationPolicy):
                raise IncompatiblePolicy("InterpretationPolicy required")
            key = (policy.policy_id, policy.version)
            if key in registered:
                raise IncompatiblePolicy("policy id and version must be unique")
            registered[key] = policy
        self._policies = MappingProxyType(registered)

    @property
    def policies(self) -> tuple[InterpretationPolicy, ...]:
        return tuple(self._policies.values())

    def _policy(self, refs: Any) -> InterpretationPolicy:
        if (not isinstance(refs, tuple) or len(refs) != 1
                or not isinstance(refs[0], InterpretationPolicyRef)):
            raise InvalidOperationalStateRequest(
                "exactly one exact InterpretationPolicyRef is required in this slice")
        ref = refs[0]
        policy = self._policies.get((ref.policy_id, ref.version))
        if policy is None or policy.ref != ref:
            raise PolicyUnavailable("interpretation policy is unavailable")
        return policy

    def get_operational_state(self, run_id: str, signal_id: str, *,
                              snapshot: Mapping[str, Any],
                              plant_context_ref: PlantContextRef,
                              engineering_selection: EngineeringSelection,
                              interpretation_policy_refs: tuple[InterpretationPolicyRef, ...],
                              event_start_ticks: int | None = None,
                              event_end_ticks: int | None = None,
                              source_id: str | None = None) -> SignalOperationalState:
        # 1. Shape, policy and bounds: all validated before any telemetry read.
        if not isinstance(signal_id, str) or (source_id is not None
                                              and not isinstance(source_id, str)):
            raise InvalidOperationalStateRequest("signal and source ids must be strings")
        if not isinstance(plant_context_ref, PlantContextRef):
            raise InvalidOperationalStateRequest("an exact PlantContextRef is required")
        if not isinstance(engineering_selection, EngineeringSelection):
            raise InvalidOperationalStateRequest(
                "an explicit EngineeringSelection is required; latest is never resolved")
        policy = self._policy(interpretation_policy_refs)
        horizon, resolution, context_ref = _snapshot_coordinates(snapshot)
        try:   # one deep plain copy, passed unchanged to every read
            snapshot = json.loads(_canonical(_thaw(snapshot)))
        except (TypeError, ValueError):
            raise InvalidOperationalStateRequest(
                "a public telemetry snapshot object is required") from None
        start_request = _request_ticks("event_start_ticks", event_start_ticks)
        end_request = _request_ticks("event_end_ticks", event_end_ticks)
        end = horizon if end_request is None else end_request
        start = (max(0, end - policy.default_window_ticks) if start_request is None
                 else start_request)
        if not start <= end <= horizon:
            raise InvalidOperationalStateRequest("window must satisfy 0 <= start <= end <= T")
        if end - start > policy.max_window_ticks:
            raise ResourceLimitExceeded("window exceeds the interpretation policy bound")
        if engineering_selection.refs:
            # No published engineering revision is resolvable before LOS-A-S2.
            raise ContextUnavailable("engineering context selection is unavailable")
        boundary = self._boundary
        # 2. The host reader's per-read limits are host configuration; the pinned policy
        #    must fit them before any run is opened or read: every complete window is
        #    requested with max_input_points and may span max_window_ticks. The policy
        #    invariant (aggregate >= reads x per-read) implies the aggregate clause; it is
        #    kept as the explicit request-level check. The lambda keeps lookup guarded.
        limits = _guarded(lambda: boundary.host_read_limits())
        if (not isinstance(limits, HostReadLimits)
                or any(type(value) is not int for value in (
                    limits.max_points, limits.max_interval_ticks, limits.max_index_work))
                or limits.max_points < 1 or limits.max_interval_ticks < 0
                or limits.max_index_work < 1):
            raise InconsistentRead("boundary returned an unexpected shape")
        host_index_work = limits.max_index_work
        index_work = _READS_PER_REQUEST * host_index_work
        if (policy.max_input_points > limits.max_points
                or policy.max_window_ticks > limits.max_interval_ticks
                or host_index_work > policy.max_index_work_per_read
                or index_work > policy.max_aggregate_index_work):
            raise ResourceLimitExceeded("host read limits cannot serve the interpretation policy")
        queries = _guarded(lambda: boundary.queries(run_id))

        # 3. The current read is the public path that validates the exact snapshot and
        #    binding; the source and the aggregate work bound are checked before any
        #    context, descriptor or history work.
        current_view, current = self._read_current(queries, signal_id, source_id, snapshot,
                                                   horizon)
        source = current_view["source_id"]
        if current_view["eligible_count"] > policy.max_aggregate_candidates:
            raise ResourceLimitExceeded("request exceeds the aggregate candidate bound")

        # 4. Pinned plant context, then the signal descriptor and its policy applicability.
        plant = _guarded(lambda: boundary.plant_context(queries, plant_context_ref, snapshot))
        descriptor = _guarded(lambda: boundary.signal_descriptor(queries, signal_id))
        if not isinstance(descriptor, SignalDescriptor) or not isinstance(plant, Mapping):
            raise InconsistentRead("boundary returned an unexpected shape")
        if descriptor.value_kind in policy.value_kinds:
            policy.tolerance(descriptor.unit)   # reject an unpinned unit before the window

        # 5. The complete unreduced window, pinned to the same snapshot.
        history, samples = self._read_history(queries, signal_id, source, snapshot, start, end,
                                              policy)
        candidates = current_view["eligible_count"] + history["eligible_count"]
        if candidates > policy.max_aggregate_candidates:
            raise ResourceLimitExceeded("request exceeds the aggregate candidate bound")

        # 6. Pure OD-1 interpretation with exact provenance.
        identities = tuple(sample.identity for sample in samples)
        selection_checksum = _sha256([_identity_json(item) for item in identities])
        window_body = {
            "snapshot_ref": snapshot["snapshot_ref"], "source_id": source,
            "signal_id": signal_id, "event_clock_id": snapshot["event_clock"]["clock_id"],
            "event_start_ticks": start, "event_end_ticks": end,
            "start_defaulted": start_request is None, "end_defaulted": end_request is None,
            "ends_before_horizon": end < horizon,
            "read": {"view": history["view"], "view_version": history["view_version"],
                     "max_points": history["max_points"],
                     "eligible_count": history["eligible_count"],
                     "returned_count": history["returned_count"], "reduced": False,
                     "reader_reduction_policy": history["reduction_policy"],
                     "coverage": history["coverage"]},
            "window_read_policy": WINDOW_READ_POLICY}
        window_ref = "operational-window:sha256:" + _sha256(
            {**window_body, "selection_checksum": selection_checksum})
        window = WindowSelection(window_ref, selection_checksum, identities,
                                 _freeze(window_body),
                                 _freeze(_diagnostics(samples, start, end, policy)))
        context = _Context(policy, descriptor, resolution, window_ref, (context_ref,))
        features = self._features(samples, current, horizon, start, end, context,
                                  engineering_selection)
        body = {
            "schema_version": OPERATIONAL_STATE_VERSION, "run_id": run_id,
            "source_id": source, "signal_id": signal_id,
            "descriptor": {"value_kind": str(descriptor.value_kind), "unit": descriptor.unit},
            "snapshot": dict(snapshot), "as_of_event_ticks": horizon,
            "context": {"plant": dict(plant), "plant_context_ref": plant_context_ref.as_json(),
                        "engineering_selection": engineering_selection.as_json(),
                        "visibility_policy_ref": snapshot["visibility_policy_ref"]},
            "policy": {"ref": policy.ref.ref, "checksum": policy.ref.checksum,
                       "parameters": policy.parameters()},
            "current_read": {"view": current_view["view"],
                             "view_version": current_view["view_version"],
                             "snapshot_ref": snapshot["snapshot_ref"],
                             "eligible_count": current_view["eligible_count"],
                             "coverage": current_view["coverage"],
                             "sample": None if current is None else
                             _identity_json(current.identity)},
            "current": None if current is None else current.as_json(),
            "work": {"candidate_records": candidates,
                     "max_aggregate_candidates": policy.max_aggregate_candidates,
                     "current_eligible_count": current_view["eligible_count"],
                     "history_eligible_count": history["eligible_count"],
                     "reads_per_request": _READS_PER_REQUEST,
                     "host_index_work_per_read": host_index_work,
                     "index_work_bound": index_work,
                     "max_index_work_per_read": policy.max_index_work_per_read,
                     "max_aggregate_index_work": policy.max_aggregate_index_work},
            "request": {"signal_id": signal_id, "source_id": source_id,
                        "event_start_ticks": start_request, "event_end_ticks": end_request,
                        "plant_context_ref": plant_context_ref.as_json(),
                        "engineering_selection": engineering_selection.as_json(),
                        "interpretation_policy_refs": [policy.ref.ref]},
            "derivation": {"algorithm": policy.algorithm, "numeric_method": policy.numeric_method,
                           "inputs": [snapshot["snapshot_ref"], window_ref]}}
        emitted = {*identities, *(() if current is None else (current.identity,)),
                   *(item for result in features.values() for item in result.value_samples)}
        if len(emitted) > policy.max_inline_sample_refs:
            raise ResourceLimitExceeded("result exceeds the inline sample-ref bound")
        frozen = _freeze(json.loads(_canonical(body)))
        provisional = SignalOperationalState(run_id, source, signal_id,
                                             MappingProxyType(features), window, frozen, "")
        content = provisional.as_json()
        del content["derivation_ref"]
        encoded = _canonical(content)
        derivation_ref = "operational-state:sha256:" + hashlib.sha256(encoded).hexdigest()
        # The size check counts the derivation ref too: one fixed-length JSON member.
        size = len(encoded) + len(_canonical({"derivation_ref": derivation_ref})) - 1
        if size > policy.max_response_bytes:
            raise ResourceLimitExceeded("result exceeds the response bound")
        result = SignalOperationalState(run_id, source, signal_id, MappingProxyType(features),
                                        window, frozen, derivation_ref)
        _guarded(lambda: boundary.screen({**content, "derivation_ref": derivation_ref}))
        return result

    @staticmethod
    def _read_current(queries: TelemetryQueries, signal_id: str, source_id: str | None,
                      snapshot: Mapping[str, Any], horizon: int
                      ) -> tuple[Mapping[str, Any], PublicSample | None]:
        """Current read at (T, K); any malformed answer is a server fault."""
        view = _guarded(lambda: queries.telemetry_current(signal_id, snapshot=snapshot))
        try:
            OperationalStateService._consistent(view, snapshot, signal_id)
            if not {"view", "view_version", "eligible_count", "coverage"} <= set(view):
                raise InconsistentRead("public read has an unexpected shape")
            source, count = view["source_id"], view["eligible_count"]
            current = None if view["sample"] is None else _public_sample(view["sample"])
        except (KeyError, TypeError):
            raise InconsistentRead("public current read has an unexpected shape") from None
        if (not isinstance(source, str) or type(count) is not int or count < 0
                or (current is None) != (count == 0)):
            raise InconsistentRead("public current read has an unexpected shape")
        if current is not None and (
                current.event_ticks > horizon
                or (current.identity.source_id, current.identity.signal_id)
                != (source, signal_id)):
            raise InconsistentRead("public read violated its snapshot or binding")
        if source_id is not None and source != source_id:
            raise UnknownBinding("unknown or ambiguous signal binding")
        return view, current

    @staticmethod
    def _read_history(queries: TelemetryQueries, signal_id: str, source: str,
                      snapshot: Mapping[str, Any], start: int, end: int,
                      policy: InterpretationPolicy
                      ) -> tuple[Mapping[str, Any], tuple[PublicSample, ...]]:
        """Complete unreduced history of the window; malformed answers are server faults."""
        history = _guarded(lambda: queries.telemetry_history(
            signal_id, snapshot=snapshot, event_start_ticks=start, event_end_ticks=end,
            max_points=policy.max_input_points))
        try:
            OperationalStateService._consistent(history, snapshot, signal_id, source)
            if (history["event_start_ticks"], history["event_end_ticks"]) != (start, end):
                raise InconsistentRead("history answered another window")
            eligible, returned = history["eligible_count"], history["returned_count"]
            if (any(type(count) is not int or count < 0 for count in (eligible, returned))
                    or type(history["reduced"]) is not bool
                    or len(history["samples"]) != returned or returned > eligible
                    or returned > policy.max_input_points
                    or history["reduced"] != (returned < eligible)):
                raise InconsistentRead("history counts disagree with its samples")
            if history["reduced"]:
                raise ResourceLimitExceeded("complete window exceeds the unreduced input bound")
            samples = tuple(_public_sample(item) for item in history["samples"])
            if not ({"view", "view_version", "max_points", "reduction_policy", "coverage"}
                    <= set(history)):
                raise InconsistentRead("public read has an unexpected shape")
        except (KeyError, TypeError):
            raise InconsistentRead("public history read has an unexpected shape") from None
        ticks = [sample.event_ticks for sample in samples]
        if ticks != sorted(ticks) or any(not start <= tick <= end for tick in ticks):
            raise InconsistentRead("public read violated its window or read order")
        for sample in samples:
            if (sample.identity.source_id, sample.identity.signal_id) != (source, signal_id):
                raise InconsistentRead("read returned another binding")
        return history, samples

    @staticmethod
    def _consistent(view: Mapping[str, Any], snapshot: Mapping[str, Any], signal_id: str,
                    source_id: str | None = None) -> None:
        if (view["snapshot"]["snapshot_ref"] != snapshot["snapshot_ref"]
                or view["signal_id"] != signal_id
                or (source_id is not None and view["source_id"] != source_id)):
            raise InconsistentRead("public read answered another snapshot or binding")

    @staticmethod
    def _features(samples: Sequence[PublicSample], current: PublicSample | None, horizon: int,
                  start: int, end: int, context: _Context, selection: EngineeringSelection
                  ) -> dict[Feature, FeatureResult]:
        policy, kind = context.policy, context.descriptor.value_kind
        features: dict[Feature, FeatureResult] = {}
        if current is None:
            for feature in (Feature.CURRENT, Feature.STALENESS):
                features[feature] = context.result(feature, FeatureStatus.NO_DATA,
                                                   basis=AS_OF_HORIZON)
        else:
            features[Feature.CURRENT] = context.result(
                Feature.CURRENT, FeatureStatus.AVAILABLE, value=current,
                value_samples=(current.identity,), basis=AS_OF_HORIZON,
                detail={"selection": "last eligible sample at or before T under K, any quality"})
            age = horizon - current.event_ticks
            features[Feature.STALENESS] = context.result(
                Feature.STALENESS, FeatureStatus.AVAILABLE,
                value=Freshness.FRESH if age <= policy.stale_after_ticks else Freshness.STALE,
                value_samples=(current.identity,), basis=AS_OF_HORIZON,
                detail={"age_ticks": age, "stale_after_ticks": policy.stale_after_ticks,
                        "current_event_ticks": current.event_ticks,
                        "as_of_event_ticks": horizon,
                        "semantics": "event-horizon freshness only"})
        for result in (*_secant(samples, start, end, context), *_extrema(samples, context)):
            features[result.feature] = result
        selection_ref = f"engineering-selection:{selection.as_json()['kind']}"
        threshold = (FeatureStatus.UNSUPPORTED_VALUE_KIND if kind != ValueKind.NUMERIC
                     else FeatureStatus.NO_CANONICAL_LIMIT)
        features[Feature.THRESHOLD] = context.result(
            Feature.THRESHOLD, threshold, basis=AS_OF_HORIZON, citations=(selection_ref,),
            detail={"engineering_selection": EXPLICIT_EMPTY})
        return {feature: features[feature] for feature in Feature}
