from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from industrial_agent_runtime import (
    Action, Budget, Coordinator, FakeProvider, FinishProposal, GateDecision,
    InformationRef, ModelStateUpdateProposal, ModelTurn, SideEffectClass,
    StateDelta, SubtaskResult, Task, TaskStatus, ToolCallRequest, ToolResult,
    ToolSpec, TraceRecorder, Visibility, WorkBatch, WorkItem, to_jsonable,
)

from tep_agent_lab.experiments import (
    ExperimentProposal, ExperimentResult, ExperimentType, Hypothesis,
    HypothesisEvidenceLink, HypothesisStatus, HypothesisType, EvidenceRelation,
)
from tep_agent_lab.investigation import (
    ObservationRecord, RcaResultIngestor, RcaState, RcaStateStore,
    project_rca_state, readiness_deficiencies,
)
from tep_agent_lab.persistence import RunLog, canonical_json


NOW = "2026-09-18T00:00:00Z"


def ref(name, kind="fixture", *, visibility=Visibility.AGENT, version="v1"):
    return InformationRef(name, kind, "fixture", version, visibility, NOW)


class Registry:
    def __init__(self, *refs):
        self.refs = {to_jsonable(item)["ref_id"] + ":" + item.kind: item for item in refs}

    def add(self, *refs):
        for item in refs:
            self.refs[to_jsonable(item)["ref_id"] + ":" + item.kind] = item

    def __call__(self, requested):
        return self.refs.get(requested.ref_id + ":" + requested.kind)


def hypothesis(name="h1", *, revision=0):
    return Hypothesis(
        hypothesis_id=name, investigation_id="i1", claim=f"Candidate {name}",
        hypothesis_type=HypothesisType.ROOT_CAUSE, scope_refs=(ref("scope"),),
        status=HypothesisStatus.PROPOSED, supporting_evidence_link_refs=(),
        contradicting_evidence_link_refs=(), experiment_refs=(), assumptions=(),
        falsification_prediction_refs=(ref("prediction"),), created_by="MODEL",
        created_at=NOW, last_updated_revision=revision)


def question(name="q1"):
    return {"question_id": name, "question": "Which candidate?",
            "why_it_matters": "Distinguishes root causes",
            "related_hypotheses": [], "resolvable_by": "TOOL",
            "priority": 1, "status": "OPEN"}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.incident = ref("incident", "Incident")
        self.registry = Registry(ref("scope"), ref("prediction"))
        self.log = RunLog(self.directory.name, {"run_id": "r1", "investigation_id": "i1"})
        self.store = RcaStateStore(
            RcaState(investigation_id="i1", goal="Find root cause",
                     incident_ref=self.incident), self.log, resolve=self.registry,
            task_id="task-1")

    def apply(self, *deltas):
        revision = self.store.revision()
        bound = tuple(replace(delta, proposed_base_revision=revision) for delta in deltas)
        return self.store.apply_batch(bound, revision)

    def test_blind_initial_projection_and_hidden_truth_fail_closed(self):
        projection = self.store.project({})
        self.assertEqual(0, projection.base_revision)
        self.assertEqual("task-1", projection.task_id)
        self.assertEqual(["incident"], [item.ref_id for item in projection.included_refs])
        hidden = ref("truth", "GroundTruth", visibility=Visibility.EVALUATOR)
        with TemporaryDirectory() as directory:
            log = RunLog(directory, {"run_id": "hidden"})
            with self.assertRaises(ValueError):
                RcaStateStore(RcaState(investigation_id="i1", goal="g",
                                       incident_ref=hidden), log)
        with TemporaryDirectory() as directory:
            wrong_log = RunLog(directory, {"run_id": "wrong", "investigation_id": "other"})
            with self.assertRaises(ValueError):
                RcaStateStore(RcaState(investigation_id="i1", goal="g",
                                       incident_ref=self.incident), wrong_log)
        self.assertEqual(
            projection,
            project_rca_state(self.store.state, {}, task_id="task-1",
                              validate_ref=self.store.resolve))

    def test_agent_visible_observation_rejects_nested_hidden_ref(self):
        hidden = ref("internal", visibility=Visibility.INTERNAL)
        with self.assertRaises(ValueError):
            ObservationRecord(
                observation_id="o", producer_request_ref=ref("request"),
                tool_or_service_ref=ref("tool"), summary={"hidden": hidden},
                artifact_refs=(), information_refs=(), provenance={},
                visibility=Visibility.AGENT, created_at=NOW)

    def test_valid_model_batch_is_atomic_and_producer_allowlisted(self):
        explanation = {
            "leading_hypothesis_ref": None,
            "current_rank_or_score_summary": "One active candidate",
            "key_evidence_link_refs": [], "key_counterevidence_link_refs": [],
            "remaining_uncertainties": ["No observation yet"],
            "last_updated_revision": 0,
        }
        self.apply(
            StateDelta("ADD_HYPOTHESIS", "h1", hypothesis(), "MODEL"),
            StateDelta("ADD_OPEN_QUESTION", "q1", question(), "MODEL"),
            StateDelta("UPDATE_WORKING_EXPLANATION", "current_best_explanation",
                       explanation, "MODEL"))
        self.assertEqual(1, self.store.revision())
        self.assertEqual(["h1"], [item.ref_id for item in self.store.state.hypothesis_refs])
        self.assertEqual("q1", self.store.state.open_questions[0].question_id)
        self.assertEqual("No observation yet", self.store.state.uncertainty_summary)
        with self.assertRaises(ValueError):
            self.apply(StateDelta("SET_GENERIC_STATUS", "generic_status", "DONE", "MODEL"))

    def test_invalid_member_rejects_whole_batch_and_is_logged(self):
        with self.assertRaises(ValueError):
            self.apply(
                StateDelta("ADD_HYPOTHESIS", "h1", hypothesis(), "MODEL"),
                StateDelta("REGISTER_OBSERVATION", "forged", {}, "MODEL"))
        self.assertEqual(0, self.store.revision())
        self.assertEqual((), self.store.state.hypothesis_refs)
        self.assertEqual("RCA_STATE_UPDATE_REJECTED", self.log.events()[-1]["type"])
        with self.assertRaises(ValueError):
            self.store.apply_batch((StateDelta("ADD_OPEN_QUESTION", "q", question("q"),
                                               "MODEL", 0),), 99)
        self.assertEqual(0, self.store.revision())

    def test_tool_result_registers_one_observation_not_evidence(self):
        artifact = ref("plot", "Artifact")
        returned = ref("measurement", "Measurement")
        self.registry.add(artifact, returned)
        result = ToolResult(
            "request-1", "SUCCESS", {"value": 42}, {},
            {"tool_name": "read_signal", "tool_version": "v2", "created_at": NOW},
            artifact_refs=(artifact,), information_refs=(returned,))
        deltas = RcaResultIngestor().derive_deltas(result)
        self.assertEqual(1, sum(delta.operation == "REGISTER_OBSERVATION" for delta in deltas))
        self.apply(*deltas)
        self.assertEqual(1, len(self.store.state.observation_refs))
        self.assertEqual(0, len(self.store.state.evidence_link_refs))
        self.assertEqual((artifact,), self.store.state.artifact_refs)

    def test_observation_becomes_evidence_only_through_later_model_link(self):
        self.apply(StateDelta("ADD_HYPOTHESIS", "h1", hypothesis(), "MODEL"))
        result = ToolResult(
            "request-1", "SUCCESS", {"value": 42}, {},
            {"tool_name": "read_signal", "tool_version": "v2", "created_at": NOW})
        self.apply(*RcaResultIngestor().derive_deltas(result))
        h_ref = self.store.state.hypothesis_refs[0]
        o_ref = self.store.state.observation_refs[0]
        link = HypothesisEvidenceLink(
            hypothesis_ref=h_ref, observation_ref=o_ref,
            relation=EvidenceRelation.SUPPORT, reason_summary="Matches prediction",
            producer="MODEL")
        self.apply(StateDelta("ADD_EVIDENCE_LINK", "h1", link, "MODEL"))
        self.assertEqual(1, len(self.store.state.observation_refs))
        self.assertEqual(1, len(self.store.state.evidence_link_refs))

    def test_plan_and_deterministically_complete_experiment(self):
        self.apply(StateDelta("ADD_HYPOTHESIS", "h1", hypothesis(), "MODEL"))
        h_ref = self.store.state.hypothesis_refs[0]
        run_spec = ref("run-spec", "ExperimentRunSpec")
        self.registry.add(run_spec)
        proposal = ExperimentProposal(
            experiment_id="e1", investigation_id="i1", goal="Distinguish",
            hypothesis_refs=(h_ref,), experiment_type=ExperimentType.SIGNAL_ANALYSIS,
            rationale="Measure response", discriminating_question="Direction?",
            prediction_refs=(ref("prediction"),), scenario_or_intervention={},
            required_input_refs=(), requested_tools=("signal",), seed_policy={},
            horizon=10, metrics=("delta",), budget_request={"trials": 1},
            safety_constraints=("read-only",), status="PLANNED")
        self.apply(StateDelta("PLAN_EXPERIMENT", "e1", proposal, "MODEL"))
        result = ExperimentResult(
            experiment_id="e1", run_spec_ref=run_spec, status="SUCCESS",
            metric_values={"delta": 2}, prediction_evaluations=(), observation_refs=(),
            rollout_artifact_refs=(), cost_usage={"trials": 1},
            started_at=NOW, completed_at=NOW)
        self.apply(*RcaResultIngestor().derive_deltas(result))
        self.assertEqual(0, len(self.store.state.planned_experiment_refs))
        self.assertEqual(1, len(self.store.state.completed_experiment_refs))
        self.assertEqual(1, len(self.store.state.observation_refs))

    def test_experiment_result_requires_registered_run_spec(self):
        self.apply(StateDelta("ADD_HYPOTHESIS", "h1", hypothesis(), "MODEL"))
        h_ref = self.store.state.hypothesis_refs[0]
        proposal = ExperimentProposal(
            experiment_id="e1", investigation_id="i1", goal="Distinguish",
            hypothesis_refs=(h_ref,), experiment_type=ExperimentType.SIGNAL_ANALYSIS,
            rationale="Measure", discriminating_question="Direction?",
            prediction_refs=(ref("prediction"),), scenario_or_intervention={},
            required_input_refs=(), requested_tools=("signal",), seed_policy={},
            horizon=10, metrics=("delta",), budget_request={},
            safety_constraints=(), status="PLANNED")
        self.apply(StateDelta("PLAN_EXPERIMENT", "e1", proposal, "MODEL"))
        result = ExperimentResult(
            experiment_id="e1", run_spec_ref=ref("missing-run", "ExperimentRunSpec"),
            status="SUCCESS", metric_values={}, prediction_evaluations=(),
            observation_refs=(), rollout_artifact_refs=(), cost_usage={},
            started_at=NOW, completed_at=NOW)
        with self.assertRaises(ValueError):
            self.apply(*RcaResultIngestor().derive_deltas(result))
        self.assertEqual((), self.store.state.completed_experiment_refs)
        self.assertEqual((), self.store.state.observation_refs)

    def test_subtask_keeps_compact_result_metadata_without_claim_transcript(self):
        child_observation, artifact = ref("child-obs", "ObservationRecord"), ref("child-art", "Artifact")
        self.registry.add(child_observation, artifact)
        trace = ref("child-trace", "RunTrace", visibility=Visibility.INTERNAL)
        result = SubtaskResult(
            "child", "DONE", {"large_claim": "must not be copied"},
            (child_observation,), (artifact,), (), {"model_calls": 1}, trace,
            {"confidence": 0.5})
        self.apply(*RcaResultIngestor().derive_deltas(result))
        self.assertEqual(["subtask-result:child"],
                         [item.ref_id for item in self.store.state.delegated_task_refs])
        snapshot = self.log.read_artifact(self.log.events()[-1]["payload"]["snapshot_checksum"])
        self.assertNotIn(b"large_claim", canonical_json(snapshot))

    def test_projection_is_bounded_deterministic_and_reconstructs_exactly(self):
        for name in ("a", "b"):
            result = ToolResult(
                name, "SUCCESS", {"value": name}, {},
                {"tool_name": "read", "tool_version": "v1", "created_at": NOW})
            self.apply(*RcaResultIngestor().derive_deltas(result))
        first = self.store.project({"max_observations": 1, "max_open_questions": 0})
        second = self.store.project({"max_observations": 1, "max_open_questions": 0})
        self.assertEqual(first, second)
        self.assertEqual(["observation:b"],
                         [item["ref_id"] for item in first.content["observation_refs"]])
        restored = RcaStateStore.reconstruct(self.log, resolve=self.registry)
        self.assertEqual(to_jsonable(self.store.state), to_jsonable(restored.state))
        self.assertEqual(first, restored.project({"max_observations": 1,
                                                  "max_open_questions": 0}))

    def test_readiness_rejects_missing_evidence_artifact_and_conclusion(self):
        evidence, artifact = ref("missing-e", "HypothesisEvidenceLink"), ref("missing-a", "Artifact")
        self.registry.add(evidence, artifact)
        issues = readiness_deficiencies(
            self.store, required_evidence_refs=(evidence,), required_artifact_refs=(artifact,))
        self.assertEqual(("missing conclusion_ref",
                          "missing required evidence ref: missing-e",
                          "missing required artifact ref: missing-a"), issues)

    def test_runtime_status_operation_exists_but_readiness_has_no_side_effect(self):
        self.assertEqual(TaskStatus.RUNNING, self.store.status())
        readiness_deficiencies(self.store)
        self.assertEqual(TaskStatus.RUNNING, self.store.status())
        revision = self.store.revision()
        self.store.apply_batch((StateDelta("SET_GENERIC_STATUS", "generic_status",
                                           "READY", "RUNTIME", revision),), revision)
        self.assertEqual(TaskStatus.READY, self.store.status())


class CoordinatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        state_dir = Path(self.directory.name) / "state"
        trace_dir = Path(self.directory.name) / "trace"
        self.log = RunLog(state_dir, {"run_id": "coordinator", "investigation_id": "i1"})
        self.registry = Registry(ref("scope"), ref("prediction"))
        self.store = RcaStateStore(
            RcaState(investigation_id="i1", goal="g", incident_ref=ref("incident")),
            self.log, resolve=self.registry, task_id="task-1")
        self.trace = TraceRecorder(trace_dir)
        self.calls = []

    def _turn(self, action=Action.NONE, **payload):
        def build(projection, limits):
            return ModelTurn(
                f"turn-{limits['budget_usage']['model_calls']}",
                limits["context_projection_ref"], projection.base_revision,
                action, **payload)
        return build

    def test_stale_same_turn_suppression_and_lexical_current_revision_ingestion(self):
        parent = self

        class Gate:
            def validate_request(self, request, spec, task, expected_state_revision,
                                 budget_usage):
                return GateDecision(request.request_id, "ALLOW", "lab", "ok", "ok", "v1",
                                    expected_state_revision=expected_state_revision)

        class Executor:
            def execute(self, request, spec):
                parent.calls.append(request.request_id)
                return ToolResult(
                    request.request_id, "SUCCESS", {"request": request.request_id}, {},
                    {"tool_name": spec.name, "tool_version": "v1", "created_at": NOW})

        class Verifier:
            def verify_result(self, result, request, spec, deltas, expected_state_revision):
                return True

            def verify_finish(self, proposal, task, expected_state_revision):
                return True

        stale = ModelStateUpdateProposal(
            "stale", -1,
            (StateDelta("ADD_OPEN_QUESTION", "q", question("q"), "MODEL", -1),))
        stale_request = ToolCallRequest("never", "read", {})
        batch = WorkBatch(
            "wave", "two reads",
            (WorkItem("z", "TOOL", (), ToolCallRequest("z", "read", {})),
             WorkItem("a", "TOOL", (), ToolCallRequest("a", "read", {}))))
        provider = FakeProvider([
            self._turn(Action.TOOL_REQUEST, state_update=stale,
                       tool_request=stale_request),
            self._turn(Action.WORK_BATCH, work_batch=batch),
            self._turn(Action.FINISH_PROPOSAL,
                       finish_proposal=FinishProposal({"answer": "done"})),
        ])
        task = Task("task-1", "g", (), ("read",), Budget(5, 5, 0, 0, 10),
                    {"type": "object"})
        spec = ToolSpec("read", "read", {}, {}, SideEffectClass.READ)
        result = Coordinator(
            task, self.store, provider, self.trace, (spec,),
            model_metadata={"provider": "fake", "model": "scripted",
                            "model_version": "v1", "prompt_template_version": "v1"},
            gate=Gate(), executor=Executor(), verifier=Verifier(),
            ingestor=RcaResultIngestor(), clock=lambda: NOW).run()
        self.assertEqual(TaskStatus.DONE, result.status)
        self.assertEqual(["a", "z"], self.calls)
        accepted = [event for event in self.log.events()
                    if event["type"] == "RCA_STATE_UPDATE_ACCEPTED"]
        self.assertEqual([0, 1], [event["payload"]["expected_revision"] for event in accepted])
        self.assertEqual(["observation:a", "observation:z"],
                         [item.ref_id for item in self.store.state.observation_refs])
        self.assertNotIn("observation:never",
                         [item.ref_id for item in self.store.state.observation_refs])
        rejected = [event for event in self.trace.read_events()
                    if event["type"] == "STATE_UPDATE" and event["status"] == "DENIED"]
        self.assertEqual(1, len(rejected))


if __name__ == "__main__":
    unittest.main()
