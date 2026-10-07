"""D0.2B healthy negative contract + fixture tests (docs/d0-2b-healthy-negative.md).

Benchmark infrastructure only: setup/v1, the healthy ``rca-dev-004`` fixture and the
frozen scorer's healthy semantics. No C0, no classifier, no identifiability/difficulty.
"""

from dataclasses import replace
import hashlib
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from industrial_agent_runtime import Visibility, canonical_json, checksum
from tep_sim import UPSTREAM_REVISION, EnvironmentConfig, TEPEnvironment

from tep_agent_lab import (application_transport, application_views, benchmark, desktop_backend,
                           playground, playground_views, tool_bridge, tool_surface)
from tep_agent_lab.benchmark import (
    BENCHMARK_FIXTURES, D0_FIXTURE, HIDDEN_SETUP_SCHEMA, HIDDEN_SETUP_SCHEMA_V1,
    LEAKAGE_POLICY_VERSION, LEAKAGE_POLICY_VERSION_V1, SETUP_POLICY_VERSION,
    SETUP_POLICY_VERSION_V1, BenchmarkCase, BenchmarkCaseSetup, BenchmarkContractError,
    BenchmarkHarness, CausalClaim, CausalMechanism, DisturbanceSetupV1, EvaluatorGroundTruth,
    HiddenSetup, MetricStatus, ModelInputRecorder, NoInterventionSetup,
    benchmark_context_sources, collect_agent_surfaces, leakage_policy_version, project_case,
    score_submission, scripted_blind_provider, submission_from_run,
)
from tep_agent_lab.canonical_context import ProjectionScope, content_checksum
from tep_agent_lab.playground import (BenchmarkPartition, LifecycleError, PrepareError,
                                      RunStatus)
from tep_agent_lab.tep_world import ReferenceWorld
from test_benchmark import make_manager, synthetic
from test_benchmark_family import CASES, assert_absent, fixture_bytes, harness
from test_playground import REVISIONS
from test_tool_surface import assert_blind

ROOT = Path(__file__).resolve().parents[1]
HEALTHY = "rca-dev-004"
TRIGGER = "XMEAS(9)"
# Frozen EVALUATOR expectations for the healthy fixture.
HEALTHY_CASE_CHECKSUM = "2b30594c0e7f286b7dbb07b1dc25638c80b59d7af202e9c1f8ceba6d513427af"
HEALTHY_TRUTH_CHECKSUM = "f8c146ee10fea247a9e924ab51a06aacf3943632527de4d044b9c328122813cd"
HEALTHY_PROJECTION_CHECKSUM = "8e7bc3f5228a2f395f4a0be847ca0485e590488ae12ccadd5aa42dfa02e931a3"
HEALTHY_SEED = 14
HEALTHY_HOURS = (0.1, 0.5)
HEALTHY_GOAL = ("Investigate the observed reactor thermal behavior and identify the most "
                "plausible supported causal mechanism using available evidence. Do not assume "
                "that an abnormal cause exists.")
# sha256 of the exact committed (LF) bytes of the historical setup/v0 fixtures, frozen
# before D0.2B. A CRLF checkout is normalized; any other byte change fails.
HISTORICAL_FILE_SHA256 = {
    "rca-dev-001.case.json": "bddcc405cb1fb66fd9f12420a4c9ff14ec69cd534f0c1a92188eec5f58836b15",
    "rca-dev-001.ground-truth.json":
        "26f61343120559748c457050959eee454d31752290faacc0a8c4b2d03d53dcc2",
    "rca-dev-002.case.json": "1dab307257e946c6e510d7f872f052904b7d60bbd1911bccd160937515fb5aa1",
    "rca-dev-002.ground-truth.json":
        "c5be2801906e9c6efad7d4bad81ce156f3883b67142e2aec20031dc267a086e5",
    "rca-dev-003.case.json": "8e21b76b406dfd22e3b07768209eee7c412bc3b5fcb9d8cb3d42cd7892e46090",
    "rca-dev-003.ground-truth.json":
        "0229e80f4ad0e606112823d5cd6f46cc90188e2f6a330430a0acedc0d7f29c19",
}
# Answer-revealing words that must never reach an Agent surface of the healthy case.
HEALTHY_ANSWER_WORDS = ("healthy", "no_fault", "no-fault", "no_abnormal", "no-abnormal",
                        "NO_ABNORMAL_CAUSE", "NO_INTERVENTION", "no_intervention",
                        HIDDEN_SETUP_SCHEMA_V1, SETUP_POLICY_VERSION_V1)


def case_record(case_id: str = HEALTHY) -> dict:
    return json.loads(fixture_bytes(BENCHMARK_FIXTURES[(case_id, "1")].case_path))


def truth_record(case_id: str = HEALTHY) -> dict:
    return json.loads(fixture_bytes(BENCHMARK_FIXTURES[(case_id, "1")].ground_truth_path))


def forbidden(h: BenchmarkHarness) -> tuple[str, ...]:
    """Evaluator-known strings of the healthy case that must not reach an Agent surface."""
    fixture = h.fixture
    return (*HEALTHY_ANSWER_WORDS, h.case.scenario_family_id, h.case.hidden_setup.checksum(),
            fixture.case_source_id, fixture.ground_truth_source_id, fixture.case_path,
            fixture.ground_truth_path, Path(fixture.case_path).name,
            Path(fixture.ground_truth_path).name, fixture.case_checksum,
            fixture.ground_truth_checksum)


def with_setup(record: dict, setup) -> dict:
    return {**record, "hidden_setup": setup}


def v1_disturbance(**changes) -> dict:
    """rca-dev-001's v0 timeline re-expressed as a setup/v1 DISTURBANCE record."""
    setup = case_record("rca-dev-001")["hidden_setup"]
    return {"schema_version": HIDDEN_SETUP_SCHEMA_V1, "kind": "DISTURBANCE",
            "pre_incident_hours": setup["pre_incident_hours"],
            "intervention": setup["intervention"],
            "post_incident_hours": setup["post_incident_hours"], **changes}


def no_intervention(**changes) -> dict:
    return {**case_record()["hidden_setup"], **changes}


def make_world(directory: Path, case: BenchmarkCase) -> tuple[TEPEnvironment, ReferenceWorld]:
    """EVALUATOR-only world on the case's own WorldSpec, as P0 builds it."""
    spec = case.world
    environment = TEPEnvironment(EnvironmentConfig(
        seed=spec.seed, backend=spec.backend, control_mode=spec.control_mode,
        record_interval=spec.record_interval, upstream_revision=UPSTREAM_REVISION,
        artifact_directory=directory / "world"))
    environment.reset()
    return environment, ReferenceWorld(environment)


# -- setup schemas --------------------------------------------------------------------
class SetupSchemaTests(unittest.TestCase):
    def assert_case_rejected(self, record):
        with self.assertRaises(BenchmarkContractError):
            BenchmarkCase.from_record(record)

    def test_historical_v0_fixture_bytes_are_unchanged(self):  # 1
        for case_id in CASES:
            fixture = BENCHMARK_FIXTURES[(case_id, "1")]
            for path in (fixture.case_path, fixture.ground_truth_path):
                data = (ROOT / path).read_bytes().replace(b"\r\n", b"\n")
                with self.subTest(path=path):
                    self.assertEqual(HISTORICAL_FILE_SHA256[Path(path).name],
                                     hashlib.sha256(data).hexdigest())
            h = harness(case_id)
            with self.subTest(case_id=case_id):
                self.assertEqual((CASES[case_id]["case"], CASES[case_id]["truth"],
                                  CASES[case_id]["projection"]),
                                 (h.fixture.case_checksum, h.fixture.ground_truth_checksum,
                                  h.projection_checksum))

    def test_v0_still_loads_without_rewriting(self):  # 2
        for case_id in CASES:
            h = harness(case_id)
            raw = case_record(case_id)["hidden_setup"]
            with self.subTest(case_id=case_id):
                self.assertIs(HiddenSetup, type(h.case.hidden_setup))
                self.assertEqual(HIDDEN_SETUP_SCHEMA, h.case.hidden_setup.schema_version)
                self.assertEqual(raw, h.case.hidden_setup.record())  # no v1 rewrite
                self.assertEqual(SETUP_POLICY_VERSION, h.benchmark_refs().setup_policy_version)
        record = case_record("rca-dev-001")
        record["hidden_setup"]["kind"] = "DISTURBANCE"  # v0 has no setup tag
        self.assert_case_rejected(record)

    def test_v1_rejects_unknown_tags_and_fields(self):  # 3
        for setup in (no_intervention(kind="HEALTHY"), no_intervention(kind="NO_FAULT"),
                      no_intervention(kind=None), no_intervention(kind="no_intervention"),
                      {key: value for key, value in no_intervention().items() if key != "kind"},
                      no_intervention(schema_version="tep-agent-lab.benchmark-setup/v2"),
                      no_intervention(unexpected=1), no_intervention(note="nominal"),
                      {key: value for key, value in no_intervention().items()
                       if key != "observation_hours"}, "NO_INTERVENTION", None, []):
            with self.subTest(setup=setup):
                self.assert_case_rejected(with_setup(case_record(), setup))
        # DISTURBANCE variants on rca-dev-001, whose timeline they match.
        BenchmarkCase.from_record(with_setup(case_record("rca-dev-001"), v1_disturbance()))
        for setup in (v1_disturbance(unexpected=1), v1_disturbance(kind="TEMPERATURE"),
                      v1_disturbance(kind="NO_INTERVENTION")):
            with self.subTest(setup=setup):
                self.assert_case_rejected(with_setup(case_record("rca-dev-001"), setup))
        record = with_setup(case_record("rca-dev-001"), v1_disturbance())
        record["hidden_setup"]["intervention"] = {**record["hidden_setup"]["intervention"],
                                                  "unexpected": 1}
        self.assert_case_rejected(record)

    def test_v1_disturbance_validates_and_executes_like_v0(self):  # 4
        v0 = harness("rca-dev-001")
        case = BenchmarkCase.from_record(with_setup(case_record("rca-dev-001"),
                                                    v1_disturbance()))
        setup = case.hidden_setup
        self.assertIs(DisturbanceSetupV1, type(setup))
        self.assertEqual(("DISTURBANCE", "IDV(4)", 1, 0.3),
                         (setup.kind, setup.intervention.disturbance_id, setup.intervention.value,
                          round(setup.timeline_hours, 9)))
        self.assertNotEqual(v0.case.hidden_setup.checksum(), setup.checksum())
        self.assertEqual(v0.projection_checksum, project_case(case).checksum())
        # Same timeline and intervention as rca-dev-001 v0: the trusted setup executes
        # identically under the v1 policy.
        stub = SimpleNamespace(case=case, fixture=D0_FIXTURE)
        with TemporaryDirectory() as directory:
            environment, world = make_world(Path(directory) / "v1", case)
            try:
                v1_attestation = BenchmarkCaseSetup(stub)(world)
            finally:
                environment.close()
            environment, world = make_world(Path(directory) / "v0", v0.case)
            try:
                v0_attestation = v0.case_setup()(world)
            finally:
                environment.close()
        self.assertEqual((SETUP_POLICY_VERSION_V1, 3), (v1_attestation.setup_policy_version,
                                                        v1_attestation.operation_count))
        self.assertEqual(v0_attestation.final_agent_observation_checksum,
                         v1_attestation.final_agent_observation_checksum)
        self.assertEqual(setup.checksum(), v1_attestation.hidden_setup_checksum)
        with self.assertRaisesRegex(BenchmarkContractError, "NO_ABNORMAL_CAUSE truth"):
            BenchmarkHarness(case, replace(v0.truth, causal_claim=CausalClaim(
                mechanism=CausalMechanism.NO_ABNORMAL_CAUSE)),
                lab_revision=REVISIONS.tep_agent_lab)

    def test_v1_no_intervention_validates(self):  # 5
        setup = harness(HEALTHY).case.hidden_setup
        self.assertIs(NoInterventionSetup, type(setup))
        self.assertEqual((HIDDEN_SETUP_SCHEMA_V1, "NO_INTERVENTION", *HEALTHY_HOURS),
                         (setup.schema_version, setup.kind, setup.pre_observation_hours,
                          setup.observation_hours))
        self.assertEqual({"schema_version", "kind", "pre_observation_hours",
                          "observation_hours"}, set(setup.record()))
        self.assertEqual(case_record()["hidden_setup"], setup.record())
        for changes in ({"observation_hours": 0}, {"observation_hours": -0.5},
                        {"pre_observation_hours": -0.1}, {"observation_hours": "0.5"},
                        {"pre_observation_hours": True}, {"observation_hours": 10 ** 400}):
            with self.subTest(changes=changes):
                self.assert_case_rejected(with_setup(case_record(), no_intervention(**changes)))
        record = case_record()
        record["agent_projection"]["initial_time_hours"] = 0.5  # not the hidden timeline
        self.assert_case_rejected(record)

    def test_no_disabled_or_fake_disturbance_is_accepted_as_healthy(self):  # 6
        incident = case_record("rca-dev-001")["hidden_setup"]
        intervention = incident["intervention"]
        for setup in ({**incident, "intervention": {**intervention, "value": 0}},
                      {**incident, "intervention": None}, {**incident, "intervention": {}},
                      {**incident, "intervention": {**intervention, "disturbance_id": None}},
                      {**incident, "intervention": {"kind": "NONE", "disturbance_id": "IDV(4)",
                                                    "value": 1}},
                      v1_disturbance(intervention={**intervention, "value": 0}),
                      v1_disturbance(intervention=None), v1_disturbance(intervention={}),
                      v1_disturbance(intervention={**intervention, "disturbance_id": None}),
                      v1_disturbance(intervention={**intervention,
                                                   "disturbance_id": "HEALTHY"}),
                      no_intervention(intervention=intervention),
                      no_intervention(intervention=None), no_intervention(intervention={}),
                      no_intervention(disturbance_id=None)):
            with self.subTest(setup=setup):
                record = with_setup(case_record(), setup)
                record["agent_projection"]["initial_time_hours"] = 0.3 if (
                    "post_incident_hours" in setup) else 0.6
                self.assert_case_rejected(record)
        healthy, d0 = harness(HEALTHY), harness("rca-dev-001")
        # Healthy truth only with NO_INTERVENTION, and NO_INTERVENTION only with healthy truth.
        with self.assertRaisesRegex(BenchmarkContractError, "NO_ABNORMAL_CAUSE truth"):
            BenchmarkHarness(d0.case, replace(d0.truth, causal_claim=healthy.truth.causal_claim),
                             lab_revision=REVISIONS.tep_agent_lab)
        with self.assertRaisesRegex(BenchmarkContractError, "NO_ABNORMAL_CAUSE truth"):
            BenchmarkHarness(healthy.case, replace(healthy.truth,
                                                   causal_claim=d0.truth.causal_claim),
                             lab_revision=REVISIONS.tep_agent_lab, fixture=healthy.fixture)
        disabled = replace(healthy.case, scoring=replace(healthy.case.scoring,
                                                         healthy_outcome_enabled=False))
        with self.assertRaisesRegex(BenchmarkContractError, "healthy_outcome_enabled"):
            BenchmarkHarness(disabled, healthy.truth, lab_revision=REVISIONS.tep_agent_lab,
                             fixture=healthy.fixture)
        enabled = replace(d0.case, scoring=replace(d0.case.scoring, healthy_outcome_enabled=True))
        with self.assertRaisesRegex(BenchmarkContractError, "healthy_outcome_enabled"):
            BenchmarkHarness(enabled, d0.truth, lab_revision=REVISIONS.tep_agent_lab)


# -- healthy fixture ------------------------------------------------------------------
class HealthyFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = harness(HEALTHY)

    def test_identity_and_metadata(self):  # 7
        h = self.harness
        identity = ("tep-rca-benchmark/v0", HEALTHY, "1")
        self.assertEqual(identity, h.case.identity())
        self.assertEqual(identity, h.truth.identity())
        self.assertEqual(identity, (h.fixture.benchmark_version, h.fixture.case_id,
                                    h.fixture.case_version))
        self.assertIs(h.fixture, BENCHMARK_FIXTURES[(HEALTHY, "1")])
        self.assertEqual((BenchmarkPartition.DEVELOPMENT, "RCA", "reactor-thermal-v0", "O3"),
                         (h.case.partition, h.case.task_family, h.case.scenario_family_id,
                          h.case.orchestration_policy.condition))
        world = h.case.world
        self.assertEqual((HEALTHY_SEED, "python", "closed_loop", 60),
                         (world.seed, world.backend, world.control_mode.value,
                          world.record_interval))
        self.assertNotIn(HEALTHY_SEED, [CASES[case_id]["seed"] for case_id in CASES])
        reference = harness("rca-dev-001").case
        self.assertEqual((reference.tool_policy, reference.budget, reference.subagent_policy),
                         (h.case.tool_policy, h.case.budget, h.case.subagent_policy))
        self.assertTrue(h.case.scoring.healthy_outcome_enabled)
        self.assertEqual(reference.scoring.causal_claim_match_fields,
                         h.case.scoring.causal_claim_match_fields)

    def test_checksums_are_exact_and_frozen(self):  # 8
        fixture = self.harness.fixture
        self.assertEqual((HEALTHY_CASE_CHECKSUM, HEALTHY_TRUTH_CHECKSUM),
                         (fixture.case_checksum, fixture.ground_truth_checksum))
        for path, frozen in ((fixture.case_path, HEALTHY_CASE_CHECKSUM),
                             (fixture.ground_truth_path, HEALTHY_TRUTH_CHECKSUM)):
            self.assertEqual(frozen, content_checksum((ROOT / path).read_bytes(),
                                                      "sha256:canonical-json"))
        self.assertEqual(HEALTHY_PROJECTION_CHECKSUM, self.harness.projection_checksum)
        original = fixture_bytes(fixture.case_path)
        changed = original.replace(b'"seed": 14,', b'"seed": 99,')
        self.assertNotEqual(original, changed)
        with self.assertRaises(BenchmarkContractError):
            BenchmarkHarness.load(REVISIONS.tep_agent_lab, fixture, read=lambda path: changed
                                  if path == fixture.case_path else fixture_bytes(path))

    def test_ground_truth_is_exactly_no_abnormal_cause(self):  # 9
        self.assertEqual({"entity_id": None, "mechanism": "NO_ABNORMAL_CAUSE",
                          "variable_or_actuator_id": None, "fault_family": None,
                          "direction_or_mode": None}, truth_record()["causal_claim"])
        self.assertEqual(CausalClaim(mechanism=CausalMechanism.NO_ABNORMAL_CAUSE),
                         self.harness.truth.causal_claim)
        for name in ("entity_id", "variable_or_actuator_id", "fault_family",
                     "direction_or_mode"):
            record = truth_record()
            record["causal_claim"][name] = "HEALTHY_REACTOR"
            with self.subTest(name=name), self.assertRaises(BenchmarkContractError):
                EvaluatorGroundTruth.from_record(record)

    def test_source_refs_are_evaluator_only_and_opaque(self):  # 10
        fixture = self.harness.fixture
        sources = benchmark_context_sources(REVISIONS.tep_agent_lab, fixture)
        self.assertEqual([(Visibility.EVALUATOR, "BENCHMARK_CASE", fixture.case_path),
                          (Visibility.EVALUATOR, "EVALUATOR_GROUND_TRUTH",
                           fixture.ground_truth_path)],
                         [(source.visibility, source.kind, source.path_or_ref)
                          for source in sources])
        hidden = self.harness.hidden_sources(REVISIONS.tep_sim)
        self.assertEqual({Visibility.EVALUATOR}, {source.visibility for source in hidden})
        for source in sources:
            text = f"{source.source_id} {source.path_or_ref}".lower()
            for word in ("healthy", "normal", "fault", "intervention", "negative", "nominal"):
                self.assertNotIn(word, text)
        for word in ("healthy", "normal", "fault", "negative", "nominal"):
            self.assertNotIn(word, f"{HEALTHY} {self.harness.projection.incident_id}")

    def test_agent_projection_carries_no_healthy_answer(self):  # 11
        h = self.harness
        record = h.projection.record()
        self.assertEqual(HEALTHY_GOAL, record["goal"])
        self.assertEqual(([TRIGGER], 0.6), (record["trigger_signal_ids"],
                                            round(record["initial_time_hours"], 9)))
        assert_blind(self, record)
        assert_absent(self, forbidden(h), record)
        assert_absent(self, forbidden(h), h.run_request().record())
        audit = h.audit({"agent_case_projection": record}, tep_sim_revision=REVISIONS.tep_sim)
        self.assertEqual((True, ()), (audit.passed, audit.findings))
        # Only identity, time and goal wording differ from the incident projections.
        incident = harness("rca-dev-002").projection.record()
        self.assertEqual({"case_id", "incident_id", "goal"},
                         {key for key in record if record[key] != incident[key]})

    def test_no_cross_case_label_leakage(self):
        for case_id in CASES:
            other = harness(case_id)
            with self.subTest(case_id=case_id):
                for visible, auditor in ((self.harness, other), (other, self.harness)):
                    audit = auditor.audit({"agent_case_projection": visible.projection.record()},
                                          tep_sim_revision=REVISIONS.tep_sim)
                    self.assertTrue(audit.passed, audit.findings)
                assert_absent(self, forbidden(self.harness), other.projection.record())

    def test_registry_and_v1_types_stay_unreachable_from_agent_modules(self):  # 19
        for module in (tool_surface, tool_bridge, application_views, application_transport,
                       desktop_backend, playground_views, playground):
            source = inspect.getsource(module)
            with self.subTest(module=module.__name__):
                for name in (HEALTHY, "NoInterventionSetup", "DisturbanceSetupV1",
                             "hidden_setup_from_record", HIDDEN_SETUP_SCHEMA_V1,
                             SETUP_POLICY_VERSION_V1, "NO_INTERVENTION"):
                    self.assertNotIn(name, source)


# -- EVALUATOR world checks -------------------------------------------------------------
class HealthyWorldTests(unittest.TestCase):
    """Healthy setup determinism and the absence of any applied intervention."""

    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name)
        cls.harness = harness(HEALTHY)
        cls.runs = {name: cls.setup(cls.root / name) for name in ("first", "second")}

    @classmethod
    def setup(cls, directory: Path):
        environment, world = make_world(directory, cls.harness.case)
        try:
            # A NO_INTERVENTION setup applies nothing: any apply attempt fails the setup.
            with mock.patch.object(environment, "apply",
                                   side_effect=AssertionError("healthy setup applied")):
                attestation = cls.harness.case_setup()(world)
            # EVALUATOR-side check only; the setup itself never reads hidden state.
            active = environment.observe().active_disturbances
            return world.observe(), list(world.history()), attestation, active
        finally:
            environment.close()

    def test_setup_applies_no_disturbance(self):  # 14
        observation, history, attestation, active = self.runs["first"]
        self.assertEqual((), tuple(active))
        self.assertEqual(2, attestation.operation_count)  # advance, advance
        self.assertEqual(self.harness.case.hidden_setup.checksum(),
                         attestation.hidden_setup_checksum)
        # Identical to a plain same-seed advance over the same timeline.
        environment, world = make_world(self.root / "plain", self.harness.case)
        try:
            for hours in HEALTHY_HOURS:
                world.advance(hours)
            self.assertEqual(checksum(world.observe()), checksum(observation))
            self.assertEqual(checksum(list(world.history())), checksum(history))
        finally:
            environment.close()

    def test_same_seed_setup_is_deterministic(self):  # 15
        (obs1, hist1, att1, _), (obs2, hist2, att2, _) = self.runs["first"], self.runs["second"]
        self.assertEqual(att1, att2)
        self.assertEqual(checksum(obs1), checksum(obs2))
        self.assertEqual(checksum(obs1), att1.final_agent_observation_checksum)
        self.assertEqual(checksum(hist1), checksum(hist2))
        self.assertEqual(37, len(hist1))  # 0.6 h at 60 s, plus the initial sample
        self.assertAlmostEqual(sum(HEALTHY_HOURS), att1.final_simulation_time_hours, places=9)
        self.assertFalse(obs1["shutdown_state"])

    def test_healthy_is_not_zero_dynamics(self):
        # The closed loop still varies: NO_ABNORMAL_CAUSE means no evaluator-injected
        # abnormal cause, not constant signals.
        _, history, _, _ = self.runs["first"]
        self.assertGreater(len({record["measurements"][TRIGGER] for record in history}), 1)
        self.assertEqual(CausalMechanism.NO_ABNORMAL_CAUSE,
                         self.harness.truth.causal_claim.mechanism)


# -- full blind run -------------------------------------------------------------------
class HealthyBlindRunTests(unittest.TestCase):
    """One create -> prepare -> start through the normal P0 / Coordinator path."""

    RUN_ID = "d0-2b-rca-dev-004"

    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name)
        cls.manager = make_manager(cls.root)
        cls.harness = harness(HEALTHY)
        cls.recorder = ModelInputRecorder(scripted_blind_provider())
        cls.prepared = cls.harness.create_and_prepare(cls.manager, cls.RUN_ID, cls.recorder)
        cls.outcome = cls.manager.start(cls.RUN_ID)
        cls.surfaces = collect_agent_surfaces(cls.manager, cls.RUN_ID,
                                              projection=cls.harness.projection,
                                              model_inputs=cls.recorder.turns)

    def test_prepare_records_valid_attestation(self):  # 13
        h = self.harness
        self.assertEqual([], h.binding_findings(self.prepared))
        benchmark = dict(self.manager.manifest(self.RUN_ID).benchmark)
        self.assertEqual(SETUP_POLICY_VERSION_V1, benchmark["setup_policy_version"])
        attestation = benchmark["case_setup_attestation"]
        self.assertEqual({
            "benchmark_version": "tep-rca-benchmark/v0", "case_id": HEALTHY, "case_version": "1",
            "setup_policy_version": SETUP_POLICY_VERSION_V1,
            "case_source_checksum": HEALTHY_CASE_CHECKSUM,
            "ground_truth_source_checksum": HEALTHY_TRUTH_CHECKSUM,
            "world_config_checksum": self.prepared.world["environment_config_checksum"],
            "hidden_setup_checksum": h.case.hidden_setup.checksum(), "operation_count": 2},
            {key: attestation[key] for key in (
                "benchmark_version", "case_id", "case_version", "setup_policy_version",
                "case_source_checksum", "ground_truth_source_checksum",
                "world_config_checksum", "hidden_setup_checksum", "operation_count")})
        self.assertAlmostEqual(0.6, attestation["final_simulation_time_hours"], places=9)
        self.assertRegex(attestation["final_agent_observation_checksum"], r"^[0-9a-f]{64}$")

    def test_fake_blind_run_completes(self):  # 16
        self.assertEqual(RunStatus.COMPLETED, self.outcome.terminal_status)
        runtime = self.outcome.runtime_result
        self.assertEqual(("DONE", []), (runtime["task_status"], list(runtime["errors"])))
        self.assertEqual(3, len(self.recorder.turns))
        trace = [event["payload"] for event in self.manager.queries(
            self.RUN_ID, ProjectionScope.EVALUATOR).events()["events"]
            if event["source"] == "runtime_trace"]
        self.assertFalse({event["type"] for event in trace
                          if "BATCH" in event["type"] or "SUBTASK" in event["type"]})
        history = [event for event in trace if event["type"] == "EXECUTE"][1]
        self.assertEqual({"window_hours": 0.6, "variables": [TRIGGER]}, json.loads(
            canonical_json(history["input_summary"]["request"]["arguments"])))

    def test_model_context_projections_carry_no_healthy_answer(self):  # 12
        projections = self.surfaces["model_context_projections"]
        self.assertEqual(3, len(projections))
        for projection in projections:
            assert_blind(self, projection)
            assert_absent(self, forbidden(self.harness), projection)
            self.assertIn(HEALTHY_GOAL, projection["content"]["task_state"]["goal"])
        for turn in self.recorder.turns:
            assert_absent(self, forbidden(self.harness), turn)

    def test_leakage_audit_passes(self):  # 17
        audit = self.harness.audit(self.surfaces, tep_sim_revision=REVISIONS.tep_sim)
        self.assertEqual((True, (), LEAKAGE_POLICY_VERSION_V1, HEALTHY_PROJECTION_CHECKSUM),
                         (audit.passed, audit.findings, audit.leakage_policy_version,
                          audit.agent_projection_checksum))
        for name, surface in self.surfaces.items():
            with self.subTest(surface=name):
                assert_blind(self, surface)
                assert_absent(self, forbidden(self.harness), surface)
        for token in ("NO_INTERVENTION", HIDDEN_SETUP_SCHEMA_V1, SETUP_POLICY_VERSION_V1):
            with self.subTest(token=token):  # the production audit itself catches these
                leaked = self.harness.audit({"agent_events": {"text": f"setup {token}"}},
                                            tep_sim_revision=REVISIONS.tep_sim)
                self.assertFalse(leaked.passed)
        for case_id in CASES:  # no incident label reaches the healthy run either
            other = harness(case_id).audit(self.surfaces, tep_sim_revision=REVISIONS.tep_sim)
            self.assertTrue(other.passed, other.findings)

    def test_saved_run_scores_healthy_outcome_as_not_available(self):  # 18
        submission = submission_from_run(self.manager, self.RUN_ID,
                                         projection=self.harness.projection)
        metrics = score_submission(submission, self.harness.truth,
                                   self.harness.case.scoring).metrics
        self.assertEqual(MetricStatus.NOT_AVAILABLE, metrics["healthy_no_abnormal_correct"].status)
        self.assertEqual(MetricStatus.NOT_AVAILABLE, metrics["top1_causal_claim_exact"].status)


class LeakagePolicyVersionTests(unittest.TestCase):
    """Leakage policy v0 for setup/v0 incidents, v1 for the setup/v1 healthy case."""

    V1_LABELS = ("NO_INTERVENTION", HIDDEN_SETUP_SCHEMA_V1, SETUP_POLICY_VERSION_V1)

    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.manager = make_manager(Path(directory.name))
        cls.harnesses = {case_id: harness(case_id) for case_id in (*CASES, HEALTHY)}
        cls.prepared = {case_id: h.create_and_prepare(cls.manager, f"policy-{case_id}",
                                                      scripted_blind_provider())
                        for case_id, h in cls.harnesses.items()}

    def expected(self, case_id):
        return LEAKAGE_POLICY_VERSION_V1 if case_id == HEALTHY else LEAKAGE_POLICY_VERSION

    def audit(self, case_id, surfaces):
        return self.harnesses[case_id].audit(surfaces, tep_sim_revision=REVISIONS.tep_sim)

    def test_policy_follows_the_setup_schema(self):  # 1, 2
        self.assertEqual(("tep-agent-lab.benchmark-leakage-policy/v0",
                          "tep-agent-lab.benchmark-leakage-policy/v1"),
                         (LEAKAGE_POLICY_VERSION, LEAKAGE_POLICY_VERSION_V1))
        for case_id, h in self.harnesses.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(self.expected(case_id),
                                 leakage_policy_version(h.case.hidden_setup))
        v1_disturbance_case = BenchmarkCase.from_record(with_setup(case_record("rca-dev-001"),
                                                                   v1_disturbance()))
        self.assertEqual(LEAKAGE_POLICY_VERSION_V1,
                         leakage_policy_version(v1_disturbance_case.hidden_setup))
        d0 = self.harnesses["rca-dev-001"]  # v1 DISTURBANCE adds no labels beyond v0
        self.assertEqual(benchmark._truth_labels(d0.case, d0.truth),
                         benchmark._truth_labels(v1_disturbance_case, d0.truth))
        with self.assertRaises(BenchmarkContractError):
            leakage_policy_version(case_record()["hidden_setup"])  # untyped record
        # Every setup schema has both a setup policy and a leakage policy.
        self.assertEqual(set(benchmark._SETUP_POLICY), set(benchmark._LEAKAGE_POLICY))

    def test_clean_audits_pass_under_their_policy(self):  # 3, 4
        for case_id, h in self.harnesses.items():
            with self.subTest(case_id=case_id):
                audit = self.audit(case_id, {"agent_case_projection": h.projection.record()})
                self.assertEqual((True, (), self.expected(case_id)),
                                 (audit.passed, audit.findings, audit.leakage_policy_version))

    def test_v1_detects_healthy_setup_contamination(self):  # 5, 6, 7
        for token in self.V1_LABELS:
            for text in (f"setup {token}", f"setup {token.lower()}"):
                audit = self.audit(HEALTHY, {"agent_events": {"text": text}})
                with self.subTest(text=text):
                    self.assertEqual((False, LEAKAGE_POLICY_VERSION_V1),
                                     (audit.passed, audit.leakage_policy_version))
                    self.assertEqual(["HIDDEN_TRUTH_LABEL"],
                                     sorted({finding.category.value
                                             for finding in audit.findings}))
            keyed = self.audit(HEALTHY, {"agent_events": {token: "x"}})
            self.assertFalse(keyed.passed)
            self.assertNotIn(token.lower(), canonical_json(keyed.record()).lower())

    def test_incident_audit_behavior_is_unchanged(self):  # 8
        for case_id in CASES:
            h = self.harnesses[case_id]
            claim = h.truth.causal_claim
            with self.subTest(case_id=case_id):
                # exactly the v0 labels: disturbance id, family, mechanism, fault family
                self.assertEqual(tuple(label.lower() for label in (
                    h.case.hidden_setup.intervention.disturbance_id, h.case.scenario_family_id,
                    claim.mechanism.value, claim.fault_family)),
                    benchmark._truth_labels(h.case, h.truth))
                # v1-only labels are not v0 behavior
                clean = self.audit(case_id, {"agent_events": {
                    "text": " ".join(self.V1_LABELS)}})
                self.assertEqual((True, LEAKAGE_POLICY_VERSION),
                                 (clean.passed, clean.leakage_policy_version))
                leaked = self.audit(case_id, {"agent_events": {
                    "text": h.case.hidden_setup.intervention.disturbance_id}})
                self.assertEqual((False, LEAKAGE_POLICY_VERSION),
                                 (leaked.passed, leaked.leakage_policy_version))
                self.assertEqual((CASES[case_id]["case"], CASES[case_id]["truth"],
                                  CASES[case_id]["projection"]),
                                 (h.fixture.case_checksum, h.fixture.ground_truth_checksum,
                                  h.projection_checksum))

    def test_benchmark_refs_and_internal_manifest_record_the_policy(self):  # 9, 10
        for case_id, h in self.harnesses.items():
            internal = dict(self.manager.manifest(f"policy-{case_id}").benchmark)
            with self.subTest(case_id=case_id):
                self.assertEqual(self.expected(case_id), h.benchmark_refs().leakage_policy_version)
                self.assertEqual(self.expected(case_id), internal["leakage_policy_version"])
                self.assertEqual(h.benchmark_refs().record(),
                                 {key: internal[key] for key in h.benchmark_refs().record()})

    def test_agent_manifest_exposes_no_policy_fields(self):  # 11
        for case_id, prepared in self.prepared.items():
            agent = self.manager.queries(f"policy-{case_id}").manifest_view()
            text = canonical_json(agent).lower()
            with self.subTest(case_id=case_id):
                self.assertNotIn("benchmark", agent["manifest"])
                self.assertNotIn("benchmark", prepared.agent_projection())
                for absent in ("leakage_policy", "leakage-policy", "setup_policy",
                               "setup-policy", "benchmark-setup", "no_intervention"):
                    self.assertNotIn(absent, text)


class HealthySetupAttestationTests(unittest.TestCase):
    """NO_INTERVENTION is still trusted setup: READY requires its attestation."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.manager = make_manager(Path(directory.name))
        self.harness = harness(HEALTHY)

    def assert_rejected(self, run_id, case_setup, needle):
        h = self.harness
        self.manager.create(run_id, h.run_request())
        with self.assertRaises(PrepareError) as caught:
            self.manager.prepare(run_id, provider=scripted_blind_provider(),
                                 context_sources=h.context_sources(REVISIONS.tep_sim),
                                 case_setup=case_setup, benchmark=h.benchmark_refs())
        self.assertIn(needle, str(caught.exception))
        self.assertEqual(RunStatus.CREATED, self.manager.get(run_id).status)
        with self.assertRaises(LifecycleError):
            self.manager.manifest(run_id)

    def test_attestation_is_mandatory(self):  # 13
        setup = self.harness.case_setup()
        for run_id, case_setup, needle in (
                ("no-setup", None, "requires a trusted case_setup"),
                ("no-attestation", lambda world: None, "typed CaseSetupAttestation"),
                ("v0-policy", lambda world: replace(
                    setup(world), setup_policy_version=SETUP_POLICY_VERSION),
                 "setup_policy_version"),
                ("wrong-observation", lambda world: replace(
                    setup(world), final_agent_observation_checksum="0" * 64),
                 "setup attestation")):
            with self.subTest(run_id=run_id):
                self.assert_rejected(run_id, case_setup, needle)


# -- scorer ---------------------------------------------------------------------------
class HealthyScorerTests(unittest.TestCase):
    def setUp(self):
        self.harness = harness(HEALTHY)

    def metric(self, claim, name="healthy_no_abnormal_correct"):
        submission = replace(synthetic(self.harness, cited=()), causal_claim=claim)
        found = score_submission(submission, self.harness.truth, self.harness.case.scoring)
        return found.metrics[name].status, found.metrics[name].value

    def test_healthy_outcome_semantics(self):  # 18
        healthy = CausalClaim(mechanism=CausalMechanism.NO_ABNORMAL_CAUSE)
        self.assertEqual((MetricStatus.AVAILABLE, True), self.metric(healthy))
        self.assertEqual((MetricStatus.AVAILABLE, True),
                         self.metric(healthy, "top1_causal_claim_exact"))
        self.assertEqual((MetricStatus.AVAILABLE, 0),
                         self.metric(healthy, "unsupported_narrative_claim_count"))
        for case_id in CASES:  # every incident claim is a false positive here
            incident = harness(case_id).truth.causal_claim
            with self.subTest(case_id=case_id):
                self.assertEqual((MetricStatus.AVAILABLE, False), self.metric(incident))
                self.assertEqual((MetricStatus.AVAILABLE, False),
                                 self.metric(incident, "top1_causal_claim_exact"))
        self.assertEqual((MetricStatus.NOT_AVAILABLE, None), self.metric(None))
        for case_id in CASES:  # incident cases keep the healthy metric NOT_APPLICABLE
            h = harness(case_id)
            score = score_submission(replace(synthetic(h), causal_claim=healthy), h.truth,
                                     h.case.scoring)
            self.assertEqual(MetricStatus.NOT_APPLICABLE,
                             score.metrics["healthy_no_abnormal_correct"].status)

    def test_rescore_is_byte_identical_and_has_no_aggregate(self):
        submission = replace(synthetic(self.harness, cited=()), causal_claim=CausalClaim(
            mechanism=CausalMechanism.NO_ABNORMAL_CAUSE))
        first = score_submission(submission, self.harness.truth, self.harness.case.scoring)
        second = score_submission(submission, self.harness.truth, self.harness.case.scoring)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        text = canonical_json(first.record()).lower()
        for absent in ("aggregate", "weighted", "total_score", "overall", "difficulty"):
            self.assertNotIn(absent, text)


if __name__ == "__main__":
    unittest.main()
