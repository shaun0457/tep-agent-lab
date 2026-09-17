import unittest
from dataclasses import replace

from tep_agent_lab.experiments import (
    DuplicateExperiment, Feature, MatchStatus, Prediction,
    canonical_experiment_key, evaluate_prediction, reject_exact_duplicate,
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


if __name__ == "__main__":
    unittest.main()
