"""P0 Playground Backend: run lifecycle, immutable manifest, outcome (playground-backend-v0.md).

The Application Plane assembles and projects existing canonical owners; it never
replaces them and is not a second Agent runtime, scheduler, TaskStateStore, ProcessGraph
or world-truth store:

- ``RunStatus`` is an application-hosting lifecycle, distinct from runtime ``TaskStatus``;
- ``RunManifest`` is published atomically at READY and is immutable thereafter;
- ``RunOutcome`` records terminal execution metadata separately;
- ``RunManager.start`` gives exactly one execution owner one ``Coordinator.run()``;
  every Agent tool call still goes through B2 gates, the lab consumer, the Executor,
  and B3 verification. There is no backend shortcut that advances Agent-visible state.

Local-first and transport-neutral: no HTTP, database, event bus or worker pool.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import threading
from typing import Any

import numpy
from industrial_agent_runtime import (VERIFIER_VERSION, Budget, Coordinator, InformationRef,
                                      ModelProvider, RuntimeResult, Task, TraceRecorder,
                                      Visibility, checksum, to_jsonable)
from industrial_agent_runtime.serialization import freeze_json
from tep_sim import (CAPABILITY_VERSION, SAFETY_LIMITS_VERSION, SCENARIO_MAPPING_VERSION,
                     UPSTREAM_REVISION, ControlMode, EnvironmentConfig, TEPEnvironment,
                     build_process_graph)
from tep_sim.snapshot import ENVIRONMENT_VERSION

from .canonical_context import (CanonicalContextRegistry, ContextSourceRef,
                                PackageSourceMaterializer, ProjectionScope,
                                SourceMaterializer, inventory_checksum)
from .investigation import (RcaResultIngestor, RcaState, RcaStateStore,
                            readiness_deficiencies)
from .persistence import RunLog, canonical_json
from .tep_world import ArtifactStore, ReferenceWorld
from .tool_bridge import BRIDGE_VERSION, AnalysisToolBridge, BridgedToolSurface
from .tool_surface import TOOL_SURFACE_VERSION, BlindRcaToolSurface

MANIFEST_VERSION = "tep-agent-lab.playground-run-manifest/v0"
OUTCOME_VERSION = "tep-agent-lab.playground-run-outcome/v0"
LIFECYCLE_VERSION = "tep-agent-lab.playground-lifecycle/v0"
VISIBILITY_POLICY_VERSION = "tep-agent-lab.playground-visibility/v0"
RCA_VISIBILITY_POLICY_VERSION = "rca-visible-v0"

TEP_SIM_REPOSITORY = "shaun0457/tep-sim"
RUNTIME_REPOSITORY = "shaun0457/industrial-agent-runtime"
LAB_REPOSITORY = "shaun0457/tep-agent-lab"

# Pinned canonical sources packaged by tep-sim 0.2.0. Content checksums are
# canonical-JSON sha256 (line-ending independent); fixture identity is tep-sim's own.
PROCESS_GRAPH_SOURCE_ID = "tep-sim.process-graph"
PROCESS_GRAPH_PATH = "src/tep_sim/fixtures/tep_process_graph_v0_2_0.json"
PROCESS_GRAPH_CONTENT_CHECKSUM = "edd41ae4dd7473d2a350ab5f11b694d651d241872ed2ea757f2a38d49426fe09"
PROCESS_GRAPH_FIXTURE = {"fixture_id": "tep-process-graph", "fixture_version": "0.2.0",
                         "fixture_content_sha256": "cc8ccc81e9f421238863457438465877850b19d9760740279e54a52468fe9a87"}
PROCESS_GRAPH_REVIEW_STATUS = "HUMAN_VERIFIED"
EVALUATOR_BINDINGS_SOURCE_ID = "tep-sim.evaluator-disturbance-bindings"
EVALUATOR_BINDINGS_PATH = "src/tep_sim/fixtures/tep_evaluator_disturbance_bindings_v0_2_0.json"
EVALUATOR_BINDINGS_CONTENT_CHECKSUM = (
    "d0cc9f81f043d8aea4b413efc1b5416ff79e540e79fbc976e425e09bfc4dc6ae")

_RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_RESERVED_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
                             *(f"lpt{i}" for i in range(10))})
_SESSION_DIR = re.compile(r"prepare-[0-9]{4,}")
_GIT_REVISION = re.compile(r"[0-9a-f]{40}")
# Keys are compared after lowercasing and dropping separators, so authToken,
# client_secret and X-Api-Key all normalize to a matching compound token.
_SECRET_KEY_SUFFIXES = ("token", "secret", "password", "passwd", "passphrase", "pwd",
                        "cookie", "credential", "credentials", "bearer")
_SECRET_KEY_PARTS = ("apikey", "privatekey", "deploykey", "accesskey", "secretkey",
                     "password", "passphrase", "credential", "authorization")
_SECRET_VALUE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk[-_](live_|test_)?[A-Za-z0-9_-]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b"
    r"|\bAIza[0-9A-Za-z_-]{30,}|\bxox[abposr]-[A-Za-z0-9-]{10,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.|\bBearer\s+[A-Za-z0-9._~+/-]{8,}"
    r"|[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)
MAX_FAILURE_DETAIL = 2000


class RunStatus(StrEnum):
    """Application-hosting lifecycle; never a mirror of runtime ``TaskStatus``."""

    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED})


class LifecycleError(RuntimeError):
    """A deterministic illegal transition or duplicate/concurrent execution attempt."""


class PrepareError(RuntimeError):
    """Preparation failed atomically: no manifest was published and the run stays CREATED."""


def secret_findings(value: Any, path: str = "$") -> list[str]:
    """Locations of credential-shaped keys/values; manifests must never carry any."""
    value = to_jsonable(value)
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            compact = re.sub(r"[^a-z0-9]", "", key.lower())
            if (compact.endswith(_SECRET_KEY_SUFFIXES)
                    or any(part in compact for part in _SECRET_KEY_PARTS)):
                found.append(f"{path}.{key}: credential-shaped key")
            found += secret_findings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += secret_findings(item, f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        found.append(f"{path}: credential-shaped value")
    return found


def _failure_text(exc: BaseException) -> str:
    """Bounded, credential-screened failure detail; hidden detail stays EVALUATOR-only."""
    text = f"{type(exc).__name__}: {exc}"
    if secret_findings(text):
        return f"{type(exc).__name__}: detail withheld (credential-shaped content)"
    return text[:MAX_FAILURE_DETAIL]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


@dataclass(frozen=True)
class SourceRevisions:
    """Exact repository revisions, attested by the trusted host (CI: scripts/check.py)."""

    tep_sim: str
    industrial_agent_runtime: str
    tep_agent_lab: str

    def __post_init__(self) -> None:
        for name in ("tep_sim", "industrial_agent_runtime", "tep_agent_lab"):
            if not isinstance(getattr(self, name), str) or not _GIT_REVISION.fullmatch(
                    getattr(self, name)):
                raise ValueError(f"{name} must be an exact 40-hex git revision")

    def by_repository(self) -> dict[str, str]:
        return {TEP_SIM_REPOSITORY: self.tep_sim,
                RUNTIME_REPOSITORY: self.industrial_agent_runtime,
                LAB_REPOSITORY: self.tep_agent_lab}

    def record(self) -> dict[str, str]:
        return {"tep_sim_git_revision": self.tep_sim,
                "industrial_agent_runtime_git_revision": self.industrial_agent_runtime,
                "tep_agent_lab_git_revision": self.tep_agent_lab}


def load_dependency_pins(path: str | Path) -> dict[str, str]:
    pins = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(pins, dict) or any(not isinstance(value, str) for value in pins.values()):
        raise ValueError("dependency pins must map names to exact version strings")
    return pins


def pinned_tep_sim_sources(tep_sim_revision: str) -> tuple[ContextSourceRef, ...]:
    """The pinned ProcessGraph (AGENT) and evaluator bindings (EVALUATOR-only) sources."""
    return (
        ContextSourceRef(
            source_id=PROCESS_GRAPH_SOURCE_ID, repository=TEP_SIM_REPOSITORY,
            git_revision=tep_sim_revision, path_or_ref=PROCESS_GRAPH_PATH,
            content_checksum=PROCESS_GRAPH_CONTENT_CHECKSUM, kind="PROCESS_GRAPH",
            schema_version="tep-sim.process-graph/v0", visibility=Visibility.AGENT,
            provenance=PROCESS_GRAPH_FIXTURE,
            governance={"review_status": PROCESS_GRAPH_REVIEW_STATUS}),
        ContextSourceRef(
            source_id=EVALUATOR_BINDINGS_SOURCE_ID, repository=TEP_SIM_REPOSITORY,
            git_revision=tep_sim_revision, path_or_ref=EVALUATOR_BINDINGS_PATH,
            content_checksum=EVALUATOR_BINDINGS_CONTENT_CHECKSUM,
            kind="EVALUATOR_GROUND_TRUTH",
            schema_version="tep-sim.evaluator-disturbance-bindings/v0",
            visibility=Visibility.EVALUATOR,
            provenance={"fixture_id": "tep-evaluator-disturbance-bindings",
                        "fixture_version": "0.2.0"},
            governance={"source_review_status": "PENDING_HUMAN_REVIEW"}),
    )


@dataclass(frozen=True)
class WorldSpec:
    """Pinned tep-sim world configuration; the upstream revision is tep-sim's own pin."""

    seed: int
    backend: str = "python"
    control_mode: ControlMode = ControlMode.CLOSED_LOOP
    record_interval: int = 60

    def __post_init__(self) -> None:
        if type(self.seed) is not int or type(self.record_interval) is not int:
            raise ValueError("seed and record_interval must be integers")
        object.__setattr__(self, "control_mode", ControlMode(self.control_mode))
        _text(self.backend, "backend")

    def record(self) -> dict[str, Any]:
        return {"seed": self.seed, "backend": self.backend,
                "control_mode": self.control_mode.value,
                "record_interval": self.record_interval, "upstream_revision": UPSTREAM_REVISION}


@dataclass(frozen=True)
class ModelSpec:
    """Model identity for the manifest; the config is recorded by checksum only."""

    provider: str
    model_name: str
    model_version: str
    prompt_template_version: str
    config: Mapping[str, Any] = field(default_factory=dict)
    sampling_parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("provider", "model_name", "model_version", "prompt_template_version"):
            _text(getattr(self, name), name)
        for name in ("config", "sampling_parameters"):
            if secret_findings(getattr(self, name)):
                raise ValueError("model configuration must not contain credentials")
            object.__setattr__(self, name, freeze_json(getattr(self, name)))

    def record(self) -> dict[str, Any]:
        return {"provider": self.provider, "model_name": self.model_name,
                "model_version": self.model_version,
                "prompt_template_version": self.prompt_template_version,
                "model_config_checksum": checksum(self.config),
                "sampling_parameters": to_jsonable(self.sampling_parameters)}

    def coordinator_metadata(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model_name,
                "model_version": self.model_version,
                "prompt_template_version": self.prompt_template_version,
                "sampling_parameters": to_jsonable(self.sampling_parameters)}


@dataclass(frozen=True)
class RunRequest:
    """What the application asks to host; identity and configuration, no live objects."""

    investigation_id: str
    goal: str
    world: WorldSpec
    model: ModelSpec
    budget: Budget
    output_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    allowed_tools: tuple[str, ...] | None = None
    projection_policy: Mapping[str, Any] = field(default_factory=dict)
    require_conclusion: bool = True

    def __post_init__(self) -> None:
        _text(self.investigation_id, "investigation_id")
        _text(self.goal, "goal")
        if not isinstance(self.world, WorldSpec) or not isinstance(self.model, ModelSpec):
            raise ValueError("typed WorldSpec and ModelSpec required")
        if not isinstance(self.budget, Budget):
            raise ValueError("typed runtime Budget required")
        if self.allowed_tools is not None:
            object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        if type(self.require_conclusion) is not bool:
            raise ValueError("require_conclusion must be a boolean")
        for name in ("output_schema", "projection_policy"):
            object.__setattr__(self, name, freeze_json(getattr(self, name)))
        # Screened before anything (even the RUN_CREATED record) is persisted.
        if secret_findings(self.record()):
            raise ValueError("run request must not contain credentials")

    def record(self) -> dict[str, Any]:
        return {"investigation_id": self.investigation_id, "goal": self.goal,
                "world": self.world.record(), "model": self.model.record(),
                "budget": to_jsonable(self.budget),
                "output_schema": to_jsonable(self.output_schema),
                "allowed_tools": (None if self.allowed_tools is None
                                  else list(self.allowed_tools)),
                "projection_policy": to_jsonable(self.projection_policy),
                "require_conclusion": self.require_conclusion}


@dataclass(frozen=True)
class BenchmarkRefs:
    """Hidden benchmark truth is referenced by EVALUATOR-visible source ids only."""

    benchmark_case_source_id: str | None = None
    evaluator_ground_truth_source_id: str | None = None


def _manifest_sources(value: Sequence[Any]) -> tuple[ContextSourceRef, ...]:
    sources = []
    for item in value:
        if isinstance(item, ContextSourceRef):
            sources.append(item)
        elif isinstance(item, Mapping):
            sources.append(ContextSourceRef(**dict(item)))
        else:
            raise ValueError("ContextSourceRef required")
    return tuple(sources)


_AGENT_MANIFEST_FIELDS = ("run_id", "created_at", "prepared_at", "manifest_version",
                          "source_revisions", "numerical_stack", "world",
                          "process_semantics", "runtime_policy", "model", "task")


@dataclass(frozen=True, kw_only=True)
class RunManifest:
    """The immutable reproducibility root of what was prepared to run."""

    run_id: str
    created_at: str
    prepared_at: str
    manifest_version: str
    source_revisions: Mapping[str, str]
    numerical_stack: Mapping[str, Any]
    world: Mapping[str, Any]
    process_semantics: Mapping[str, Any]
    runtime_policy: Mapping[str, Any]
    model: Mapping[str, Any]
    task: Mapping[str, Any]
    benchmark: Mapping[str, Any]
    canonical_context_sources: tuple[ContextSourceRef, ...]
    context_inventory_checksum: str
    storage: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_context_sources",
                           _manifest_sources(self.canonical_context_sources))
        for name in ("source_revisions", "numerical_stack", "world", "process_semantics",
                     "runtime_policy", "model", "task", "benchmark", "storage"):
            object.__setattr__(self, name, freeze_json(getattr(self, name)))
        if inventory_checksum(self.canonical_context_sources) != self.context_inventory_checksum:
            raise ValueError("manifest context inventory checksum mismatch")

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "RunManifest":
        return cls(**dict(record))

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)

    def checksum(self) -> str:
        return hashlib.sha256(canonical_json(self.record())).hexdigest()

    def agent_projection(self) -> dict[str, Any]:
        """AGENT scope allowlist: no benchmark refs, storage layout, or hidden sources.

        A field added to the manifest later stays out of AGENT scope until listed here.
        """
        full = self.record()
        record = {name: full[name] for name in _AGENT_MANIFEST_FIELDS}
        visible = [source for source in self.canonical_context_sources
                   if source.visibility == Visibility.AGENT]
        record["canonical_context_sources"] = [source.record() for source in visible]
        record["context_inventory_checksum"] = inventory_checksum(visible)
        return record


@dataclass(frozen=True, kw_only=True)
class RunOutcome:
    """Terminal execution metadata, kept separate from the immutable manifest."""

    run_id: str
    outcome_version: str
    terminal_status: RunStatus
    started_at: str
    finished_at: str
    manifest_checksum: str
    runtime_result: Mapping[str, Any] | None
    failure_category: str | None
    failure_detail: str | None
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "terminal_status", RunStatus(self.terminal_status))
        if self.terminal_status not in TERMINAL_RUN_STATUSES:
            raise ValueError("RunOutcome records COMPLETED or FAILED only")
        for name in ("runtime_result", "provenance"):
            object.__setattr__(self, name, freeze_json(getattr(self, name)))

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "RunOutcome":
        return cls(**dict(record))

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)

    def checksum(self) -> str:
        return hashlib.sha256(canonical_json(self.record())).hexdigest()


def _runtime_record(result: RuntimeResult) -> dict[str, Any]:
    return {"task_id": result.task_id, "task_status": result.status.value,
            "structured_output": to_jsonable(result.structured_output),
            "state_revision": result.state_revision, "trace_ref": to_jsonable(result.trace_ref),
            "budget_usage": to_jsonable(result.budget_usage),
            "warnings": list(result.warnings), "errors": list(result.errors)}


@dataclass
class RunSession:
    """Ephemeral in-process assembly of one prepared run. Never durable canonical state."""

    directory: Path
    world: ReferenceWorld
    artifacts: ArtifactStore
    surface: BridgedToolSurface
    run_log: RunLog
    store: RcaStateStore
    trace: TraceRecorder
    task: Task
    provider: ModelProvider
    registry: CanonicalContextRegistry
    closed: bool = False

    def close(self) -> None:
        """Release simulator handles; in-memory history/lineage stays readable."""
        if self.closed:
            return
        self.closed = True
        try:
            self.surface.surface.sandbox.close()
        finally:
            try:
                self.world.environment.close()
            except Exception:
                pass


@dataclass
class _Run:
    run_id: str
    directory: Path
    lifecycle: RunLog
    status: RunStatus
    request: Mapping[str, Any]
    request_object: RunRequest | None = None
    preparing: bool = False
    attempts: int = 0
    session_dir: Path | None = None
    manifest_checksum: str | None = None
    session: RunSession | None = None
    outcome: RunOutcome | None = None
    owner: str | None = None


@dataclass(frozen=True)
class RunInfo:
    """Trusted application read of one run; visibility-filtered views live in queries."""

    run_id: str
    status: RunStatus
    manifest_checksum: str | None
    outcome: RunOutcome | None


def _write_once(path: Path, data: bytes) -> None:
    """Atomically publish a file that must not already exist.

    Temp + fsync, then a hard link that fails if the target exists (never overwrites),
    then a directory fsync where the platform supports it. The temp is always removed.
    """
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class RunManager:
    """Transport-neutral lifecycle owner for local, single-process Playground runs.

    It does not authorize model tool calls, duplicate B2/B3, or schedule tasks.
    """

    def __init__(self, root: str | Path, *, revisions: SourceRevisions,
                 dependency_pins: Mapping[str, str],
                 materializers: Mapping[str, SourceMaterializer] | None = None,
                 clock: Callable[[], str] | None = None) -> None:
        if not isinstance(revisions, SourceRevisions):
            raise TypeError("SourceRevisions required")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.revisions = revisions
        self.dependency_pins = dict(dependency_pins)
        self.materializers = dict(materializers if materializers is not None else
                                  {TEP_SIM_REPOSITORY: PackageSourceMaterializer("tep_sim")})
        self.clock = clock or _now
        self._lock = threading.Lock()
        self._runs: dict[str, _Run] = {}

    # -- identity and lookup ------------------------------------------------------------
    def _checked_id(self, run_id: Any) -> str:
        if (not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id)
                or run_id in _RESERVED_NAMES):
            raise ValueError("run_id must match [a-z0-9][a-z0-9-]{2,63} and not be reserved")
        return run_id

    def _record(self, run_id: Any) -> _Run:
        """Caller holds the lock. Runs from another process are loaded read-only."""
        run_id = self._checked_id(run_id)
        run = self._runs.get(run_id)
        if run is None:
            run = self._load(run_id)
            self._runs[run_id] = run
        return run

    def _load(self, run_id: str) -> _Run:
        directory = self.root / run_id
        if not (directory / "lifecycle" / "manifest.json").exists():
            raise KeyError(f"unknown run {run_id}")
        lifecycle = RunLog(directory / "lifecycle")
        events = lifecycle.events()
        if not events or events[0]["type"] != "RUN_CREATED":
            raise LifecycleError("run lifecycle log has no creation record")
        run = _Run(run_id, directory, lifecycle, RunStatus.CREATED,
                   freeze_json(events[0]["payload"]["request"]))
        for event in events:
            payload = event["payload"]
            if event["type"] in ("RUN_READY", "PREPARE_FAILED"):
                run.attempts += 1
            if event["type"] == "RUN_READY":
                if not _SESSION_DIR.fullmatch(str(payload.get("session"))):
                    raise LifecycleError("lifecycle log names an invalid session directory")
                run.status, run.session_dir = RunStatus.READY, directory / payload["session"]
                run.manifest_checksum = payload["manifest_checksum"]
            elif event["type"] == "RUN_STARTED":
                run.status, run.owner = RunStatus.RUNNING, payload["owner"]
            elif event["type"] in ("RUN_COMPLETED", "RUN_FAILED"):
                run.status = RunStatus(payload["terminal_status"])
                run.outcome = self._read_outcome(run, payload["outcome_checksum"])
        return run

    def get(self, run_id: str) -> RunInfo:
        """Trusted application/EVALUATOR read; never hand this to an AGENT consumer."""
        with self._lock:
            run = self._record(run_id)
            return RunInfo(run.run_id, run.status, run.manifest_checksum, run.outcome)

    # -- create -------------------------------------------------------------------------
    def create(self, run_id: str, request: RunRequest) -> RunInfo:
        run_id = self._checked_id(run_id)
        if not isinstance(request, RunRequest):
            raise TypeError("RunRequest required")
        with self._lock:
            directory = self.root / run_id
            if run_id in self._runs:
                raise LifecycleError(f"run {run_id} already exists")
            try:
                directory.mkdir()
            except FileExistsError as exc:
                raise LifecycleError(f"run {run_id} already exists") from exc
            try:
                lifecycle = RunLog(directory / "lifecycle",
                                   {"run_id": run_id, "record": LIFECYCLE_VERSION})
                lifecycle.append("RUN_CREATED", {"at": self.clock(),
                                                 "request": request.record()})
            except BaseException:
                shutil.rmtree(directory, ignore_errors=True)  # do not burn the run id
                raise
            run = _Run(run_id, directory, lifecycle, RunStatus.CREATED,
                       freeze_json(request.record()), request)
            self._runs[run_id] = run
            return RunInfo(run_id, run.status, None, None)

    # -- prepare ------------------------------------------------------------------------
    def prepare(self, run_id: str, *, provider: ModelProvider,
                context_sources: Sequence[ContextSourceRef],
                case_setup: Callable[[ReferenceWorld], None] | None = None,
                benchmark: BenchmarkRefs | None = None) -> RunManifest:
        """Resolve/attest every source, assemble the session, publish one manifest.

        All or nothing: on any failure the session is torn down, no manifest is
        published, and the run stays CREATED (a later prepare uses a fresh attempt).
        """
        with self._lock:
            run = self._record(run_id)
            if run.status != RunStatus.CREATED or run.preparing:
                raise LifecycleError(f"prepare requires CREATED, run is {run.status.value}")
            if run.request_object is None:
                raise LifecycleError("a run loaded from disk cannot be prepared again")
            run.preparing = True
            run.attempts += 1
            attempt = f"prepare-{run.attempts:04d}"
        session = None
        try:
            session, manifest = self._assemble(run, attempt, provider, tuple(context_sources),
                                               case_setup, benchmark or BenchmarkRefs())
            _write_once(session.directory / "manifest.json",
                        canonical_json(manifest.record()))
            with self._lock:
                run.lifecycle.append("RUN_READY", {
                    "at": manifest.prepared_at, "session": attempt,
                    "manifest_checksum": manifest.checksum(),
                    "agent_manifest_checksum": checksum(manifest.agent_projection())})
                run.status, run.session, run.session_dir = RunStatus.READY, session, session.directory
                run.manifest_checksum = manifest.checksum()
            return manifest
        except BaseException as exc:
            if session is not None:
                session.close()
            # No partial manifest and no unreferenced hidden-truth world artifacts.
            shutil.rmtree(run.directory / attempt, ignore_errors=True)
            with self._lock:
                run.lifecycle.append("PREPARE_FAILED", {
                    "at": self.clock(), "session": attempt,
                    "category": type(exc).__name__, "detail": _failure_text(exc)})
            if not isinstance(exc, Exception):
                raise
            raise PrepareError(f"prepare failed: {_failure_text(exc)}") from exc
        finally:
            with self._lock:
                run.preparing = False

    def _attest_dependencies(self) -> None:
        expected = {"tep-sim": self.revisions.tep_sim,
                    "industrial-agent-runtime": self.revisions.industrial_agent_runtime}
        for name, revision in expected.items():
            if self.dependency_pins.get(name) != revision:
                raise PrepareError(f"{name} revision is not the lab dependency pin")
        if self.dependency_pins.get("numpy") != numpy.__version__:
            raise PrepareError("numpy is not the pinned numerical stack version")

    def _assemble(self, run: _Run, attempt: str, provider: ModelProvider,
                  sources: tuple[ContextSourceRef, ...], case_setup, benchmark: BenchmarkRefs
                  ) -> tuple[RunSession, RunManifest]:
        request = run.request_object
        if provider is None or not callable(getattr(provider, "generate", None)):
            raise PrepareError("a ModelProvider is required")
        self._attest_dependencies()
        directory = run.directory / attempt
        directory.mkdir()
        # Canonical context: register + attest every source before anything uses it.
        registry = CanonicalContextRegistry(self.revisions.by_repository(), self.materializers)
        for source in sources:
            registry.register(source)
        hidden_content = {(ref.repository, ref.path_or_ref) for ref in
                          registry.inventory(ProjectionScope.EVALUATOR)
                          if ref.visibility != Visibility.AGENT}
        hidden_checksums = {ref.content_checksum for ref in
                            registry.inventory(ProjectionScope.EVALUATOR)
                            if ref.visibility != Visibility.AGENT}
        for ref in registry.inventory(ProjectionScope.AGENT):
            if ((ref.repository, ref.path_or_ref) in hidden_content
                    or ref.content_checksum in hidden_checksums):
                raise PrepareError("an AGENT source aliases hidden source content")
        graph_sources = [source for source in registry.inventory(ProjectionScope.AGENT)
                         if source.kind == "PROCESS_GRAPH"]
        if len(graph_sources) != 1:
            raise PrepareError("exactly one AGENT-visible PROCESS_GRAPH source is required")
        graph_source = graph_sources[0]
        graph = build_process_graph(registry.resolve(graph_source.source_id,
                                                     ProjectionScope.AGENT).json())
        provenance = graph.provenance
        if (not provenance.pinned or provenance.content_sha256 !=
                graph_source.provenance.get("fixture_content_sha256")
                or provenance.review_status != graph_source.governance.get("review_status")):
            raise PrepareError("ProcessGraph provenance differs from its context source")
        hidden = {}
        for name in ("benchmark_case_source_id", "evaluator_ground_truth_source_id"):
            source_id = getattr(benchmark, name)
            if source_id is not None:
                if registry.ref(source_id, ProjectionScope.EVALUATOR).visibility != \
                        Visibility.EVALUATOR:
                    raise PrepareError(f"{name} must reference an EVALUATOR-only source")
                hidden[name] = source_id
        environment = None
        world = None
        try:
            environment = TEPEnvironment(EnvironmentConfig(
                seed=request.world.seed, backend=request.world.backend,
                control_mode=request.world.control_mode,
                record_interval=request.world.record_interval,
                upstream_revision=UPSTREAM_REVISION, artifact_directory=directory / "world"))
            environment.reset()
            # The world uses exactly the attested ProcessGraph source.
            world = ReferenceWorld(environment, graph)
            if case_setup is not None:
                case_setup(world)  # trusted harness setup; never an Agent tool
            session = self._session(run, request, directory, world, provider, registry)
        except Exception:
            if environment is not None:
                environment.close()
            raise
        try:
            return session, self._manifest(run, attempt, request, session, registry,
                                           graph_source, hidden, case_setup is not None)
        except Exception:
            session.close()
            raise

    def _manifest(self, run: _Run, attempt: str, request: RunRequest, session: RunSession,
                  registry: CanonicalContextRegistry, graph_source: ContextSourceRef,
                  hidden: Mapping[str, str], case_setup_applied: bool) -> RunManifest:
        provenance = session.world.graph.provenance
        created_at = run.lifecycle.events()[0]["payload"]["at"]
        specs = session.surface.tool_specs()
        allowed = tuple(spec for spec in specs if spec.name in session.task.allowed_tools)
        inventory = registry.freeze()
        manifest = RunManifest(
            run_id=run.run_id, created_at=created_at, prepared_at=self.clock(),
            manifest_version=MANIFEST_VERSION,
            source_revisions=self.revisions.record(),
            numerical_stack={"python": platform.python_version(),
                             "python_implementation": platform.python_implementation(),
                             "numpy": numpy.__version__},
            world={"environment_version": ENVIRONMENT_VERSION,
                   "upstream_revision": UPSTREAM_REVISION,
                   "environment_config": request.world.record(),
                   "environment_config_checksum": checksum(request.world.record()),
                   "deterministic_seed": request.world.seed,
                   "capability_version": CAPABILITY_VERSION,
                   "scenario_mapping_version": SCENARIO_MAPPING_VERSION,
                   "safety_limits_version": SAFETY_LIMITS_VERSION},
            process_semantics={
                "source_id": graph_source.source_id, "fixture_id": provenance.fixture_id,
                "fixture_version": provenance.fixture_version,
                "fixture_checksum": provenance.content_sha256,
                "review_status": provenance.review_status,
                "review_record": (None if provenance.review_record is None
                                  else to_jsonable(provenance.review_record))},
            runtime_policy={
                # equals the runtime trace's MODEL_TURN registered_tool_set_version
                "tool_set_version": checksum(allowed),
                "allowed_tools": [spec.name for spec in allowed],
                "gate_policy_version": session.surface.gate_policy().policy_version,
                "visibility_policy_version": VISIBILITY_POLICY_VERSION,
                "rca_projection_policy_version": RCA_VISIBILITY_POLICY_VERSION,
                "projection_policy": to_jsonable(request.projection_policy),
                "tool_surface_version": TOOL_SURFACE_VERSION,
                "bridge_version": BRIDGE_VERSION, "verifier_version": VERIFIER_VERSION,
                "finish_policy": {"require_conclusion": request.require_conclusion}},
            model=request.model.record(),
            task={"task_id": session.task.task_id,
                  "investigation_id": request.investigation_id, "goal": request.goal,
                  "incident_ref": to_jsonable(session.task.context_refs[0]),
                  "budget": to_jsonable(request.budget),
                  "output_schema_checksum": checksum(request.output_schema)},
            benchmark={**hidden, "case_setup_applied": case_setup_applied},
            canonical_context_sources=inventory,
            context_inventory_checksum=inventory_checksum(inventory),
            storage={"session": attempt, "world": f"{attempt}/world",
                     "lab_artifacts": f"{attempt}/lab-artifacts",
                     "rca_state": f"{attempt}/state", "runtime_trace": f"{attempt}/trace"})
        findings = secret_findings(manifest.record())
        if findings:
            raise PrepareError("manifest would contain credentials: " + findings[0])
        return manifest

    def _session(self, run: _Run, request: RunRequest, directory: Path,
                 world: ReferenceWorld, provider: ModelProvider,
                 registry: CanonicalContextRegistry) -> RunSession:
        artifacts = ArtifactStore(directory / "lab-artifacts")
        holder: list[RcaStateStore] = []

        def finish_verifier(proposal: Any, task: Task, revision: Any) -> bool:
            return not readiness_deficiencies(
                holder[0], require_conclusion=request.require_conclusion)

        surface = BlindRcaToolSurface(world, artifacts, finish_verifier=finish_verifier,
                                      clock=self.clock)
        bridge = AnalysisToolBridge(artifacts, reference_revision=surface.reference_revision,
                                    clock=self.clock)
        bridged = BridgedToolSurface(surface, bridge)
        task_id = f"task-{run.run_id}"
        incident = InformationRef(f"incident-{run.run_id}", "Incident", "tep-agent-lab",
                                  "v0", Visibility.AGENT, self.clock())
        run_log = RunLog(directory / "state", {"run_id": run.run_id,
                                               "investigation_id": request.investigation_id})
        store = RcaStateStore(
            RcaState(investigation_id=request.investigation_id, goal=request.goal,
                     incident_ref=incident),
            run_log, resolve=lambda ref: artifacts.resolve(ref) or (
                ref if ref == incident else None),
            task_id=task_id, visibility_policy_version=RCA_VISIBILITY_POLICY_VERSION)
        holder.append(store)
        names = tuple(spec.name for spec in bridged.tool_specs())
        allowed = names if request.allowed_tools is None else request.allowed_tools
        unknown = sorted(set(allowed) - set(names))
        if unknown:
            raise PrepareError(f"allowed_tools are not registered: {unknown}")
        task = Task(task_id, request.goal, (incident,), tuple(allowed), request.budget,
                    request.output_schema, metadata={"run_id": run.run_id})
        return RunSession(directory, world, artifacts, bridged, run_log, store,
                          TraceRecorder(directory / "trace"), task, provider, registry)

    # -- start --------------------------------------------------------------------------
    def start(self, run_id: str) -> RunOutcome:
        """Accepted once, from READY only; the caller becomes the sole execution owner."""
        with self._lock:
            run = self._record(run_id)
            if run.status != RunStatus.READY:
                raise LifecycleError(f"start requires READY, run is {run.status.value}")
            if run.session is None:
                raise LifecycleError("READY session is not live in this process; "
                                     "v0 sessions are not resumable")
            owner = f"execution-owner:{run.run_id}"
            started_at = self.clock()
            run.lifecycle.append("RUN_STARTED", {"at": started_at, "owner": owner})
            run.status, run.owner = RunStatus.RUNNING, owner
            session = run.session
        request = run.request_object
        result, failure = None, None
        try:
            coordinator = Coordinator(
                session.task, session.store, session.provider, session.trace,
                session.surface.tool_specs(),
                model_metadata=request.model.coordinator_metadata(),
                gate=session.surface, executor=session.surface, verifier=session.surface,
                ingestor=RcaResultIngestor(), gate_policy=session.surface.gate_policy(),
                reference_guard=session.surface,
                projection_policy=request.projection_policy, clock=self.clock)
            result = coordinator.run()
        except BaseException as exc:  # the hosted execution did not reach a RuntimeResult
            failure = exc
        finally:
            session.close()
        try:
            outcome = self._outcome(run, owner, started_at, result, failure)
        except Exception as exc:  # e.g. a non-JSON runtime result: still terminal FAILED
            failure = failure or exc
            outcome = self._outcome(run, owner, started_at, None, failure)
        with self._lock:
            # Durable first; memory follows disk. On a persistence error the run stays
            # RUNNING (as a reload would report) and the error propagates.
            _write_once(session.directory / "outcome.json", canonical_json(outcome.record()))
            run.lifecycle.append(f"RUN_{outcome.terminal_status.value}", {
                "at": outcome.finished_at, "terminal_status": outcome.terminal_status.value,
                "outcome_checksum": outcome.checksum()})
            run.status, run.outcome = outcome.terminal_status, outcome
        if failure is not None and not isinstance(failure, Exception):
            raise failure  # KeyboardInterrupt/SystemExit after recording FAILED
        return outcome

    def _outcome(self, run: _Run, owner: str, started_at: str,
                 result: RuntimeResult | None, failure: BaseException | None) -> RunOutcome:
        return RunOutcome(
            run_id=run.run_id, outcome_version=OUTCOME_VERSION,
            terminal_status=RunStatus.COMPLETED if failure is None else RunStatus.FAILED,
            started_at=started_at, finished_at=self.clock(),
            manifest_checksum=run.manifest_checksum,
            runtime_result=None if result is None else _runtime_record(result),
            failure_category=None if failure is None else "EXECUTION_ERROR",
            failure_detail=None if failure is None else _failure_text(failure),
            provenance={"execution_owner": owner,
                        "executor": "industrial_agent_runtime.Coordinator",
                        "verifier_version": VERIFIER_VERSION,
                        "runtime_task_status_owner": "RcaStateStore (runtime TaskStateStore)"})

    # -- durable reads ------------------------------------------------------------------
    def _read_outcome(self, run: _Run, expected: str) -> RunOutcome:
        data = (run.session_dir / "outcome.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise LifecycleError("outcome checksum differs from its lifecycle record")
        return RunOutcome.from_record(json.loads(data))

    def manifest(self, run_id: str) -> RunManifest:
        """Trusted/EVALUATOR only: the internal manifest, re-verified against READY.

        AGENT consumers use ``queries(run_id).manifest_view()`` instead.
        """
        with self._lock:
            run = self._record(run_id)
            if run.manifest_checksum is None:
                raise LifecycleError(f"run {run.run_id} has no published manifest")
            path = run.session_dir / "manifest.json"
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != run.manifest_checksum:
            raise LifecycleError("manifest bytes differ from the checksum published at READY")
        return RunManifest.from_record(json.loads(data))

    def queries(self, run_id: str, scope: ProjectionScope = ProjectionScope.AGENT):
        """Read-only projections bound to one scope at construction (AGENT by default)."""
        from .playground_views import RunQueries
        with self._lock:
            run = self._record(run_id)
        return RunQueries(self, run, ProjectionScope(scope))
