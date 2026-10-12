"""Versioned trusted live delivery lifecycle, beside P0 and runtime lifecycles.

LOS-B-S1 runs a finite producer thread, independently of public reads. Pause,
pacing alternatives, capacity-stop, retry and restart belong to later slices.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import secrets
import threading
from typing import Any

from industrial_agent_runtime import checksum, to_jsonable
from industrial_agent_runtime.serialization import freeze_json

from .persistence import canonical_json
from .run_telemetry import INGEST_CLOCK, LiveTelemetry
from .telemetry import SourceObservation

LIVE_LIFECYCLE_VERSION = "tep-agent-lab.live-plant-lifecycle/v0"
LIVE_PROVENANCE_VERSION = "tep-agent-lab.live-plant-provenance/v0"
LIVE_POLICY_VERSION = "tep-agent-lab.live-plant-demo-policy/v0"


class LivePlantState(StrEnum):
    PREPARED = "PREPARED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"  # reserved by the owning v0 lifecycle; no S1 transition
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class LivePlantError(RuntimeError):
    """Illegal trusted lifecycle operation; never contains raw source diagnostics."""


@dataclass(frozen=True)
class LivePlantPolicy:
    """OD-6 proposed demonstration bounds, pinned by exact content checksum.

    S1 reserves the entire configured trajectory at preparation, rather than
    implementing S3's dynamic capacity-stop or retention/eviction decisions.
    """

    version: str = LIVE_POLICY_VERSION
    cadence_ticks: int = 1
    step_count: int = 300
    retained_sample_cap: int = 16_000
    retained_byte_cap: int = 16 * 1024 * 1024
    sample_byte_cap: int = 1024
    batch_byte_cap: int = 64 * 1024
    pending_batch_cap: int = 1
    commit_cap: int = 301
    index_record_cap: int = 16_000
    association_byte_cap: int = 1024
    session_event_cap: int = 3
    event_byte_cap: int = 1024
    simulator_history_cap: int = 0
    rollout_artifact_cap: int = 0
    session_cap: int = 1

    def __post_init__(self) -> None:
        if self.version != LIVE_POLICY_VERSION:
            raise ValueError("unsupported live policy version")
        for name in self.__dataclass_fields__:
            if name != "version":
                value = getattr(self, name)
                minimum = 0 if name in ("simulator_history_cap", "rollout_artifact_cap") else 1
                if type(value) is not int or value < minimum:
                    raise ValueError(f"{name} must be an integer >= {minimum}")
        if (self.cadence_ticks != 1 or self.pending_batch_cap != 1 or self.session_cap != 1
                or self.simulator_history_cap != 0 or self.rollout_artifact_cap != 0):
            raise ValueError("unsupported S1 delivery/resource policy")

    def reserve(self, telemetry: LiveTelemetry) -> None:
        """Check whole trajectory bounds before any physics step or initial commit."""
        commits = self.step_count + 1
        signals = len(telemetry.source.signal_ids)
        samples = commits * signals
        if (commits > telemetry.policy.max_scan_records or commits > self.commit_cap
                or samples > self.retained_sample_cap or samples > self.index_record_cap
                or samples * self.sample_byte_cap > self.retained_byte_cap
                or signals * self.sample_byte_cap > self.batch_byte_cap
                or self.session_event_cap < 3):
            raise ValueError("configured trajectory exceeds reserved live/reader bounds")
        # The pinned source emits floats only, no free-form metadata. Reserve the
        # longest finite float, final sequence/event tick and diagnostic UTC tick.
        for signal in telemetry.source.signal_ids:
            worst = {"signal_id": signal, "source_id": telemetry.source_id,
                     "sequence": self.step_count,
                     "event_time": {"clock_id": telemetry.source.event_clock.clock_id,
                                    "ticks": self.step_count},
                     "value": -2.2250738585072014e-308, "quality": "GOOD",
                     "source_status_code": None, "source_metadata_ref": None,
                     "ingest_time": {"clock_id": INGEST_CLOCK.clock_id, "ticks": 9007199254740991}}
            if len(canonical_json(worst)) > self.sample_byte_cap:
                raise ValueError("pinned source record exceeds the reserved sample size")
        association = LiveCommit(self.step_count, self.step_count, self.step_count,
                                 commits, "0" * 64)
        event = {"version": LIVE_LIFECYCLE_VERSION, "state": "PREPARED",
                 "reason": "resource_close_unavailable", "last_step": self.step_count,
                 "ingest_sequence": commits}
        if (len(canonical_json(to_jsonable(association))) > self.association_byte_cap
                or len(canonical_json(event)) > self.event_byte_cap):
            raise ValueError("session metadata exceeds its reserved record size")


@dataclass(frozen=True)
class LiveCommit:
    step: int
    source_sequence: int
    event_ticks: int
    ingest_sequence: int
    batch_checksum: str


class LivePlantSession:
    """Single-owner finite producer. All diagnostics/provenance are trusted-only.

    ``advance`` returns one sanitized source record through the existing process
    adapter. It is never exposed by RunQueries, application reads or Agent tools.
    No world history, rollout artifact, second state model or scheduler is built.
    """

    def __init__(self, *, session_id: str, telemetry: LiveTelemetry, policy: LivePlantPolicy,
                 initial: Mapping[str, Any], advance: Callable[[], Mapping[str, Any]],
                 close_physics: Callable[[], None]) -> None:
        policy.reserve(telemetry)
        self.session_id, self.telemetry, self.policy = session_id, telemetry, policy
        self._advance: Callable[[], Mapping[str, Any]] | None = advance
        self._close_physics: Callable[[], None] | None = close_physics
        self._condition = threading.Condition()
        self._state = LivePlantState.PREPARED
        self._owner: str | None = None
        self._thread: threading.Thread | None = None
        self._physics_closed = False
        self._commits: list[LiveCommit] = []
        self._events: list[Mapping[str, Any]] = []
        self._pending: tuple[SourceObservation, ...] | None = None
        self._provenance: Mapping[str, Any] | None = None
        self._diagnostic: Mapping[str, Any] | None = None
        self._deliver(0, initial)
        self._event(LivePlantState.PREPARED, "initial_committed")

    @property
    def state(self) -> LivePlantState:
        with self._condition:
            return self._state

    @property
    def owner(self) -> str | None:
        with self._condition:
            return self._owner

    @property
    def physics_closed(self) -> bool:
        with self._condition:
            return self._physics_closed

    @property
    def commits(self) -> tuple[LiveCommit, ...]:
        with self._condition:
            return tuple(self._commits)

    @property
    def last_step(self) -> int:
        with self._condition:
            return self._commits[-1].step

    @property
    def events(self) -> tuple[Mapping[str, Any], ...]:
        with self._condition:
            return tuple(self._events)

    @property
    def provenance(self) -> Mapping[str, Any]:
        if self._provenance is None:
            raise LivePlantError("live attachment has not been attested")
        return self._provenance

    def attach(self, manifest: Any, graph_source: Any) -> None:
        """Pin READY attachment before it is published; never mutate that inventory."""
        if self._provenance is not None:
            raise LivePlantError("live attachment is immutable")
        self._provenance = freeze_json({
            "version": LIVE_PROVENANCE_VERSION, "lifecycle_version": LIVE_LIFECYCLE_VERSION,
            "run_id": manifest.run_id, "session_id": self.session_id,
            "manifest_checksum": manifest.checksum(),
            "inventory_checksum": manifest.context_inventory_checksum,
            "source_revisions": manifest.source_revisions,
            "numerical_stack": manifest.numerical_stack,
            "world": manifest.world,
            "plant_source": graph_source.record(), "engineering_selection": [],
            "context_ref": self.telemetry.store.context_ref,
            "visibility_policy_ref": self.telemetry.store.visibility_policy_ref,
            "source_version": self.telemetry.source_version,
            "source_id": self.telemetry.source_id,
            "binding_checksum": checksum([b.describe() for b in self.telemetry.source.bindings()]),
            "clock": self.telemetry.source.event_clock.as_json(),
            "store": self.telemetry.store.ref.as_json(),
            "reader_policy": to_jsonable(self.telemetry.policy),
            "delivery": "synchronous-deterministic/v0",
            "policy": to_jsonable(self.policy), "policy_checksum": checksum(to_jsonable(self.policy)),
            "read_lifetime": "retained in this process; no eviction or durable recovery"})
        if len(canonical_json(to_jsonable(self._provenance))) > self.policy.batch_byte_cap:
            raise LivePlantError("session provenance exceeds its reserved frame size")

    def _event(self, state: LivePlantState, reason: str) -> None:
        event = freeze_json({"version": LIVE_LIFECYCLE_VERSION, "state": state.value,
                             "reason": reason, "last_step": self.last_step,
                             "ingest_sequence": self._commits[-1].ingest_sequence})
        if (len(self._events) >= self.policy.session_event_cap
                or len(canonical_json(to_jsonable(event))) > self.policy.event_byte_cap):
            raise LivePlantError("reserved lifecycle event capacity exceeded")
        self._events.append(event)

    def _deliver(self, step: int, record: Mapping[str, Any]) -> None:
        if record["shutdown_state"]:
            raise LivePlantError("source unavailable")
        # Exactly one translation: retain the assigned tuple until commit, never
        # allocate another identity or advance again during synchronous delivery.
        batch = self.telemetry.source.translate_sanitized(record)
        self._pending = batch
        expected_tick = step * self.policy.cadence_ticks
        if any(item.event_time.ticks != expected_tick for item in batch):
            raise LivePlantError("source event cadence differs from prepared policy")
        if len(canonical_json(to_jsonable(batch))) > self.policy.batch_byte_cap:
            raise LivePlantError("source batch exceeds reserved frame size")
        result = self.telemetry.commit_batch(batch)
        association = LiveCommit(step, batch[0].sequence, expected_tick,
                                 result.ingest_sequence, checksum(to_jsonable(batch)))
        if len(canonical_json(to_jsonable(association))) > self.policy.association_byte_cap:
            raise LivePlantError("commit association exceeds reserved size")
        with self._condition:
            self._commits.append(association)
            self._pending = None
            self._condition.notify_all()

    def start(self) -> None:
        """Acquire one owner and launch; public reads never call this operation."""
        with self._condition:
            if self._state != LivePlantState.PREPARED or self._owner is not None:
                raise LivePlantError("live start requires an unowned PREPARED session")
            if self._provenance is None:
                raise LivePlantError("live attachment has not been attested")
            self._owner = secrets.token_hex(16)
            self._state = LivePlantState.ACTIVE
            self._event(self._state, "trusted_start")
            self._thread = threading.Thread(target=self._produce, name="live-plant", daemon=False)
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                self._finish(LivePlantState.FAILED, "producer_unavailable")
                raise

    def _produce(self) -> None:
        state, reason = LivePlantState.STOPPED, "configured_steps_reached"
        try:
            for step in range(1, self.policy.step_count + 1):
                if self._advance is None:
                    raise LivePlantError("producer resource unavailable")
                self._deliver(step, self._advance())
        except BaseException as exc:
            # Minimal fail-closed guard, no retry/recovery/failure policy from S3.
            # Retain type only, never copy raw simulator text into public logs.
            self._diagnostic = freeze_json({"category": type(exc).__name__,
                                           "last_step": self.last_step,
                                           "pending_batch_checksum": (None if self._pending is None
                                               else checksum(to_jsonable(self._pending)))})
            state, reason = LivePlantState.FAILED, "source_unavailable"
        finally:
            self._finish(state, reason)

    def _finish(self, state: LivePlantState, reason: str) -> None:
        closed = False
        try:
            if self._close_physics is not None:
                self._close_physics()
            closed = True
        except BaseException:
            state, reason = LivePlantState.FAILED, "resource_close_unavailable"
        finally:
            # Drop bound environment callbacks as well as closing the public
            # resource. Only committed telemetry/diagnostics remain retained.
            self._advance = None
            self._close_physics = None
        with self._condition:
            self._physics_closed = closed
            self._owner = None
            self._state = state
            self._event(state, reason)
            self._condition.notify_all()

    def wait_for_commit(self, ingest_sequence: int, timeout: float = 30) -> None:
        """Trusted supervision only; waits for a producer, never advances it."""
        if type(ingest_sequence) is not int or not 1 <= ingest_sequence <= self.policy.step_count + 1:
            raise ValueError("commit must lie within the prepared trajectory")
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._commits[-1].ingest_sequence >= ingest_sequence
                or self._state in (LivePlantState.STOPPED, LivePlantState.FAILED), timeout)
            if not ready or self._commits[-1].ingest_sequence < ingest_sequence:
                raise LivePlantError("requested committed prefix unavailable")

    def wait(self, timeout: float = 30) -> None:
        """Join the producer for cleanup; reads remain retained after it ends."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise LivePlantError("live producer has not ended")

    def close_prepared(self) -> None:
        """Release an unstarted preparation; active stop arrives in LOS-B-S2."""
        with self._condition:
            if self._state == LivePlantState.PREPARED:
                self._finish(LivePlantState.STOPPED, "preparation_closed")
