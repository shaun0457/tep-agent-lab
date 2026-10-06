"""Transport-neutral, visibility-scoped read projections of one Playground run.

Every view is derived from an existing canonical owner (manifest/outcome files, the
lifecycle log, runtime ``TraceRecorder``, lab ``RunLog``/``RcaStateStore`` snapshots,
lab ``ArtifactStore`` files, the attested ProcessGraph source, the live world) and can
be rebuilt from it. Views never become a second TaskStateStore, ProcessGraph, or world
truth store, and ``Trace != Observation != Evidence`` stays visible in their shapes.

AGENT is the default scope. AGENT views are screened for hidden-truth vocabulary and
fail closed; EVALUATOR is an explicit trusted scope and is never fed back into a
model ``ContextProjection`` or tool result.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any

from industrial_agent_runtime import (InformationRef, TraceRecorder, Visibility, checksum,
                                      to_jsonable)
from industrial_agent_runtime.serialization import freeze_json
from tep_sim import build_process_graph
from tep_sim.evaluator_bindings import build_evaluator_disturbance_bindings

from .canonical_context import (CanonicalContextRegistry, ProjectionScope,
                                inventory_checksum, visible_in)
from .persistence import RunLog
from .tep_world import ARTIFACT_OWNER, leakage_findings

VIEW_VERSION = "tep-agent-lab.playground-views/v0"
MAX_TELEMETRY_RECORDS = 1000
_LAB_ARTIFACT_ID = re.compile(r"tep-artifact-[0-9]{6,}")
_STATE_EVENTS = {"RCA_STATE_INITIALIZED", "RCA_STATE_UPDATE_ACCEPTED",
                 "RCA_STATUS_TRANSITION_ACCEPTED"}


class VisibilityViolation(RuntimeError):
    """An AGENT projection would carry hidden-truth material; nothing is returned."""


class UnknownArtifact(LookupError):
    """Raised alike for an unknown, unissued, hidden, or corrupted artifact ref."""

    def __init__(self) -> None:
        super().__init__("unknown artifact")


class ViewUnavailable(LookupError):
    """The view's owner is not available for this run (e.g. not prepared yet)."""


class RunQueries:
    """Read-only query service; no method mutates any canonical owner."""

    def __init__(self, manager: Any, run: Any, scope: ProjectionScope) -> None:
        # The scope is fixed here: an AGENT-bound service cannot be asked for EVALUATOR.
        self._manager, self._run, self.scope = manager, run, ProjectionScope(scope)
        self._registry: CanonicalContextRegistry | None = None

    # -- shared plumbing ----------------------------------------------------------------
    def _view(self, name: str, scope: ProjectionScope, body: Mapping[str, Any]) -> Any:
        view = {"view": name, "view_version": VIEW_VERSION, "scope": scope.value,
                "run_id": self._run.run_id, **to_jsonable(body)}
        if scope == ProjectionScope.AGENT and leakage_findings(view):
            raise VisibilityViolation(f"{name} AGENT projection failed the leakage screen")
        return freeze_json(view)

    def _manifest(self):
        if self._run.manifest_checksum is None:
            raise ViewUnavailable("run has no published manifest")
        return self._manager.manifest(self._run.run_id)

    def _session_dir(self):
        if self._run.session_dir is None:
            raise ViewUnavailable("run has no prepared session")
        return self._run.session_dir

    def _live(self):
        session = self._run.session
        if self._run.status.value == "RUNNING":
            # The single execution owner mutates world/sandbox state; v0 takes no
            # world lock, so live-world views are offered only outside RUNNING.
            raise ViewUnavailable("live world views are unavailable while RUNNING")
        if session is None:
            raise ViewUnavailable("no in-process session; world views are ephemeral in v0")
        return session

    def registry(self) -> CanonicalContextRegistry:
        """The live frozen registry, or one rebuilt and re-attested from the manifest."""
        session = self._run.session
        if session is not None:
            return session.registry
        if self._registry is None:
            manifest = self._manifest()
            revisions = {}
            for repository, key in (("shaun0457/tep-sim", "tep_sim_git_revision"),
                                    ("shaun0457/industrial-agent-runtime",
                                     "industrial_agent_runtime_git_revision"),
                                    ("shaun0457/tep-agent-lab", "tep_agent_lab_git_revision")):
                revisions[repository] = manifest.source_revisions[key]
            registry = CanonicalContextRegistry(revisions, self._manager.materializers)
            for source in manifest.canonical_context_sources:
                registry.register(source)  # a source changed after the run fails here
            registry.freeze()
            self._registry = registry
        return self._registry

    def _trace_events(self) -> list[dict[str, Any]]:
        path = self._session_dir() / "trace" / "events.jsonl"
        if not path.exists():
            return []
        raw = path.read_bytes()
        complete = raw[:raw.rfind(b"\n") + 1]  # ignore a final line still being written
        return [json.loads(line) for line in complete.decode("utf-8").splitlines()]

    def _state_log(self) -> RunLog:
        return RunLog(self._session_dir() / "state")

    def _state_snapshot(self) -> dict[str, Any]:
        log = self._state_log()
        accepted = [event for event in log.events() if event["type"] in _STATE_EVENTS]
        if not accepted:
            raise ViewUnavailable("investigation state has not been initialized")
        return log.read_artifact(accepted[-1]["payload"]["snapshot_checksum"])

    # -- RunSummaryView -----------------------------------------------------------------
    def run_summary(self) -> Any:
        scope, run = self.scope, self._run
        body: dict[str, Any] = {"run_status": run.status.value,
                                "status_owner": "playground RunManager (application hosting)"}
        if run.manifest_checksum is not None:
            manifest = self._manifest()
            body["manifest"] = {
                "manifest_version": manifest.manifest_version,
                # AGENT gets the checksum of its own projection: a checksum over the
                # internal manifest would let hidden benchmark refs be guessed offline.
                "checksum": (manifest.checksum() if scope == ProjectionScope.EVALUATOR
                             else checksum(manifest.agent_projection())),
                "checksum_of": ("internal manifest" if scope == ProjectionScope.EVALUATOR
                                else "AGENT manifest projection")}
            body["task_id"] = manifest.task["task_id"]
            body["investigation_id"] = manifest.task["investigation_id"]
            try:
                state = self._state_snapshot()["state"]
                body["runtime_task_status"] = {
                    "status": state["generic_status"], "state_revision": state["revision"],
                    "owner": "runtime TaskStateStore (lab RcaStateStore)"}
            except ViewUnavailable:
                pass
        session = run.session
        body["resources"] = {"session": ("NONE" if session is None else
                                         "RELEASED" if session.closed else "LIVE")}
        if run.outcome is not None:
            body["outcome"] = self._outcome(run.outcome, scope)
        return self._view("RunSummaryView", scope, body)

    @staticmethod
    def _outcome(outcome: Any, scope: ProjectionScope) -> dict[str, Any]:
        if scope == ProjectionScope.EVALUATOR:
            return outcome.record()
        runtime = outcome.runtime_result
        return {"terminal_status": outcome.terminal_status.value,
                "started_at": outcome.started_at, "finished_at": outcome.finished_at,
                "runtime_result": None if runtime is None else {
                    "task_status": runtime["task_status"],
                    "state_revision": runtime["state_revision"],
                    "error_count": len(runtime["errors"])},
                # a hidden setup/evaluator error never becomes an Agent-side oracle
                "failure_category": outcome.failure_category}

    # -- manifest / ContextInventoryView ------------------------------------------------
    def manifest_view(self) -> Any:
        scope, manifest = self.scope, self._manifest()
        record = (manifest.record() if scope == ProjectionScope.EVALUATOR
                  else manifest.agent_projection())
        return self._view("RunManifestView", scope, {"manifest": record})

    def context_inventory(self) -> Any:
        """Only visible sources; hidden ones leave no id, path, description, or count."""
        scope = self.scope
        sources = self.registry().inventory(scope)
        return self._view("ContextInventoryView", scope, {
            "frozen": self.registry().frozen,
            "sources": [source.record() for source in sources],
            "inventory_checksum": inventory_checksum(sources)})

    # -- ProcessGraphView ---------------------------------------------------------------
    def process_graph(self) -> Any:
        scope, registry = self.scope, self.registry()
        source = next(ref for ref in registry.inventory(ProjectionScope.AGENT)
                      if ref.kind == "PROCESS_GRAPH")
        graph = build_process_graph(registry.resolve(source.source_id, scope).json())
        provenance = graph.provenance
        bindings = [binding.describe() for binding in graph.bindings()]
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for binding in bindings:
            by_entity.setdefault(binding["attached_to"], []).append(binding)
        body: dict[str, Any] = {
            "source": {"source_id": source.source_id, "git_revision": source.git_revision,
                       "content_checksum": source.content_checksum},
            "provenance": {
                "fixture_id": provenance.fixture_id,
                "fixture_version": provenance.fixture_version,
                "content_sha256": provenance.content_sha256,
                "review_status": provenance.review_status, "pinned": provenance.pinned,
                "review_record": (None if provenance.review_record is None
                                  else to_jsonable(provenance.review_record))},
            "nodes": [{"node_id": node.node_id, "kind": node.kind.value, "name": node.name,
                       "tag": node.tag, "bindings": by_entity.get(node.node_id, [])}
                      for node in graph.nodes()],
            "edges": [{"edge_id": edge.edge_id, "kind": edge.kind.value,
                       "source_node": edge.source_node, "target_node": edge.target_node,
                       "name": edge.name, "stream_number": edge.stream_number,
                       "bindings": by_entity.get(edge.edge_id, [])}
                      for edge in graph.edges()],
            # The UI must not invent pipe-flow physics: direction is topology only,
            # dynamic values exist only where a bound runtime variable exists.
            "flow_semantics": "edge direction is topology only; values come from bindings",
        }
        if scope == ProjectionScope.EVALUATOR:
            overlays = []
            for ref in registry.inventory(scope):
                if ref.kind == "EVALUATOR_GROUND_TRUTH" and \
                        ref.schema_version == "tep-sim.evaluator-disturbance-bindings/v0":
                    hidden = build_evaluator_disturbance_bindings(
                        registry.resolve(ref.source_id, scope).json(), graph)
                    overlays.append({"source_id": ref.source_id,
                                     "bindings": [b.describe() for b in hidden.bindings()],
                                     "unbound": to_jsonable(hidden.unbound_disturbances)})
            body["evaluator_overlays"] = overlays
        return self._view("ProcessGraphView", scope, body)

    # -- TelemetryView ------------------------------------------------------------------
    def telemetry(self, *, variables: tuple[str, ...] | None = None,
                  max_records: int = 120) -> Any:
        """Bounded sanitized reference history; dense data stays artifact-backed."""
        scope = self.scope
        if type(max_records) is not int or not 1 <= max_records <= MAX_TELEMETRY_RECORDS:
            raise ValueError(f"max_records must be in [1, {MAX_TELEMETRY_RECORDS}]")
        if variables is not None and (isinstance(variables, str) or any(
                not isinstance(name, str) for name in variables)):
            raise ValueError("variables must be a sequence of variable ids")
        history = self._live().world.history()
        selected = history[-max_records:]
        if variables is not None:
            wanted = set(variables)
            selected = tuple({**record,
                              "measurements": {k: v for k, v in record["measurements"].items()
                                               if k in wanted},
                              "manipulated_variables": {
                                  k: v for k, v in record["manipulated_variables"].items()
                                  if k in wanted}} for record in selected)
        dense = [to_jsonable(ref) for ref in self._issued_lab_artifacts()
                 if ref.kind in ("HistoryWindowArtifact", "RolloutTelemetryArtifact")]
        return self._view("TelemetryView", scope, {
            "source": "ReferenceWorld sanitized history",
            "total_records": len(history), "returned_records": len(selected),
            "current": history[-1], "records": list(selected), "artifact_refs": dense})

    # -- InvestigationView --------------------------------------------------------------
    def investigation(self) -> Any:
        """RcaState plus its typed records; Observation and Evidence stay separate."""
        scope = self.scope
        snapshot = self._state_snapshot()
        state = snapshot["state"]
        objects = {entry["ref"]["ref_id"]: entry["value"] for entry in snapshot["objects"]
                   if entry["ref"]["visibility"] == Visibility.AGENT.value}

        def records(name: str) -> list[dict[str, Any]]:
            return [{"ref": ref, "record": objects.get(ref["ref_id"])} for ref in state[name]]

        return self._view("InvestigationView", scope, {
            "source": "lab RcaStateStore snapshot (RunLog)",
            "state_revision": state["revision"],
            "runtime_task_status": state["generic_status"],
            "state": state,
            "observations": records("observation_refs"),
            "evidence_links": records("evidence_link_refs"),
            "hypotheses": records("hypothesis_refs"),
            "completed_experiments": records("completed_experiment_refs")})

    # -- BranchTreeView -----------------------------------------------------------------
    def branch_tree(self) -> Any:
        scope, session = self.scope, self._live()
        records = session.surface.surface.sandbox.lineage_records()
        return self._view("BranchTreeView", scope, {
            "root": {"handle": "reference", "kind": "REFERENCE",
                     "status": "RELEASED" if session.closed else "LIVE"},
            "nodes": list(records)})

    # -- BudgetView ---------------------------------------------------------------------
    def budget(self) -> Any:
        """Limits from the manifest; usage and reservations derived from the runtime trace."""
        scope, manifest = self.scope, self._manifest()
        usage: dict[str, float] = {}
        reservations = []
        for event in self._trace_events():
            for name, amount in event["budget_delta"].items():
                usage[name] = usage.get(name, 0) + amount
            if event["type"] == "EXECUTE" and event["status"] == "DISPATCHED":
                reservations.append({"request_id": event["input_summary"]["request"]["request_id"],
                                     "reserved": event["input_summary"]["reserved"]})
        return self._view("BudgetView", scope, {
            "source": "runtime TraceRecorder budget deltas", "limits": manifest.task["budget"],
            "usage": usage, "reservations": reservations})

    # -- RunEventView -------------------------------------------------------------------
    def events(self) -> Any:
        """One chronological feed that preserves each event's source identity.

        Order: lifecycle events up to READY, the lab state initialization (at prepare),
        RUN_STARTED, runtime trace events each followed by the lab state events whose
        resulting revision they produced, remaining lab events in lab order, then the
        terminal lifecycle event.
        The feed is a view; it is never engineering evidence.
        """
        scope = self.scope
        lifecycle = [self._event("application_lifecycle", event["sequence"],
                                 f"lifecycle-{event['sequence']:06d}", event["type"], None,
                                 event["payload"].get("at"), event["payload"], scope)
                     for event in self._run.lifecycle.events()]
        if scope == ProjectionScope.AGENT:  # failed setup attempts are not an AGENT signal
            lifecycle = [item for item in lifecycle if item["type"] != "PREPARE_FAILED"]
        after = [item for item in lifecycle if item["type"] in ("RUN_COMPLETED", "RUN_FAILED")]
        started = [item for item in lifecycle if item["type"] == "RUN_STARTED"]
        before = [item for item in lifecycle if item not in after and item not in started]
        lab, anchors = [], {}
        if self._run.session_dir is not None and (self._session_dir() / "state").exists():
            for event in self._state_log().events():
                item = self._event("lab_run_log", event["sequence"],
                                   f"rca-{event['sequence']:06d}", event["type"], None, None,
                                   event["payload"], scope)
                lab.append(item)
                revision = event["payload"].get("resulting_revision")
                if revision is not None:
                    anchors[revision] = item
        feed = list(before)
        placed = set()
        if lab and lab[0]["type"] == "RCA_STATE_INITIALIZED":
            feed.append(lab[0])
            placed.add(lab[0]["source_event_id"])
        feed += started
        trace = self._trace_events() if self._run.session_dir is not None else []
        for index, event in enumerate(trace):
            outputs = event.get("output_summary")
            feed.append(self._event("runtime_trace", index, event["event_id"], event["type"],
                                    event["status"], event["timestamp"], event, scope))
            revision = outputs.get("resulting_revision") if isinstance(outputs, dict) else None
            if event["status"] == "ACCEPTED" and revision in anchors:
                item = anchors[revision]
                if item["source_event_id"] not in placed:
                    feed.append(item)
                    placed.add(item["source_event_id"])
        feed += [item for item in lab if item["source_event_id"] not in placed]
        feed += after
        for position, item in enumerate(feed):
            item["position"] = position
        return self._view("RunEventView", scope, {
            "semantics": "Trace != Observation != Evidence; this feed is a view only",
            "events": feed})

    @staticmethod
    def _event(source: str, sequence: int, event_id: str, kind: str, status: str | None,
               timestamp: str | None, payload: Any, scope: ProjectionScope) -> dict[str, Any]:
        item = {"source": source, "source_sequence": sequence, "source_event_id": event_id,
                "type": kind, "status": status, "timestamp": timestamp}
        if scope == ProjectionScope.EVALUATOR:
            item["payload"] = payload
            return item
        if source == "runtime_trace":
            # Control-plane metadata only; raw inputs, projection refs, and runtime
            # errors stay in the EVALUATOR scope.
            outputs = payload.get("output_summary")
            item.update({key: payload.get(key) for key in (
                "request_id", "batch_id", "work_id") if payload.get(key) is not None})
            item["budget_delta"] = payload.get("budget_delta", {})
            if isinstance(outputs, dict) and isinstance(outputs.get("reason_code"), str):
                item["reason_code"] = outputs["reason_code"]
        elif source == "lab_run_log":
            for key in ("expected_revision", "resulting_revision", "operations"):
                if key in payload:
                    item[key] = payload[key]
        return item

    # -- ArtifactView -------------------------------------------------------------------
    def _issued_lab_artifacts(self) -> tuple[InformationRef, ...]:
        """Exact Agent-visible artifact refs ingested into the run's RcaState."""
        try:
            state = self._state_snapshot()["state"]
        except ViewUnavailable:
            return ()
        return tuple(InformationRef(**ref) for ref in state["artifact_refs"])

    def _runtime_artifacts(self) -> tuple[InformationRef, ...]:
        refs, seen = [], set()
        for event in self._trace_events():
            ref = event.get("context_projection_ref")
            if ref is not None and ref["ref_id"] not in seen:
                seen.add(ref["ref_id"])
                refs.append(InformationRef(**ref))
        return tuple(refs)

    def _known_artifacts(self, scope: ProjectionScope) -> tuple[InformationRef, ...]:
        known = self._issued_lab_artifacts()
        if scope == ProjectionScope.EVALUATOR:
            known += self._runtime_artifacts()
        return tuple(ref for ref in known if visible_in(ref.visibility, scope))

    def artifacts(self) -> Any:
        scope = self.scope
        return self._view("ArtifactView", scope, {
            "artifacts": [to_jsonable(ref) for ref in self._known_artifacts(scope)]})

    def get_artifact(self, ref: InformationRef) -> Any:
        """Exact typed ref only: visibility first, then identity, then checksum.

        Strings, mappings, and filesystem paths are refused; no raw path is returned.
        """
        scope = self.scope
        if type(ref) is not InformationRef or not visible_in(ref.visibility, scope):
            raise UnknownArtifact()
        if ref not in self._known_artifacts(scope):
            raise UnknownArtifact()
        if ref.owner == ARTIFACT_OWNER and _LAB_ARTIFACT_ID.fullmatch(ref.ref_id):
            try:
                data = (self._session_dir() / "lab-artifacts" / f"{ref.ref_id}.jsonl").read_bytes()
            except OSError as exc:
                raise UnknownArtifact() from exc
            if hashlib.sha256(data).hexdigest() != ref.checksum:
                raise UnknownArtifact()
            content: Any = [json.loads(line) for line in data.decode("utf-8").splitlines()
                            if line]
        elif ref.kind == "ContextProjection":
            try:
                content = TraceRecorder(self._session_dir() / "trace").read_projection(ref)
            except (OSError, ValueError) as exc:
                raise UnknownArtifact() from exc
        else:
            raise UnknownArtifact()
        return self._view("ArtifactContent", scope, {"ref": to_jsonable(ref),
                                                     "content": content})
