"""D0 benchmark-case contract tests (docs/specs/benchmark-case-v0.md)."""

from dataclasses import replace
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from industrial_agent_runtime import (Budget, Coordinator, FakeProvider, Visibility,
                                      canonical_json, checksum)
from tep_sim import ControlMode, TEPEnvironment

from tep_agent_lab import benchmark, e0_demo
from tep_agent_lab.benchmark import (
    D0_FIXTURE, LEAKAGE_POLICY_VERSION, METRIC_NAMES, SCORER_VERSION, SETUP_POLICY_VERSION,
    SUBMISSION_SCHEMA, BenchmarkCase, BenchmarkContractError,
    BenchmarkHarness, BenchmarkSubmission, CausalClaim, CausalMechanism, EvaluatorGroundTruth,
    EvidenceRefStatus, LeakageCategory, MetricStatus, ModelInputRecorder, SubmissionResources,
    SubmissionSource, SubmittedRef, benchmark_context_sources, benchmark_materializers,
    collect_agent_surfaces, project_case, score_submission, scripted_blind_provider,
    strict_json, submission_from_run, verified_fixture,
)
from tep_agent_lab.canonical_context import (
    CanonicalContextRegistry, DirectorySourceMaterializer, PackageSourceMaterializer,
    ProjectionScope, UnknownContextSource, content_checksum,
)
from tep_agent_lab.playground import (
    CASE_SETUP_ATTESTATION_VERSION, EVALUATOR_BINDINGS_SOURCE_ID, LAB_REPOSITORY,
    PROCESS_GRAPH_SOURCE_ID, BenchmarkPartition, BenchmarkRefs,
    CaseSetupAttestation, LifecycleError, PrepareError, RunManager, RunStatus, WorldSpec,
)
from test_playground import PINS, REVISIONS
from test_tool_surface import NOW, assert_blind

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "d0-rca-dev-001"
# Frozen expectations: changing the fixture or projection semantics needs a new version.
CASE_CHECKSUM = "94cfb522e7d1d17c8bf1a5f5e43913c1c3d80297d552c014ada20ffc9d8ed47c"
TRUTH_CHECKSUM = "2feb22f1657624cbd739aeada44033758eceaa2555c540de1adaf381a91155f7"
PROJECTION_CHECKSUM = "a9565b8ba5e21b53e2c22176fa79da36d48536c6325a629656ff32168bea6d35"
HIDDEN_ID = "IDV(4)"
FAULT_FAMILY = "INLET_TEMPERATURE_STEP"


def fixture_bytes(path: str) -> bytes:
    return PackageSourceMaterializer("tep_agent_lab").read_bytes(path)


def case_record() -> dict:
    return json.loads(fixture_bytes(D0_FIXTURE.case_path))


def truth_record() -> dict:
    return json.loads(fixture_bytes(D0_FIXTURE.ground_truth_path))


def harness() -> BenchmarkHarness:
    return BenchmarkHarness.load(REVISIONS.tep_agent_lab)


def forbidden(h: BenchmarkHarness) -> tuple[str, ...]:
    """Evaluator-known strings that must never reach an Agent surface."""
    fixture = h.fixture
    return (HIDDEN_ID, FAULT_FAMILY, "TEMPERATURE_DISTURBANCE", h.case.scenario_family_id,
            "reactor_cooling.inlet_temperature_step_disturbance",
            h.case.hidden_setup.checksum(), fixture.case_source_id,
            fixture.ground_truth_source_id, fixture.case_path, fixture.ground_truth_path,
            Path(fixture.case_path).name, Path(fixture.ground_truth_path).name,
            fixture.case_checksum, fixture.ground_truth_checksum,
            EVALUATOR_BINDINGS_SOURCE_ID)


def make_manager(root: Path, materializers=None) -> RunManager:
    return RunManager(root, revisions=REVISIONS, dependency_pins=PINS,
                      materializers=materializers or benchmark_materializers(),
                      clock=lambda: NOW)


def synthetic(h: BenchmarkHarness, *, claim=None, cited=None, available=None,
              resources=None) -> BenchmarkSubmission:
    """Evaluator-side scorer fixture; never injected into a blind run."""
    available = ("observation:a", "observation:b") if available is None else available
    return BenchmarkSubmission(
        schema_version=SUBMISSION_SCHEMA, benchmark_version=h.case.benchmark_version,
        case_id=h.case.case_id, case_version=h.case.case_version,
        source=SubmissionSource.SYNTHETIC, run_id=None,
        causal_claim=h.truth.causal_claim if claim is None else claim,
        cited_evidence_refs=(SubmittedRef(ref_id="observation:a", visibility=Visibility.AGENT),)
        if cited is None else cited,
        available_observation_ref_ids=available,
        resources=resources or SubmissionResources(model_calls=3, tool_calls=2, rollout_count=0,
                                                   simulated_horizon_seconds=0),
        terminal_task_status="DONE")


# -- contract -------------------------------------------------------------------------
class ContractTests(unittest.TestCase):
    def assert_case_rejected(self, record):
        with self.assertRaises(BenchmarkContractError):
            BenchmarkCase.from_record(record)

    def test_valid_case_loads(self):  # 1
        h = harness()
        self.assertEqual(("tep-rca-benchmark/v0", "rca-dev-001", "1"), h.case.identity())
        self.assertEqual(BenchmarkPartition.DEVELOPMENT, h.case.partition)
        self.assertEqual("RCA", h.case.task_family)
        self.assertEqual("reactor-thermal-v0", h.case.scenario_family_id)
        self.assertEqual(WorldSpec(seed=11, backend="python", control_mode=ControlMode.CLOSED_LOOP,
                                   record_interval=60), h.case.world)
        setup = h.case.hidden_setup
        self.assertEqual((0.1, "DISTURBANCE", HIDDEN_ID, 1, 0.2),
                         (setup.pre_incident_hours, setup.intervention.kind,
                          setup.intervention.disturbance_id, setup.intervention.value,
                          setup.post_incident_hours))
        self.assertEqual(Budget(4, 2, 0, 0, 16, extra_dimensions={
            "simulation_snapshots": 0, "simulation_branches": 0, "simulation_rollouts": 0,
            "simulated_horizon_seconds": 0}), h.case.budget)
        self.assertEqual(SCORER_VERSION, h.case.scoring.scorer_version)

    def test_unknown_fixture_fields_are_rejected(self):  # 2
        for path in ((), ("world",), ("agent_projection",), ("hidden_setup",),
                     ("hidden_setup", "intervention"), ("budget",),
                     ("budget", "extra_dimensions"), ("scoring",), ("tool_policy",)):
            record = case_record()
            target = record
            for key in path:
                target = target[key]
            target["unexpected"] = 1
            with self.subTest(path=path):
                self.assert_case_rejected(record)
        truth = truth_record()
        truth["causal_claim"]["description"] = "free text"
        with self.assertRaises(BenchmarkContractError):
            EvaluatorGroundTruth.from_record(truth)
        with self.assertRaises(BenchmarkContractError):  # duplicate JSON keys
            strict_json(b'{"case_id": "a", "case_id": "b"}')

    def test_invalid_partition_is_rejected(self):  # 3
        for value in ("development", "TRAIN", "", None):
            with self.subTest(partition=value):
                self.assert_case_rejected({**case_record(), "partition": value})

    def test_invalid_or_nonfinite_timing_is_rejected(self):  # 4
        for name, value in (("pre_incident_hours", -0.1), ("pre_incident_hours", "0.1"),
                            ("post_incident_hours", float("inf")),
                            ("post_incident_hours", True), ("pre_incident_hours", None)):
            record = case_record()
            record["hidden_setup"][name] = value
            with self.subTest(name=name, value=value):
                self.assert_case_rejected(record)
        record = case_record()
        record["agent_projection"]["initial_time_hours"] = 0.5  # not the hidden timeline
        self.assert_case_rejected(record)
        for token in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(token=token), self.assertRaises(BenchmarkContractError):
                strict_json(b'{"pre_incident_hours": ' + token + b"}")

    def test_multi_intervention_fixture_is_rejected(self):  # 5
        record = case_record()
        single = record["hidden_setup"]["intervention"]
        record["hidden_setup"]["intervention"] = [single, {**single, "disturbance_id": "IDV(1)"}]
        self.assert_case_rejected(record)
        record = case_record()
        record["hidden_setup"]["interventions"] = [single]
        self.assert_case_rejected(record)
        for change in ({"kind": "VALVE"}, {"disturbance_id": "IDV(999)"},
                       {"disturbance_id": "XMV(10)"}):
            record = case_record()
            record["hidden_setup"]["intervention"].update(change)
            with self.subTest(change=change):
                self.assert_case_rejected(record)

    def test_case_id_does_not_encode_hidden_truth(self):  # 6
        h = harness()
        self.assertEqual([], [label for label in ("idv", "4", "fault", "cooling", "temperature",
                                                  "disturbance") if label in h.case.case_id])
        for case_id in ("idv4-blind-case", "case-idv-4", "cooling-water-fault",
                        "reactor-cooling-temperature-fault", "reactor-thermal-v0-01",
                        "Rca-Dev-001", "r"):
            with self.subTest(case_id=case_id):
                self.assert_case_rejected({**case_record(), "case_id": case_id})
        for name, value in (("goal", "Find the fault"), ("incident_id", "incident-idv4"),
                            ("goal", "Investigate disturbance IDV(4)")):
            record = case_record()
            record["agent_projection"][name] = value
            with self.subTest(name=name, value=value):
                self.assert_case_rejected(record)

    def test_ground_truth_identity_must_match_case(self):  # 7
        h = harness()
        for name, value in (("case_id", "rca-dev-002"), ("case_version", "2"),
                            ("benchmark_version", "tep-rca-benchmark/v1")):
            truth = EvaluatorGroundTruth.from_record({**truth_record(), name: value})
            with self.subTest(name=name), self.assertRaises(BenchmarkContractError):
                BenchmarkHarness(h.case, truth, lab_revision=REVISIONS.tep_agent_lab)

    def test_mechanism_vocabulary_is_frozen(self):  # 8
        self.assertEqual(
            ["FLOW_DISTURBANCE", "TEMPERATURE_DISTURBANCE", "COMPOSITION_DISTURBANCE",
             "VALVE_STICKING", "CONTROL_ACTION_OR_LOOP", "REACTION_OR_PROCESS_DYNAMICS",
             "MEASUREMENT_OR_SENSOR", "MULTIPLE_OR_INTERACTING_CAUSES",
             "OTHER_SUPPORTED_MECHANISM", "NO_ABNORMAL_CAUSE"],
            [mechanism.value for mechanism in CausalMechanism])
        self.assertEqual(["VALID_RELEVANT_REF", "VALID_BUT_IRRELEVANT_REF", "UNUSED_OBSERVATION",
                          "MISSING_REF", "HIDDEN_REF_VIOLATION", "UNSUPPORTED_NARRATIVE_CLAIM"],
                         [status.value for status in EvidenceRefStatus])
        self.assertEqual(["DEVELOPMENT", "RESEARCH", "HIDDEN_EVAL"],
                         [partition.value for partition in BenchmarkPartition])
        for mechanism in ("IDV4", "temperature_disturbance", "INLET_TEMPERATURE_STEP", None):
            record = truth_record()
            record["causal_claim"]["mechanism"] = mechanism
            with self.subTest(mechanism=mechanism), self.assertRaises(BenchmarkContractError):
                EvaluatorGroundTruth.from_record(record)
        with self.assertRaises(BenchmarkContractError):  # healthy truth carries no cause
            CausalClaim(mechanism="NO_ABNORMAL_CAUSE", entity_id="reactor")

    def test_agent_projection_is_pure_and_deterministic(self):  # 9
        self.assertEqual(["case"], list(inspect.signature(project_case).parameters))
        first, second = project_case(harness().case), project_case(harness().case)
        self.assertEqual(first, second)
        self.assertEqual(canonical_json(first.record()), canonical_json(second.record()))
        self.assertEqual({
            "schema_version": "tep-agent-lab.benchmark-agent-projection/v0",
            "benchmark_version": "tep-rca-benchmark/v0", "case_id": "rca-dev-001",
            "case_version": "1", "incident_id": "incident-rca-dev-001",
            "trigger_signal_ids": ["XMEAS(9)"], "initial_time_hours": 0.3,
            "allowed_tools": ["get_capability_summary", "get_history"],
            "projection_policy_version": "tep-agent-lab.benchmark-agent-projection-policy/v0"},
            {key: value for key, value in first.record().items()
             if key not in ("goal", "budget")})
        with self.assertRaises(TypeError):  # ground truth is not a valid input
            project_case(harness().truth)
        # hidden fields do not change the projection
        record = case_record()
        record["hidden_setup"]["intervention"]["disturbance_id"] = "IDV(1)"
        record["scenario_family_id"] = "other-family"
        self.assertEqual(first, project_case(BenchmarkCase.from_record(record)))
        h = harness()
        visible = canonical_json(first.record())
        for token in forbidden(h):
            self.assertNotIn(token, visible)
        assert_blind(self, first.record())
        with self.assertRaises(BenchmarkContractError):
            replace(first, goal="Locate the fault")

    def test_agent_projection_checksum_is_deterministic(self):  # 10
        self.assertEqual(PROJECTION_CHECKSUM, harness().projection_checksum)
        self.assertEqual(PROJECTION_CHECKSUM, project_case(harness().case).checksum())
        record = case_record()
        record["agent_projection"]["trigger_signal_ids"] = ["XMEAS(21)"]
        record["case_version"] = "2"
        self.assertNotEqual(PROJECTION_CHECKSUM,
                            project_case(BenchmarkCase.from_record(record)).checksum())

    def test_harness_builds_existing_p0_inputs(self):
        h = harness()
        request = h.run_request()
        self.assertEqual(("incident-rca-dev-001", h.case.world, h.case.budget,
                          ("get_capability_summary", "get_history")),
                         (request.investigation_id, request.world, request.budget,
                          request.allowed_tools))
        self.assertTrue(request.goal.startswith(h.projection.goal))
        self.assertIn("XMEAS(9)", request.goal)
        assert_blind(self, request.record())
        for token in forbidden(h):
            self.assertNotIn(token, canonical_json(request.record()))
        refs = h.benchmark_refs()
        self.assertEqual({
            "benchmark_version": "tep-rca-benchmark/v0", "case_id": "rca-dev-001",
            "case_version": "1", "partition": "DEVELOPMENT",
            "benchmark_case_source_id": D0_FIXTURE.case_source_id,
            "evaluator_ground_truth_source_id": D0_FIXTURE.ground_truth_source_id,
            "agent_projection_checksum": PROJECTION_CHECKSUM, "scorer_version": SCORER_VERSION,
            "setup_policy_version": SETUP_POLICY_VERSION,
            "leakage_policy_version": LEAKAGE_POLICY_VERSION}, refs.record())

    def test_benchmark_refs_are_complete_or_unbound(self):
        self.assertFalse(BenchmarkRefs().bound)
        bound = harness().benchmark_refs()
        self.assertTrue(bound.bound)
        for name in ("case_id", "partition", "agent_projection_checksum",
                     "evaluator_ground_truth_source_id", "leakage_policy_version"):
            with self.subTest(missing=name), self.assertRaises(ValueError):
                replace(bound, **{name: None})
        with self.assertRaises(ValueError):
            BenchmarkRefs(benchmark_case_source_id="only-one-ref")
        for name, value in (("partition", "TRAIN"), ("agent_projection_checksum", "abc"),
                            ("evaluator_ground_truth_source_id", D0_FIXTURE.case_source_id),
                            ("case_id", " ")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                replace(bound, **{name: value})

    def test_setup_attestation_is_strictly_typed(self):
        good = dict(schema_version=CASE_SETUP_ATTESTATION_VERSION,
                    benchmark_version="tep-rca-benchmark/v0", case_id="rca-dev-001",
                    case_version="1", setup_policy_version=SETUP_POLICY_VERSION,
                    case_source_checksum="a" * 64, ground_truth_source_checksum="b" * 64,
                    world_config_checksum="c" * 64, hidden_setup_checksum="d" * 64,
                    operation_count=3, final_simulation_time_hours=0.3,
                    final_agent_observation_checksum="e" * 64)
        attestation = CaseSetupAttestation(**good)
        self.assertEqual(attestation, CaseSetupAttestation.from_record(attestation.record()))
        for record in ({**good, "raw_hidden_setup": {}}, {k: v for k, v in good.items()
                                                          if k != "operation_count"}):
            with self.assertRaises(ValueError):
                CaseSetupAttestation.from_record(record)
        for name, value in (("schema_version", "v1"), ("operation_count", 0),
                            ("operation_count", True), ("final_simulation_time_hours", -1.0),
                            ("final_simulation_time_hours", float("nan")),
                            ("hidden_setup_checksum", "XYZ"), ("case_id", "")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                CaseSetupAttestation(**{**good, name: value})

    def test_hostile_inputs_fail_as_contract_errors(self):
        for data in (b'{"a": ' + b"1" * 5000 + b"}", b"[" * 100000 + b"]" * 100000,
                     b"\xff\xfe"):
            with self.subTest(size=len(data)), self.assertRaises(BenchmarkContractError):
                strict_json(data)
        record = case_record()
        record["hidden_setup"]["pre_incident_hours"] = 10 ** 400
        self.assert_case_rejected(record)
        record = case_record()
        record["agent_projection"]["trigger_signal_ids"] = "XMEAS(9)"  # not a list
        self.assert_case_rejected(record)
        submission = synthetic(harness()).record()
        with self.assertRaises(BenchmarkContractError):
            BenchmarkSubmission.from_record({**submission,
                                             "available_observation_ref_ids": "observation:a"})

    def test_numeric_spelling_does_not_change_checksums(self):
        integral, decimal = case_record(), case_record()
        for record, hours in ((integral, 1), (decimal, 1.0)):
            record["hidden_setup"]["pre_incident_hours"] = hours
            record["agent_projection"]["initial_time_hours"] = 1.2
        self.assertEqual(BenchmarkCase.from_record(integral).hidden_setup.checksum(),
                         BenchmarkCase.from_record(decimal).hidden_setup.checksum())
        record = case_record()
        record["hidden_setup"]["intervention"]["value"] = 1.0  # tep-sim applies integers
        self.assert_case_rejected(record)
        h = harness()
        one = synthetic(h, resources=SubmissionResources(
            model_calls=1, tool_calls=1, rollout_count=0, simulated_horizon_seconds=0))
        other = replace(one, resources=replace(one.resources, simulated_horizon_seconds=0.0))
        self.assertEqual(score_submission(one, h.truth, h.case.scoring).canonical_bytes(),
                         score_submission(other, h.truth, h.case.scoring).canonical_bytes())
        record = case_record()
        record["budget"]["extra_dimensions"]["simulation_rollouts"] = 0.0
        self.assertEqual(PROJECTION_CHECKSUM,
                         project_case(BenchmarkCase.from_record(record)).checksum())

    def test_inactive_intervention_is_rejected(self):
        record = case_record()
        record["hidden_setup"]["intervention"]["value"] = 0  # would leave the world healthy
        self.assert_case_rejected(record)

    def test_harness_attests_only_the_frozen_fixture_content(self):
        h = harness()
        other = replace(h.case.hidden_setup, intervention=replace(
            h.case.hidden_setup.intervention, disturbance_id="IDV(1)"))
        with self.assertRaises(BenchmarkContractError):
            BenchmarkHarness(replace(h.case, hidden_setup=other), h.truth,
                             lab_revision=REVISIONS.tep_agent_lab)
        claim = replace(h.truth.causal_claim, fault_family="OTHER")
        with self.assertRaises(BenchmarkContractError):
            BenchmarkHarness(h.case, replace(h.truth, causal_claim=claim),
                             lab_revision=REVISIONS.tep_agent_lab)

    def test_scripted_provider_reads_any_float_spelling_of_the_time(self):
        for text, expected in (("simulation time 0.3 h", "0.3"),
                               ("simulation time 1e-05 h", "1e-05"),
                               ("simulation time 2.5e+03 h", "2.5e+03")):
            self.assertEqual(expected, benchmark._GOAL_TIME.search(text).group(1))

    def test_d0_scope_excludes_c0_and_real_models(self):
        source = inspect.getsource(benchmark)
        for absent in ("candidate_causes", "difficulty", "EASY", "MEDIUM", "HARD",
                       "anthropic", "openai", "gemini", "aggregate_score", "weighted"):
            self.assertNotIn(absent, source)


# -- canonical context sources ----------------------------------------------------------
class SourceTests(unittest.TestCase):
    def registry(self, materializers=None):
        registry = CanonicalContextRegistry(REVISIONS.by_repository(),
                                            materializers or benchmark_materializers())
        for source in harness().context_sources(REVISIONS.tep_sim):
            registry.register(source)
        return registry

    def test_benchmark_sources_are_separate_and_evaluator_only(self):  # 11, 12
        case_source, truth_source = benchmark_context_sources(REVISIONS.tep_agent_lab)
        for source, kind, path in ((case_source, "BENCHMARK_CASE", D0_FIXTURE.case_path),
                                   (truth_source, "EVALUATOR_GROUND_TRUTH",
                                    D0_FIXTURE.ground_truth_path)):
            with self.subTest(kind=kind):
                self.assertEqual((Visibility.EVALUATOR, kind, LAB_REPOSITORY,
                                  REVISIONS.tep_agent_lab, path),
                                 (source.visibility, source.kind, source.repository,
                                  source.git_revision, source.path_or_ref))
        self.assertNotEqual(case_source.source_id, truth_source.source_id)
        for source in (case_source, truth_source):  # file/source names carry no answer
            self.assertNotIn("idv", f"{source.source_id} {source.path_or_ref}".lower())
            self.assertNotIn("fault", f"{source.source_id} {source.path_or_ref}".lower())
        self.assertNotIn("causal_claim", json.loads(fixture_bytes(D0_FIXTURE.case_path)))

    def test_scopes_resolve_exactly_by_visibility(self):  # 13, 14
        registry = self.registry()
        case_source, truth_source = benchmark_context_sources(REVISIONS.tep_agent_lab)
        self.assertEqual(HIDDEN_ID, registry.resolve(
            case_source.source_id, ProjectionScope.EVALUATOR).json()["hidden_setup"][
                "intervention"]["disturbance_id"])
        self.assertEqual(FAULT_FAMILY, registry.resolve(
            truth_source.source_id, ProjectionScope.EVALUATOR).json()["causal_claim"][
                "fault_family"])
        for source in (case_source, truth_source):
            with self.subTest(source=source.kind), self.assertRaises(UnknownContextSource):
                registry.resolve(source.source_id, ProjectionScope.AGENT)
        agent = registry.inventory(ProjectionScope.AGENT)
        self.assertEqual([PROCESS_GRAPH_SOURCE_ID], [source.source_id for source in agent])

    def test_fixture_checksums_are_exact_and_frozen(self):  # 16
        self.assertEqual((CASE_CHECKSUM, TRUTH_CHECKSUM),
                         (D0_FIXTURE.case_checksum, D0_FIXTURE.ground_truth_checksum))
        for path, expected in ((D0_FIXTURE.case_path, CASE_CHECKSUM),
                               (D0_FIXTURE.ground_truth_path, TRUTH_CHECKSUM)):
            self.assertEqual(expected, content_checksum(
                (ROOT / path).read_bytes(), "sha256:canonical-json"))

    def test_changed_fixture_bytes_fail_attestation(self):  # 17
        original = fixture_bytes(D0_FIXTURE.case_path)
        changed = original.replace(b'"value": 1', b'"value": 0.5')
        self.assertNotEqual(original, changed)
        with self.assertRaises(BenchmarkContractError):
            verified_fixture(changed, D0_FIXTURE.case_checksum)
        with self.assertRaises(BenchmarkContractError):
            BenchmarkHarness.load(REVISIONS.tep_agent_lab, read=lambda path: changed
                                  if path == D0_FIXTURE.case_path else fixture_bytes(path))
        with TemporaryDirectory() as directory:  # P0 attestation of a mutated checkout
            checkout = Path(directory) / "lab"
            for path in (D0_FIXTURE.case_path, D0_FIXTURE.ground_truth_path):
                (checkout / path).parent.mkdir(parents=True, exist_ok=True)
                (checkout / path).write_bytes(fixture_bytes(path))
            (checkout / D0_FIXTURE.ground_truth_path).write_bytes(
                fixture_bytes(D0_FIXTURE.ground_truth_path).replace(b'"STEP"', b'"RAMP"'))
            materializers = {**benchmark_materializers(),
                             LAB_REPOSITORY: DirectorySourceMaterializer(checkout)}
            manager = make_manager(Path(directory) / "runs", materializers)
            h = harness()
            with self.assertRaises(PrepareError):
                h.create_and_prepare(manager, RUN_ID, scripted_blind_provider())
            self.assertEqual(RunStatus.CREATED, manager.get(RUN_ID).status)


# -- P0 setup attestation -----------------------------------------------------------------
class SetupAttestationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.manager = make_manager(self.root)
        self.harness = harness()

    def manifests(self, run_id):
        return sorted(path for path in (self.root / run_id).rglob("manifest.json")
                      if path.parent.name not in ("lifecycle", "state"))

    def assert_rejected(self, case_setup, *, needle=None, run_id=RUN_ID):
        h = self.harness
        self.manager.create(run_id, h.run_request())
        with self.assertRaises(PrepareError) as caught:
            self.manager.prepare(run_id, provider=scripted_blind_provider(),
                                 context_sources=h.context_sources(REVISIONS.tep_sim),
                                 case_setup=case_setup, benchmark=h.benchmark_refs())
        info = self.manager.get(run_id)
        self.assertEqual((RunStatus.CREATED, None), (info.status, info.manifest_checksum))
        self.assertEqual([], self.manifests(run_id))  # no manifest published
        self.assertFalse((self.root / run_id / "prepare-0001").exists())  # session removed
        with self.assertRaises(LifecycleError):
            self.manager.manifest(run_id)
        if needle:
            self.assertIn(needle, str(caught.exception))
        agent_feed = canonical_json(self.manager.queries(run_id).events())
        self.assertNotIn("PREPARE_FAILED", agent_feed)
        for token in forbidden(h):
            self.assertNotIn(token, agent_feed)

    def tampered(self, **changes):
        setup = self.harness.case_setup()
        return lambda world: replace(setup(world), **changes)

    def test_bound_benchmark_requires_a_case_setup(self):  # 18
        self.assert_rejected(None, needle="requires a trusted case_setup")

    def test_setup_returning_none_fails_atomically(self):  # 19
        self.assert_rejected(lambda world: None, needle="typed CaseSetupAttestation")

    def test_untyped_attestation_is_rejected(self):  # 18
        setup = self.harness.case_setup()
        self.assert_rejected(lambda world: setup(world).record(),
                             needle="typed CaseSetupAttestation")

    def test_wrong_benchmark_identity_fails(self):  # 20
        self.assert_rejected(self.tampered(case_id="rca-dev-999"), needle="case_id")

    def test_wrong_case_version_fails(self):  # 20
        self.assert_rejected(self.tampered(case_version="2"), needle="case_version")

    def test_wrong_setup_policy_version_fails(self):  # 21
        self.assert_rejected(self.tampered(setup_policy_version="other-policy/v0"),
                             needle="setup_policy_version")

    def test_bad_checksums_fail(self):
        for changes in ({"case_source_checksum": "0" * 64},
                        {"ground_truth_source_checksum": "0" * 64},
                        {"world_config_checksum": "0" * 64},
                        {"final_agent_observation_checksum": "0" * 64},
                        {"final_simulation_time_hours": 0.25}):
            with self.subTest(changes=changes):
                self.assert_rejected(self.tampered(**changes), needle="setup attestation",
                                     run_id=f"bad-{next(iter(changes)).replace('_', '-')}")

    def test_setup_exception_fails_atomically(self):
        def broken(world):
            raise RuntimeError("setup failed")
        self.assert_rejected(broken, needle="setup failed")

    def test_e0_developer_demo_setup_is_unchanged(self):  # 24
        demo = e0_demo.DemoHarness()
        self.manager.create("e0-unbound", e0_demo.run_request())
        manifest = self.manager.prepare(
            "e0-unbound", provider=e0_demo.scripted_provider(demo.baseline),
            context_sources=self.harness.context_sources(REVISIONS.tep_sim)[:2],
            case_setup=demo)
        self.assertEqual(RunStatus.READY, self.manager.get("e0-unbound").status)
        self.assertEqual({"case_setup_applied": True}, dict(manifest.benchmark))
        self.assertIsNotNone(demo.baseline_handle)

    def test_unbound_run_never_records_an_attestation(self):
        h = self.harness
        self.manager.create("unbound-setup", h.run_request())
        manifest = self.manager.prepare(
            "unbound-setup", provider=scripted_blind_provider(),
            context_sources=h.context_sources(REVISIONS.tep_sim), case_setup=h.case_setup())
        self.assertEqual({"case_setup_applied": True}, dict(manifest.benchmark))
        self.assertEqual(["benchmark"], h.binding_findings(manifest))  # not a benchmark run


# -- blind runtime --------------------------------------------------------------------
class BlindRunTests(unittest.TestCase):
    """One real benchmark run: create -> prepare (attested setup) -> start (Coordinator)."""

    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name)
        cls.harness = harness()
        cls.manager = make_manager(cls.root)
        cls.recorder = ModelInputRecorder(scripted_blind_provider())
        with mock.patch.object(Coordinator, "run", autospec=True,
                               side_effect=Coordinator.run) as cls.coordinator_run, \
                mock.patch("tep_agent_lab.playground.TEPEnvironment",
                           wraps=TEPEnvironment) as cls.environments:
            cls.prepared = cls.harness.create_and_prepare(cls.manager, RUN_ID, cls.recorder)
            cls.outcome = cls.manager.start(RUN_ID)
        cls.queries = cls.manager.queries(RUN_ID)
        cls.evaluator = cls.manager.queries(RUN_ID, ProjectionScope.EVALUATOR)
        cls.surfaces = collect_agent_surfaces(cls.manager, RUN_ID,
                                              projection=cls.harness.projection,
                                              model_inputs=cls.recorder.turns)
        cls.audit = cls.harness.audit(cls.surfaces, tep_sim_revision=REVISIONS.tep_sim)

    def assert_hidden_absent(self, value):
        text = canonical_json(value)
        for token in forbidden(self.harness):
            self.assertNotIn(token.lower(), text.lower())

    def test_run_goes_through_normal_coordinator_path(self):  # 25
        events = self.evaluator.events()["events"]
        trace = [event["payload"] for event in events if event["source"] == "runtime_trace"]
        for request_id in ("d0-1-capabilities", "d0-2-history"):
            with self.subTest(request_id=request_id):
                stages = {event["output_summary"]["stage"] for event in trace
                          if event["type"] == "GATE"
                          and event["input_summary"]["request_id"] == request_id}
                self.assertLessEqual({"G0_SCHEMA", "G1_AUTHORITY", "G2_BUDGET",
                                      "G3_SIDE_EFFECT", "CONSUMER"}, stages)
                dispatched = [event for event in trace if event["type"] == "EXECUTE"
                              and event["input_summary"]["request"]["request_id"] == request_id]
                verified = [event["status"] for event in trace if event["type"] == "VERIFY_RESULT"
                            and event["input_summary"]["request_id"] == request_id]
                self.assertEqual((1, ["ACCEPTED"]), (len(dispatched), verified))
        self.assertEqual(3, len([event for event in trace if event["type"] == "MODEL_TURN"]))

    def test_run_completes_with_valid_runtime_result(self):  # 26
        self.assertEqual(RunStatus.COMPLETED, self.outcome.terminal_status)
        runtime = self.outcome.runtime_result
        self.assertEqual(("DONE", []), (runtime["task_status"], list(runtime["errors"])))
        self.assertEqual((3, 2), (runtime["budget_usage"]["model_calls"],
                                  runtime["budget_usage"]["tool_calls"]))
        self.assertEqual(self.prepared.checksum(), self.outcome.manifest_checksum)
        # the fixture tool policy is exactly what P0 registered and exposed
        self.assertEqual(["get_capability_summary", "get_history"],
                         list(self.prepared.runtime_policy["allowed_tools"]))

    def test_model_inputs_contain_no_hidden_truth(self):  # 27
        self.assertEqual(3, len(self.recorder.turns))
        for turn in self.recorder.turns:
            assert_blind(self, turn)
            self.assert_hidden_absent(turn)
            self.assertEqual({"get_capability_summary", "get_history"},
                             {spec["name"] for spec in turn["tool_specs"]})
            self.assertIn("XMEAS(9)", turn["context_projection"]["content"]["task_state"]["goal"])
        history = [event["payload"] for event in self.evaluator.events()["events"]
                   if event["source"] == "runtime_trace" and event["type"] == "EXECUTE"][1]
        self.assertEqual({"window_hours": 0.3, "variables": ["XMEAS(9)"]}, json.loads(
            canonical_json(history["input_summary"]["request"]["arguments"])))
        persisted = self.prepared.storage["runtime_trace"]
        projections = list((self.root / RUN_ID / persisted / "projections").iterdir())
        self.assertEqual(3, len(projections))
        for path in projections:  # the exact persisted model-visible projections
            assert_blind(self, json.loads(path.read_bytes()))
            self.assert_hidden_absent(json.loads(path.read_bytes()))

    def test_no_evaluator_refs_enter_rca_state(self):  # 28
        state = self.evaluator.investigation()["state"]
        refs = []

        def collect(value):
            if isinstance(value, dict):
                if "visibility" in value and "ref_id" in value:
                    refs.append(value)
                for item in value.values():
                    collect(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)
        collect(json.loads(canonical_json(state)))
        self.assertTrue(refs)
        self.assertEqual(set(), {ref["visibility"] for ref in refs} - {"AGENT", "INTERNAL"})
        self.assert_hidden_absent(state)
        self.assertEqual((), self.evaluator.investigation()["evidence_links"])

    def test_agent_views_and_manifest_carry_no_hidden_truth(self):  # 15, 23, 29
        for name, surface in self.surfaces.items():
            with self.subTest(surface=name):
                assert_blind(self, surface)
                self.assert_hidden_absent(surface)
        manifest = self.queries.manifest_view()["manifest"]
        self.assertNotIn("benchmark", manifest)
        self.assertNotIn("storage", manifest)
        self.assertNotIn("benchmark", self.prepared.agent_projection())
        inventory = self.queries.context_inventory()
        self.assertEqual([(PROCESS_GRAPH_SOURCE_ID, "AGENT")],
                         [(source["source_id"], source["visibility"])
                          for source in inventory["sources"]])
        self.assertEqual(4, len(self.evaluator.context_inventory()["sources"]))
        self.assertNotIn("evaluator_overlays", self.queries.process_graph())

    def test_internal_manifest_records_exact_benchmark_binding(self):  # 22
        record = dict(self.manager.manifest(RUN_ID).benchmark)
        attestation = record.pop("case_setup_attestation")
        self.assertEqual({**self.harness.benchmark_refs().record(), "case_setup_applied": True},
                         record)
        world = self.prepared.world
        self.assertEqual({
            "schema_version": CASE_SETUP_ATTESTATION_VERSION,
            "benchmark_version": "tep-rca-benchmark/v0", "case_id": "rca-dev-001",
            "case_version": "1", "setup_policy_version": SETUP_POLICY_VERSION,
            "case_source_checksum": CASE_CHECKSUM, "ground_truth_source_checksum": TRUTH_CHECKSUM,
            "world_config_checksum": world["environment_config_checksum"],
            "hidden_setup_checksum": self.harness.case.hidden_setup.checksum(),
            "operation_count": 3},
            {key: value for key, value in attestation.items()
             if key not in ("final_simulation_time_hours", "final_agent_observation_checksum")})
        self.assertAlmostEqual(0.3, attestation["final_simulation_time_hours"], places=9)
        self.assertRegex(attestation["final_agent_observation_checksum"], r"^[0-9a-f]{64}$")
        self.assertEqual(dict(attestation),
                         CaseSetupAttestation.from_record(attestation).record())

    def test_prepared_run_hosts_exactly_the_projected_case(self):
        self.assertEqual([], self.harness.binding_findings(self.prepared))
        task, policy = dict(self.prepared.task), dict(self.prepared.runtime_policy)
        for changes, expected in (
                ({"task": {**task, "goal": "something else"}}, ["goal"]),
                ({"task": {**task, "budget": {**task["budget"], "max_tool_calls": 9}}},
                 ["budget"]),
                ({"runtime_policy": {**policy, "allowed_tools": ["get_history"]}},
                 ["allowed_tools"]),
                ({"benchmark": {**self.prepared.benchmark,
                                "agent_projection_checksum": "0" * 64}}, ["benchmark"])):
            with self.subTest(expected=expected):
                self.assertEqual(expected, self.harness.binding_findings(
                    replace(self.prepared, **changes)))

    def test_attested_setup_actually_applied_the_hidden_intervention(self):
        # P0 cannot see the hidden setup; this evaluator test shows the attested
        # observation is the incident world, not an un-intervened one.
        from test_tool_surface import make_world
        attested = self.manager.manifest(RUN_ID).benchmark["case_setup_attestation"]
        with TemporaryDirectory() as directory:
            healthy = make_world(Path(directory) / "healthy", inject=False)
            incident = make_world(Path(directory) / "incident", inject=True)
            try:
                self.assertNotEqual(checksum(healthy.observe()),
                                    attested["final_agent_observation_checksum"])
                self.assertEqual(checksum(incident.observe()),
                                 attested["final_agent_observation_checksum"])
            finally:
                healthy.environment.close()
                incident.environment.close()

    def test_ground_truth_agrees_with_pinned_evaluator_binding(self):
        overlay = self.evaluator.process_graph()["evaluator_overlays"][0]
        binding = next(item for item in overlay["bindings"]
                       if item["runtime_variable_id"] == HIDDEN_ID)
        claim = self.harness.truth.causal_claim
        self.assertEqual((claim.entity_id, "temperature", CausalMechanism.TEMPERATURE_DISTURBANCE),
                         (binding["attached_to"], binding["quantity"], claim.mechanism))
        self.assertEqual("reactor_cooling.inlet_temperature_step_disturbance",
                         binding["semantic_entity_id"])
        topology = self.queries.process_graph()
        self.assertIn(claim.entity_id, {item["node_id"] for item in topology["nodes"]}
                      | {item["edge_id"] for item in topology["edges"]})

    def test_no_second_scheduler_world_or_runtime_path(self):  # 30
        self.assertEqual(1, self.coordinator_run.call_count)
        self.assertEqual(1, self.environments.call_count)
        for name in ("TEPEnvironment", "EnvironmentConfig", "Coordinator", "RcaStateStore",
                     "RcaState", "TraceRecorder", "TaskStateStore", "Executor"):
            self.assertFalse(hasattr(benchmark, name), name)  # owns no execution path
        source = inspect.getsource(benchmark)
        for absent in ("_runs", "RunManifest(", "_Run", ".session", "_assemble"):
            self.assertNotIn(absent, source)  # no private P0 state, no second manifest
        self.assertEqual(["manifest.json"], sorted(
            path.name for path in (self.root / RUN_ID).rglob("manifest.json")
            if path.parent.name not in ("lifecycle", "state")))

    def test_real_d0_case_passes_leakage_audit(self):  # 38
        self.assertTrue(self.audit.passed, self.audit.findings)
        self.assertEqual((), self.audit.findings)
        self.assertEqual((PROJECTION_CHECKSUM, LEAKAGE_POLICY_VERSION),
                         (self.audit.agent_projection_checksum,
                          self.audit.leakage_policy_version))
        self.assertLessEqual({"agent_case_projection", "agent_manifest",
                              "agent_context_inventory", "agent_process_graph", "agent_events",
                              "model_inputs", "model_context_projections"}, set(self.surfaces))
        # the persisted per-turn projections are audited, not only the live capture
        self.assertEqual([turn["context_projection"]["checksum"]
                          for turn in self.recorder.turns],
                         [projection["checksum"]
                          for projection in self.surfaces["model_context_projections"]])

    def test_saved_run_is_scored_without_inventing_a_claim(self):
        submission = submission_from_run(self.manager, RUN_ID,
                                         projection=self.harness.projection)
        score = score_submission(submission, self.harness.truth, self.harness.case.scoring)
        metrics = score.record()["metrics"]
        for name in ("top1_causal_claim_exact", "causal_mechanism_match", "entity_match"):
            self.assertEqual("NOT_AVAILABLE", metrics[name]["status"])
        self.assertEqual({"model_calls": 3, "tool_calls": 2, "rollout_count": 0,
                          "simulated_horizon_seconds": 0, "terminal_task_status": "DONE"},
                         {name: metrics[name]["value"] for name in (
                             "model_calls", "tool_calls", "rollout_count",
                             "simulated_horizon_seconds", "terminal_task_status")})
        self.assertEqual(2, metrics["evidence_ref_status_counts"]["value"]["counts"][
            "UNUSED_OBSERVATION"])


# -- scorer ---------------------------------------------------------------------------
class ScorerTests(unittest.TestCase):
    def setUp(self):
        self.harness = harness()
        self.config = self.harness.case.scoring

    def score(self, submission):
        return score_submission(submission, self.harness.truth, self.config)

    def metrics(self, submission):
        return {name: (metric.status, metric.value)
                for name, metric in self.score(submission).metrics.items()}

    def test_exact_claim_match(self):  # 31
        metrics = self.metrics(synthetic(self.harness))
        for name in ("top1_causal_claim_exact", "causal_mechanism_match", "entity_match",
                     "variable_or_actuator_match", "fault_family_match",
                     "direction_or_mode_match"):
            self.assertEqual((MetricStatus.AVAILABLE, True), metrics[name])
        self.assertEqual((MetricStatus.NOT_APPLICABLE, None),
                         metrics["healthy_no_abnormal_correct"])
        self.assertEqual((MetricStatus.AVAILABLE, 0), metrics["hidden_ref_violation_count"])

    def test_one_field_mismatch_changes_only_its_metrics(self):  # 32
        perfect = self.metrics(synthetic(self.harness))
        wrong = replace(self.harness.truth.causal_claim, entity_id="reactor")
        mismatch = self.metrics(synthetic(self.harness, claim=wrong))
        changed = {name for name in METRIC_NAMES if perfect[name] != mismatch[name]}
        self.assertEqual({"entity_match", "top1_causal_claim_exact"}, changed)
        self.assertEqual((MetricStatus.AVAILABLE, False), mismatch["entity_match"])
        mechanism = replace(self.harness.truth.causal_claim,
                            mechanism=CausalMechanism.FLOW_DISTURBANCE)
        self.assertEqual({"causal_mechanism_match", "top1_causal_claim_exact"},
                         {name for name, value in self.metrics(synthetic(
                             self.harness, claim=mechanism)).items() if value != perfect[name]})

    def test_no_aggregate_score_exists(self):  # 33
        record = self.score(synthetic(self.harness)).record()
        self.assertEqual(list(METRIC_NAMES), list(record["metrics"]))
        self.assertEqual({"schema_version", "scorer_version", "benchmark_version", "case_id",
                          "case_version", "submission_checksum", "ground_truth_checksum",
                          "scoring_config_checksum", "metrics"}, set(record))
        text = canonical_json(record).lower()
        for absent in ("aggregate", "weighted", "total_score", "overall", "difficulty"):
            self.assertNotIn(absent, text)

    def test_score_output_names_scorer_version(self):  # 34
        record = self.score(synthetic(self.harness)).record()
        self.assertEqual(SCORER_VERSION, record["scorer_version"])
        with self.assertRaises(BenchmarkContractError):
            score_submission(synthetic(self.harness), self.harness.truth,
                             replace(self.config, scorer_version="other-scorer/v9"))

    def test_saved_input_rescore_is_byte_identical(self):  # 35
        submission = synthetic(self.harness, claim=replace(
            self.harness.truth.causal_claim, direction_or_mode="RAMP"))
        first = self.score(submission).canonical_bytes()
        with TemporaryDirectory() as directory:
            saved_input = Path(directory) / "submission.json"
            saved_output = Path(directory) / "score.json"
            saved_input.write_bytes(canonical_json(submission.record()).encode("utf-8"))
            saved_output.write_bytes(first)
            reloaded = BenchmarkSubmission.from_record(json.loads(saved_input.read_bytes()))
            self.assertEqual(submission, reloaded)
            second = self.score(reloaded).canonical_bytes()
            self.assertEqual(saved_output.read_bytes(), second)

    def test_scorer_never_invokes_a_model_or_simulator(self):  # 36, 37
        self.assertEqual(["submission", "truth", "config"],
                         list(inspect.signature(score_submission).parameters))
        expected = self.score(synthetic(self.harness)).canonical_bytes()
        refuse = mock.Mock(side_effect=AssertionError("must not be called"))
        with mock.patch.object(FakeProvider, "generate", refuse), \
                mock.patch.object(ModelInputRecorder, "generate", refuse), \
                mock.patch.object(TEPEnvironment, "__init__", refuse), \
                mock.patch.object(TEPEnvironment, "observe", refuse):
            self.assertEqual(expected, self.score(synthetic(self.harness)).canonical_bytes())
        refuse.assert_not_called()

    def test_evidence_statuses_are_structural_only(self):
        cited = (SubmittedRef(ref_id="observation:a", visibility=Visibility.AGENT),
                 SubmittedRef(ref_id="observation:gone", visibility=Visibility.AGENT),
                 SubmittedRef(ref_id=D0_FIXTURE.ground_truth_source_id,
                              visibility=Visibility.EVALUATOR))
        value = self.metrics(synthetic(self.harness, cited=cited))[
            "evidence_ref_status_counts"][1]
        self.assertEqual({"VALID_RELEVANT_REF": None, "VALID_BUT_IRRELEVANT_REF": None,
                          "UNUSED_OBSERVATION": 1, "MISSING_REF": 1, "HIDDEN_REF_VIOLATION": 1,
                          "UNSUPPORTED_NARRATIVE_CLAIM": 0}, dict(value["counts"]))
        self.assertEqual(1, value["valid_refs_relevance_unclassified"])
        unsupported = self.metrics(synthetic(self.harness, cited=()))
        self.assertEqual((MetricStatus.AVAILABLE, 1),
                         unsupported["unsupported_narrative_claim_count"])
        resources = SubmissionResources(model_calls=None, tool_calls=1, rollout_count=None,
                                        simulated_horizon_seconds=None)
        partial = self.metrics(synthetic(self.harness, resources=resources))
        self.assertEqual((MetricStatus.NOT_AVAILABLE, None), partial["model_calls"])
        self.assertEqual((MetricStatus.AVAILABLE, 1), partial["tool_calls"])

    def test_configuration_controls_applicability(self):
        narrow = replace(self.config, causal_claim_match_fields=("mechanism",),
                         healthy_outcome_enabled=True)
        claim = replace(self.harness.truth.causal_claim, fault_family="OTHER")
        metrics = score_submission(synthetic(self.harness, claim=claim), self.harness.truth,
                                   narrow).metrics
        self.assertEqual(MetricStatus.NOT_APPLICABLE, metrics["fault_family_match"].status)
        self.assertEqual((MetricStatus.AVAILABLE, True),
                         (metrics["top1_causal_claim_exact"].status,
                          metrics["top1_causal_claim_exact"].value))
        self.assertEqual(True, metrics["healthy_no_abnormal_correct"].value)
        healthy = synthetic(self.harness, claim=CausalClaim(mechanism="NO_ABNORMAL_CAUSE"))
        self.assertEqual(False, score_submission(healthy, self.harness.truth, narrow).metrics[
            "healthy_no_abnormal_correct"].value)

    def test_submission_is_strictly_typed(self):
        record = synthetic(self.harness).record()
        for change in ({"unexpected": 1}, {"source": "LLM_JUDGE"},
                       {"case_id": "rca-dev-002"}):
            with self.subTest(change=change), self.assertRaises(BenchmarkContractError):
                submission = BenchmarkSubmission.from_record({**record, **change})
                self.score(submission)
        with self.assertRaises(ValueError):
            BenchmarkSubmission.from_record({**record, "resources": {
                **record["resources"], "tool_calls": -1}})


# -- leakage audit --------------------------------------------------------------------
class LeakageAuditTests(unittest.TestCase):
    def setUp(self):
        self.harness = harness()
        self.clean = {"agent_case_projection": self.harness.projection.record()}

    def audit(self, surfaces):
        return self.harness.audit(surfaces, tep_sim_revision=REVISIONS.tep_sim)

    def categories(self, surfaces):
        return {(finding.surface, finding.category) for finding in self.audit(surfaces).findings}

    def test_clean_projection_passes(self):
        audit = self.audit(self.clean)
        self.assertEqual((True, ()), (audit.passed, audit.findings))

    def test_contaminated_agent_projection_fails(self):  # 39
        record = {**self.harness.projection.record(),
                  "goal": f"Check {FAULT_FAMILY.lower()} on the cooling inlet"}
        audit = self.audit({"agent_case_projection": record})
        self.assertFalse(audit.passed)
        self.assertEqual({("agent_case_projection", LeakageCategory.HIDDEN_TRUTH_LABEL)},
                         self.categories({"agent_case_projection": record}))
        self.assertEqual("$.goal", audit.findings[0].location)
        family = {**self.harness.projection.record(), "incident_id": "reactor-thermal-v0"}
        self.assertFalse(self.audit({"agent_case_projection": family}).passed)

    def test_hidden_source_id_and_path_contamination_fails(self):  # 40
        fixture = self.harness.fixture
        for value in (fixture.ground_truth_source_id, fixture.case_path,
                      "rca-dev-001.ground-truth.json", fixture.case_checksum,
                      EVALUATOR_BINDINGS_SOURCE_ID, self.harness.case.hidden_setup.checksum()):
            surfaces = {"agent_context_inventory": {"sources": [{"source_id": value}]}}
            with self.subTest(value=value):
                audit = self.audit(surfaces)
                self.assertFalse(audit.passed)
                self.assertTrue({LeakageCategory.HIDDEN_SOURCE_REF,
                                 LeakageCategory.HIDDEN_SETUP_CHECKSUM}
                                & {finding.category for finding in audit.findings})
        structural = {"agent_context_inventory": {"sources": [
            {"source_id": "innocent", "visibility": "EVALUATOR", "kind": "PROCESS_GRAPH"},
            {"source_id": "innocent-2", "visibility": "AGENT", "kind": "BENCHMARK_CASE"}]}}
        structure = [finding.location for finding in self.audit(structural).findings
                     if finding.category == LeakageCategory.EVALUATOR_STRUCTURE]
        self.assertEqual(["$.sources[0]", "$.sources[1]"], structure)

    def test_disturbance_id_contamination_fails(self):  # 41
        for surface in ({"text": f"inject {HIDDEN_ID}"}, {"text": "idv_4 suspected"},
                        {HIDDEN_ID: "as a key"}, [{"nested": ["IDV4"]}]):
            with self.subTest(surface=surface):
                audit = self.audit({"model_inputs": surface})
                self.assertFalse(audit.passed)
                self.assertIn(LeakageCategory.HIDDEN_DISTURBANCE_ID,
                              {finding.category for finding in audit.findings})

    def test_findings_never_copy_the_hidden_payload(self):  # 42
        payload = {"text": f"{HIDDEN_ID} {FAULT_FAMILY} {self.harness.fixture.case_path}",
                   f"{HIDDEN_ID}-key": "x"}
        audit = self.audit({"agent_events": payload})
        self.assertFalse(audit.passed)
        text = canonical_json(audit.record())
        for token in forbidden(self.harness):
            self.assertNotIn(token, text)
        self.assertIn("$.<key 0>", {finding.location for finding in audit.findings})
        fixture = self.harness.fixture
        for key in (self.harness.case.hidden_setup.checksum(), fixture.ground_truth_source_id,
                    FAULT_FAMILY.lower()):
            with self.subTest(key=key):
                keyed = self.audit({"agent_events": {key: {"nested": "x"}}})
                self.assertFalse(keyed.passed)
                self.assertNotIn(key, canonical_json(keyed.record()))
        for finding in audit.findings:  # location + category only
            self.assertEqual({"surface", "location", "category"},
                             set(json.loads(canonical_json(finding))))
        with self.assertRaises(BenchmarkContractError):  # passed is derived, not asserted
            replace(audit, passed=True)


if __name__ == "__main__":
    unittest.main()
