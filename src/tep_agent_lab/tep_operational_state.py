"""Process-pack (TEP) composition of the generic Operational State service over P0 reads.

LOS-A-S1 (docs/p1-4a-operational-state.md). The generic ``operational_state`` module
owns the contracts and OD-1 interpretation; this module supplies only:

- the reviewed TEP policy record with the proposed OD-2 values (unit-specific trend
  tolerances grounded in the clean simulated stream, cadence-specific gap/freshness);
- signal descriptors from the AGENT ProcessGraphView, resolved by the same rule as the
  application signal reads (exact unit marker; numeric MEASURES/ACTUATES bindings);
- the exact interim PROCESS_GRAPH source citation (P1.1C context ref, not a P1.3
  PlantContextRevision) and the AGENT leakage screen;
- the mapping of P0-specific read failures to the service's failure families.

Every telemetry read goes through ``RunManager.queries`` (public P0 RunQueries) with
the caller's exact snapshot. Nothing here steps a plant, opens a store or adds an
Agent tool, transport method or Application API version.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from .application_views import AmbiguousSignal, UnknownSignal, resolve_signal_binding
from .canonical_context import ContextSourceRef, ProjectionScope
from .operational_state import (ContextUnavailable, HostReadLimits, InterpretationPolicy,
                                InvalidOperationalStateRequest, OperationalStateService,
                                PlantContextRef, SignalDescriptor, UnknownRun, ValueKind,
                                VisibilityViolation)
from .playground_views import RunQueries, ViewUnavailable
from .playground_views import VisibilityViolation as P0VisibilityViolation
from .run_telemetry import context_ref_for
from .telemetry import SnapshotUnavailable, UnknownBinding, index_work_bound
from .tep_world import leakage_findings

INTERIM_PROCESS_GRAPH_SOURCE = "INTERIM_PROCESS_GRAPH_SOURCE"
_NUMERIC_RELATIONS = frozenset({"MEASURES", "ACTUATES"})
_T = TypeVar("_T")

# Proposed OD-2 values (owner confirmation pending in the LOS-A-S1 PR). Tolerances are
# in signal unit per second; see docs/p1-4a-operational-state.md for the measurement.
TEP_CLEAN_STREAM_POLICY = InterpretationPolicy(
    policy_id="tep-agent-lab.tep-clean-stream-operational-state",
    version="v0",
    default_window_ticks=120,
    max_window_ticks=999,
    max_input_points=1000,
    # Current prefix + window candidates; equals the default reader scan bound.
    max_aggregate_candidates=50_000,
    # Index probes per public read: the default P0 reader's bound, max(50,000, 128);
    # one current and one history read per request.
    max_index_work_per_read=50_000,
    max_aggregate_index_work=100_000,
    max_gap_ticks=1,
    stale_after_ticks=1,
    trend_tolerance_per_second={
        "%": 0.25, "deg C": 0.02, "kPa": 0.1, "kW": 0.02, "kg/hr": 2.5,
        "kscmh": 0.02, "m3/hr": 0.1, "mol%": 0.02},
    max_response_bytes=1_048_576)


def interim_plant_context(snapshot: Mapping[str, Any]) -> PlantContextRef:
    """Pin the exact interim PROCESS_GRAPH source ref recorded in a public snapshot."""
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("context_ref"), str):
        raise InvalidOperationalStateRequest("a public telemetry snapshot object is required")
    return PlantContextRef(INTERIM_PROCESS_GRAPH_SOURCE, snapshot["context_ref"])


def _descriptor(graph: Mapping[str, Any], signal_id: str) -> SignalDescriptor:
    try:
        _, binding = resolve_signal_binding(graph, signal_id)
    except (UnknownSignal, AmbiguousSignal):
        raise UnknownBinding("unknown or ambiguous signal binding") from None
    if binding.relation not in _NUMERIC_RELATIONS:
        raise UnknownBinding("unknown or ambiguous signal binding")
    return SignalDescriptor(ValueKind.NUMERIC, binding.unit)


def served_signals(queries: Any) -> tuple[str, ...]:
    """Sorted canonical signal ids the boundary serves, from the public AGENT graph view."""
    graph = queries.process_graph()
    signals = {binding["semantic_entity_id"] for category in ("nodes", "edges")
               for entity in graph[category] for binding in entity["bindings"]}
    served = []
    for signal in sorted(signals):
        try:
            _descriptor(graph, signal)
        except UnknownBinding:
            continue
        served.append(signal)
    return tuple(served)


class _AgentReads:
    """Public AGENT RunQueries with P0-specific failures mapped to the service families."""

    def __init__(self, queries: RunQueries) -> None:
        self._queries = queries

    @staticmethod
    def _call(read: Callable[[], _T], unavailable: type[LookupError]) -> _T:
        try:
            return read()
        except ViewUnavailable:
            raise unavailable("unavailable") from None
        except P0VisibilityViolation:
            raise VisibilityViolation("AGENT visibility violation") from None

    def process_graph(self) -> Any:
        return self._call(self._queries.process_graph, ContextUnavailable)

    def context_inventory(self) -> Any:
        return self._call(self._queries.context_inventory, ContextUnavailable)

    def telemetry_current(self, signal_id: str, *, snapshot: Mapping[str, Any]) -> Any:
        return self._call(lambda: self._queries.telemetry_current(signal_id, snapshot=snapshot),
                          SnapshotUnavailable)

    def telemetry_history(self, signal_id: str, *, snapshot: Mapping[str, Any],
                          event_start_ticks: int, event_end_ticks: int,
                          max_points: int) -> Any:
        return self._call(lambda: self._queries.telemetry_history(
            signal_id, snapshot=snapshot, event_start_ticks=event_start_ticks,
            event_end_ticks=event_end_ticks, max_points=max_points), SnapshotUnavailable)


class P0OperationalStateBoundary:
    """Public P0 queries (AGENT scope) plus pinned graph context for one RunManager."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def host_read_limits(self) -> HostReadLimits:
        """Per-read limits of the readers this manager builds for every run.

        Host configuration: the manager's read-only TelemetryReadPolicy, from which each
        run's reader is built. The reader itself is reachable only through private run
        state, so it is not read here.
        """
        policy = self._manager.telemetry_policy
        return HostReadLimits(policy.max_points_limit, policy.max_interval_ticks,
                              index_work_bound(policy.max_scan_records))

    def queries(self, run_id: str) -> _AgentReads:
        try:
            queries = self._manager.queries(run_id)
        except KeyError:
            raise UnknownRun("unknown run") from None
        except ValueError:
            raise InvalidOperationalStateRequest("invalid run id") from None
        if queries.scope != ProjectionScope.AGENT:
            raise VisibilityViolation("operational state requires AGENT scope")
        return _AgentReads(queries)

    def signal_descriptor(self, queries: Any, signal_id: str) -> SignalDescriptor:
        """Declared kind and exact unit, resolved like the application signal reads."""
        return _descriptor(queries.process_graph(), signal_id)

    def plant_context(self, queries: Any, ref: PlantContextRef,
                      snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        """Telemetry-only context: cite the exact attested AGENT PROCESS_GRAPH source.

        The P1.1C store context ref maps through its recorded graph source; it is not
        relabeled as a P1.3 PlantContextRevision.
        """
        if ref.kind != INTERIM_PROCESS_GRAPH_SOURCE or ref.ref != snapshot["context_ref"]:
            raise ContextUnavailable("plant context is unavailable")
        graphs = [record for record in queries.context_inventory()["sources"]
                  if record["kind"] == "PROCESS_GRAPH"]
        if len(graphs) != 1:
            raise ContextUnavailable("plant context is unavailable")
        source = ContextSourceRef(**graphs[0])
        if context_ref_for(source) != ref.ref:
            raise ContextUnavailable("plant context is unavailable")
        return {"kind": INTERIM_PROCESS_GRAPH_SOURCE, "context_ref": ref.ref,
                "source": {name: graphs[0][name] for name in (
                    "source_id", "repository", "git_revision", "path_or_ref",
                    "content_checksum", "checksum_method", "kind", "schema_version")},
                "mapping": "P1.1C graph-source context ref; not a PlantContextRevision"}

    def screen(self, result: Mapping[str, Any]) -> None:
        if leakage_findings(result):
            raise VisibilityViolation("operational state AGENT result failed the leakage screen")


def operational_state_service(manager: Any) -> OperationalStateService:
    """Direct LOS-A-S1 service over one RunManager's public P0 queries."""
    return OperationalStateService(P0OperationalStateBoundary(manager),
                                   (TEP_CLEAN_STREAM_POLICY,))
