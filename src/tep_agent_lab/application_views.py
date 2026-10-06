"""Bounded AGENT application assembly over public P0 projections only."""

from collections.abc import Callable, Mapping
import math
from typing import Any

from industrial_agent_runtime import TaskStatus

from .application_models import Binding, Entity, EntityType, Run, Signal, SignalHistory, SignalPoint
from .canonical_context import ProjectionScope
from .playground import RunStatus
from .playground_views import MAX_TELEMETRY_RECORDS, RunQueries, ViewUnavailable, VisibilityViolation
from .tep_world import leakage_findings

MAX_HISTORY_POINTS = 1000
TIME_BOUND_TOLERANCE_HOURS = 1e-9


class UnknownEntity(LookupError):
    """No matching AGENT-visible graph entity."""


class UnknownSignal(LookupError):
    """No matching AGENT-visible graph binding."""


class AmbiguousSignal(ValueError):
    """Graph bindings disagree about the signal's semantics."""


class ApplicationViewService:
    """Pass ``manager.queries`` as queries_for_run; scope is always AGENT.

    History reads only the bounded recent P0 telemetry projection. An explicit
    window requiring records outside that projection is unavailable, never
    silently clipped. No data is cached or canonical state modified.
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

    def get_run(self, run_id: str) -> Run:
        queries = self._queries(run_id)
        summary = self._checked(queries.run_summary(), run_id)
        status = summary.get("runtime_task_status", {}).get("status")
        if status is None:
            runtime = summary.get("outcome", {}).get("runtime_result")
            status = None if runtime is None else runtime["task_status"]
        manifest = summary.get("manifest", {})
        try:
            telemetry = self._checked(queries.telemetry(variables=(), max_records=1), run_id)
            time = telemetry["current"]["simulation_time_hours"]
        except ViewUnavailable:
            time = None
        return Run(run_id, RunStatus(summary["run_status"]),
                   None if status is None else TaskStatus(status), time,
                   summary["view_version"], manifest.get("manifest_version"),
                   manifest.get("checksum"))

    @staticmethod
    def _entities(graph: Mapping[str, Any]):
        for category, key, kind in (("nodes", "node_id", EntityType.NODE),
                                    ("edges", "edge_id", EntityType.EDGE)):
            for entity in graph[category]:
                yield entity[key], kind, entity

    @staticmethod
    def _binding(binding: Mapping[str, Any]) -> Binding:
        return Binding(binding["runtime_variable_id"], binding["quantity"],
                       binding["relation"], binding["unit"])

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
                   if binding["runtime_variable_id"] == signal_id]
        if not matches:
            raise UnknownSignal("unknown signal")
        semantics = {(b.quantity, b.relation, b.unit) for _, b in matches}
        if len(semantics) != 1:
            raise AmbiguousSignal("inconsistent signal binding metadata")
        return tuple(sorted({identity for identity, _ in matches})), matches[0][1]

    @staticmethod
    def _value(record: Mapping[str, Any], signal_id: str) -> float | None:
        values = [record[group][signal_id] for group in ("measurements", "manipulated_variables")
                  if signal_id in record[group]]
        if len(values) > 1 and len(set(values)) != 1:
            raise AmbiguousSignal("inconsistent telemetry values")
        if not values:
            return None
        value = float(values[0])
        if not math.isfinite(value):
            raise ViewUnavailable("nonfinite telemetry value")
        return value

    def get_signal(self, run_id: str, signal_id: str) -> Signal:
        queries = self._queries(run_id)
        entities, binding = self._resolve(queries, run_id, signal_id)
        value, time = None, None
        try:
            telemetry = self._checked(queries.telemetry(variables=(signal_id,), max_records=1), run_id)
            value = self._value(telemetry["current"], signal_id)
            if value is not None:
                time = telemetry["current"]["simulation_time_hours"]
        except ViewUnavailable:
            pass
        return Signal(signal_id, entities, binding.quantity, binding.relation, binding.unit,
                      value is not None, value, time)

    def get_signal_history(self, run_id: str, signal_id: str, *,
                           start_hours: float | None = None, end_hours: float | None = None,
                           max_points: int = 500) -> SignalHistory:
        queries = self._queries(run_id)
        _, binding = self._resolve(queries, run_id, signal_id)
        for value in (start_hours, end_hours):
            if value is not None and (type(value) not in (int, float)
                                      or not math.isfinite(value) or value < 0):
                raise ValueError("history bounds must be finite nonnegative hours")
        if start_hours is not None and end_hours is not None and end_hours < start_hours:
            raise ValueError("end_hours must be >= start_hours")
        if type(max_points) is not int or not 1 <= max_points <= MAX_HISTORY_POINTS:
            raise ValueError(f"max_points must be in [1, {MAX_HISTORY_POINTS}]")
        telemetry = self._checked(queries.telemetry(variables=(signal_id,),
                                                    max_records=MAX_TELEMETRY_RECORDS), run_id)
        records = telemetry["records"]
        if not records:
            raise ViewUnavailable("no telemetry records")
        first, last = (records[i]["simulation_time_hours"] for i in (0, -1))
        start = first if start_hours is None else start_hours
        end = last if end_hours is None else end_hours
        if end < start:
            raise ValueError("end_hours must be >= start_hours")
        omitted = telemetry["total_records"] > telemetry["returned_records"]
        if ((omitted and start < first - TIME_BOUND_TOLERANCE_HOURS)
                or end > last + TIME_BOUND_TOLERANCE_HOURS):
            raise ViewUnavailable("requested window exceeds available telemetry projection")
        points = []
        for record in records:
            time = record["simulation_time_hours"]
            value = self._value(record, signal_id)
            if value is None:
                raise ViewUnavailable("signal history is not available in telemetry")
            if start - TIME_BOUND_TOLERANCE_HOURS <= time <= end + TIME_BOUND_TOLERANCE_HOURS:
                points.append(SignalPoint(time, value))
        count = len(points)
        if count > max_points:
            # Integer-spaced indices include both endpoints; one point means latest.
            points = ([points[-1]] if max_points == 1 else
                      [points[i * (count - 1) // (max_points - 1)] for i in range(max_points)])
        return SignalHistory(signal_id, binding.quantity, binding.unit, start, end,
                             tuple(points), "P0 TelemetryView.records", telemetry["view_version"],
                             run_id, first, last, omitted, count, count > max_points)
