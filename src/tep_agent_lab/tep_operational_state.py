"""Process-pack (TEP) composition of the generic Operational State service over P0 reads.

LOS-A-S1 (docs/p1-4a-operational-state.md). The generic ``operational_state`` module
owns the contracts and OD-1 interpretation; this module supplies only:

- the reviewed TEP policy record with the proposed OD-2 values (unit-specific trend
  tolerances grounded in the clean simulated stream, cadence-specific gap/freshness);
- signal descriptors from the AGENT ProcessGraphView (exact unit marker; numeric
  MEASURES/ACTUATES bindings);
- the exact interim PROCESS_GRAPH source citation (P1.1C context ref, not a P1.3
  PlantContextRevision) and the AGENT leakage screen.

Every telemetry read goes through ``RunManager.queries`` (public P0 RunQueries) with
the caller's exact snapshot. Nothing here steps a plant, opens a store or adds an
Agent tool, transport method or Application API version.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .application_views import AmbiguousSignal, UnknownSignal
from .canonical_context import ContextSourceRef, ProjectionScope
from .operational_state import (ContextUnavailable, InterpretationPolicy,
                                InvalidOperationalStateRequest, OperationalStateService,
                                PlantContextRef, SignalDescriptor, UnknownRun, ValueKind)
from .playground_views import RunQueries, VisibilityViolation
from .run_telemetry import context_ref_for
from .tep_world import leakage_findings

INTERIM_PROCESS_GRAPH_SOURCE = "INTERIM_PROCESS_GRAPH_SOURCE"
_NUMERIC_RELATIONS = frozenset({"MEASURES", "ACTUATES"})

# Proposed OD-2 values (owner confirmation pending in the LOS-A-S1 PR). Tolerances are
# in signal unit per second; see docs/p1-4a-operational-state.md for the measurement.
TEP_CLEAN_STREAM_POLICY = InterpretationPolicy(
    policy_id="tep-agent-lab.tep-clean-stream-operational-state",
    version="v0",
    default_window_ticks=120,
    max_window_ticks=999,
    max_input_points=1000,
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


class P0OperationalStateBoundary:
    """Public P0 queries (AGENT scope) plus pinned graph context for one RunManager."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def queries(self, run_id: str) -> RunQueries:
        try:
            queries = self._manager.queries(run_id)
        except KeyError:
            raise UnknownRun("unknown run") from None
        except ValueError:
            raise InvalidOperationalStateRequest("invalid run id") from None
        if queries.scope != ProjectionScope.AGENT:
            raise VisibilityViolation("operational state requires AGENT scope")
        return queries

    def signal_descriptor(self, queries: RunQueries, signal_id: str) -> SignalDescriptor:
        graph = queries.process_graph()
        bindings = [binding for category in ("nodes", "edges") for entity in graph[category]
                    for binding in entity["bindings"]
                    if binding["semantic_entity_id"] == signal_id]
        if not bindings or any(b["relation"] not in _NUMERIC_RELATIONS for b in bindings):
            raise UnknownSignal("unknown signal")
        units = {binding["unit"] for binding in bindings}
        if len(units) != 1:
            raise AmbiguousSignal("inconsistent signal binding metadata")
        return SignalDescriptor(ValueKind.NUMERIC, units.pop())

    def plant_context(self, queries: RunQueries, ref: PlantContextRef,
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
