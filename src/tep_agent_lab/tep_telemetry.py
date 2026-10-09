"""TEP simulation source: sanitized TEP observations -> canonical telemetry batches.

P1.1B adapter (docs/p1-1b-tep-simulation-source.md)::

    TEPEnvironment / ReferenceWorld      (owners; they advance physics)
      -> tep_world.sanitize_observation   (existing Agent-visible allowlist)
      -> TEPSimulationSource.translate_sanitized
      -> tuple[SourceObservation, ...]    (one atomic batch per source record)
      -> TelemetryIngestor                (trusted caller supplies ingest_time)

TEP-specific identifiers exist inside this adapter, but they do not enter the
generic telemetry contract: every canonical ``signal_id`` is the human-verified
ProcessGraph ``semantic_entity_id``; XMEAS/XMV runtime ids stay adapter-local
binding metadata. Dependency direction is one way: this module imports
``telemetry``; ``telemetry`` never imports this module or ``tep_sim``.

The source holds no world: it never resets, steps, rolls out, applies, forks or
snapshots a simulator, and it holds no TEP physics. It translates records only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Any

from tep_sim import BindingMethod, BindingRelation, ProcessGraph

from .telemetry import (ClockDescriptor, ClockKind, Quality, SourceObservation,
                        TelemetryError, TimePoint, UnknownBinding, _checked_id)

TEP_SOURCE_VERSION = "tep-agent-lab.tep-simulation-source/v0"

SECONDS_PER_HOUR = 3600
# TEP advances in integral one-second steps, but tep-sim accumulates simulation
# time as a float sum of 1/3600 h. Measured drift: 1e-4 s at 30 simulated days,
# 1.2e-3 s at 90 days, 1.1e-2 s at 365 days. 50 ms covers a simulated year with
# margin while staying 20x tighter than one tick, so off-grid times (for example
# half a second) are still rejected. The sanitized record carries no step count.
TICK_TOLERANCE_SECONDS = 5e-2

# Exact key set of ``tep_world.sanitize_observation`` output. Anything else (raw
# Observation fields such as active disturbances, run internals) fails closed.
SANITIZED_FIELDS = frozenset({"simulation_time_hours", "measurements",
                              "manipulated_variables", "shutdown_state", "safety_margins"})

# Authoritative relation -> sanitized value map. DISTURBS is evaluator-only and absent.
_VALUE_MAPS = {BindingRelation.MEASURES: "measurements",
               BindingRelation.ACTUATES: "manipulated_variables"}
_HUMAN_VERIFIED = "HUMAN_VERIFIED"


class TEPSourceError(TelemetryError):
    """Invalid TEP source configuration or source record (fails closed)."""


def _checked_text(name: str, value: Any) -> str:
    # Same identifier rule as the generic core, raised as a source configuration error.
    try:
        return _checked_id(name, value)
    except TelemetryError as error:
        raise TEPSourceError(str(error)) from error


@dataclass(frozen=True)
class TEPSignalBinding:
    """Adapter-local record of one visible ProcessGraph binding and its provenance.

    ``signal_id`` (the semantic entity id) is the only field that enters the
    canonical telemetry store; everything else is TEP-specific provenance.
    """

    signal_id: str
    runtime_variable_id: str
    attached_to: str
    relation: BindingRelation
    quantity: str
    unit: str
    mapping_method: BindingMethod
    mapping_source_refs: tuple[str, ...]
    graph_fixture_id: str
    graph_fixture_version: str
    graph_content_sha256: str

    def describe(self) -> dict[str, Any]:
        return {"signal_id": self.signal_id, "runtime_variable_id": self.runtime_variable_id,
                "attached_to": self.attached_to, "relation": self.relation.value,
                "quantity": self.quantity, "unit": self.unit,
                "mapping_method": self.mapping_method.value,
                "mapping_source_refs": list(self.mapping_source_refs),
                "graph": {"fixture_id": self.graph_fixture_id,
                          "fixture_version": self.graph_fixture_version,
                          "content_sha256": self.graph_content_sha256}}


def build_signal_bindings(graph: ProcessGraph) -> tuple[TEPSignalBinding, ...]:
    """Deterministic binding table from ``ProcessGraph.bindings()``, sorted by signal id.

    Requires a pinned, human-verified graph and human-verified MEASURES/ACTUATES
    bindings that are one-to-one between semantic entity and runtime variable.
    """
    if not isinstance(graph, ProcessGraph):
        raise TEPSourceError("ProcessGraph required")
    provenance = graph.provenance
    if not provenance.pinned or provenance.review_status != _HUMAN_VERIFIED:
        raise TEPSourceError("source bindings require a pinned human-verified ProcessGraph")
    table: list[TEPSignalBinding] = []
    signals: set[str] = set()
    runtime: set[str] = set()
    for binding in graph.bindings():
        if binding.relation not in _VALUE_MAPS:
            # Never name the binding: a non-visible relation may identify a hidden cause.
            raise TEPSourceError("unsupported binding relation in ProcessGraph")
        _checked_text("semantic_entity_id", binding.semantic_entity_id)
        if binding.provenance.method is not BindingMethod.HUMAN_VERIFIED_MAPPING:
            raise TEPSourceError(f"binding {binding.semantic_entity_id} is not human-verified")
        if binding.semantic_entity_id in signals:
            raise TEPSourceError(f"duplicate canonical signal id {binding.semantic_entity_id}")
        if binding.runtime_variable_id in runtime:
            raise TEPSourceError(f"runtime variable {binding.runtime_variable_id} bound twice")
        signals.add(binding.semantic_entity_id)
        runtime.add(binding.runtime_variable_id)
        table.append(TEPSignalBinding(
            signal_id=binding.semantic_entity_id,
            runtime_variable_id=binding.runtime_variable_id,
            attached_to=binding.attached_to, relation=binding.relation,
            quantity=binding.quantity, unit=binding.describe()["unit"],
            mapping_method=binding.provenance.method,
            mapping_source_refs=tuple(binding.provenance.source_refs),
            graph_fixture_id=provenance.fixture_id,
            graph_fixture_version=provenance.fixture_version,
            graph_content_sha256=provenance.content_sha256))
    if not table:
        raise TEPSourceError("ProcessGraph has no visible bindings")
    if signals & runtime:
        # ``TEPSimulationSource.binding`` resolves either id kind; keep them disjoint.
        raise TEPSourceError("canonical signal id collides with a runtime variable id")
    return tuple(sorted(table, key=lambda item: item.signal_id))


def simulation_ticks(simulation_time_hours: Any) -> int:
    """Exact simulation-second tick of a sanitized ``simulation_time_hours``.

    ``ticks = round(hours * 3600)``, accepted only when
    ``|hours * 3600 - ticks| <= TICK_TOLERANCE_SECONDS``. Values must be finite and
    non-negative; off-grid times are rejected, never floored or truncated.
    """
    if type(simulation_time_hours) not in (int, float):
        raise TEPSourceError("simulation_time_hours must be a number")
    try:
        seconds = float(simulation_time_hours) * SECONDS_PER_HOUR
    except OverflowError:
        seconds = math.inf
    if not math.isfinite(seconds) or seconds < 0:
        raise TEPSourceError("simulation_time_hours must be finite and non-negative")
    ticks = round(seconds)
    if abs(seconds - ticks) > TICK_TOLERANCE_SECONDS:
        raise TEPSourceError("simulation time is not an integral simulator second")
    return ticks


class TEPSimulationSource:
    """Translator for one simulator source incarnation (one reset/run).

    Identity is fixed at construction and supplied by the caller: ``source_id`` and
    a SIMULATION clock (1 s/tick, explicit origin). Source sequence is the event
    tick, so one incarnation must never be reused across a simulator reset: a new
    reset needs a new ``source_id``/clock. The source owns no store, context ref,
    visibility policy or ingest time.
    """

    def __init__(self, graph: ProcessGraph, *, source_id: str, clock_id: str,
                 clock_origin: str) -> None:
        self._bindings = build_signal_bindings(graph)
        self.source_id = _checked_text("source_id", source_id)
        self.event_clock = ClockDescriptor(_checked_text("clock_id", clock_id),
                                           ClockKind.SIMULATION, Fraction(1),
                                           _checked_text("clock_origin", clock_origin))
        self._by_signal = {item.signal_id: item for item in self._bindings}
        self._by_runtime = {item.runtime_variable_id: item for item in self._bindings}
        self._expected = {field: frozenset(item.runtime_variable_id for item in self._bindings
                                           if _VALUE_MAPS[item.relation] == field)
                          for field in _VALUE_MAPS.values()}

    @property
    def signal_ids(self) -> tuple[str, ...]:
        return tuple(self._by_signal)

    def bindings(self) -> tuple[TEPSignalBinding, ...]:
        return self._bindings

    def binding(self, identifier: str) -> TEPSignalBinding:
        """Resolve by canonical signal id or adapter-local runtime variable id."""
        found = self._by_signal.get(identifier) or self._by_runtime.get(identifier)
        if found is None:
            raise UnknownBinding("no visible TEP source binding")
        return found

    def store_bindings(self) -> dict[str, frozenset[str]]:
        return {self.source_id: frozenset(self._by_signal)}

    def event_time(self, simulation_time_hours: Any) -> TimePoint:
        return TimePoint(self.event_clock.clock_id, simulation_ticks(simulation_time_hours))

    def translate_sanitized(self, record: Mapping[str, Any]) -> tuple[SourceObservation, ...]:
        """One sanitized TEP record -> one batch of every visible bound signal.

        All items share source_id, event_time and sequence (= event tick) and carry
        quality GOOD. Fails closed on unexpected fields, missing or unbound runtime
        values, nonfinite values or an invalid simulation time; nothing is dropped.
        ``shutdown_state`` and ``safety_margins`` are validated but not emitted.
        """
        if not isinstance(record, Mapping):
            raise TEPSourceError("sanitized observation mapping required")
        # Unexpected names are counted, never echoed: they may be hidden-state keys.
        keys = set(record)
        if keys != SANITIZED_FIELDS:
            raise TEPSourceError(
                f"sanitized observation fields mismatch: {len(keys - SANITIZED_FIELDS)} "
                f"unexpected, missing {sorted(SANITIZED_FIELDS - keys)}")
        if type(record["shutdown_state"]) is not bool:
            raise TEPSourceError("shutdown_state must be a boolean")
        if not isinstance(record["safety_margins"], Mapping):
            raise TEPSourceError("safety_margins must be a mapping")
        if not all(isinstance(key, str) and type(value) is float and math.isfinite(value)
                   for key, value in record["safety_margins"].items()):
            raise TEPSourceError("safety_margins must map names to finite floats")
        event_time = self.event_time(record["simulation_time_hours"])
        values: dict[str, float] = {}
        for field, expected in self._expected.items():
            mapping = record[field]
            if not isinstance(mapping, Mapping):
                raise TEPSourceError(f"{field} must be a mapping")
            present = set(mapping)
            if present != expected:
                raise TEPSourceError(
                    f"{field} does not match visible bindings: {len(present - expected)} "
                    f"unbound, missing {sorted(expected - present)}")
            for runtime_id in sorted(expected):
                value = mapping[runtime_id]
                if type(value) is not float or not math.isfinite(value):
                    raise TEPSourceError(f"{runtime_id} value must be a finite float")
                values[runtime_id] = value
        return tuple(SourceObservation(signal_id=item.signal_id, source_id=self.source_id,
                                       event_time=event_time, sequence=event_time.ticks,
                                       value=values[item.runtime_variable_id],
                                       quality=Quality.GOOD)
                     for item in self._bindings)
