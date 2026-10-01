"""C4 blind-RCA Tool Surface acceptance tests (docs/specs/tool-surface-v0.md)."""

from dataclasses import replace
import hashlib
import inspect
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from industrial_agent_runtime import (
    Action, Budget, Coordinator, FakeProvider, FinishProposal, GatePipeline,
    InformationRef, ModelTurn, ResultVerificationPipeline, SideEffectClass, StateDelta,
    Task, TaskStatus, ToolCallRequest, ToolResult, ToolSpec, TraceRecorder, Visibility,
    canonical_json, checksum, to_jsonable,
)
from industrial_agent_runtime.gates import GateDenied, reconcile
from industrial_agent_runtime.schema import instance_errors, schema_errors
from tep_sim import (REGISTRY, UPSTREAM_REVISION, ControlMode, DisturbanceIntervention,
                     EnvironmentConfig, TEPEnvironment)
from tep_sim.snapshot import observation_sha256

from tep_agent_lab import tep_world, tool_surface
from tep_agent_lab.investigation import RcaResultIngestor, RcaState, RcaStateStore
from tep_agent_lab.persistence import RunLog
from tep_agent_lab.tep_world import (ArtifactStore, ReferenceWorld, WorldError,
                                     leakage_findings)
from tep_agent_lab.tool_surface import (
    BLIND_RCA_EXCLUDED_TOOLS, ISOLATION_GUARANTEE, SANDBOX_POLICY_TAG,
    SIMULATION_DIMENSIONS, BlindRcaToolSurface, ResultInvariantError, simulation_quota,
    tool_version,
)

NOW = "2026-10-01T00:00:00Z"
INJECTED = "IDV(4)"  # evaluator-chosen hidden cause of the incident fixture
HIDDEN_NAMES = tuple(variable.name.lower() for key, variable in REGISTRY.items()
                     if key.startswith("IDV(") and variable.name != "Unknown")


def make_world(directory: Path, *, inject: bool = True) -> ReferenceWorld:
    """Harness-side incident: a closed-loop plant with an evaluator-injected cause."""
    environment = TEPEnvironment(EnvironmentConfig(
        seed=11, backend="python", control_mode=ControlMode.CLOSED_LOOP,
        record_interval=60, upstream_revision=UPSTREAM_REVISION,
        artifact_directory=directory / "world"))
    environment.reset()
    world = ReferenceWorld(environment)
    world.advance(0.1)
    world.designate_baseline()  # pre-incident state, before any hidden injection
    if inject:
        environment.apply(DisturbanceIntervention(INJECTED, 1))
    world.advance(0.2)
    return world


def assert_blind(case: unittest.TestCase, value) -> None:
    """No hidden-truth vocabulary, injected id, or disturbance display name."""
    case.assertEqual([], leakage_findings(value))
    text = canonical_json(to_jsonable(value)).lower()
    case.assertNotIn(INJECTED.lower(), text)
    case.assertNotIn("active_disturbances", text)
    for name in HIDDEN_NAMES:
        case.assertNotIn(name, text)


def reference_fingerprint(world: ReferenceWorld) -> tuple:
    environment = world.environment
    provenance = (Path(environment.config.artifact_directory) / environment.run_id
                  / "provenance.json").read_bytes()
    return (world.revision(), observation_sha256(environment.observe()),
            len(world.history()), hashlib.sha256(provenance).hexdigest())


class SurfaceCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.world = make_world(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.world.environment.close()
        cls.directory.cleanup()

    def setUp(self):
        self.artifact_dir = TemporaryDirectory()
        self.addCleanup(self.artifact_dir.cleanup)
        self.surface = BlindRcaToolSurface(self.world, ArtifactStore(self.artifact_dir.name),
                                           clock=lambda: NOW)
        self.specs = {spec.name: spec for spec in self.surface.tool_specs()}
        self.sequence = 0

    def request(self, name, arguments=None):
        self.sequence += 1
        return ToolCallRequest(f"req-{self.sequence}", name, arguments or {})

    def decide(self, request):
        return self.surface.validate_request(request, self.specs[request.tool_name], None, 0, {})

    def call(self, name, arguments=None):
        """Lab consumer path: validate -> execute -> lab result invariants."""
        request = self.request(name, arguments)
        self.assertEqual([], instance_errors(request.arguments,
                                             self.specs[name].input_schema))
        decision = self.decide(request)
        self.assertEqual("ALLOW", decision.decision, decision.reason)
        result = self.surface.execute(request, self.specs[name])
        self.assertEqual("SUCCESS", result.status, result.error)
        self.surface.check_result(result, request, self.specs[name],
                                  RcaResultIngestor().derive_deltas(result))
        self.assertEqual([], instance_errors(result.structured_output,
                                             self.specs[name].output_schema))
        assert_blind(self, result)
        return result

    def branch(self):
        """Fork of the current (incident) reference state: ``reference`` lineage."""
        snapshot = self.call("snapshot_environment").structured_output["snapshot_id"]
        return self.call("fork_environment", {"snapshot_id": snapshot}).structured_output[
            "branch_id"]

    def baseline_branch(self):
        (baseline,) = self.world.baselines()
        result = self.call("fork_environment", {"snapshot_id": baseline})
        self.assertEqual("baseline", result.structured_output["lineage"])
        return result.structured_output["branch_id"]


class RegistryAndLeakageTests(SurfaceCase):
    def test_default_registry_has_no_answer_leaking_or_mutating_tools(self):
        names = set(self.specs)
        self.assertEqual(set(), names & BLIND_RCA_EXCLUDED_TOOLS)
        self.assertNotIn("get_related_disturbances", names)
        self.assertTrue({"get_current_observation", "get_history", "get_variable_metadata",
                         "get_process_node", "get_neighbors", "get_related_measurements",
                         "get_related_actuators", "get_safety_margins",
                         "check_scenario_capability", "snapshot_environment",
                         "fork_environment", "run_rollout"} <= names)
        self.assertEqual({SideEffectClass.READ, SideEffectClass.SIMULATE},
                         {spec.side_effect_class for spec in self.specs.values()})
        # names, descriptions, schemas, and metadata carry no hidden vocabulary
        assert_blind(self, [to_jsonable(spec) for spec in self.specs.values()])

    def test_evaluator_bindings_are_never_imported_or_held(self):
        imports = re.compile(r"^\s*(from|import)\s.*evaluator_bindings", re.MULTILINE)
        for module in (tool_surface, tep_world):
            self.assertIsNone(imports.search(inspect.getsource(module)))
        held = canonical_json(to_jsonable({key: repr(value) for key, value
                                           in vars(self.surface).items()}))
        self.assertNotIn("EvaluatorDisturbanceBindings", held)

    def test_every_tool_is_an_explicit_deterministic_runtime_contract(self):
        again = BlindRcaToolSurface(self.world, ArtifactStore(self.artifact_dir.name))
        self.assertEqual(self.surface.registered_tool_set_version,
                         again.registered_tool_set_version)
        self.assertEqual([checksum(spec) for spec in self.surface.tool_specs()],
                         [checksum(spec) for spec in again.tool_specs()])
        for spec in self.specs.values():
            self.assertEqual([], schema_errors(spec.input_schema), spec.name)
            self.assertEqual([], schema_errors(spec.output_schema), spec.name)
            self.assertEqual(tool_version(spec.name), spec.provider_metadata["tool_version"])
            if spec.side_effect_class == SideEffectClass.SIMULATE:
                self.assertEqual(ISOLATION_GUARANTEE, spec.isolation_guarantee)
                self.assertEqual((SANDBOX_POLICY_TAG,), spec.required_policy_tags)
                self.assertTrue(set(spec.declared_budget_draw) <= set(SIMULATION_DIMENSIONS))
                self.assertEqual(set(spec.declared_budget_draw), set(spec.max_budget_draw))
            else:
                self.assertEqual({}, dict(spec.declared_budget_draw), spec.name)
                self.assertIsNone(spec.isolation_guarantee)
        rollout = self.specs["run_rollout"]
        self.assertEqual({"simulation_rollouts": 1, "simulated_horizon_seconds": 3600.0},
                         dict(rollout.max_budget_draw))

    def test_gate_policy_grants_read_and_isolated_simulate_only(self):
        policy = self.surface.gate_policy()
        self.assertEqual({SideEffectClass.READ, SideEffectClass.SIMULATE},
                         set(policy.granted_side_effect_classes))
        self.assertEqual(set(SIMULATION_DIMENSIONS), set(policy.simulation_dimensions))
        self.assertEqual(set(self.specs), set(policy.tool_allowlist))
        self.assertIn(SideEffectClass.MUTATE, policy.approval_required_for)

    def test_hidden_truth_cannot_be_recovered_through_registered_tools(self):
        """Exhaustively call every READ tool on every node and variable."""
        outputs = [self.call("get_capability_summary"), self.call("get_safety_margins"),
                   self.call("get_current_observation")]
        for node in self.world.graph.nodes():
            for name in ("get_process_node", "get_neighbors", "get_related_measurements",
                         "get_related_actuators"):
                outputs.append(self.call(name, {"node_id": node.node_id}))
        visible = sorted(key for key in REGISTRY if not key.startswith("IDV("))
        for start in range(0, len(visible), 16):
            outputs.append(self.call("get_variable_metadata",
                                     {"ids": visible[start:start + 16]}))
        for result in outputs:
            assert_blind(self, result)
            self.assertEqual((), result.information_refs)
        # disturbance ids are not even expressible as arguments
        for name, arguments in (("get_variable_metadata", {"ids": [INJECTED]}),
                                ("get_history", {"window_hours": 0.1,
                                                 "variables": [INJECTED]})):
            self.assertTrue(instance_errors(arguments, self.specs[name].input_schema))

    def test_leakage_screen_and_artifact_store_fail_closed(self):
        self.assertTrue(leakage_findings({"active_disturbances": []}))
        self.assertTrue(leakage_findings({"note": "idv_4 looks likely"}))
        self.assertTrue(leakage_findings({"candidate_cause": "x"}))
        self.assertEqual([], leakage_findings({"note": "default reactor behavior"}))
        store = ArtifactStore(self.artifact_dir.name)
        with self.assertRaises(WorldError):
            store.put_records("Telemetry", [{"active_disturbances": ["IDV(1)"]}], NOW)
        ref = store.put_records("Telemetry", [{"x": 1.0}], NOW)
        self.assertEqual(ref, store.resolve(ref))
        # a second store over the same root never overwrites an issued artifact
        other = ArtifactStore(self.artifact_dir.name).put_records("Telemetry", [{"y": 2.0}], NOW)
        self.assertNotEqual(ref.ref_id, other.ref_id)
        self.assertTrue(store.verify(ref))
        self.assertIsNone(store.resolve(replace(ref, checksum="0" * 64)))
        (Path(self.artifact_dir.name) / f"{ref.ref_id}.jsonl").write_text("tampered")
        self.assertFalse(store.verify(ref))


class ReadToolTests(SurfaceCase):
    def test_reactor_topology_measurements_and_actuators(self):
        node = self.call("get_process_node", {"node_id": "reactor"}).structured_output
        self.assertEqual("REACTOR", node["node"]["kind"])
        neighbors = to_jsonable(self.call("get_neighbors", {
            "node_id": "reactor", "direction": "upstream", "max_depth": 1}).structured_output)
        self.assertIn("reactor_feed_mixer", [item["node_id"] for item in neighbors["upstream"]])
        self.assertEqual([], neighbors["downstream"])
        measured = self.call("get_related_measurements", {"node_id": "reactor"})
        self.assertEqual({"XMEAS(7)", "XMEAS(8)", "XMEAS(9)"},
                         {b["runtime_variable_id"] for b in measured.structured_output["bindings"]})
        actuators = self.call("get_related_actuators", {"node_id": "reactor",
                                                        "include_incident_streams": True})
        self.assertIn("XMV(10)", {b["runtime_variable_id"]
                                  for b in actuators.structured_output["bindings"]})
        self.assertEqual("PENDING_HUMAN_REVIEW",
                         measured.provenance["process_graph_review_status"])
        unknown = self.decide(self.request("get_process_node", {"node_id": "no_such_node"}))
        self.assertEqual(("DENY", "INVALID_REQUEST"), (unknown.decision, unknown.reason_code))

    def test_current_observation_is_typed_and_sanitized(self):
        output = self.call("get_current_observation").structured_output
        raw = self.world.environment.observe()
        self.assertIn(INJECTED, raw.active_disturbances)  # hidden truth exists ...
        self.assertNotIn("active_disturbances", output)    # ... but is not visible
        self.assertEqual(dict(raw.measurements), output["measurements"])
        self.assertEqual(dict(raw.manipulated_variables), output["manipulated_variables"])
        filtered = self.call("get_current_observation",
                             {"variables": ["XMEAS(9)", "XMV(10)"]}).structured_output
        self.assertEqual(["XMEAS(9)"], list(filtered["measurements"]))
        self.assertEqual(["XMV(10)"], list(filtered["manipulated_variables"]))
        metadata = self.call("get_variable_metadata", {"ids": ["XMEAS(9)"]}).structured_output
        self.assertEqual("reactor", metadata["variables"][0]["binding"]["attached_to"])

    def test_bounded_history_is_previewed_and_artifact_backed(self):
        variables = ["XMEAS(9)", "XMV(10)"]
        result = self.call("get_history", {"window_hours": 0.3, "variables": variables})
        output = result.structured_output
        self.assertEqual(19, output["shape"]["records"])  # 0.3 h at 1-minute records
        self.assertLessEqual(len(output["preview"]["simulation_time_hours"]),
                             self.surface.limits.max_preview_points)
        self.assertFalse(output["preview_complete"])
        (ref,) = result.artifact_refs
        self.assertEqual(to_jsonable(ref), output["artifact_ref"])
        rows = self.surface.artifacts.read(ref)
        self.assertEqual(19, len(rows))
        history = self.world.history()[-19:]
        self.assertEqual([record["measurements"]["XMEAS(9)"] for record in history],
                         [row["XMEAS(9)"] for row in rows])
        small = self.call("get_history", {"window_hours": 0.05, "variables": variables})
        self.assertTrue(small.structured_output["preview_complete"])
        self.assertIsNone(small.structured_output["artifact_ref"])
        self.assertEqual((), small.artifact_refs)
        too_long = {"window_hours": 99.0, "variables": variables}
        self.assertTrue(instance_errors(too_long, self.specs["get_history"].input_schema))
        too_many = {"window_hours": 0.1, "variables": [f"XMEAS({i})" for i in range(1, 10)]}
        self.assertTrue(instance_errors(too_many, self.specs["get_history"].input_schema))
        duplicate = self.decide(self.request("get_history", {
            "window_hours": 0.1, "variables": ["XMEAS(9)", "XMEAS(9)"]}))
        self.assertEqual("INVALID_REQUEST", duplicate.reason_code)
        missing = self.decide(self.request("get_variable_metadata", {"ids": ["XMEAS(99)"]}))
        self.assertEqual("INVALID_REQUEST", missing.reason_code)

    def test_safety_margins_and_capability_summary(self):
        margins = self.call("get_safety_margins").structured_output
        self.assertEqual(8, len(margins["limits"]))
        observed = self.world.environment.observe().safety_margins
        for limit in margins["limits"]:
            self.assertEqual(observed[limit["limit_id"]], limit["margin"])
        summary = self.call("get_capability_summary").structured_output
        self.assertEqual("closed_loop", summary["control_mode"])
        self.assertEqual("NOT_EXPOSED_IN_BLIND_RCA",
                         summary["semantic_scenarios"]["enumeration"])
        self.assertIn("fire", [item["domain_id"]
                               for item in summary["unsupported_consequence_domains"]])
        self.assertEqual(list(self.world.baselines()),
                         [item["snapshot_id"] for item in summary["baseline_snapshots"]])
        self.assertEqual("BASELINE_LINEAGE_BRANCHES_ONLY", summary["scenario_application"])


class SimulateToolTests(SurfaceCase):
    def test_snapshot_fork_rollout_is_isolated_and_reference_unchanged(self):
        before = reference_fingerprint(self.world)
        snapshot = self.call("snapshot_environment")
        self.assertEqual({"simulation_snapshots": 1}, dict(snapshot.actual_budget_draw))
        branch = self.call("fork_environment",
                           {"snapshot_id": snapshot.structured_output["snapshot_id"]})
        branch_id = branch.structured_output["branch_id"]
        self.assertEqual({"simulation_branches": 1}, dict(branch.actual_budget_draw))
        rollout = self.call("run_rollout", {"branch_id": branch_id, "horizon_hours": 0.05})
        output = rollout.structured_output
        self.assertEqual(before, reference_fingerprint(self.world))
        self.assertEqual("horizon_reached", output["termination_reason"])
        self.assertEqual({"simulation_rollouts": 1, "simulated_horizon_seconds": 180},
                         dict(rollout.actual_budget_draw))
        self.assertAlmostEqual(output["start_time_hours"] + 0.05, output["end_time_hours"])
        branch_env = self.surface.sandbox.branch(branch_id)
        self.assertIsNot(branch_env, self.world.environment)
        self.assertGreater(branch_env.observe().simulation_time,
                           self.world.environment.observe().simulation_time)
        # a second rollout on the same branch continues there; reference still unchanged
        self.call("run_rollout", {"branch_id": branch_id, "horizon_hours": 0.01})
        self.assertEqual(before, reference_fingerprint(self.world))

    def test_semantic_scenario_rollout_on_branch_only(self):
        before = reference_fingerprint(self.world)
        branch_id = self.baseline_branch()
        result = self.call("run_rollout", {
            "branch_id": branch_id, "horizon_hours": 0.02,
            "scenarios": [{"scenario_id": "condenser_cooling_water_inlet_temperature_step"}],
            "preview_variables": ["XMEAS(9)", "XMEAS(21)"]})
        output = to_jsonable(result.structured_output)
        self.assertEqual([{"scenario_id": "condenser_cooling_water_inlet_temperature_step",
                           "parameters": {}, "intervention_count": 1}],
                         output["applied_scenarios"])
        self.assertEqual({"XMEAS(9)", "XMEAS(21)"}, set(output["preview"]["values"]))
        self.assertEqual(before, reference_fingerprint(self.world))
        # the branch did change: its hidden state now differs from the reference
        self.assertNotEqual(self.surface.sandbox.branch(branch_id).observe().active_disturbances,
                            self.world.environment.observe().active_disturbances)

    def test_scenarios_on_incident_state_forks_are_denied(self):
        """Re-applying an active cause on an incident fork is a no-op oracle."""
        branch_id = self.branch()
        self.assertEqual("reference", self.surface.sandbox.lineage(branch_id))
        for scenario_id in ("reactor_cooling_water_inlet_temperature_step",
                            "condenser_cooling_water_inlet_temperature_step"):
            decision = self.decide(self.request("run_rollout", {
                "branch_id": branch_id, "horizon_hours": 0.01,
                "scenarios": [{"scenario_id": scenario_id}]}))
            self.assertEqual(("DENY", "POLICY_DENIED"), (decision.decision, decision.reason_code))
        # forward prediction without a scenario stays available on incident forks
        self.call("run_rollout", {"branch_id": branch_id, "horizon_hours": 0.01})
        with self.assertRaises(ValueError):
            self.world.designate_baseline()  # hidden state present: not a baseline

    def test_dense_rollout_output_is_artifact_backed_and_sanitized(self):
        result = self.call("run_rollout", {"branch_id": self.branch(), "horizon_hours": 0.25})
        output = to_jsonable(result.structured_output)
        (ref,) = result.artifact_refs
        self.assertEqual("RolloutTelemetryArtifact", ref.kind)
        self.assertEqual(Visibility.AGENT, ref.visibility)
        self.assertEqual(to_jsonable(ref), output["artifact_ref"])
        self.assertNotIn(self.root.as_posix().lower(), canonical_json(to_jsonable(result)))
        rows = self.surface.artifacts.read(ref)
        self.assertEqual(output["shape"]["records"], len(rows))
        self.assertEqual(16, len(rows))  # initial record + 15 one-minute records
        self.assertLess(len(output["preview"]["simulation_time_hours"]), len(rows))
        assert_blind(self, rows)
        self.assertTrue(all(set(row) == {"simulation_time_hours", "measurements",
                                         "manipulated_variables", "shutdown_state",
                                         "safety_margins"} for row in rows))
        self.assertEqual([], output["safety"]["limit_crossings"])

    def test_simulate_cannot_target_or_mutate_the_reference(self):
        before = reference_fingerprint(self.world)
        arguments = {"branch_id": "reference", "horizon_hours": 0.01}
        self.assertTrue(instance_errors(arguments, self.specs["run_rollout"].input_schema))
        denied = self.decide(self.request("run_rollout", arguments))
        self.assertEqual(("DENY", "POLICY_DENIED"), (denied.decision, denied.reason_code))
        unknown = self.decide(self.request("run_rollout", {"branch_id": "branch-9999",
                                                           "horizon_hours": 0.01}))
        self.assertEqual("INVALID_REQUEST", unknown.reason_code)
        # an executor reached without authorization still refuses
        blocked = self.surface.execute(self.request("run_rollout", arguments),
                                       self.specs["run_rollout"])
        self.assertEqual("POLICY_DENIED", blocked.status)
        self.assertEqual(before, reference_fingerprint(self.world))

    def test_reference_mutate_is_unavailable_and_denied(self):
        mutate = ToolSpec("apply_validated_intervention", "apply", {"type": "object"},
                          {"type": "object"}, SideEffectClass.MUTATE)
        task = Task("t", "g", (), (*self.specs, mutate.name), Budget(5, 5, 0, 0, 5),
                    {"type": "object"})
        request = ToolCallRequest("m1", mutate.name, {})
        registered = GatePipeline(task, self.specs, self.surface.gate_policy(), self.surface,
                                  None, self.surface)
        with self.assertRaises(GateDenied) as unknown:
            registered.authorize(request, {}, {}, 0)
        self.assertEqual("UNKNOWN_TOOL", unknown.exception.decision.reason_code)
        smuggled = GatePipeline(task, {**self.specs, mutate.name: mutate},
                                self.surface.gate_policy(), self.surface, None, self.surface)
        with self.assertRaises(GateDenied) as denied:
            smuggled.authorize(request, {}, {}, 0)
        self.assertEqual("G1_AUTHORITY", denied.exception.decision.stage)
        consumer = self.surface.validate_request(request, mutate, task, 0, {})
        self.assertEqual(("DENY", "POLICY_DENIED"), (consumer.decision, consumer.reason_code))

    def test_unsupported_scenarios_fail_explicitly_without_simulating(self):
        check = lambda scenario_id: self.call(  # noqa: E731
            "check_scenario_capability", {"scenario": {"scenario_id": scenario_id}}
        ).structured_output
        self.assertEqual(("UNSUPPORTED", "consequence:fire"),
                         (check("fire")["status"], check("fire")["missing_capability"]))
        ambiguous = check("loss_of_cooling")
        self.assertEqual("AMBIGUOUS", ambiguous["status"])
        self.assertNotIn("candidates", ambiguous)  # tested mappings would list causes
        self.assertNotIn("reactor_cooling", ambiguous["reason"])
        self.assertEqual("UNSUPPORTED", check("pump_seizure")["status"])
        manual_only = {"scenario": {"scenario_id": "reactor_cooling_water_flow_reduction",
                                    "parameters": {"valve_position_percent": 10.0}}}
        self.assertEqual("UNSUPPORTED", self.call("check_scenario_capability",
                                                  manual_only).structured_output["status"])
        compile_denied = self.decide(self.request("compile_process_deviation", manual_only))
        self.assertEqual("UNSUPPORTED_CAPABILITY", compile_denied.reason_code)
        branch_id = self.baseline_branch()
        start = self.surface.sandbox.branch(branch_id).observe().simulation_time
        for scenario, code in ((manual_only["scenario"], "UNSUPPORTED_CAPABILITY"),
                               ({"scenario_id": "fire"}, "UNSUPPORTED_CAPABILITY"),
                               ({"scenario_id": "loss_of_cooling"}, "INVALID_REQUEST")):
            decision = self.decide(self.request("run_rollout", {
                "branch_id": branch_id, "horizon_hours": 0.01, "scenarios": [scenario]}))
            self.assertEqual(("DENY", code), (decision.decision, decision.reason_code))
            self.assertNotIn("reactor_cooling", decision.reason)
        self.assertEqual(start, self.surface.sandbox.branch(branch_id).observe().simulation_time)
        compiled = self.call("compile_process_deviation", {"scenario": {
            "scenario_id": "reactor_cooling_water_inlet_temperature_step"}}).structured_output
        compiled = to_jsonable(compiled)
        self.assertEqual([{"kind": "PROCESS_DEVIATION", "target": None, "value": None}],
                         compiled["interventions"])

    def test_simulate_reservation_and_actual_budget_reconcile(self):
        task = Task("t", "g", (), tuple(self.specs), Budget(
            5, 5, 0, 0, 5, extra_dimensions=simulation_quota(
                snapshots=1, branches=1, rollouts=1, horizon_seconds=3600)),
            {"type": "object"})
        pipeline = GatePipeline(task, self.specs, self.surface.gate_policy(), self.surface,
                                None, self.surface)
        reservation = pipeline.reservation("r", self.specs["run_rollout"])
        self.assertEqual({"simulation_rollouts": 1, "simulated_horizon_seconds": 3600.0},
                         reservation)
        result = self.call("run_rollout", {"branch_id": self.branch(), "horizon_hours": 0.02})
        accounting = reconcile(reservation, result.actual_budget_draw, task.budget)
        self.assertEqual((), accounting.violations)
        self.assertEqual({"simulation_rollouts": 1, "simulated_horizon_seconds": 72},
                         dict(accounting.charged))


class ResultVerificationTests(SurfaceCase):
    def test_tool_results_satisfy_b3_provenance_and_tool_version(self):
        pipeline = ResultVerificationPipeline(self.surface, RcaResultIngestor())
        for name, arguments in (("get_process_node", {"node_id": "reactor"}),
                                ("snapshot_environment", {})):
            request = self.request(name, arguments)
            result = self.surface.execute(request, self.specs[name])
            spec = self.specs[name]
            reservation = dict(spec.declared_budget_draw)
            accounting = reconcile(reservation, result.actual_budget_draw,
                                   Budget(1, 1, 0, 0, 1, extra_dimensions={
                                       name: 10 for name in SIMULATION_DIMENSIONS}))
            verified = pipeline.verify(result, request, spec, accounting, {}, {}, 0, lambda: 0)
            self.assertEqual("ACCEPT", verified.decision.decision)
            self.assertEqual(spec.provider_metadata["tool_version"],
                             result.provenance["tool_version"])
            self.assertEqual({"REGISTER_OBSERVATION"}, {d.operation for d in verified.deltas})
        forged = replace(result, provenance={**result.provenance, "tool_version": "other"})
        with self.assertRaises(Exception) as rejected:
            pipeline.verify(forged, request, spec, accounting, {}, {}, 0, lambda: 0)
        self.assertEqual("TOOL_VERSION_MISMATCH", rejected.exception.decision.reason_code)

    def test_lab_invariants_reject_tampering_evidence_and_bad_accounting(self):
        request = self.request("run_rollout", {"branch_id": self.branch(),
                                               "horizon_hours": 0.01})
        spec = self.specs["run_rollout"]
        result = self.surface.execute(request, spec)
        deltas = RcaResultIngestor().derive_deltas(result)
        self.surface.check_result(result, request, spec, deltas)
        tampered = (
            replace(result, actual_budget_draw={"simulation_rollouts": 1,
                                                "simulated_horizon_seconds": 1}),
            replace(result, structured_output={**result.structured_output,
                                               "note": "IDV(4) active"}),
            replace(result, artifact_refs=()),
        )
        for bad in tampered:
            with self.assertRaises(ResultInvariantError):
                self.surface.check_result(bad, request, spec, deltas)
            self.assertFalse(self.surface.verify_result(bad, request, spec, deltas, 0))
        evidence = StateDelta("ADD_EVIDENCE_LINK", "link", {}, "RESULT_INGESTION")
        with self.assertRaises(ResultInvariantError):
            self.surface.check_result(result, request, spec, (*deltas, evidence))
        unexecuted = self.request("get_safety_margins")
        fabricated = ToolResult(unexecuted.request_id, "SUCCESS", {}, {}, {
            "tool_name": "get_safety_margins", "tool_version": tool_version("get_safety_margins"),
            "surface_version": tool_surface.TOOL_SURFACE_VERSION,
            "request_id": unexecuted.request_id, "created_at": NOW})
        self.assertFalse(self.surface.verify_result(
            fabricated, unexecuted, self.specs["get_safety_margins"], (), 0))


class ReferenceChangeTests(unittest.TestCase):
    def test_reference_change_during_execution_is_stale_and_unverifiable(self):
        with TemporaryDirectory() as directory:
            world = make_world(Path(directory), inject=False)
            surface = BlindRcaToolSurface(world, ArtifactStore(Path(directory) / "artifacts"),
                                          clock=lambda: NOW)
            specs = {spec.name: spec for spec in surface.tool_specs()}
            request = ToolCallRequest("r1", "get_safety_margins", {})
            result = surface.execute(request, specs["get_safety_margins"])
            world.advance(0.01)  # harness moves the reference after execution
            self.assertFalse(surface.verify_result(result, request, specs["get_safety_margins"],
                                                   (), 0))
            # a runner that lets the reference move mid-execution yields STALE_STATE
            surface._tools["get_safety_margins"] = replace(
                surface._tools["get_safety_margins"],
                run=lambda *args: (world.advance(0.01), ({}, ()))[1])
            stale = surface.execute(ToolCallRequest("r2", "get_safety_margins", {}),
                                    specs["get_safety_margins"])
            self.assertEqual("STALE_STATE", stale.status)

            # the scenario rule is lineage-based, so a healthy plant answers the same way
            snapshot = surface.execute(ToolCallRequest("r3", "snapshot_environment", {}),
                                       specs["snapshot_environment"])
            branch = surface.execute(ToolCallRequest("r4", "fork_environment", {
                "snapshot_id": snapshot.structured_output["snapshot_id"]}),
                specs["fork_environment"]).structured_output["branch_id"]
            decision = surface.validate_request(ToolCallRequest("r5", "run_rollout", {
                "branch_id": branch, "horizon_hours": 0.01,
                "scenarios": [{"scenario_id": "reactor_cooling_water_inlet_temperature_step"}]}),
                specs["run_rollout"], None, 0, {})
            self.assertEqual("POLICY_DENIED", decision.reason_code)

            # an unexpected runner error becomes a generic, sanitized failure result
            def broken(*args):
                raise KeyError("IDV(4) secret detail")
            surface._tools["get_capability_summary"] = replace(
                surface._tools["get_capability_summary"], run=broken)
            failed = surface.execute(ToolCallRequest("r6", "get_capability_summary", {}),
                                     specs["get_capability_summary"])
            self.assertEqual(("INVALID_REQUEST", "internal tool error"),
                             (failed.status, failed.error))
            assert_blind(self, failed)

            # retiring a failed branch also drops the snapshots it owns
            owned = surface.execute(ToolCallRequest("r7", "snapshot_environment", {
                "source": branch}), specs["snapshot_environment"])
            owned_id = owned.structured_output["snapshot_id"]
            surface.sandbox.retire(branch)
            self.assertFalse(surface.sandbox.has_branch(branch))
            self.assertFalse(surface.sandbox.has_snapshot(owned_id))
            world.environment.close()


class CoordinatorIngestionTests(unittest.TestCase):
    """Runtime B1-B3 + lab C1 ingestion over the real tool surface."""

    def test_verified_results_become_observations_not_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            world = make_world(root)
            surface = BlindRcaToolSurface(world, ArtifactStore(root / "artifacts"),
                                          finish_verifier=lambda *args: True,
                                          clock=lambda: NOW)
            incident = InformationRef("incident-1", "Incident", "fixture", "v1",
                                      Visibility.AGENT, NOW)
            store = RcaStateStore(
                RcaState(investigation_id="i1", goal="find the root cause",
                         incident_ref=incident),
                RunLog(root / "state", {"run_id": "c4", "investigation_id": "i1"}),
                resolve=lambda ref: surface.artifacts.resolve(ref) or (
                    ref if ref == incident else None),
                task_id="task-1")
            before = reference_fingerprint(world)
            requests = [
                ToolCallRequest("a-topology", "get_related_measurements", {"node_id": "reactor"}),
                ToolCallRequest("b-snapshot", "snapshot_environment", {}),
                # snapshot-0001 is the harness-designated pre-incident baseline
                ToolCallRequest("c-fork", "fork_environment", {"snapshot_id": "snapshot-0001"}),
                ToolCallRequest("d-rollout", "run_rollout", {
                    "branch_id": "branch-0003", "horizon_hours": 0.05,
                    "scenarios": [{"scenario_id": "reactor_cooling_water_inlet_temperature_step"}]}),
                ToolCallRequest("e-unsupported", "run_rollout", {
                    "branch_id": "branch-0003", "horizon_hours": 0.01,
                    "scenarios": [{"scenario_id": "fire"}]}),
                ToolCallRequest("f-reference", "run_rollout", {
                    "branch_id": "reference", "horizon_hours": 0.01}),
            ]

            def turn(action, **payload):
                def build(projection, limits):
                    return ModelTurn(f"turn-{limits['budget_usage']['model_calls']}",
                                     limits["context_projection_ref"], projection.base_revision,
                                     action, **payload)
                return build

            provider = FakeProvider(
                [turn(Action.TOOL_REQUEST, tool_request=request) for request in requests]
                + [turn(Action.FINISH_PROPOSAL, finish_proposal=FinishProposal({"done": True}))])
            task = Task("task-1", "find the root cause", (incident,),
                        tuple(spec.name for spec in surface.tool_specs()),
                        Budget(10, 10, 0, 0, 20, extra_dimensions=simulation_quota(
                            snapshots=2, branches=2, rollouts=3, horizon_seconds=3 * 3600)),
                        {"type": "object"})
            trace = TraceRecorder(root / "trace")
            result = Coordinator(
                task, store, provider, trace, surface.tool_specs(),
                model_metadata={"provider": "fake", "model": "scripted",
                                "model_version": "v1", "prompt_template_version": "v1"},
                gate=surface, executor=surface, verifier=surface,
                ingestor=RcaResultIngestor(), gate_policy=surface.gate_policy(),
                reference_guard=surface, clock=lambda: NOW).run()

            self.assertEqual(TaskStatus.DONE, result.status, result.errors)
            state = store.state
            self.assertEqual(["observation:a-topology", "observation:b-snapshot",
                              "observation:c-fork", "observation:d-rollout"],
                             [ref.ref_id for ref in state.observation_refs])
            self.assertEqual((), state.evidence_link_refs)
            self.assertEqual((), state.hypothesis_refs)
            self.assertEqual(1, len(state.artifact_refs))
            self.assertEqual("RolloutTelemetryArtifact", state.artifact_refs[0].kind)
            self.assertEqual(before, reference_fingerprint(world))
            usage = dict(result.budget_usage)
            self.assertEqual((1, 1, 1, 180), (usage["simulation_snapshots"],
                                              usage["simulation_branches"],
                                              usage["simulation_rollouts"],
                                              usage["simulated_horizon_seconds"]))
            events = trace.read_events()
            consumer_denials = [event["output_summary"]["reason_code"] for event in events
                                if event["type"] == "GATE" and event["status"] == "DENY"]
            self.assertIn("UNSUPPORTED_CAPABILITY", consumer_denials)
            self.assertIn("INVALID_ARGUMENTS", consumer_denials)  # SIMULATE on "reference"
            accepted = [event for event in events
                        if event["type"] == "VERIFY_RESULT" and event["status"] == "ACCEPTED"]
            self.assertEqual(4, len(accepted))
            projection = store.project({})
            assert_blind(self, projection.content)
            world.environment.close()


if __name__ == "__main__":
    unittest.main()
