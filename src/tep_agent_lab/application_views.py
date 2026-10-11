"""Bounded AGENT application assembly over public P0 projections only.

Owning contract: docs/specs/application-api-v1.md. Telemetry-bearing reads are pinned
to one exact TelemetrySnapshot obtained through ``open_telemetry_snapshot``; no read
resolves "latest". Graph data comes only from the AGENT ProcessGraphView.
"""

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from industrial_agent_runtime import TaskStatus

from .application_models import (Binding, CanonicalPoint, Entity, EntityType, InvalidRequest,
                                 Run, Signal, SignalHistory, TelemetrySnapshot,
                                 snapshot_from_view, snapshot_to_view)
from .canonical_context import ProjectionScope
from .playground import RunStatus
from .playground_views import RunQueries, VisibilityViolation
from .telemetry import (ClockMismatch, ResourceLimitExceeded, SnapshotUnavailable,
                        TelemetryError, TelemetryRequestError)
from .tep_world import leakage_findings

MAX_HISTORY_POINTS = 1000
__all__ = ["AmbiguousSignal", "ApplicationViewService", "InvalidRequest", "MAX_HISTORY_POINTS",
           "UnknownEntity", "UnknownSignal"]


class UnknownEntity(LookupError):
    """No matching AGENT-visible graph entity."""


class UnknownSignal(LookupError):
    """No matching AGENT-visible graph binding for a canonical signal id."""


class AmbiguousSignal(ValueError):
    """Graph bindings disagree about the signal's semantics."""


_ReadResult = TypeVar("_ReadResult")


class _InvalidTelemetryRequest(InvalidRequest, TelemetryRequestError):
    """Read-boundary client validation; preserves the direct service exception family."""


def _telemetry_read(read: Callable[..., _ReadResult], *args: Any, **kwargs: Any) -> _ReadResult:
    """Translate client validation only around the public P0 telemetry read call."""
    try:
        return read(*args, **kwargs)
    except (ClockMismatch, ResourceLimitExceeded):
        raise
    except TelemetryRequestError:
        raise _InvalidTelemetryRequest("invalid telemetry read arguments") from None


def _optional_ticks(name: str, value: Any) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise InvalidRequest(f"{name} must be a non-negative integer or None")


def _point(sample: Mapping[str, Any]) -> CanonicalPoint:
    return CanonicalPoint(sample["event_ticks"], sample["sequence"], sample["value"],
                          sample["quality"])


class ApplicationViewService:
    """Pass ``manager.queries`` as queries_for_run; scope is always AGENT.

    No data is cached and no canonical state is modified. Each call assembles fresh
    projections; only telemetry values share the caller's pinned snapshot.
    """

    def __init__(self, queries_for_run: Callable[[str], RunQueries]) -> None:
        self._queries_for_run = queries_for_run

    def _queries(self, run_id: str) -> RunQueries:
        queries = self._queries_for_run(run_id)
        if queries.scope != ProjectionScope.AGENT:
            raise VisibilityViolation("application reads require AGENT scope")
        return queries

    @staticmethod
    def _checked(view: Mapping[str, Any], run_id: str) -> Mapping[str, Any]:
        if (view["scope"] != ProjectionScope.AGENT.value or view["run_id"] != run_id
                or leakage_findings(view)):
            raise VisibilityViolation("invalid application projection")
        return view

    @staticmethod
    def _pinned(run_id: str, snapshot: Any) -> dict[str, Any]:
        if not isinstance(snapshot, TelemetrySnapshot):
            raise InvalidRequest("a TelemetrySnapshot from open_telemetry_snapshot is required")
        if snapshot.run_id != run_id:
            raise SnapshotUnavailable("snapshot belongs to another run")
        return snapshot_to_view(snapshot)

    def open_telemetry_snapshot(self, run_id: str, *, event_horizon_ticks: int | None = None,
                                ingest_sequence: int | None = None) -> TelemetrySnapshot:
        """The only read that chooses (T, K); defaults are resolved once and returned."""
        _optional_ticks("event_horizon_ticks", event_horizon_ticks)
        _optional_ticks("ingest_sequence", ingest_sequence)
        queries = self._queries(run_id)
        view = self._checked(_telemetry_read(
            queries.telemetry_snapshot, event_horizon_ticks=event_horizon_ticks,
            ingest_sequence=ingest_sequence), run_id)
        try:
            return snapshot_from_view(run_id, view["snapshot"])
        except InvalidRequest:
            # This snapshot was produced by the server, not supplied by the client.
            raise TelemetryError("invalid server telemetry snapshot") from None

    def get_run(self, run_id: str, *, snapshot: TelemetrySnapshot | None = None) -> Run:
        queries = self._queries(run_id)
        pinned = None if snapshot is None else self._pinned(run_id, snapshot)
        summary = self._checked(queries.run_summary(), run_id)
        status = summary.get("runtime_task_status", {}).get("status")
        if status is None:
            runtime = summary.get("outcome", {}).get("runtime_result")
            status = None if runtime is None else runtime["task_status"]
        manifest = summary.get("manifest", {})
        snapshot_ref = event_ticks = None
        if pinned is not None:
            last = self._checked(_telemetry_read(
                queries.telemetry_last_event, snapshot=pinned), run_id)
            snapshot_ref, event_ticks = last["snapshot"]["snapshot_ref"], last["event_ticks"]
        return Run(run_id, RunStatus(summary["run_status"]),
                   None if status is None else TaskStatus(status), summary["view_version"],
                   manifest.get("manifest_version"), manifest.get("checksum"),
                   snapshot_ref, event_ticks)

    @staticmethod
    def _entities(graph: Mapping[str, Any]):
        for category, key, kind in (("nodes", "node_id", EntityType.NODE),
                                    ("edges", "edge_id", EntityType.EDGE)):
            for entity in graph[category]:
                yield entity[key], kind, entity

    @staticmethod
    def _binding(binding: Mapping[str, Any]) -> Binding:
        return Binding(binding["semantic_entity_id"], binding["runtime_variable_id"],
                       binding["quantity"], binding["relation"], binding["unit"])

    def get_entity(self, run_id: str, entity_id: str) -> Entity:
        graph = self._checked(self._queries(run_id).process_graph(), run_id)
        matches = [(kind, entity) for identity, kind, entity in self._entities(graph)
                   if identity == entity_id]
        if len(matches) != 1:
            raise UnknownEntity("unknown entity")
        kind, entity = matches[0]
        source, target = entity.get("source_node"), entity.get("target_node")
        upstream = ((source,) if kind == EntityType.EDGE else tuple(sorted({
            edge["source_node"] for edge in graph["edges"] if edge["target_node"] == entity_id})))
        downstream = ((target,) if kind == EntityType.EDGE else tuple(sorted({
            edge["target_node"] for edge in graph["edges"] if edge["source_node"] == entity_id})))
        return Entity(entity_id, kind, entity["name"], entity["kind"], entity.get("tag"),
                      source, target, upstream, downstream,
                      tuple(self._binding(binding) for binding in entity["bindings"]))

    def _resolve(self, queries: RunQueries, run_id: str, signal_id: str):
        graph = self._checked(queries.process_graph(), run_id)
        matches = [(identity, self._binding(binding))
                   for identity, _, entity in self._entities(graph)
                   for binding in entity["bindings"]
                   if binding["semantic_entity_id"] == signal_id]
        if not matches:
            raise UnknownSignal("unknown signal")
        semantics = {(b.quantity, b.relation, b.unit) for _, b in matches}
        if len(semantics) != 1:
            raise AmbiguousSignal("inconsistent signal binding metadata")
        binding = min(matches, key=lambda item: (item[0], item[1].source_locator))[1]
        return tuple(sorted({identity for identity, _ in matches})), binding

    @staticmethod
    def _same_signal(view: Mapping[str, Any], signal_id: str) -> None:
        if view["signal_id"] != signal_id:
            raise VisibilityViolation("telemetry view answered another signal")

    def get_signal(self, run_id: str, signal_id: str, *, snapshot: TelemetrySnapshot) -> Signal:
        queries = self._queries(run_id)
        pinned = self._pinned(run_id, snapshot)
        entities, binding = self._resolve(queries, run_id, signal_id)
        view = self._checked(_telemetry_read(
            queries.telemetry_current, signal_id, snapshot=pinned), run_id)
        self._same_signal(view, signal_id)
        sample = view["sample"]
        return Signal(run_id, signal_id, view["source_id"], binding.source_locator, entities,
                      binding.quantity, binding.relation, binding.unit,
                      view["snapshot"]["snapshot_ref"], view["status"], view["eligible_count"],
                      None if sample is None else _point(sample))

    def get_signal_history(self, run_id: str, signal_id: str, *, snapshot: TelemetrySnapshot,
                           event_start_ticks: int | None = None,
                           event_end_ticks: int | None = None,
                           max_points: int = 500) -> SignalHistory:
        _optional_ticks("event_start_ticks", event_start_ticks)
        _optional_ticks("event_end_ticks", event_end_ticks)
        if (event_start_ticks is not None and event_end_ticks is not None
                and event_end_ticks < event_start_ticks):
            raise InvalidRequest("event_end_ticks must be >= event_start_ticks")
        if type(max_points) is not int or not 1 <= max_points <= MAX_HISTORY_POINTS:
            raise InvalidRequest(f"max_points must be in [1, {MAX_HISTORY_POINTS}]")
        queries = self._queries(run_id)
        pinned = self._pinned(run_id, snapshot)
        _, binding = self._resolve(queries, run_id, signal_id)
        view = self._checked(_telemetry_read(queries.telemetry_history,
            signal_id, snapshot=pinned, event_start_ticks=event_start_ticks,
            event_end_ticks=event_end_ticks, max_points=max_points), run_id)
        self._same_signal(view, signal_id)
        return SignalHistory(run_id, signal_id, view["source_id"], binding.quantity, binding.unit,
                             view["snapshot"]["snapshot_ref"], view["event_start_ticks"],
                             view["event_end_ticks"], view["max_points"], view["eligible_count"],
                             view["returned_count"], view["reduced"], view["reduction_policy"],
                             view["coverage"], view["status"],
                             tuple(_point(sample) for sample in view["samples"]))
