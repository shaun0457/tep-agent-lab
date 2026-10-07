"""Lab-side adapters over the pinned tep-sim World Plane for the blind-RCA surface.

No TEP physics lives here. Every simulation, capability, bound, control-mode,
snapshot, and safety fact comes from the public ``tep_sim`` API. This module owns
only lab policy concerns (tool-surface-v0.md):

- Agent-visible sanitization of world outputs (hidden disturbance state removed);
- the harness-owned reference world and its salted, non-invertible revision;
- opaque snapshot/branch handles for isolated SIMULATE work;
- content-addressed Agent-visible artifacts for dense telemetry.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
import secrets
from typing import Any

from industrial_agent_runtime import InformationRef, Visibility, to_jsonable
from tep_sim import (ArtifactRef, ProcessGraph, Snapshot, TEPEnvironment,
                     load_process_graph)
from tep_sim.snapshot import observation_sha256

ARTIFACT_OWNER = "tep-agent-lab"
ARTIFACT_VERSION = "tep-agent-lab.artifacts/v0"
REFERENCE = "reference"
BASELINE = "baseline"

# Any spelling of a disturbance id (IDV(4), IDV6, idv_1, ...), mirroring tep-sim's
# visible-graph guard, plus vocabulary that only evaluator/ground-truth data needs.
_DISTURBANCE_ID = re.compile(r"(?<![A-Za-z])IDV[\s_\-(]*\d+", re.IGNORECASE)
_HIDDEN_TERMS = re.compile(r"(?<![a-z])(disturbance|evaluator|fault|candidate_cause)",
                           re.IGNORECASE)
HIDDEN_KEYS = frozenset({"active_disturbances"})


class WorldError(Exception):
    """A world operation failed with an Agent-reportable failure code."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


def leakage_findings(value: Any, path: str = "$") -> list[str]:
    """Every location where Agent-visible data carries hidden-truth vocabulary."""
    value = to_jsonable(value)
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key = str(key)
            if key in HIDDEN_KEYS or _hidden_text(key):
                found.append(f"{path}.{key}: hidden key")
            found += leakage_findings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += leakage_findings(item, f"{path}[{index}]")
    elif isinstance(value, str) and _hidden_text(value):
        found.append(f"{path}: hidden value")
    return found


def _hidden_text(text: str) -> bool:
    return bool(_DISTURBANCE_ID.search(text) or _HIDDEN_TERMS.search(text))


def names_disturbance_id(text: str) -> bool:
    """Any spelling of a disturbance id (IDV(4), idv_4, ...)."""
    return bool(_DISTURBANCE_ID.search(text))


def hidden_vocabulary(text: str) -> bool:
    """Hidden-truth vocabulary that ``leakage_findings`` screens Agent data for."""
    return _hidden_text(text)


def sanitize_observation(record: Any) -> dict[str, Any]:
    """Agent-visible view of a tep-sim Observation or telemetry record.

    Allowlist, not denylist: only plant measurements, manipulated variables,
    shutdown state, and safety margins survive. Active disturbances never do.
    """
    get = record.get if isinstance(record, Mapping) else (lambda name: getattr(record, name))
    return {"simulation_time_hours": float(get("simulation_time")),
            "measurements": {key: float(value) for key, value in get("measurements").items()},
            "manipulated_variables": {key: float(value) for key, value
                                      in get("manipulated_variables").items()},
            "shutdown_state": bool(get("shutdown_state")),
            "safety_margins": {key: float(value) for key, value
                               in get("safety_margins").items()}}


def read_telemetry(artifact: ArtifactRef) -> list[dict[str, Any]]:
    """Checksum-verified records of a tep-sim telemetry artifact (A1 contract)."""
    try:
        data = Path(artifact.path).read_bytes()
    except OSError as exc:
        raise WorldError("ARTIFACT_ERROR", "telemetry artifact is unreadable") from exc
    if hashlib.sha256(data).hexdigest() != artifact.sha256:
        raise WorldError("ARTIFACT_ERROR", "telemetry artifact checksum mismatch")
    records = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    if not records:
        raise WorldError("ARTIFACT_ERROR", "telemetry artifact has no records")
    return records


def _canonical(value: Any) -> bytes:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


class ArtifactStore:
    """Content-addressed, Agent-visible lab artifacts with opaque sequential ids.

    Raw tep-sim artifact paths never leave the lab; dense data is re-written here
    after sanitization and leakage screening. ``resolve`` is the exact-ref
    resolver the RCA TaskStateStore needs for result-carried artifact refs.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._refs: dict[str, InformationRef] = {}
        self._sequence = 0

    def put_records(self, kind: str, records: Sequence[Mapping[str, Any]],
                    created_at: str) -> InformationRef:
        findings = leakage_findings(list(records))
        if findings:
            raise WorldError("ARTIFACT_ERROR", "artifact content failed the leakage screen")
        data = b"".join(_canonical(record) + b"\n" for record in records)
        try:
            while True:  # exclusive create: an issued artifact is never overwritten
                self._sequence += 1
                ref_id = f"tep-artifact-{self._sequence:06d}"
                try:
                    with (self.root / f"{ref_id}.jsonl").open("xb") as stream:
                        stream.write(data)
                    break
                except FileExistsError:
                    continue
        except OSError as exc:
            raise WorldError("ARTIFACT_ERROR", "artifact could not be persisted") from exc
        ref = InformationRef(ref_id, kind, ARTIFACT_OWNER, ARTIFACT_VERSION, Visibility.AGENT,
                             created_at, hashlib.sha256(data).hexdigest())
        self._refs[ref_id] = ref
        return ref

    def resolve(self, ref: InformationRef) -> InformationRef | None:
        return ref if self._refs.get(getattr(ref, "ref_id", None)) == ref else None

    def verify(self, ref: InformationRef) -> bool:
        """The ref was issued here and its persisted bytes still match its checksum."""
        if self.resolve(ref) is None:
            return False
        try:
            data = (self.root / f"{ref.ref_id}.jsonl").read_bytes()
        except OSError:
            return False
        return hashlib.sha256(data).hexdigest() == ref.checksum

    def read(self, ref: InformationRef) -> list[dict[str, Any]]:
        if not self.verify(ref):
            raise WorldError("ARTIFACT_ERROR", "artifact is unknown or corrupted")
        data = (self.root / f"{ref.ref_id}.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in data.splitlines() if line]


class ReferenceWorld:
    """Harness-owned reference (non-sandbox) world. Never registered as a tool.

    The benchmark harness creates the incident (including any evaluator-chosen
    disturbance) directly on ``environment`` and advances it with ``advance``.
    Agent tools only read sanitized views or fork isolated branches from it.
    """

    def __init__(self, environment: TEPEnvironment, graph: ProcessGraph | None = None, *,
                 revision_salt: str | None = None) -> None:
        if not isinstance(environment, TEPEnvironment):
            raise TypeError("TEPEnvironment required")
        self.environment = environment
        self.graph = graph if graph is not None else load_process_graph()
        # Salted so the Agent cannot test disturbance guesses against a revision
        # computed from otherwise-visible observation fields.
        self._salt = revision_salt if revision_salt is not None else secrets.token_hex(16)
        self._history = [sanitize_observation(environment.observe())]
        self._handles = 0
        self._baselines: dict[str, Snapshot] = {}

    def new_handle(self, prefix: str) -> str:
        """World-unique opaque handle; tep-sim snapshot/branch directories never collide."""
        self._handles += 1
        return f"{prefix}-{self._handles:04d}"

    def designate_baseline(self) -> str:
        """Trusted harness only: register the current state as a counterfactual origin.

        Semantic scenarios run only on branches descending from such an origin (see
        ``SimulationSandbox``): re-applying a cause already active in a fork of the
        incident state is a no-op that would reveal the hidden cause.

        The benchmark harness is authoritative for calling this before any hidden
        incident mutation; neither the Agent nor ``active_disturbances`` decides it.
        The ``active_disturbances`` check below is only an additional guard for IDV
        injections. It cannot detect other hidden incident state (XMV, constraint, or
        operating-condition manipulation) and is not proof that a state is clean.
        """
        if self.environment.observe().active_disturbances:
            raise ValueError("IDV guard: a counterfactual origin must precede hidden "
                             "disturbance injection")
        handle = self.new_handle("snapshot")
        self._baselines[handle] = self.environment.snapshot(snapshot_id=handle)
        return handle

    def baselines(self) -> dict[str, Snapshot]:
        return dict(self._baselines)

    def advance(self, horizon_hours: float) -> None:
        """Harness/evaluator only: step the reference world and extend its history."""
        result = self.environment.rollout(horizon_hours)
        records = read_telemetry(result.telemetry)
        self._history.extend(sanitize_observation(record) for record in records[1:])

    def observe(self) -> dict[str, Any]:
        return sanitize_observation(self.environment.observe())

    def history(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._history)

    @property
    def control_mode(self):
        return self.environment.config.control_mode

    def revision(self) -> str:
        """Salted fingerprint of the complete reference state, hidden fields included."""
        state = {"run_id": self.environment.run_id, "branch_id": self.environment.branch_id,
                 "observation": observation_sha256(self.environment.observe()),
                 "history_records": len(self._history)}
        digest = hashlib.sha256(self._salt.encode("utf-8") + _canonical(state)).hexdigest()
        return f"reference-{digest}"


class SimulationSandbox:
    """Opaque snapshot/branch handles over tep-sim A2 snapshot/fork. Lab bookkeeping only.

    A handle is a world-unique sequential id; tep-sim paths, run ids, and random
    state metadata are never exposed. The reference world is never a branch.
    Every snapshot and branch carries a lineage: ``baseline`` when it descends from
    a harness-designated baseline, else ``reference``.
    """

    def __init__(self, world: ReferenceWorld) -> None:
        self.world = world
        # handle -> (snapshot, owning environment, source handle, lineage)
        self._snapshots: dict[str, tuple[Snapshot, TEPEnvironment, str, str]] = {}
        # handle -> (environment, parent snapshot handle, lineage)
        self._branches: dict[str, tuple[TEPEnvironment, str, str]] = {}
        # no longer usable handle -> (kind, parent/source handle, lineage, status);
        # read-projection bookkeeping only
        self._retired: dict[str, tuple[str, str, str, str]] = {}
        self._closed = False

    def _snapshot_entry(self, handle: Any):
        if not isinstance(handle, str):
            return None
        if handle in self._snapshots:
            return self._snapshots[handle]
        baseline = self.world.baselines().get(handle)
        if baseline is not None:
            return baseline, self.world.environment, REFERENCE, BASELINE
        return None

    def has_snapshot(self, handle: Any) -> bool:
        return self._snapshot_entry(handle) is not None

    def has_branch(self, handle: Any) -> bool:
        return isinstance(handle, str) and handle in self._branches

    def branch(self, handle: str) -> TEPEnvironment:
        if not self.has_branch(handle):
            raise WorldError("INVALID_REQUEST", "unknown or retired branch")
        return self._branches[handle][0]

    def branch_parent(self, handle: str) -> str:
        return self._branches[handle][1]

    def lineage(self, handle: str) -> str:
        """``baseline`` or ``reference`` for a branch, snapshot, or the reference."""
        if handle in self._branches:
            return self._branches[handle][2]
        entry = self._snapshot_entry(handle)
        return entry[3] if entry is not None else REFERENCE

    def source(self, handle: str) -> TEPEnvironment:
        if handle == REFERENCE:
            return self.world.environment
        return self.branch(handle)

    def snapshot(self, source: str) -> tuple[str, Snapshot]:
        environment = self.source(source)
        handle = self.world.new_handle("snapshot")
        snapshot = environment.snapshot(snapshot_id=handle)
        self._snapshots[handle] = (snapshot, environment, source, self.lineage(source))
        return handle, snapshot

    def snapshot_source(self, handle: str) -> str:
        return self._snapshot_entry(handle)[2]

    def fork(self, snapshot_handle: str) -> tuple[str, TEPEnvironment]:
        entry = self._snapshot_entry(snapshot_handle)
        if entry is None:
            raise WorldError("INVALID_REQUEST", "unknown snapshot")
        snapshot, environment, _source, lineage = entry
        handle = self.world.new_handle("branch")
        branch = environment.fork(snapshot, branch_id=handle)
        if branch is environment:
            raise WorldError("SIMULATION_FAILED", "fork did not produce an isolated branch")
        self._branches[handle] = (branch, snapshot_handle, lineage)
        return handle, branch

    def retire(self, handle: str) -> None:
        """Remove a failed branch and the snapshots it owns (they can no longer fork).

        Removal happens first, so the branch is unavailable even if closing fails.
        """
        entry = self._branches.pop(handle, None)
        if entry is not None:
            self._retired[handle] = ("BRANCH", entry[1], entry[2], "RETIRED")
            for name in [key for key, value in self._snapshots.items() if value[2] == handle]:
                _, _, source, lineage = self._snapshots.pop(name)
                self._retired[name] = ("SNAPSHOT", source, lineage, "RETIRED")
            try:
                entry[0].close()
            except Exception:
                pass

    def lineage_records(self) -> tuple[dict[str, Any], ...]:
        """Opaque-handle lineage for read projections; no paths, run ids, or random state."""
        released = "RELEASED" if self._closed else None
        records = [{"handle": handle, "kind": "SNAPSHOT", "parent": REFERENCE,
                    "lineage": BASELINE, "status": released or "DESIGNATED_BASELINE"}
                   for handle in sorted(self.world.baselines())]
        records += [{"handle": handle, "kind": "SNAPSHOT", "parent": source,
                     "lineage": lineage, "status": "AVAILABLE"}
                    for handle, (_, _, source, lineage) in tuple(self._snapshots.items())]
        records += [{"handle": handle, "kind": "BRANCH", "parent": parent,
                     "lineage": lineage, "status": "ACTIVE"}
                    for handle, (_, parent, lineage) in tuple(self._branches.items())]
        records += [{"handle": handle, "kind": kind, "parent": parent,
                     "lineage": lineage, "status": status}
                    for handle, (kind, parent, lineage, status) in tuple(self._retired.items())]
        return tuple(sorted(records, key=lambda record: record["handle"]))

    def close(self) -> None:
        """Release every branch environment (idempotent); lineage stays readable.

        Snapshots can no longer be forked once their environments are closed, so they
        are reported as released too.
        """
        self._closed = True
        while self._snapshots:
            handle, (_, _, source, lineage) = self._snapshots.popitem()
            self._retired[handle] = ("SNAPSHOT", source, lineage, "RELEASED")
        while self._branches:
            handle, (environment, parent, lineage) = self._branches.popitem()
            self._retired[handle] = ("BRANCH", parent, lineage, "RELEASED")
            try:
                environment.close()
            except Exception:
                pass
