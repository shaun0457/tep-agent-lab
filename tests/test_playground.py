"""P0 Playground Backend acceptance tests (docs/specs/playground-backend-v0.md)."""

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest import mock

from industrial_agent_runtime import (
    Action, Budget, Coordinator, FakeProvider, FinishProposal, InformationRef, ModelTurn,
    TaskStatus, ToolCallRequest, Visibility, canonical_json, checksum, to_jsonable,
)
from tep_sim import DisturbanceIntervention

from tep_agent_lab.canonical_context import (
    BYTES_SHA256, CanonicalContextRegistry, ContextSourceError, ContextSourceRef,
    DirectorySourceMaterializer, PackageSourceMaterializer, ProjectionScope,
    UnknownContextSource, content_checksum,
)
from tep_agent_lab.persistence import canonical_json as lab_canonical_json
from tep_agent_lab.playground import (
    EVALUATOR_BINDINGS_SOURCE_ID, LAB_REPOSITORY, PROCESS_GRAPH_SOURCE_ID,
    TEP_SIM_REPOSITORY, BenchmarkRefs, LifecycleError, ModelSpec, PrepareError,
    RunManager, RunRequest, RunStatus, SourceRevisions, WorldSpec, load_dependency_pins,
    pinned_tep_sim_sources, secret_findings,
)
from tep_agent_lab.playground_views import UnknownArtifact, ViewUnavailable
from tep_agent_lab.tep_world import SimulationSandbox, leakage_findings
from tep_agent_lab.tool_surface import simulation_quota
from test_tool_surface import INJECTED, NOW, assert_blind

ROOT = Path(__file__).resolve().parents[1]
PINS = load_dependency_pins(ROOT / "dependency-pins.json")
CASE_ID = "idv4-blind-case"
CASE_SOURCE_ID = "benchmark-case.idv4-blind-case"
CASE_PATH = "benchmarks/cases/idv4-blind-case.json"
VARIABLES = ["XMEAS(9)", "XMEAS(21)"]


def lab_revision() -> str:
    """The checkout revision when available; any exact 40-hex revision serves the tests."""
    path = ROOT.as_posix()
    try:
        revision = subprocess.check_output(
            ["git", "-c", f"safe.directory={path}", "-C", path, "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "a" * 40
    return revision if len(revision) == 40 else "a" * 40


REVISIONS = SourceRevisions(PINS["tep-sim"], PINS["industrial-agent-runtime"], lab_revision())


def harness_case(world) -> None:
    """Trusted benchmark harness: pre-incident baseline, then a hidden injected cause."""
    world.advance(0.1)
    world.designate_baseline()
    world.environment.apply(DisturbanceIntervention(INJECTED, 1))
    world.advance(0.2)


def write_case_repo(directory: Path, content=None) -> ContextSourceRef:
    """A synthetic lab-repository benchmark case: evaluator-only hidden truth."""
    content = content or {"case_id": CASE_ID, "injected_cause": INJECTED}
    path = directory / CASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return ContextSourceRef(
        source_id=CASE_SOURCE_ID, repository=LAB_REPOSITORY,
        git_revision=REVISIONS.tep_agent_lab, path_or_ref=CASE_PATH,
        content_checksum=content_checksum(path.read_bytes(), "sha256:canonical-json"),
        kind="BENCHMARK_CASE", schema_version="tep-agent-lab.benchmark-case/v0",
        visibility=Visibility.EVALUATOR)


def request(**changes) -> RunRequest:
    base = RunRequest(
        investigation_id="inv-1", goal="find the root cause", world=WorldSpec(seed=11),
        model=ModelSpec("fake", "scripted", "v1", "v1", config={"temperature": 0}),
        budget=Budget(12, 12, 0, 0, 30, extra_dimensions=simulation_quota(
            snapshots=1, branches=1, rollouts=1, horizon_seconds=3600)),
        require_conclusion=False)
    return replace(base, **changes)


def turn(make=None):
    """Scripted typed turn bound to the exact projection; refs come from the projection."""
    def build(projection, limits):
        turn_id = f"turn-{limits['budget_usage']['model_calls']}"
        if make is None:
            return ModelTurn(turn_id, limits["context_projection_ref"],
                             projection.base_revision, Action.FINISH_PROPOSAL,
                             finish_proposal=FinishProposal({"done": True}))
        return ModelTurn(turn_id, limits["context_projection_ref"],
                         projection.base_revision, Action.TOOL_REQUEST,
                         tool_request=make(projection.content["task_state"]))
    return build


def artifact(state, kind):
    return next(ref for ref in state["artifact_refs"] if ref["kind"] == kind)


def investigation_script() -> FakeProvider:
    requests = [
        lambda state: ToolCallRequest("a-history", "get_history",
                                      {"window_hours": 0.3, "variables": VARIABLES}),
        lambda state: ToolCallRequest("b-fork", "fork_environment",
                                      {"snapshot_id": "snapshot-0001"}),
        lambda state: ToolCallRequest("c-rollout", "run_rollout",
                                      {"branch_id": "branch-0002", "horizon_hours": 0.2}),
        lambda state: ToolCallRequest("d-features", "compute_response_features", {
            "trajectory_ref": artifact(state, "HistoryWindowArtifact"),
            "variables": VARIABLES,
            "baseline_window": {"start_hours": 0.0, "end_hours": 0.1},
            "analysis_window": {"start_hours": 0.1, "end_hours": 0.3},
            "features": [{"feature": "DELTA"}, {"feature": "PEAK"}]}),
    ]
    return FakeProvider([turn(make) for make in requests] + [turn()])


class PlaygroundCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.case_repo = self.root / "lab-repo"
        self.case_source = write_case_repo(self.case_repo)
        self.manager = self.make_manager()

    def make_manager(self, *, revisions=REVISIONS, pins=PINS, case_repo=None):
        return RunManager(self.root / "runs", revisions=revisions, dependency_pins=pins,
                          materializers={
                              TEP_SIM_REPOSITORY: PackageSourceMaterializer("tep_sim"),
                              LAB_REPOSITORY: DirectorySourceMaterializer(
                                  case_repo or self.case_repo)},
                          clock=lambda: NOW)

    def sources(self):
        return (*pinned_tep_sim_sources(REVISIONS.tep_sim), self.case_source)

    def prepare(self, run_id="run-1", provider=None, **changes):
        """A non-benchmark P0 run that still registers EVALUATOR-only sources.

        Bound benchmark runs (typed setup attestation) are covered in test_benchmark.
        """
        self.manager.create(run_id, request(**changes))
        return self.manager.prepare(
            run_id, provider=provider or investigation_script(),
            context_sources=self.sources(), case_setup=harness_case)

    def manifests_on_disk(self, run_id="run-1"):
        return sorted(path.relative_to(self.root).as_posix()
                      for path in (self.root / "runs" / run_id).rglob("manifest.json")
                      if path.parent.name != "lifecycle" and path.parent.name != "state")


class CanonicalContextRegistryTests(PlaygroundCase):
    def registry(self):
        return CanonicalContextRegistry(REVISIONS.by_repository(), {
            TEP_SIM_REPOSITORY: PackageSourceMaterializer("tep_sim"),
            LAB_REPOSITORY: DirectorySourceMaterializer(self.case_repo)})

    def test_source_ref_identifies_immutable_content_strictly(self):
        good = self.case_source
        for field, value in (("git_revision", "main"), ("git_revision", "abc"),
                             ("path_or_ref", "../secrets.json"), ("path_or_ref", "/etc/x"),
                             ("path_or_ref", "C:/x.json"), ("path_or_ref", "a\\b.json"),
                             ("path_or_ref", "a//b.json"), ("path_or_ref", "./a.json"),
                             ("path_or_ref", "a/"), ("path_or_ref", "con/x.json"),
                             ("path_or_ref", "x./y.json"),
                             ("content_checksum", "deadbeef"), ("kind", "lower"),
                             ("checksum_method", "md5")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                replace(good, **{field: value})
        with self.assertRaises(FrozenInstanceError):  # a registered ref cannot be relabeled
            good.visibility = Visibility.AGENT

    def test_exact_resolution_and_mismatch_detection(self):
        registry = self.registry()
        for source in self.sources():
            registry.register(source)
        first = registry.resolve(PROCESS_GRAPH_SOURCE_ID, ProjectionScope.AGENT)
        again = registry.resolve(PROCESS_GRAPH_SOURCE_ID, ProjectionScope.AGENT)
        self.assertEqual(first, again)  # deterministic direct resolution
        self.assertEqual("tep-process-graph", first.json()["fixture_id"])
        with self.assertRaises(ContextSourceError):  # revision mismatch
            self.registry().register(replace(self.case_source, git_revision="0" * 40))
        with self.assertRaises(ContextSourceError):  # checksum mismatch
            self.registry().register(replace(self.case_source, content_checksum="0" * 64))
        with self.assertRaises(ContextSourceError):  # no local materialization
            self.registry().register(replace(self.case_source,
                                             repository=TEP_SIM_REPOSITORY,
                                             git_revision=REVISIONS.tep_sim))
        with self.assertRaises(ContextSourceError):  # duplicate id
            registry.register(self.case_source)
        # a materialized source mutated after registration fails closed on resolution
        (self.case_repo / CASE_PATH).write_text('{"case_id": "other"}', encoding="utf-8")
        with self.assertRaises(ContextSourceError):
            registry.resolve(CASE_SOURCE_ID, ProjectionScope.EVALUATOR)

    def test_checksum_methods(self):
        data = b'{"b": 1,\r\n "a": [2]}'
        self.assertEqual(content_checksum(data, "sha256:canonical-json"),
                         content_checksum(b'{"a":[2],"b":1}', "sha256:canonical-json"))
        self.assertEqual(hashlib.sha256(data).hexdigest(), content_checksum(data, BYTES_SHA256))

    def test_agent_scope_cannot_resolve_or_count_evaluator_sources(self):
        registry = self.registry()
        for source in self.sources():
            registry.register(source)
        inventory = registry.freeze()
        self.assertTrue(registry.frozen)
        self.assertEqual(3, len(inventory))
        self.assertEqual([PROCESS_GRAPH_SOURCE_ID],
                         [ref.source_id for ref in registry.inventory(ProjectionScope.AGENT)])
        messages = set()
        for source_id in (CASE_SOURCE_ID, EVALUATOR_BINDINGS_SOURCE_ID, "no-such-source", 7):
            with self.assertRaises(UnknownContextSource) as caught:
                registry.resolve(source_id, ProjectionScope.AGENT)
            messages.add(str(caught.exception))
        self.assertEqual({"unknown context source"}, messages)  # hidden == unknown
        self.assertEqual(INJECTED, registry.resolve(
            CASE_SOURCE_ID, ProjectionScope.EVALUATOR).json()["injected_cause"])
        with self.assertRaises(ContextSourceError):  # frozen at READY
            registry.register(replace(self.case_source, source_id="late-source"))

    def test_registry_accepts_only_typed_trusted_refs(self):
        with self.assertRaises(ContextSourceError):
            self.registry().register(self.case_source.record())

    def test_secret_scan(self):
        self.assertEqual([], secret_findings({"max_total_tokens": 1, "approximate_tokens": 2}))
        for value in ({"api_key": "x"}, {"auth": {"Authorization": "x"}},
                      {"note": "-----BEGIN RSA PRIVATE KEY-----"},
                      {"deploy_key": "x"}, {"x": "sk-" + "a" * 20}, {"authToken": "x"},
                      {"clientSecret": "x"}, {"X-Api-Key": "x"}, {"pwd": "x"},
                      {"x": "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.sig"},
                      {"x": "https://user:hunter2@example.com/repo"},
                      {"x": "sk_live_" + "a" * 20}):
            with self.subTest(value=value):
                self.assertNotEqual([], secret_findings(value))
        with self.assertRaises(ValueError):
            ModelSpec("p", "m", "v", "t", config={"api_key": "sk-" + "a" * 20})
        with self.assertRaises(ValueError):  # screened before RUN_CREATED is persisted
            request(projection_policy={"accessToken": "x"})


class LifecycleTests(PlaygroundCase):
    def test_create_is_created_only_and_identity_is_validated(self):
        info = self.manager.create("run-1", request())
        self.assertEqual(RunStatus.CREATED, info.status)
        self.assertIsNone(info.manifest_checksum)
        with self.assertRaises(LifecycleError):
            self.manager.create("run-1", request())
        for bad in ("Run_1", "../x", "a", "", "con", "nul"):
            with self.subTest(run_id=bad), self.assertRaises(ValueError):
                self.manager.create(bad, request())
        with self.assertRaises(KeyError):
            self.manager.get("run-unknown")

    def test_illegal_transitions_fail_deterministically(self):
        self.manager.create("run-1", request())
        with self.assertRaises(LifecycleError):
            self.manager.start("run-1")  # CREATED -> RUNNING is illegal
        self.assertEqual(RunStatus.CREATED, self.manager.get("run-1").status)
        self.manager.prepare("run-1", provider=investigation_script(),
                             context_sources=self.sources(), case_setup=harness_case)
        with self.assertRaises(LifecycleError):  # READY cannot be prepared again
            self.manager.prepare("run-1", provider=investigation_script(),
                                 context_sources=self.sources())
        self.assertEqual(RunStatus.READY, self.manager.get("run-1").status)

    def failed_prepare(self, sources, *, manager=None, benchmark=None, provider=None):
        manager = manager or self.manager
        manager.create("run-1", request())
        with self.assertRaises(PrepareError) as caught:
            manager.prepare("run-1", provider=provider or investigation_script(),
                            context_sources=sources, case_setup=harness_case,
                            benchmark=benchmark)
        info = manager.get("run-1")
        self.assertEqual(RunStatus.CREATED, info.status)
        self.assertIsNone(info.manifest_checksum)
        self.assertEqual([], self.manifests_on_disk())  # no partial manifest
        types = [event["type"] for event in manager.queries(
            "run-1", ProjectionScope.EVALUATOR).events()["events"]]
        self.assertIn("PREPARE_FAILED", types)
        self.assertNotIn("RUN_READY", types)
        with self.assertRaises(LifecycleError):
            manager.manifest("run-1")
        return caught.exception

    def test_prepare_is_atomic_and_retryable(self):
        broken = replace(self.case_source, content_checksum="0" * 64)
        self.failed_prepare((*pinned_tep_sim_sources(REVISIONS.tep_sim), broken))
        self.assertFalse((self.root / "runs" / "run-1" / "prepare-0001").exists())
        agent_feed = self.manager.queries("run-1").events()["events"]
        self.assertNotIn("PREPARE_FAILED", canonical_json(agent_feed))
        # no sequence gap reveals the hidden failed attempt
        self.assertEqual(list(range(len(agent_feed))),
                         [event["source_sequence"] for event in agent_feed])
        manifest = self.manager.prepare(
            "run-1", provider=investigation_script(), context_sources=self.sources(),
            case_setup=harness_case)
        self.assertEqual(RunStatus.READY, self.manager.get("run-1").status)
        self.assertEqual("prepare-0002", manifest.storage["session"])
        self.assertEqual(["runs/run-1/prepare-0002/manifest.json"], self.manifests_on_disk())

    def test_revision_mismatch_fails_prepare(self):
        stale = replace(self.case_source, git_revision="1" * 40)
        self.failed_prepare((*pinned_tep_sim_sources(REVISIONS.tep_sim), stale))

    def test_dependency_pins_are_attested(self):
        manager = self.make_manager(revisions=replace(REVISIONS, tep_sim="2" * 40))
        self.failed_prepare(self.sources(), manager=manager)

    def test_numerical_stack_is_attested(self):
        manager = self.make_manager(pins={**PINS, "numpy": "0.0.0"})
        self.failed_prepare(self.sources(), manager=manager)

    def test_credentials_never_reach_a_manifest(self):
        leaky = replace(self.case_source, provenance={"deploy_key": "ssh-ed25519 AAAA"})
        self.failed_prepare((*pinned_tep_sim_sources(REVISIONS.tep_sim), leaky))

    def test_hidden_truth_refs_must_be_evaluator_only(self):
        refs = BenchmarkRefs(
            benchmark_version="b/v0", case_id="case-001", case_version="1",
            partition="DEVELOPMENT", benchmark_case_source_id=CASE_SOURCE_ID,
            evaluator_ground_truth_source_id=PROCESS_GRAPH_SOURCE_ID,
            agent_projection_checksum="0" * 64, scorer_version="s/v0",
            setup_policy_version="p/v0", leakage_policy_version="l/v0")
        self.failed_prepare(self.sources(), benchmark=refs)
        failure = [event for event in self.manager.queries(
            "run-1", ProjectionScope.EVALUATOR).events()["events"]
            if event["type"] == "PREPARE_FAILED"]
        self.assertIn("EVALUATOR-only", failure[0]["payload"]["detail"])

    def test_agent_source_cannot_alias_hidden_content(self):
        alias = replace(self.case_source, source_id="innocent-notes",
                        visibility=Visibility.AGENT)
        self.failed_prepare((*self.sources(), alias))

    def test_alias_check_spans_paths_and_checksum_methods(self):
        copy = self.case_repo / "notes" / "copy.json"
        copy.parent.mkdir()
        copy.write_bytes((self.case_repo / CASE_PATH).read_bytes())
        alias = replace(self.case_source, source_id="innocent-notes", path_or_ref="notes/copy.json",
                        visibility=Visibility.AGENT, checksum_method=BYTES_SHA256,
                        content_checksum=hashlib.sha256(copy.read_bytes()).hexdigest())
        self.failed_prepare((*self.sources(), alias))

    def test_other_process_progress_is_observed(self):
        self.prepare(provider=FakeProvider([turn()]))
        observer = self.make_manager()
        self.assertEqual(RunStatus.READY, observer.get("run-1").status)
        self.manager.start("run-1")
        info = observer.get("run-1")
        self.assertEqual((RunStatus.COMPLETED, "COMPLETED"),
                         (info.status, info.outcome.terminal_status.value))

    def test_retired_snapshots_keep_lineage_connected(self):
        world = mock.Mock()
        world.baselines.return_value = {}
        sandbox = SimulationSandbox(world)
        sandbox._branches["branch-0001"] = (mock.Mock(), "snapshot-0000", "baseline")
        sandbox._snapshots["snapshot-0002"] = (None, None, "branch-0001", "baseline")
        sandbox._branches["branch-0003"] = (mock.Mock(), "snapshot-0002", "baseline")
        sandbox.retire("branch-0001")
        records = {record["handle"]: record for record in sandbox.lineage_records()}
        self.assertEqual("RETIRED", records["snapshot-0002"]["status"])
        self.assertEqual("snapshot-0002", records["branch-0003"]["parent"])
        sandbox.close()
        sandbox.close()  # idempotent
        self.assertEqual({"RETIRED", "RELEASED"},
                         {record["status"] for record in sandbox.lineage_records()})

    def test_process_graph_source_is_required(self):
        self.failed_prepare((self.case_source,))

    def test_failed_case_setup_tears_down_without_manifest(self):
        def broken_setup(world):
            raise RuntimeError(f"harness failed while injecting {INJECTED}")

        self.manager.create("run-1", request())
        with self.assertRaises(PrepareError):
            self.manager.prepare("run-1", provider=investigation_script(),
                                 context_sources=self.sources(), case_setup=broken_setup)
        self.assertEqual([], self.manifests_on_disk())
        agent = self.manager.queries("run-1").events()
        assert_blind(self, agent)  # the hidden setup error is not an Agent oracle
        evaluator = self.manager.queries("run-1", ProjectionScope.EVALUATOR).events()
        self.assertIn(INJECTED, canonical_json(evaluator))


class ConcurrencyTests(PlaygroundCase):
    def test_concurrent_start_has_exactly_one_execution_owner(self):
        entered, release = threading.Event(), threading.Event()

        def blocking_finish(projection, limits):
            entered.set()  # the owner is inside Coordinator.run(): status is RUNNING
            self.assertTrue(release.wait(timeout=60))
            return turn()(projection, limits)

        self.prepare(provider=FakeProvider([blocking_finish]))
        results, errors = [], []

        def start():
            try:
                results.append(self.manager.start("run-1"))
            except BaseException as exc:  # every failure is collected, none is lost
                errors.append(exc)

        owner = threading.Thread(target=start)
        owner.start()
        try:
            self.assertTrue(entered.wait(timeout=60))
            self.assertEqual(RunStatus.RUNNING, self.manager.get("run-1").status)
            contender = threading.Thread(target=start)
            contender.start()
            contender.join(timeout=60)
            self.assertFalse(contender.is_alive())
            # the contender is rejected by the RUNNING owner check, not a terminal one
            self.assertEqual(1, len(errors))
            self.assertIsInstance(errors[0], LifecycleError)
            self.assertIn("RUNNING", str(errors[0]))
            with self.assertRaises(ViewUnavailable):  # no unsynchronized live-world reads
                self.manager.queries("run-1").telemetry()
        finally:
            release.set()
            owner.join(timeout=60)
        self.assertFalse(owner.is_alive())
        self.assertEqual((1, 1), (len(results), len(errors)))
        self.assertEqual(RunStatus.COMPLETED, results[0].terminal_status)
        started = [event for event in self.manager.queries("run-1").events()["events"]
                   if event["type"] == "RUN_STARTED"]
        self.assertEqual(1, len(started))
        with self.assertRaises(LifecycleError):  # terminal never returns to RUNNING
            self.manager.start("run-1")

    def test_execution_failure_is_a_failed_outcome_without_agent_oracle(self):
        self.prepare(provider=FakeProvider([turn()]))
        with mock.patch.object(Coordinator, "run",
                               side_effect=RuntimeError(f"hidden setup used {INJECTED}")):
            outcome = self.manager.start("run-1")
        self.assertEqual(RunStatus.FAILED, outcome.terminal_status)
        self.assertEqual("EXECUTION_ERROR", outcome.failure_category)
        self.assertIsNone(outcome.runtime_result)
        summary = self.manager.queries("run-1").run_summary()
        self.assertEqual("FAILED", summary["run_status"])
        self.assertEqual("RELEASED", summary["resources"]["session"])
        assert_blind(self, summary)
        evaluator = self.manager.queries("run-1", ProjectionScope.EVALUATOR)
        self.assertIn(INJECTED, evaluator.run_summary()["outcome"]["failure_detail"])
        self.assertEqual(outcome, self.make_manager().get("run-1").outcome)  # durable

    def test_interrupt_is_recorded_as_failed_then_reraised(self):
        self.prepare(provider=FakeProvider([turn()]))
        with mock.patch.object(Coordinator, "run", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.manager.start("run-1")
        info = self.make_manager().get("run-1")
        self.assertEqual(RunStatus.FAILED, info.status)
        self.assertEqual("EXECUTION_ERROR", info.outcome.failure_category)

    def test_credentials_in_failure_detail_are_withheld(self):
        self.prepare(provider=FakeProvider([turn()]))
        leaked = "Authorization: Bearer abcdefghijklmnop0123"
        with mock.patch.object(Coordinator, "run", side_effect=RuntimeError(leaked)):
            outcome = self.manager.start("run-1")
        self.assertNotIn("abcdefghijklmnop0123", outcome.failure_detail)
        self.assertEqual([], secret_findings(outcome.record()))


class RuntimeTaskOutcomeTests(PlaygroundCase):
    """Frozen P0 semantic: application COMPLETED != task success.

    A real ``Coordinator.run()`` that returns a valid ``RuntimeResult`` is a hosted
    COMPLETED run whatever the runtime ``TaskStatus``; only a hosting/execution-path
    failure is application FAILED.
    """

    def assert_completed_with_task_status(self, outcome, task_status):
        self.assertEqual(RunStatus.COMPLETED, outcome.terminal_status)
        self.assertIsNone(outcome.failure_category)
        self.assertEqual(task_status, outcome.runtime_result["task_status"])
        self.assertEqual(RunStatus.COMPLETED, self.manager.get("run-1").status)
        for manager in (self.manager, self.make_manager()):  # in-process and reloaded
            summary = manager.queries("run-1").run_summary()
            self.assertEqual("COMPLETED", summary["run_status"])
            self.assertEqual(task_status, summary["runtime_task_status"]["status"])
            self.assertEqual(task_status, summary["outcome"]["runtime_result"]["task_status"])
            assert_blind(self, summary)

    def test_budget_exhaustion_is_completed_hosting_with_exhausted_task(self):
        self.prepare(budget=Budget(1, 12, 0, 0, 30, extra_dimensions=simulation_quota(
            snapshots=1, branches=1, rollouts=1, horizon_seconds=3600)))
        outcome = self.manager.start("run-1")
        self.assert_completed_with_task_status(outcome, "EXHAUSTED")
        self.assertEqual(1, outcome.runtime_result["budget_usage"]["model_calls"])

    def test_runtime_task_failure_without_exception_is_completed_hosting(self):
        provider = FakeProvider([turn()])
        self.prepare(provider=provider, budget=Budget(
            12, 12, 0, 0, 30, max_total_tokens=1000, extra_dimensions=simulation_quota(
                snapshots=1, branches=1, rollouts=1, horizon_seconds=3600)))
        outcome = self.manager.start("run-1")  # Coordinator.run() returns; it does not raise
        self.assert_completed_with_task_status(outcome, "FAILED")
        self.assertIn("token-metered provider execution requires downstream accounting",
                      outcome.runtime_result["errors"])
        self.assertEqual(0, outcome.runtime_result["budget_usage"]["model_calls"])
        self.assertEqual([], provider.projections)  # fail-closed before any provider turn


class ExecutionTests(unittest.TestCase):
    """One real fake-provider run: Coordinator -> B2 -> consumer -> Executor -> B3."""

    @classmethod
    def setUpClass(cls):
        cls.case = PlaygroundCase("setUp")
        cls.case.setUp()
        cls.addClassCleanup(cls.case.doCleanups)
        cls.manager = cls.case.manager
        cls.prepared = cls.case.prepare()
        cls.ready_checksum = cls.manager.get("run-1").manifest_checksum
        cls.outcome = cls.manager.start("run-1")
        cls.queries = cls.manager.queries("run-1")
        cls.evaluator = cls.manager.queries("run-1", ProjectionScope.EVALUATOR)
        cls.session = cls.manager._runs["run-1"].session

    def assert_no_paths(self, value):
        text = canonical_json(to_jsonable(value))
        for form in (str(self.case.root), self.case.root.as_posix(),
                     json.dumps(str(self.case.root))[1:-1]):
            self.assertNotIn(form, text)

    def agent_text(self, value) -> str:
        return canonical_json(to_jsonable(value))

    def assert_agent_safe(self, value):
        assert_blind(self, value)
        text = self.agent_text(value)
        for hidden in (CASE_SOURCE_ID, CASE_PATH, CASE_ID, EVALUATOR_BINDINGS_SOURCE_ID,
                       "evaluator_disturbance", "benchmark_case", "ground_truth"):
            self.assertNotIn(hidden, text)

    def test_run_completes_through_existing_authority_path(self):
        self.assertEqual(RunStatus.COMPLETED, self.outcome.terminal_status)
        runtime = self.outcome.runtime_result
        self.assertEqual("DONE", runtime["task_status"], runtime["errors"])
        events = self.session.trace.read_events()
        for request_id in ("a-history", "b-fork", "c-rollout", "d-features"):
            stages = [event["output_summary"]["stage"] for event in events
                      if event["type"] == "GATE" and event["input_summary"]["request_id"]
                      == request_id]
            self.assertLessEqual({"G0_SCHEMA", "G1_AUTHORITY", "G2_BUDGET", "G3_SIDE_EFFECT",
                                  "CONSUMER"}, set(stages))
            dispatched = [event for event in events if event["type"] == "EXECUTE"
                          and event["input_summary"]["request"]["request_id"] == request_id]
            verified = [event for event in events if event["type"] == "VERIFY_RESULT"
                        and event["input_summary"]["request_id"] == request_id]
            self.assertEqual((1, ["ACCEPTED"]),
                             (len(dispatched), [event["status"] for event in verified]))
        accepted = [event for event in events
                    if event["type"] == "VERIFY_RESULT" and event["status"] == "ACCEPTED"]
        self.assertEqual(4, len(accepted))
        state = self.session.store.state
        self.assertEqual(4, len(state.observation_refs))
        self.assertEqual((), state.evidence_link_refs)  # observation, not evidence

    def test_application_status_and_task_status_are_distinct_domains(self):
        self.assertEqual(TaskStatus.DONE, self.session.store.status())
        self.assertEqual(RunStatus.COMPLETED, self.manager.get("run-1").status)
        self.assertNotIn("DONE", {status.value for status in RunStatus})
        summary = self.queries.run_summary()
        self.assertEqual("COMPLETED", summary["run_status"])
        self.assertEqual("DONE", summary["runtime_task_status"]["status"])
        self.assertNotEqual(summary["status_owner"], summary["runtime_task_status"]["owner"])

    def test_manifest_is_immutable_and_outcome_is_separate(self):
        manifest = self.manager.manifest("run-1")
        self.assertEqual(self.prepared, manifest)
        self.assertEqual(self.ready_checksum, manifest.checksum())
        session_dir = self.session.directory
        self.assertEqual(self.ready_checksum, hashlib.sha256(
            (session_dir / "manifest.json").read_bytes()).hexdigest())
        outcome = json.loads((session_dir / "outcome.json").read_bytes())
        self.assertEqual(self.ready_checksum, outcome["manifest_checksum"])
        self.assertNotIn("terminal_status", manifest.record())
        with self.assertRaises(FrozenInstanceError):
            manifest.world = {}

    def test_manifest_identifies_exact_versions(self):
        manifest = self.prepared
        self.assertEqual(REVISIONS.record(), dict(manifest.source_revisions))
        self.assertEqual(PINS["numpy"], manifest.numerical_stack["numpy"])
        self.assertEqual(("tep-process-graph", "0.2.0", "HUMAN_VERIFIED"),
                         (manifest.process_semantics["fixture_id"],
                          manifest.process_semantics["fixture_version"],
                          manifest.process_semantics["review_status"]))
        self.assertEqual("cc8ccc81e9f421238863457438465877850b19d9760740279e54a52468fe9a87",
                         manifest.process_semantics["fixture_checksum"])
        self.assertIsNotNone(manifest.process_semantics["review_record"])
        turns = [event for event in self.session.trace.read_events()
                 if event["type"] == "MODEL_TURN"]
        self.assertTrue(turns)
        for event in turns:  # the manifest names exactly what the runtime traced
            self.assertEqual(manifest.runtime_policy["tool_set_version"],
                             event["registered_tool_set_version"])
            self.assertEqual((manifest.model["provider"], manifest.model["model_name"],
                              manifest.model["model_version"]),
                             (event["provider"], event["model"], event["model_version"]))
        self.assertEqual(checksum({"temperature": 0}), manifest.model["model_config_checksum"])
        self.assertEqual([], secret_findings(manifest.record()))
        self.assertEqual(3, len(manifest.canonical_context_sources))

    def test_context_inventory_is_frozen_and_trusted(self):
        registry = self.session.registry
        self.assertTrue(registry.frozen)
        self.assertEqual(self.prepared.canonical_context_sources,
                         registry.inventory(ProjectionScope.EVALUATOR))
        with self.assertRaises(ContextSourceError):
            registry.register(replace(self.case.case_source, source_id="model-added"))
        tool_names = {spec.name for spec in self.session.surface.tool_specs()}
        self.assertFalse({name for name in tool_names
                          if "context" in name or "source" in name or "registry" in name})

    def test_agent_views_are_visibility_filtered(self):
        agent_inventory = self.queries.context_inventory()
        self.assertEqual([PROCESS_GRAPH_SOURCE_ID],
                         [source["source_id"] for source in agent_inventory["sources"]])
        self.assertEqual(3, len(self.evaluator.context_inventory()["sources"]))
        agent_manifest = self.queries.manifest_view()
        self.assertNotIn("benchmark", agent_manifest["manifest"])
        self.assertNotIn("storage", agent_manifest["manifest"])
        evaluator_manifest = self.evaluator.manifest_view()
        # unbound (non-benchmark) run: boolean-only setup record, no hidden refs
        self.assertEqual({"case_setup_applied": True},
                         dict(evaluator_manifest["manifest"]["benchmark"]))
        agent_views = [agent_inventory, agent_manifest, self.queries.run_summary(),
                       self.queries.process_graph(), self.queries.telemetry(),
                       self.queries.investigation(), self.queries.branch_tree(),
                       self.queries.budget(), self.queries.events(), self.queries.artifacts()]
        for view in agent_views:
            with self.subTest(view=view["view"]):
                self.assertEqual("AGENT", view["scope"])
                self.assert_agent_safe(view)
        summary = self.queries.run_summary()
        self.assertNotEqual(self.ready_checksum, summary["manifest"]["checksum"])

    def test_process_graph_view(self):
        view = self.queries.process_graph()
        self.assertEqual((17, 18), (len(view["nodes"]), len(view["edges"])))
        self.assertEqual("HUMAN_VERIFIED", view["provenance"]["review_status"])
        self.assertIsNotNone(view["provenance"]["review_record"])
        self.assertEqual(PROCESS_GRAPH_SOURCE_ID, view["source"]["source_id"])
        self.assertNotIn("evaluator_overlays", view)
        kinds = {binding["runtime_variable_kind"] for node in [*view["nodes"], *view["edges"]]
                 for binding in node["bindings"]}
        self.assertEqual({"XMEAS", "XMV"}, kinds)
        evaluator = self.evaluator.process_graph()
        overlay = evaluator["evaluator_overlays"][0]
        self.assertEqual(EVALUATOR_BINDINGS_SOURCE_ID, overlay["source_id"])
        self.assertIn(INJECTED, canonical_json(overlay))

    def test_telemetry_view_is_bounded(self):
        view = self.queries.telemetry(variables=("XMEAS(9)",), max_records=5)
        self.assertEqual(5, view["returned_records"])
        self.assertGreater(view["total_records"], 5)
        self.assertEqual({"XMEAS(9)"}, set(view["records"][0]["measurements"]))
        self.assertEqual({"XMEAS(9)"}, set(view["current"]["measurements"]))
        generated = self.queries.telemetry(variables=(name for name in ["XMEAS(9)"]),
                                           max_records=1)
        self.assertEqual({"XMEAS(9)"}, set(generated["current"]["measurements"]))
        self.assertEqual({"HistoryWindowArtifact", "RolloutTelemetryArtifact"},
                         {ref["kind"] for ref in view["artifact_refs"]})
        for bad in (0, 1001, 2.5):
            with self.subTest(max_records=bad), self.assertRaises(ValueError):
                self.queries.telemetry(max_records=bad)

    def test_investigation_view_keeps_observation_and_evidence_distinct(self):
        view = self.queries.investigation()
        self.assertEqual(["observation:a-history", "observation:b-fork",
                          "observation:c-rollout", "observation:d-features"],
                         [item["ref"]["ref_id"] for item in view["observations"]])
        self.assertTrue(all(item["record"]["summary"] for item in view["observations"]))
        self.assertEqual((), view["evidence_links"])
        self.assertEqual((), view["hypotheses"])
        self.assertEqual(self.session.store.revision(), view["state_revision"])

    def test_branch_tree_view(self):
        view = self.queries.branch_tree()
        nodes = {node["handle"]: node for node in view["nodes"]}
        self.assertEqual(("SNAPSHOT", "baseline", "RELEASED"),  # reference env closed
                         (nodes["snapshot-0001"]["kind"], nodes["snapshot-0001"]["lineage"],
                          nodes["snapshot-0001"]["status"]))
        self.assertEqual(("BRANCH", "snapshot-0001", "baseline", "RELEASED"),
                         (nodes["branch-0002"]["kind"], nodes["branch-0002"]["parent"],
                          nodes["branch-0002"]["lineage"], nodes["branch-0002"]["status"]))
        self.assertEqual("RELEASED", view["root"]["status"])  # resources cleaned up
        self.assert_no_paths(view)

    def test_budget_view_matches_runtime_usage(self):
        view = self.queries.budget()
        usage = self.outcome.runtime_result["budget_usage"]
        self.assertEqual({key: value for key, value in usage.items() if value},
                         {key: value for key, value in view["usage"].items() if value})
        self.assertEqual(["a-history", "b-fork", "c-rollout", "d-features"],
                         [item["request_id"] for item in view["reservations"]])
        self.assertEqual(12, view["limits"]["max_model_calls"])

    def test_event_view_preserves_source_identity_and_order(self):
        events = self.queries.events()["events"]
        self.assertEqual({"application_lifecycle", "runtime_trace", "lab_run_log"},
                         {event["source"] for event in events})
        for event in events:
            for key in ("source", "source_sequence", "source_event_id", "type", "position"):
                self.assertIn(key, event)
            self.assertNotIn("payload", event)
        trace = [event["source_event_id"] for event in events
                 if event["source"] == "runtime_trace"]
        self.assertEqual([event["event_id"] for event in self.session.trace.read_events()],
                         trace)
        types = [event["type"] for event in events]
        self.assertEqual(["RUN_CREATED", "RUN_READY", "RCA_STATE_INITIALIZED", "RUN_STARTED"],
                         types[:4])
        self.assertEqual("runtime_trace", events[4]["source"])  # started before any trace
        self.assertEqual("RUN_COMPLETED", types[-1])
        for index, event in enumerate(events):
            if event["type"] == "RCA_STATE_UPDATE_ACCEPTED":
                anchor = events[index - 1]
                self.assertEqual(("runtime_trace", "ACCEPTED"),
                                 (anchor["source"], anchor["status"]))
        evaluator = self.evaluator.events()["events"]
        self.assertTrue(all("payload" in event for event in evaluator))

    def test_artifact_access_requires_exact_visible_refs(self):
        listed = self.queries.artifacts()["artifacts"]
        self.assertEqual(["HistoryWindowArtifact", "RolloutTelemetryArtifact"],
                         [ref["kind"] for ref in listed])
        ref = InformationRef(**listed[0])
        content = self.queries.get_artifact(ref)
        self.assertEqual(listed[0], content["ref"])
        self.assertTrue(content["content"])
        self.assert_no_paths(content)
        artifact_path = str(self.session.directory / "lab-artifacts" / f"{ref.ref_id}.jsonl")
        for bad in (listed[0], ref.ref_id, artifact_path, replace(ref, checksum="0" * 64),
                    replace(ref, ref_id="tep-artifact-999999"),
                    replace(ref, visibility=Visibility.EVALUATOR)):
            with self.subTest(bad=bad), self.assertRaises(UnknownArtifact):
                self.queries.get_artifact(bad)
        projection_ref = InformationRef(**next(
            event["context_projection_ref"] for event in self.session.trace.read_events()
            if event["type"] == "MODEL_TURN"))
        with self.assertRaises(UnknownArtifact):  # INTERNAL runtime artifact: not AGENT
            self.queries.get_artifact(projection_ref)
        projection = self.evaluator.get_artifact(projection_ref)
        self.assertEqual("task-run-1", projection["content"]["task_id"])

    def test_model_context_never_receives_registry_or_evaluator_material(self):
        directory = self.session.directory / "trace" / "projections"
        projections = [json.loads(path.read_bytes()) for path in directory.iterdir()]
        self.assertTrue(projections)
        for projection in projections:
            self.assertEqual({"task_state", "runtime_feedback"}, set(projection["content"]))
            text = canonical_json(projection)
            for absent in ("fixtures/", "tep_process_graph", PROCESS_GRAPH_SOURCE_ID,
                           EVALUATOR_BINDINGS_SOURCE_ID, CASE_SOURCE_ID, CASE_ID,
                           self.prepared.canonical_context_sources[0].content_checksum):
                self.assertNotIn(absent, text)
            assert_blind(self, projection)
        for event in self.session.trace.read_events():
            if event["type"] == "TOOL_RESULT":
                assert_blind(self, event["output_summary"])

    def test_post_run_inspection_from_durable_sources(self):
        fresh = self.case.make_manager()
        info = fresh.get("run-1")
        self.assertEqual((RunStatus.COMPLETED, self.ready_checksum),
                         (info.status, info.manifest_checksum))
        self.assertEqual(self.outcome, info.outcome)
        self.assertEqual(self.prepared, fresh.manifest("run-1"))
        queries = fresh.queries("run-1")
        self.assertEqual(self.queries.investigation(), queries.investigation())
        self.assertEqual(self.queries.events(), queries.events())
        self.assertEqual(self.queries.budget(), queries.budget())
        self.assertEqual(self.queries.process_graph(), queries.process_graph())
        self.assertEqual(self.queries.artifacts(), queries.artifacts())
        with self.assertRaises(ViewUnavailable):
            queries.telemetry()
        with self.assertRaises(LifecycleError):
            fresh.start("run-1")

    def test_post_run_source_mutation_is_detected(self):
        mutated = self.case.root / "mutated-lab-repo"
        shutil.copytree(self.case.case_repo, mutated)
        (mutated / CASE_PATH).write_text('{"case_id": "rewritten"}', encoding="utf-8")
        queries = self.case.make_manager(case_repo=mutated).queries(
            "run-1", ProjectionScope.EVALUATOR)
        with self.assertRaises(ContextSourceError):
            queries.context_inventory()

    def test_manifest_tampering_is_detected(self):
        fresh_root = self.case.root / "tamper"
        shutil.copytree(self.case.root / "runs", fresh_root)
        path = fresh_root / "run-1" / "prepare-0001" / "manifest.json"
        record = json.loads(path.read_bytes())
        record["model"]["model_version"] = "v2"
        path.write_bytes(lab_canonical_json(record))
        tampered = RunManager(fresh_root, revisions=REVISIONS, dependency_pins=PINS)
        with self.assertRaises(LifecycleError):
            tampered.manifest("run-1")


if __name__ == "__main__":
    unittest.main()
