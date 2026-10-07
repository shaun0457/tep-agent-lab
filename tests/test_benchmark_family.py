"""D0.2A reactor-thermal-v0 incident family tests (docs/d0-2a-incident-family.md).

Benchmark infrastructure only: no C0, no identifiability or difficulty, no healthy fixture.
"""

from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from industrial_agent_runtime import Visibility, canonical_json, checksum
from tep_sim import UPSTREAM_REVISION, EnvironmentConfig, TEPEnvironment

from tep_agent_lab import (application_transport, application_views, benchmark,
                           desktop_backend, e0_demo, playground, playground_views, tool_bridge,
                           tool_surface)
from tep_agent_lab.benchmark import (
    BENCHMARK_FIXTURES, D0_FIXTURE, LEAKAGE_POLICY_VERSION, BenchmarkCase,
    BenchmarkContractError, BenchmarkHarness, CausalMechanism, EvaluatorGroundTruth,
    MetricStatus, ModelInputRecorder, benchmark_context_sources, collect_agent_surfaces,
    iter_development_fixtures, load_fixture, project_case, score_submission,
    scripted_blind_provider,
)
from tep_agent_lab.canonical_context import (PackageSourceMaterializer, ProjectionScope,
                                             content_checksum)
from tep_agent_lab.playground import (EVALUATOR_BINDINGS_SOURCE_ID, PROCESS_GRAPH_REVIEW_STATUS,
                                      BenchmarkPartition, RunStatus)
from tep_agent_lab.tep_world import ReferenceWorld
from test_benchmark import (CASE_CHECKSUM, PROJECTION_CHECKSUM, TRUTH_CHECKSUM, make_manager,
                            synthetic)
from test_playground import REVISIONS
from test_tool_surface import assert_blind

ROOT = Path(__file__).resolve().parents[1]
FAMILY = "reactor-thermal-v0"
TRIGGER = "XMEAS(9)"
# Frozen per-case expectations (EVALUATOR-only). The binding semantics are the pinned
# tep-sim evaluator overlay's; tests re-derive them from that overlay, not from here.
CASES = {
    "rca-dev-001": dict(
        case="e3305b5cd4ba1e2c59c625e5e067ceb067c90a9b9db3631d4d38bc80c088e176",
        truth="2feb22f1657624cbd739aeada44033758eceaa2555c540de1adaf381a91155f7",
        projection="a9565b8ba5e21b53e2c22176fa79da36d48536c6325a629656ff32168bea6d35",
        seed=11, hidden="IDV(4)", hours=(0.1, 0.2),
        semantic="reactor_cooling.inlet_temperature_step_disturbance",
        fault_family="INLET_TEMPERATURE_STEP"),
    "rca-dev-002": dict(
        case="ea571876db66c11e40dfb554849b60d5819edd74f8044eabcf7046ad9c76caf5",
        truth="7f6d3bb9621a72f56252db028633a60da7e09c4fa5f641b0846ee73c91a28ec0",
        projection="75ff97da2b509f4e7e5836289bdccbac91557ade48eea6e36caa5f73bcb6d6ef",
        seed=12, hidden="IDV(11)", hours=(0.1, 0.5),
        semantic="reactor_cooling.inlet_temperature_random_disturbance",
        fault_family="INLET_TEMPERATURE_RANDOM_VARIATION"),
    "rca-dev-003": dict(
        case="f2ed7acf8fdd568e0832535d47db74820a00bef6db4120cbe6c31b98ad502a7d",
        truth="09aa3063514ec22499494092d3fdc7eb35a87b46bf144daaf614ad6ebf8455a0",
        projection="3d6a4a02a9046ae71c323307b4846467203470bcfb8f01b3b5a407df44802042",
        seed=13, hidden="IDV(14)", hours=(0.1, 0.5),
        semantic="reactor_cooling.valve_sticking_disturbance",
        fault_family="REACTOR_COOLING_VALVE_STICKING"),
}
NEW_CASES = ("rca-dev-002", "rca-dev-003")
# Lab interpretation of the evaluator overlay's quantity -> frozen mechanism vocabulary.
QUANTITY_MECHANISM = {"temperature": CausalMechanism.TEMPERATURE_DISTURBANCE,
                      "valve_position": CausalMechanism.VALVE_STICKING}
# Canonical bytes of the D0.1 synthetic re-score, frozen before D0.2A: the scorer and
# rca-dev-001 truth are unchanged, so this output must stay byte-identical.
D0_1_SYNTHETIC_SCORE_SHA256 = "bc9ca2626c7158d6a9c4c2c311cb18ddb41990064cb3e2004509564741f48430"


def harness(case_id: str) -> BenchmarkHarness:
    return load_fixture(case_id, "1", lab_revision=REVISIONS.tep_agent_lab)


def fixture_bytes(path: str) -> bytes:
    return PackageSourceMaterializer("tep_agent_lab").read_bytes(path)


def forbidden(h: BenchmarkHarness) -> tuple[str, ...]:
    """Evaluator-known strings of one case that must never reach an Agent surface."""
    fixture, claim = h.fixture, h.truth.causal_claim
    expected = CASES[h.case.case_id]
    return (expected["hidden"], expected["semantic"], claim.fault_family,
            claim.mechanism.value, h.case.scenario_family_id, h.case.hidden_setup.checksum(),
            fixture.case_source_id, fixture.ground_truth_source_id, fixture.case_path,
            fixture.ground_truth_path, Path(fixture.case_path).name,
            Path(fixture.ground_truth_path).name, fixture.case_checksum,
            fixture.ground_truth_checksum, EVALUATOR_BINDINGS_SOURCE_ID)


def assert_absent(test: unittest.TestCase, tokens, value) -> None:
    text = canonical_json(value).lower()
    for token in tokens:
        test.assertNotIn(token.lower(), text)


def setup_world(directory: Path, h: BenchmarkHarness, *, incident: bool = True):
    """EVALUATOR-only world on the case's own WorldSpec, as P0 builds it.

    With ``incident=False`` the same seed advances over the same timeline without the
    hidden intervention: a test-only comparison trajectory, not a healthy fixture.
    """
    world_spec = h.case.world
    environment = TEPEnvironment(EnvironmentConfig(
        seed=world_spec.seed, backend=world_spec.backend,
        control_mode=world_spec.control_mode, record_interval=world_spec.record_interval,
        upstream_revision=UPSTREAM_REVISION, artifact_directory=directory / "world"))
    environment.reset()
    world = ReferenceWorld(environment)
    try:
        if incident:
            attestation = h.case_setup()(world)
        else:
            setup = h.case.hidden_setup
            world.advance(setup.pre_incident_hours)
            world.advance(setup.post_incident_hours)
            attestation = None
        return world.observe(), world.history(), attestation
    finally:
        environment.close()


# -- registry / fixtures --------------------------------------------------------------
class FamilyFixtureTests(unittest.TestCase):
    def test_registry_contains_exactly_the_three_incident_fixtures(self):  # 1
        self.assertEqual([("rca-dev-001", "1"), ("rca-dev-002", "1"), ("rca-dev-003", "1")],
                         list(BENCHMARK_FIXTURES))
        self.assertIs(D0_FIXTURE, BENCHMARK_FIXTURES[("rca-dev-001", "1")])
        with self.assertRaises(TypeError):  # immutable
            BENCHMARK_FIXTURES[("rca-dev-004", "1")] = D0_FIXTURE
        self.assertEqual(list(CASES), [h.case.case_id for h in iter_development_fixtures(
            REVISIONS.tep_agent_lab)])
        for case_id, version in (("rca-dev-004", "1"), ("rca-dev-001", "2")):
            with self.subTest(case_id=case_id), self.assertRaises(BenchmarkContractError):
                load_fixture(case_id, version, lab_revision=REVISIONS.tep_agent_lab)

    def test_rca_dev_001_is_unchanged(self):  # 2
        h = harness("rca-dev-001")
        self.assertEqual((CASE_CHECKSUM, TRUTH_CHECKSUM, PROJECTION_CHECKSUM),
                         (h.fixture.case_checksum, h.fixture.ground_truth_checksum,
                          h.projection_checksum))
        self.assertEqual(CASES["rca-dev-001"]["case"], CASE_CHECKSUM)

    def test_family_membership_and_identities(self):  # 3
        sources = set()
        for case_id, expected in CASES.items():
            h = harness(case_id)
            with self.subTest(case_id=case_id):
                identity = ("tep-rca-benchmark/v0", case_id, "1")
                self.assertEqual(identity, h.case.identity())
                self.assertEqual(identity, h.truth.identity())
                self.assertEqual(identity, (h.fixture.benchmark_version, h.fixture.case_id,
                                            h.fixture.case_version))
                self.assertEqual((BenchmarkPartition.DEVELOPMENT, "RCA", FAMILY, "O3"),
                                 (h.case.partition, h.case.task_family,
                                  h.case.scenario_family_id,
                                  h.case.orchestration_policy.condition))
                world, setup = h.case.world, h.case.hidden_setup
                self.assertEqual((expected["seed"], "python", "closed_loop", 60),
                                 (world.seed, world.backend, world.control_mode.value,
                                  world.record_interval))
                self.assertEqual((*expected["hours"], "DISTURBANCE", expected["hidden"], 1),
                                 (setup.pre_incident_hours, setup.post_incident_hours,
                                  setup.intervention.kind, setup.intervention.disturbance_id,
                                  setup.intervention.value))
                self.assertEqual(harness("rca-dev-001").case.tool_policy, h.case.tool_policy)
                self.assertEqual(harness("rca-dev-001").case.budget, h.case.budget)
            sources |= {h.fixture.case_source_id, h.fixture.ground_truth_source_id}
        self.assertEqual(2 * len(CASES), len(sources))

    def test_fixture_checksums_are_exact_and_frozen(self):  # 4
        for case_id, expected in CASES.items():
            fixture = BENCHMARK_FIXTURES[(case_id, "1")]
            with self.subTest(case_id=case_id):
                self.assertEqual((expected["case"], expected["truth"]),
                                 (fixture.case_checksum, fixture.ground_truth_checksum))
                for path, frozen in ((fixture.case_path, expected["case"]),
                                     (fixture.ground_truth_path, expected["truth"])):
                    self.assertEqual(frozen, content_checksum(
                        (ROOT / path).read_bytes(), "sha256:canonical-json"))
                self.assertEqual(expected["projection"], harness(case_id).projection_checksum)
        for case_id in NEW_CASES:  # a changed byte fails before parsing
            fixture = BENCHMARK_FIXTURES[(case_id, "1")]
            original = fixture_bytes(fixture.case_path)
            seed = CASES[case_id]["seed"]
            changed = original.replace(f'"seed": {seed},'.encode(), b'"seed": 99,')
            self.assertNotEqual(original, changed)
            with self.subTest(case_id=case_id), self.assertRaises(BenchmarkContractError):
                load_fixture(case_id, "1", lab_revision=REVISIONS.tep_agent_lab,
                             read=lambda path, c=changed, f=fixture:
                             c if path == f.case_path else fixture_bytes(path))

    def test_fixture_files_reject_unknown_fields(self):  # 5
        for case_id in CASES:
            fixture = BENCHMARK_FIXTURES[(case_id, "1")]
            for path in ((), ("world",), ("agent_projection",), ("hidden_setup",),
                         ("hidden_setup", "intervention"), ("budget",), ("scoring",)):
                record = json.loads(fixture_bytes(fixture.case_path))
                target = record
                for key in path:
                    target = target[key]
                target["unexpected"] = 1
                with self.subTest(case_id=case_id, path=path), \
                        self.assertRaises(BenchmarkContractError):
                    BenchmarkCase.from_record(record)
            for path in ((), ("causal_claim",)):
                record = json.loads(fixture_bytes(fixture.ground_truth_path))
                target = record
                for key in path:
                    target = target[key]
                target["description"] = "free text"
                with self.subTest(case_id=case_id, truth=path), \
                        self.assertRaises(BenchmarkContractError):
                    EvaluatorGroundTruth.from_record(record)

    def test_each_fixture_keeps_exactly_one_hidden_disturbance(self):
        for case_id in NEW_CASES:
            record = json.loads(fixture_bytes(BENCHMARK_FIXTURES[(case_id, "1")].case_path))
            single = record["hidden_setup"]["intervention"]
            for intervention in ([single, single], None, {**single, "value": 0}):
                broken = {**record, "hidden_setup": {**record["hidden_setup"],
                                                     "intervention": intervention}}
                with self.subTest(case_id=case_id, intervention=intervention), \
                        self.assertRaises(BenchmarkContractError):
                    BenchmarkCase.from_record(broken)

    def test_context_sources_are_evaluator_only(self):  # 6
        for case_id in CASES:
            fixture = BENCHMARK_FIXTURES[(case_id, "1")]
            sources = benchmark_context_sources(REVISIONS.tep_agent_lab, fixture)
            with self.subTest(case_id=case_id):
                self.assertEqual([(Visibility.EVALUATOR, "BENCHMARK_CASE", fixture.case_path),
                                  (Visibility.EVALUATOR, "EVALUATOR_GROUND_TRUTH",
                                   fixture.ground_truth_path)],
                                 [(source.visibility, source.kind, source.path_or_ref)
                                  for source in sources])
                hidden = harness(case_id).hidden_sources(REVISIONS.tep_sim)
                self.assertEqual({Visibility.EVALUATOR}, {source.visibility for source in hidden})
                for source in sources:  # opaque file/source names
                    text = f"{source.source_id} {source.path_or_ref}".lower()
                    for word in ("idv", "fault", "random", "valve", "sticking", "cooling"):
                        self.assertNotIn(word, text)

    def test_case_ids_are_opaque(self):
        for case_id in CASES:
            h = harness(case_id)
            with self.subTest(case_id=case_id):
                for word in ("idv", "11", "14", "fault", "random", "valve", "sticking",
                             "cooling", "temperature", FAMILY):
                    self.assertNotIn(word, f"{h.case.case_id} {h.projection.incident_id}")


# -- Agent projections ------------------------------------------------------------------
class FamilyProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harnesses = {case_id: harness(case_id) for case_id in CASES}

    def test_projections_are_structurally_equivalent(self):
        records = {case_id: h.projection.record() for case_id, h in self.harnesses.items()}
        reference = records["rca-dev-001"]
        for case_id, record in records.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(list(reference), list(record))
                varying = {key for key in record if record[key] != reference[key]}
                legitimate = (set() if case_id == "rca-dev-001"
                              else {"case_id", "incident_id", "initial_time_hours"})
                self.assertEqual(legitimate, varying)  # world/time only; same goal/tools/budget
                self.assertEqual([TRIGGER], record["trigger_signal_ids"])
                self.assertAlmostEqual(sum(CASES[case_id]["hours"]),
                                       record["initial_time_hours"], places=9)

    def test_projections_pass_leakage_audit(self):  # 7
        for case_id, h in self.harnesses.items():
            audit = h.audit({"agent_case_projection": h.projection.record()},
                            tep_sim_revision=REVISIONS.tep_sim)
            with self.subTest(case_id=case_id):
                self.assertEqual((True, ()), (audit.passed, audit.findings))
                self.assertEqual(CASES[case_id]["projection"], audit.agent_projection_checksum)

    def test_hidden_ids_and_labels_are_absent(self):  # 8
        for case_id, h in self.harnesses.items():
            with self.subTest(case_id=case_id):
                record = h.projection.record()
                assert_blind(self, record)
                assert_absent(self, forbidden(h), record)
                assert_absent(self, forbidden(h), h.run_request().record())
                self.assertNotIn("scenario_family", canonical_json(record))

    def test_no_cross_case_label_leakage(self):  # 9
        for visible_id, visible in self.harnesses.items():
            for other_id, other in self.harnesses.items():
                with self.subTest(visible=visible_id, other=other_id):
                    assert_absent(self, forbidden(other), visible.projection.record())
                    audit = other.audit({"agent_case_projection": visible.projection.record()},
                                        tep_sim_revision=REVISIONS.tep_sim)
                    self.assertTrue(audit.passed, audit.findings)

    def test_orchestration_condition_is_not_agent_visible(self):  # 10
        for case_id, h in self.harnesses.items():
            record = json.loads(fixture_bytes(h.fixture.case_path))
            for condition in sorted(benchmark.ORCHESTRATION_CONDITIONS):
                record["orchestration_policy"]["condition"] = condition
                projection = project_case(BenchmarkCase.from_record(record))
                with self.subTest(case_id=case_id, condition=condition):
                    self.assertEqual(CASES[case_id]["projection"], projection.checksum())
                    self.assertNotIn("orchestration", canonical_json(projection.record()))

    def test_scorer_scores_a_synthetic_submission_per_truth(self):  # 21
        for case_id, h in self.harnesses.items():
            metrics = score_submission(synthetic(h), h.truth, h.case.scoring).metrics
            with self.subTest(case_id=case_id):
                for name in ("top1_causal_claim_exact", "causal_mechanism_match", "entity_match",
                             "variable_or_actuator_match", "fault_family_match",
                             "direction_or_mode_match"):
                    self.assertEqual((MetricStatus.AVAILABLE, True),
                                     (metrics[name].status, metrics[name].value))
                wrong = replace(h.truth.causal_claim, direction_or_mode="STEP"
                                if h.truth.causal_claim.direction_or_mode != "STEP" else "RAMP")
                self.assertFalse(score_submission(synthetic(h, claim=wrong), h.truth,
                                                  h.case.scoring).metrics[
                    "top1_causal_claim_exact"].value)

    def test_d0_1_saved_rescore_is_byte_identical(self):  # 22
        h = self.harnesses["rca-dev-001"]
        first = score_submission(synthetic(h), h.truth, h.case.scoring).canonical_bytes()
        self.assertEqual(D0_1_SYNTHETIC_SCORE_SHA256, hashlib.sha256(first).hexdigest())
        with TemporaryDirectory() as directory:
            saved = Path(directory) / "submission.json"
            saved.write_bytes(canonical_json(synthetic(h).record()).encode("utf-8"))
            reloaded = benchmark.BenchmarkSubmission.from_record(json.loads(saved.read_bytes()))
            self.assertEqual(first, score_submission(reloaded, h.truth,
                                                     h.case.scoring).canonical_bytes())

    def test_registry_is_not_reachable_from_agent_or_application_surfaces(self):  # 20
        for module in (tool_surface, tool_bridge, application_views, application_transport,
                       desktop_backend, playground_views, playground):
            source = inspect.getsource(module)
            with self.subTest(module=module.__name__):
                for name in ("BENCHMARK_FIXTURES", "iter_development_fixtures", "load_fixture",
                             "PackagedFixture", "from .benchmark", "import benchmark",
                             "tep_agent_lab.benchmark", "importlib"):
                    self.assertNotIn(name, source)
        # Behaviorally: importing every Agent/application module in a fresh interpreter
        # never loads the benchmark module (and so never its registry).
        modules = ", ".join(module.__name__ for module in (
            tool_surface, tool_bridge, application_views, application_transport,
            desktop_backend, playground_views, playground, e0_demo))
        probe = (f"import importlib, sys\nfor name in '{modules}'.split(', '):\n"
                 "    importlib.import_module(name)\n"
                 "print('tep_agent_lab.benchmark' in sys.modules)")
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in sys.path if path)}
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                                env=env, cwd=ROOT, check=True)
        self.assertEqual("False", result.stdout.strip())


# -- EVALUATOR world checks -------------------------------------------------------------
class FamilyWorldTests(unittest.TestCase):
    """Environment/setup determinism and incident-vs-healthy viability; not C0."""

    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        root = Path(directory.name)
        cls.worlds = {}
        for case_id in NEW_CASES:
            h = harness(case_id)
            cls.worlds[case_id] = {
                name: setup_world(root / case_id / name, h, incident=name != "healthy")
                for name in ("first", "second", "healthy")}

    def test_same_seed_setup_is_deterministic(self):  # 14, 15
        for case_id, runs in self.worlds.items():
            (obs1, hist1, att1), (obs2, hist2, att2) = runs["first"], runs["second"]
            with self.subTest(case_id=case_id):
                self.assertEqual(att1, att2)
                self.assertEqual(checksum(obs1), checksum(obs2))
                self.assertEqual(checksum(obs1), att1.final_agent_observation_checksum)
                self.assertEqual(checksum(list(hist1)), checksum(list(hist2)))
                self.assertGreater(len(hist1), 0)

    def test_incident_trajectory_differs_from_same_seed_healthy(self):  # 16
        for case_id, runs in self.worlds.items():
            (obs, hist, _), (h_obs, h_hist, _) = runs["first"], runs["healthy"]
            with self.subTest(case_id=case_id):
                self.assertNotEqual(checksum(obs), checksum(h_obs))
                self.assertNotEqual(checksum(list(hist)), checksum(list(h_hist)))
                self.assertEqual(len(hist), len(h_hist))
                incident = [record["measurements"][TRIGGER] for record in hist]
                healthy = [record["measurements"][TRIGGER] for record in h_hist]
                pre = round(CASES[case_id]["hours"][0] * 60)
                self.assertEqual(incident[:pre], healthy[:pre])  # identical before onset
                self.assertNotEqual(incident[pre:], healthy[pre:])
                self.assertFalse(obs["shutdown_state"])
                self.assertAlmostEqual(sum(CASES[case_id]["hours"]),
                                       obs["simulation_time_hours"], places=9)


# -- full blind runs ----------------------------------------------------------------------
class FamilyBlindRunTests(unittest.TestCase):
    """One create -> prepare -> start per fixture through the normal P0 / Coordinator path."""

    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.manager = make_manager(Path(directory.name))
        cls.runs = {}
        for case_id in CASES:
            h = harness(case_id)
            recorder = ModelInputRecorder(scripted_blind_provider())
            run_id = f"d0-2a-{case_id}"
            prepared = h.create_and_prepare(cls.manager, run_id, recorder)
            outcome = cls.manager.start(run_id)
            surfaces = collect_agent_surfaces(cls.manager, run_id, projection=h.projection,
                                              model_inputs=recorder.turns)
            cls.runs[case_id] = dict(harness=h, run_id=run_id, prepared=prepared,
                                     outcome=outcome, surfaces=surfaces, recorder=recorder)

    def evaluator(self, case_id):
        return self.manager.queries(self.runs[case_id]["run_id"], ProjectionScope.EVALUATOR)

    def agent(self, case_id):
        return self.manager.queries(self.runs[case_id]["run_id"])

    def test_each_fixture_prepares_with_valid_attestation(self):  # 17
        for case_id, run in self.runs.items():
            h, prepared = run["harness"], run["prepared"]
            attestation = self.manager.manifest(run["run_id"]).benchmark[
                "case_setup_attestation"]
            with self.subTest(case_id=case_id):
                self.assertEqual([], h.binding_findings(prepared))
                self.assertEqual((case_id, CASES[case_id]["case"], CASES[case_id]["truth"],
                                  h.case.hidden_setup.checksum(), 3),
                                 (attestation["case_id"], attestation["case_source_checksum"],
                                  attestation["ground_truth_source_checksum"],
                                  attestation["hidden_setup_checksum"],
                                  attestation["operation_count"]))
                self.assertAlmostEqual(sum(CASES[case_id]["hours"]),
                                       attestation["final_simulation_time_hours"], places=9)

    def test_each_fake_blind_run_completes(self):  # 18
        for case_id, run in self.runs.items():
            outcome = run["outcome"]
            with self.subTest(case_id=case_id):
                self.assertEqual(RunStatus.COMPLETED, outcome.terminal_status)
                self.assertEqual(("DONE", []), (outcome.runtime_result["task_status"],
                                                list(outcome.runtime_result["errors"])))
                self.assertEqual(3, len(run["recorder"].turns))
                for turn in run["recorder"].turns:
                    self.assertEqual({"get_capability_summary", "get_history"},
                                     {spec["name"] for spec in turn["tool_specs"]})
                    self.assertIn(TRIGGER, turn["context_projection"]["content"]["task_state"][
                        "goal"])

    def test_each_leakage_audit_passes(self):  # 19
        for case_id, run in self.runs.items():
            audit = run["harness"].audit(run["surfaces"], tep_sim_revision=REVISIONS.tep_sim)
            with self.subTest(case_id=case_id):
                self.assertEqual((True, (), LEAKAGE_POLICY_VERSION),
                                 (audit.passed, audit.findings, audit.leakage_policy_version))
                for surface in run["surfaces"].values():
                    assert_blind(self, surface)
                    assert_absent(self, forbidden(run["harness"]), surface)

    def test_run_surfaces_carry_no_other_case_labels(self):  # 9
        for visible_id, run in self.runs.items():
            for other_id, other in self.runs.items():
                if other_id == visible_id:
                    continue
                with self.subTest(visible=visible_id, other=other_id):
                    audit = other["harness"].audit(run["surfaces"],
                                                   tep_sim_revision=REVISIONS.tep_sim)
                    self.assertTrue(audit.passed, audit.findings)
                    assert_absent(self, forbidden(other["harness"]), run["surfaces"])

    def test_truth_agrees_with_pinned_evaluator_binding(self):  # 11, 12
        for case_id, run in self.runs.items():
            h = run["harness"]
            hidden = h.case.hidden_setup.intervention.disturbance_id
            overlays = self.evaluator(case_id).process_graph()["evaluator_overlays"]
            self.assertEqual([EVALUATOR_BINDINGS_SOURCE_ID],
                             [overlay["source_id"] for overlay in overlays])
            matches = [binding for binding in overlays[0]["bindings"]
                       if binding["runtime_variable_id"] == hidden]
            with self.subTest(case_id=case_id):
                self.assertEqual(1, len(matches))  # fail closed on ambiguity
                binding, claim = matches[0], h.truth.causal_claim
                self.assertEqual("DISTURBS", binding["relation"])
                self.assertEqual(CASES[case_id]["semantic"], binding["semantic_entity_id"])
                self.assertEqual(binding["attached_to"], claim.entity_id)
                self.assertEqual(QUANTITY_MECHANISM[binding["quantity"]], claim.mechanism)
                self.assertEqual(CASES[case_id]["fault_family"], claim.fault_family)
                topology = self.agent(case_id).process_graph()
                self.assertIn(claim.entity_id, {edge["edge_id"] for edge in topology["edges"]}
                              | {node["node_id"] for node in topology["nodes"]})

    def test_actuator_ids_are_verified_or_null(self):  # 13
        for case_id, run in self.runs.items():
            claim = run["harness"].truth.causal_claim
            topology = self.agent(case_id).process_graph()
            self.assertEqual(PROCESS_GRAPH_REVIEW_STATUS, topology["provenance"]["review_status"])
            actuators = [binding["runtime_variable_id"]
                         for item in topology["edges"] + topology["nodes"]
                         for binding in item["bindings"]
                         if binding["attached_to"] == claim.entity_id
                         and binding["relation"] == "ACTUATES"]
            overlay = self.evaluator(case_id).process_graph()["evaluator_overlays"][0]
            hidden = run["harness"].case.hidden_setup.intervention.disturbance_id
            binding = next(item for item in overlay["bindings"]
                           if item["runtime_variable_id"] == hidden)
            names_actuator = any("actuat" in key.lower() for key in binding)
            with self.subTest(case_id=case_id):
                # Verified or null: an id is recorded only when the overlay names an
                # actuator and it is the unique HUMAN_VERIFIED actuator on the entity.
                if names_actuator:
                    self.assertIn(claim.variable_or_actuator_id, (None, *actuators))
                    self.assertLessEqual(len(actuators), 1)
                else:
                    self.assertIsNone(claim.variable_or_actuator_id)
                if case_id == "rca-dev-003":
                    # IDV(14): the verified graph has one actuator on the entity, but the
                    # pinned overlay does not bind the sticking valve to it.
                    self.assertEqual(1, len(actuators))
                    self.assertFalse(names_actuator)
                    self.assertEqual(CausalMechanism.VALVE_STICKING, claim.mechanism)

    def test_runs_stay_o3_with_no_workbatch_or_subtask(self):
        for case_id in self.runs:
            kinds = {event["payload"]["type"] for event in self.evaluator(case_id).events()[
                "events"] if event["source"] == "runtime_trace"}
            with self.subTest(case_id=case_id):
                self.assertFalse({kind for kind in kinds if "BATCH" in kind or "SUBTASK" in kind})


if __name__ == "__main__":
    unittest.main()
