import unittest
from dataclasses import FrozenInstanceError, replace
from tempfile import TemporaryDirectory

from industrial_agent_runtime import InformationRef, ModelStateUpdateProposal, StateDelta, Visibility, to_jsonable
from tep_agent_lab.persistence import RunLog

from tep_agent_lab.experiments import (
    DuplicateExperiment, Feature, MatchStatus, Prediction,
    canonical_experiment_key, evaluate_prediction, reject_exact_duplicate,
    EvidenceRelation, ExperimentInterpretation, ExperimentProposal, ExperimentResult,
    ExperimentRunSpec, ExperimentType, Hypothesis, HypothesisEvidenceLink,
    HypothesisStatus, HypothesisType, PredictionEvaluation, interpretation_to_deltas,
)


class PredictionTests(unittest.TestCase):
    def prediction(self, **changes):
        fields = dict(prediction_id="p1", hypothesis_ref="h1", variable_ref="v1",
                      feature=Feature.PEAK, window_or_horizon=30,
                      expected_value_or_range=(124.0, 128.0), tolerance=0.25)
        return Prediction(**(fields | changes))

    def test_range_tolerance_and_distance(self):
        prediction = self.prediction()
        for value in (124, 126, 128.25):
            self.assertEqual(MatchStatus.MATCH, evaluate_prediction(prediction, value).match_status)
        result = evaluate_prediction(prediction, 129)
        self.assertEqual(MatchStatus.CONTRADICT, result.match_status)
        self.assertEqual(1, result.metric_distance)

    def test_competing_predictions_remain_separate(self):
        first = self.prediction(feature=Feature.DELTA, expected_value_or_range=4)
        second = replace(first, prediction_id="p2", hypothesis_ref="h2", expected_value_or_range=-4)
        self.assertEqual(MatchStatus.MATCH, evaluate_prediction(first, 4).match_status)
        self.assertEqual(MatchStatus.CONTRADICT, evaluate_prediction(second, 4).match_status)

    def test_decimal_tolerance_boundary_and_mutable_conditions(self):
        prediction = self.prediction(expected_value_or_range=128.0, tolerance=0.3)
        self.assertEqual(MatchStatus.MATCH, evaluate_prediction(prediction, 128.3).match_status)
        self.assertEqual(MatchStatus.MATCH, evaluate_prediction(prediction, 127.7).match_status)
        with self.assertRaises(ValueError):
            self.prediction(conditions=[{"threshold": [1]}])
        with self.assertRaises(ValueError):
            self.prediction(metric_ref={"id": "metric"})

    def test_categorical_direction_and_event(self):
        direction = self.prediction(feature=Feature.DIRECTION, expected_value_or_range="INCREASE")
        self.assertEqual(MatchStatus.MATCH, evaluate_prediction(direction, "INCREASE").match_status)
        self.assertEqual(MatchStatus.INCONCLUSIVE, evaluate_prediction(direction, "UNKNOWN").match_status)
        event = self.prediction(feature=Feature.EVENT_OR_SHUTDOWN, expected_value_or_range=True)
        self.assertEqual(MatchStatus.CONTRADICT, evaluate_prediction(event, False).match_status)
        self.assertEqual(MatchStatus.INCONCLUSIVE, evaluate_prediction(event, 1).match_status)

    def test_missing_and_nonfinite_are_not_fabricated(self):
        for value in (None, float("nan"), float("inf"), "124", True):
            self.assertEqual(MatchStatus.INCONCLUSIVE,
                             evaluate_prediction(self.prediction(), value).match_status)

    def test_unsupported_and_qualitative_are_unscored(self):
        self.assertEqual(MatchStatus.UNSCORED,
                         evaluate_prediction(self.prediction(), 125, supported=False).match_status)
        qualitative = self.prediction(feature=Feature.QUALITATIVE_UNSCORED,
                                      expected_value_or_range="complex coupled effect")
        self.assertEqual(MatchStatus.UNSCORED, evaluate_prediction(qualitative, None).match_status)

    def test_invalid_prediction_rejected(self):
        for changes in ({"tolerance": -1}, {"window_or_horizon": 0},
                        {"expected_value_or_range": (4, 1)},
                        {"expected_value_or_range": float("nan")},
                        {"feature": "MADE_UP"}, {"expected_value_or_range": True}):
            with self.assertRaises(ValueError):
                self.prediction(**changes)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.content = dict(parent_state_content_checksum="abc123",
                            resolved_interventions=[{"target": "opaque", "value": 1}],
                            tool_config_versions={"tool": "v1", "config": "v2"},
                            horizon=30, seed_policy={"seeds": [1, 2]},
                            metric_scorer_versions={"metric": "v1"}, preprocessing=None)

    def test_new_snapshot_id_cannot_evade_dedup(self):
        # Two different snapshot refs resolve to the same content before hashing.
        snapshots = {"snapshot-a": "abc123", "snapshot-b": "abc123"}
        keys = [canonical_experiment_key(**(self.content | {"parent_state_content_checksum": content}))
                for content in snapshots.values()]
        self.assertEqual(keys[0], keys[1])
        with self.assertRaises(DuplicateExperiment):
            reject_exact_duplicate(keys[1], [keys[0]])

    def test_mapping_order_and_numeric_representations_normalize(self):
        first = canonical_experiment_key(**self.content)
        second = canonical_experiment_key(**(self.content | {
            "horizon": 30.0, "resolved_interventions": [{"value": 1.0, "target": "opaque"}],
            "tool_config_versions": {"config": "v2", "tool": "v1"}}))
        self.assertEqual(first, second)

    def test_all_semantic_inputs_change_key(self):
        first = canonical_experiment_key(**self.content)
        changes = {"parent_state_content_checksum": "changed", "resolved_interventions": [],
                   "tool_config_versions": {"tool": "v2"}, "horizon": 31,
                   "seed_policy": {"seeds": [2, 1]}, "metric_scorer_versions": {"metric": "v2"},
                   "preprocessing": {"filter": "v1"}}
        for key, value in changes.items():
            with self.subTest(field=key):
                other = canonical_experiment_key(**(self.content | {key: value}))
                self.assertNotEqual(first, other)
                reject_exact_duplicate(other, [first])

    def test_invalid_identity_rejected(self):
        for changes in ({"horizon": float("nan")}, {"horizon": 0},
                        {"parent_state_content_checksum": ""}, {"metric_scorer_versions": {}},
                        {"seed_policy": {"seed": float("inf")}}):
            with self.assertRaises(ValueError):
                canonical_experiment_key(**(self.content | changes))


def ref(name, *, visibility=Visibility.AGENT):
    return InformationRef(name, "fixture", "tep-agent-lab", "v1", visibility, "2026-09-18")


class ExperimentContractTests(unittest.TestCase):
    def hypothesis(self, name="h1", **changes):
        content = dict(hypothesis_id=name, investigation_id="i1", claim="Testable candidate",
                       hypothesis_type=HypothesisType.ROOT_CAUSE, scope_refs=(ref("scope"),),
                       status=HypothesisStatus.PROPOSED, supporting_evidence_link_refs=(),
                       contradicting_evidence_link_refs=(), experiment_refs=(), assumptions=(),
                       falsification_prediction_refs=(ref("p1"),), created_by="MODEL",
                       created_at="2026-09-18", last_updated_revision=7)
        return Hypothesis(**(content | changes))

    def proposal(self, **changes):
        content = dict(experiment_id="e1", investigation_id="i1", goal="Distinguish candidates",
                       hypothesis_refs=(ref("h1"), ref("h2")),
                       experiment_type=ExperimentType.COUNTERFACTUAL_ROLLOUT,
                       rationale="Opposed predictions", discriminating_question="Which direction?",
                       prediction_refs=(ref("p1"), ref("p2")),
                       scenario_or_intervention={"fixture": [1]}, required_input_refs=(ref("state"),),
                       requested_tools=("isolated_rollout",), seed_policy={"seeds": [1]},
                       horizon=30, metrics=("delta",), budget_request={"rollouts": 1},
                       safety_constraints=("isolated-only",), status="PLANNED")
        return ExperimentProposal(**(content | changes))

    def run_spec(self, **changes):
        identity = dict(parent_state_content_checksum="abc123",
                        resolved_interventions=[{"target": "opaque", "value": 1}],
                        tool_config_versions={"environment": "commit", "tool": "v1", "policy": "v1"},
                        horizon=30, seed_policy={"seeds": [1]},
                        metric_scorer_versions={"metric": "v1", "scorer": "v1"}, preprocessing=None)
        content = dict(run_spec_id="rs1", experiment_id="e1",
                       parent_state_content_checksum=identity["parent_state_content_checksum"],
                       branch_or_snapshot_ref=ref("snapshot-a"),
                       tool_config_versions=identity["tool_config_versions"],
                       resolved_interventions=identity["resolved_interventions"],
                       seeds_or_seed_policy=identity["seed_policy"], horizon=identity["horizon"],
                       metric_scorer_versions=identity["metric_scorer_versions"],
                       resource_limits={"rollouts": 1},
                       canonical_experiment_key=canonical_experiment_key(**identity))
        return ExperimentRunSpec(**(content | changes))

    def result(self, **changes):
        content = dict(experiment_id="e1", run_spec_ref=ref("rs1"), status="FAILED",
                       metric_values={}, prediction_evaluations=(), observation_refs=(),
                       rollout_artifact_refs=(), cost_usage={"rollouts": 1},
                       started_at="2026-09-18T00:00:00Z", completed_at="2026-09-18T00:00:01Z",
                       failure_class="SIMULATION_FAILED")
        return ExperimentResult(**(content | changes))

    def interpretation(self, **changes):
        link = HypothesisEvidenceLink(hypothesis_ref=ref("h1"), observation_ref=ref("obs1"),
                                      relation=EvidenceRelation.SUPPORT,
                                      reason_summary="Direction matched", producer="MODEL")
        question = dict(question_id="q1", question="Alternative cause?", why_it_matters="Ambiguity",
                        related_hypotheses=[ref("h1")], resolvable_by="EXPERIMENT",
                        priority=1, status="OPEN")
        content = dict(interpretation_id="int1", experiment_ref=ref("e1"),
                       proposed_evidence_links=(link,), hypothesis_updates=(self.hypothesis(),),
                       conclusion_summary="Candidate remains plausible", residual_uncertainty="Ambiguous",
                       next_questions=(question,))
        return ExperimentInterpretation(**(content | changes))

    def test_competing_hypotheses_predictions_and_plan_are_data(self):
        first, second = self.hypothesis(), self.hypothesis("h2")
        predictions = [Prediction("p1", first.hypothesis_id, "v1", Feature.DELTA, 30, 4),
                       Prediction("p2", second.hypothesis_id, "v1", Feature.DELTA, 30, -4)]
        proposal = self.proposal()
        deltas = tuple(StateDelta("ADD_HYPOTHESIS", h.hypothesis_id, h, "MODEL", 7)
                       for h in (first, second)) + (
                           StateDelta("PLAN_EXPERIMENT", proposal.experiment_id, proposal, "MODEL", 7),)
        update = ModelStateUpdateProposal("proposal", 7, deltas)
        self.assertEqual(["ADD_HYPOTHESIS", "ADD_HYPOTHESIS", "PLAN_EXPERIMENT"],
                         [delta.operation for delta in update.deltas])
        self.assertEqual([MatchStatus.MATCH, MatchStatus.CONTRADICT],
                         [evaluate_prediction(p, 4).match_status for p in predictions])
        self.assertEqual("PLANNED", update.deltas[-1].value_or_ref["status"])

    def test_proposal_and_run_spec_are_deeply_immutable(self):
        scenario = {"values": [1]}
        proposal = self.proposal(scenario_or_intervention=scenario)
        scenario["values"].append(2)
        self.assertEqual((1,), proposal.scenario_or_intervention["values"])
        with self.assertRaises(TypeError):
            proposal.budget_request["rollouts"] = 2
        frozen = self.run_spec()
        with self.assertRaises(TypeError):
            frozen.resolved_interventions[0]["value"] = 2
        with self.assertRaises(FrozenInstanceError):
            frozen.horizon = 99

    def test_run_spec_recomputes_key_and_preserves_snapshot_independence(self):
        first = self.run_spec()
        second = self.run_spec(branch_or_snapshot_ref=ref("new-identical-snapshot"))
        self.assertEqual(first.canonical_experiment_key, second.canonical_experiment_key)
        with self.assertRaises(DuplicateExperiment):
            reject_exact_duplicate(second.canonical_experiment_key, (first.canonical_experiment_key,))
        with self.assertRaises(ValueError):
            self.run_spec(horizon=31)
        with self.assertRaises(ValueError):
            self.run_spec(canonical_experiment_key="fabricated")
        self.assertEqual(first.canonical_experiment_key, replace(first).canonical_experiment_key)

    def test_failed_result_is_serializable_and_retained_in_history(self):
        failed = self.result()
        with TemporaryDirectory() as directory:
            log = RunLog(directory, {"run_id": "r1"})
            checksum = log.put_artifact(to_jsonable(failed))
            log.append("EXPERIMENT_RESULT", {"artifact_checksum": checksum, "status": failed.status})
            reopened = RunLog(directory)
            self.assertEqual("FAILED", reopened.events()[0]["payload"]["status"])
            self.assertEqual("SIMULATION_FAILED", reopened.read_artifact(checksum)["failure_class"])
            self.assertEqual({"rollouts": 1}, reopened.read_artifact(checksum)["cost_usage"])

    def test_deterministic_result_remains_separate_from_interpretation(self):
        evaluation = PredictionEvaluation("p1", 4, MatchStatus.MATCH, 0)
        result = self.result(status="OK", failure_class=None, metric_values={"delta": 4},
                             prediction_evaluations=(evaluation,), observation_refs=(ref("obs1"),))
        interpreted = self.interpretation()
        self.assertNotIn("metric_values", to_jsonable(interpreted))
        self.assertEqual(4, result.metric_values["delta"])
        self.assertEqual((evaluation,), result.prediction_evaluations)

    def test_interpretation_mapping_has_exact_targets_revision_and_producer(self):
        interpretation = self.interpretation()
        deltas = interpretation_to_deltas(interpretation, base_revision=7)
        self.assertEqual(["ADD_EVIDENCE_LINK", "UPDATE_HYPOTHESIS", "ADD_OPEN_QUESTION",
                          "UPDATE_WORKING_EXPLANATION", "ADD_EXPERIMENT_INTERPRETATION"],
                         [delta.operation for delta in deltas])
        self.assertEqual(["h1", "h1", "q1", "current_best_explanation", "int1"],
                         [delta.target_ref_or_path for delta in deltas])
        self.assertTrue(all(delta.producer == "MODEL" and delta.proposed_base_revision == 7
                            for delta in deltas))
        self.assertEqual({"leading_hypothesis_ref", "current_rank_or_score_summary",
                          "key_evidence_link_refs", "key_counterevidence_link_refs",
                          "remaining_uncertainties"}, set(deltas[3].value_or_ref))
        self.assertEqual("Candidate remains plausible",
                         deltas[3].value_or_ref["current_rank_or_score_summary"])
        self.assertEqual(("Ambiguous",),
                         deltas[3].value_or_ref["remaining_uncertainties"])
        self.assertNotIn("last_updated_revision", deltas[3].value_or_ref)
        with self.assertRaises(TypeError):
            deltas[0].value_or_ref["relation"] = "CONTRADICT"
        # Pure mapping preserves the original revision even if called much later.
        self.assertTrue(all(d.proposed_base_revision == 7 for d in
                            interpretation_to_deltas(interpretation, base_revision=7)))

    def test_interpretation_copy_preserves_frozen_questions(self):
        interpretation = self.interpretation()
        copied = replace(interpretation)
        self.assertEqual(to_jsonable(interpretation), to_jsonable(copied))
        with self.assertRaises(TypeError):
            copied.next_questions[0]["priority"] = 9

    def test_mapping_cannot_impersonate_ingestion_or_change_revision_shape(self):
        for revision in (None, True, -1, {}, ""):
            with self.assertRaises(ValueError):
                interpretation_to_deltas(self.interpretation(), base_revision=revision)
        with self.assertRaises(ValueError):
            interpretation_to_deltas(self.interpretation(), base_revision=7, producer="RUNTIME")
        interpretation = self.interpretation()
        forged = replace(interpretation.proposed_evidence_links[0], producer="RESULT_INGESTION")
        with self.assertRaises(ValueError):
            interpretation_to_deltas(replace(interpretation, proposed_evidence_links=(forged,)),
                                     base_revision=7)
        self.assertTrue(all(d.proposed_base_revision == "revision-7" for d in
                            interpretation_to_deltas(interpretation, base_revision="revision-7")))

    def test_invalid_status_refs_scalars_and_nested_hidden_truth_are_rejected(self):
        for changes in ({"status": "UNKNOWN"}, {"hypothesis_type": "ROLE"},
                        {"prior_weight": float("nan")}, {"last_updated_revision": True},
                        {"scope_refs": ("untyped",)}, {"assumptions": ({"mutable": []},)}):
            with self.assertRaises((ValueError, TypeError)):
                self.hypothesis(**changes)
        hidden = ref("hidden", visibility=Visibility.EVALUATOR)
        for changes in ({"hypothesis_refs": (hidden,)}, {"scenario_or_intervention": {"nested": hidden}},
                        {"horizon": 0}, {"budget_request": {"rollouts": -1}}, {"status": ""}):
            with self.assertRaises((ValueError, TypeError)):
                self.proposal(**changes)
        for changes in ({"metric_values": {"x": float("inf")}}, {"cost_usage": {"rollouts": True}},
                        {"prediction_evaluations": ({"mutable": []},)}, {"failure_class": []}):
            with self.assertRaises((ValueError, TypeError)):
                self.result(**changes)
        with self.assertRaises(ValueError):
            self.run_spec(tool_config_versions={"tool": []})
        with self.assertRaises(ValueError):
            self.interpretation(next_questions=({"question_id": "incomplete"},))

    def test_prediction_evaluation_rejects_mutable_and_nonfinite_result_fields(self):
        for observed in ({"x": 1}, [], float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                PredictionEvaluation("p1", observed, MatchStatus.MATCH)
        with self.assertRaises(ValueError):
            PredictionEvaluation("p1", 1, "invented")
        with self.assertRaises(ValueError):
            PredictionEvaluation("p1", 1, MatchStatus.MATCH, -1)


if __name__ == "__main__":
    unittest.main()
