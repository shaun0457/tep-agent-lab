"""Immutable, domain-generic application reads; serialize with runtime to_jsonable.

Owning contract: docs/specs/application-api-v1.md.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any

from industrial_agent_runtime import TaskStatus

from .playground import RunStatus
from .telemetry import TelemetryRequestError, parse_event_resolution


class InvalidRequest(ValueError):
    """Client-supplied application arguments are invalid (transport INVALID_REQUEST)."""


class EntityType(StrEnum):
    NODE = "NODE"
    EDGE = "EDGE"


@dataclass(frozen=True)
class TelemetrySnapshot:
    """One exact (T, K) telemetry read snapshot; callers pass it back unchanged."""

    run_id: str
    snapshot_ref: str
    contract: str
    store_id: str
    incarnation: str
    namespace: str
    ingest_sequence: int
    event_horizon_ticks: int
    event_clock_id: str
    event_clock_kind: str
    event_resolution: str
    event_clock_origin: str
    context_ref: str
    visibility_policy_ref: str

    def __post_init__(self) -> None:
        _validate_snapshot_fields({name: getattr(self, name) for name in SNAPSHOT_FIELDS})


_INTEGER_FIELDS = frozenset({"ingest_sequence", "event_horizon_ticks"})
SNAPSHOT_FIELDS = tuple(field.name for field in fields(TelemetrySnapshot))
MAX_SNAPSHOT_TEXT = 256


def snapshot_from_json(value: Any) -> TelemetrySnapshot:
    """Strict JSON -> TelemetrySnapshot (Application API v1 §Telemetry snapshot).

    Exact keys; integers are non-negative ints (not booleans); every other field is a
    non-empty string of at most MAX_SNAPSHOT_TEXT characters; the resolution is a
    reduced positive fraction ``n/d``. Raises InvalidRequest.
    """
    if not isinstance(value, Mapping) or set(value) != set(SNAPSHOT_FIELDS):
        raise InvalidRequest("snapshot must have exactly the TelemetrySnapshot fields")
    return TelemetrySnapshot(**{name: value[name] for name in SNAPSHOT_FIELDS})


def _validate_snapshot_fields(value: Mapping[str, Any]) -> None:
    """Shared field checks for typed construction, JSON parsing and direct reads."""
    for name in SNAPSHOT_FIELDS:
        item = value[name]
        if name in _INTEGER_FIELDS:
            valid = type(item) is int and item >= 0
        else:
            valid = type(item) is str and 0 < len(item) <= MAX_SNAPSHOT_TEXT
        if not valid:
            raise InvalidRequest(f"snapshot field {name} is invalid")
    try:
        parse_event_resolution(value["event_resolution"])
    except TelemetryRequestError:
        raise InvalidRequest("snapshot resolution must be a reduced positive fraction") from None


def snapshot_from_view(run_id: str, body: Mapping[str, Any]) -> TelemetrySnapshot:
    """Flatten a P0 ``TelemetrySnapshotView.snapshot`` object."""
    store, clock = body["store"], body["event_clock"]
    return TelemetrySnapshot(
        run_id=run_id, snapshot_ref=body["snapshot_ref"], contract=body["contract"],
        store_id=store["store_id"], incarnation=store["incarnation"],
        namespace=store["namespace"], ingest_sequence=body["ingest_sequence"],
        event_horizon_ticks=body["event_horizon"]["ticks"], event_clock_id=clock["clock_id"],
        event_clock_kind=clock["kind"], event_resolution=clock["resolution"],
        event_clock_origin=clock["origin"], context_ref=body["context_ref"],
        visibility_policy_ref=body["visibility_policy_ref"])


def snapshot_to_view(snapshot: TelemetrySnapshot) -> dict[str, Any]:
    """The P0 snapshot object that P0 rebuilds, re-checks and resolves."""
    _validate_snapshot_fields({name: getattr(snapshot, name) for name in SNAPSHOT_FIELDS})
    return {"contract": snapshot.contract,
            "store": {"store_id": snapshot.store_id, "incarnation": snapshot.incarnation,
                      "namespace": snapshot.namespace},
            "ingest_sequence": snapshot.ingest_sequence,
            "event_horizon": {"clock_id": snapshot.event_clock_id,
                              "ticks": snapshot.event_horizon_ticks},
            "event_clock": {"clock_id": snapshot.event_clock_id,
                            "kind": snapshot.event_clock_kind,
                            "resolution": snapshot.event_resolution,
                            "origin": snapshot.event_clock_origin},
            "context_ref": snapshot.context_ref,
            "visibility_policy_ref": snapshot.visibility_policy_ref,
            "snapshot_ref": snapshot.snapshot_ref}


@dataclass(frozen=True)
class CanonicalPoint:
    """One canonical sample; identity is (Signal.source_id, Signal.signal_id, sequence)."""

    event_ticks: int
    sequence: int
    value: bool | int | float | str | None
    quality: str


@dataclass(frozen=True)
class Run:
    run_id: str
    run_status: RunStatus
    task_status: TaskStatus | None
    view_version: str
    manifest_version: str | None
    manifest_checksum: str | None
    snapshot_ref: str | None
    telemetry_event_ticks: int | None


@dataclass(frozen=True)
class Binding:
    signal_id: str          # canonical signal id (ProcessGraph semantic_entity_id)
    source_locator: str     # adapter-native label (e.g. a runtime id); display only
    quantity: str
    relation: str
    unit: str


@dataclass(frozen=True)
class Entity:
    entity_id: str
    entity_type: EntityType
    name: str
    kind: str
    tag: str | None
    source_entity_id: str | None
    target_entity_id: str | None
    upstream: tuple[str, ...]
    downstream: tuple[str, ...]
    bindings: tuple[Binding, ...]


@dataclass(frozen=True)
class Signal:
    run_id: str
    signal_id: str
    source_id: str
    source_locator: str
    bound_entity_ids: tuple[str, ...]
    quantity: str
    relation: str
    unit: str
    snapshot_ref: str
    status: str
    eligible_count: int
    current: CanonicalPoint | None


@dataclass(frozen=True)
class SignalHistory:
    run_id: str
    signal_id: str
    source_id: str
    quantity: str
    unit: str
    snapshot_ref: str
    event_start_ticks: int
    event_end_ticks: int
    max_points: int
    eligible_count: int
    returned_count: int
    reduced: bool
    reduction_policy: str
    coverage: str
    status: str
    points: tuple[CanonicalPoint, ...]
