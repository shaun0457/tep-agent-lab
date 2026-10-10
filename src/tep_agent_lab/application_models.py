"""Immutable, domain-generic application reads; serialize with runtime to_jsonable.

Owning contract: docs/specs/application-api-v1.md.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any

from industrial_agent_runtime import TaskStatus

from .playground import RunStatus


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
    event_clock_origin: str | None
    context_ref: str
    visibility_policy_ref: str


_INTEGER_FIELDS = frozenset({"ingest_sequence", "event_horizon_ticks"})
_OPTIONAL_FIELDS = frozenset({"event_clock_origin"})
SNAPSHOT_FIELDS = tuple(field.name for field in fields(TelemetrySnapshot))


def snapshot_from_json(value: Any) -> TelemetrySnapshot:
    """Strict JSON -> TelemetrySnapshot: exact keys; integers are not booleans."""
    if not isinstance(value, Mapping) or set(value) != set(SNAPSHOT_FIELDS):
        raise ValueError("snapshot must have exactly the TelemetrySnapshot fields")
    for name in SNAPSHOT_FIELDS:
        item = value[name]
        if name in _INTEGER_FIELDS:
            valid = type(item) is int
        elif name in _OPTIONAL_FIELDS:
            valid = item is None or type(item) is str
        else:
            valid = type(item) is str
        if not valid:
            raise ValueError(f"snapshot field {name} has an invalid type")
    return TelemetrySnapshot(**{name: value[name] for name in SNAPSHOT_FIELDS})


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
