"""Immutable, domain-generic application reads; serialize with runtime to_jsonable."""

from dataclasses import dataclass
from enum import StrEnum

from industrial_agent_runtime import TaskStatus

from .playground import RunStatus


class EntityType(StrEnum):
    NODE = "NODE"
    EDGE = "EDGE"


@dataclass(frozen=True)
class Run:
    run_id: str
    run_status: RunStatus
    task_status: TaskStatus | None
    simulation_time_hours: float | None
    view_version: str
    manifest_version: str | None
    manifest_checksum: str | None


@dataclass(frozen=True)
class Binding:
    signal_id: str
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
    signal_id: str
    bound_entity_ids: tuple[str, ...]
    quantity: str
    relation: str
    unit: str
    telemetry_available: bool
    current_value: float | None
    simulation_time_hours: float | None


@dataclass(frozen=True)
class SignalPoint:
    simulation_time_hours: float
    value: float


@dataclass(frozen=True)
class SignalHistory:
    signal_id: str
    quantity: str
    unit: str
    start_hours: float
    end_hours: float
    points: tuple[SignalPoint, ...]
    source: str
    source_view_version: str
    run_id: str
    available_start_hours: float
    available_end_hours: float
    earlier_records_omitted: bool
    selected_points: int
    downsampled: bool
