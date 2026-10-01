"""C5 minimal Tool Bridge acceptance tests (docs/specs/tool-bridge-v0.md, C5 contract)."""

from dataclasses import replace
import inspect
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import numpy

from industrial_agent_runtime import (
    Action, Budget, Coordinator, FakeProvider, FinishProposal, GatePipeline, InformationRef,
    ModelTurn, ResultVerificationPipeline, SideEffectClass, StateDelta, Task, TaskStatus,
    ToolCallRequest, ToolResult, TraceRecorder, Visibility, checksum, to_jsonable,
)
from industrial_agent_runtime.gates import GateDenied, reconcile
from industrial_agent_runtime.schema import instance_errors, schema_errors

from tep_agent_lab import tool_bridge
from tep_agent_lab.experiments import Feature, MatchStatus, Prediction, evaluate_prediction
from tep_agent_lab.investigation import RcaResultIngestor, RcaState, RcaStateStore
from tep_agent_lab.persistence import RunLog
from tep_agent_lab.tep_world import ArtifactStore
from tep_agent_lab.tool_bridge import (
    ANALYSIS_POLICY_TAG, BRIDGE_VERSION, CURVE_KIND, AnalysisToolBridge, BridgedToolSurface,
    bridge_tool_version,
)
from tep_agent_lab.tool_surface import (BLIND_RCA_EXCLUDED_TOOLS, BlindRcaToolSurface,
                                        ResultInvariantError, simulation_quota)
from test_tool_surface import NOW, assert_blind, make_world, reference_fingerprint

BRIDGE_TOOLS = {"compute_response_features", "analyze_cross_correlation",
                "compare_trajectories"}


def hours(seconds: float) -> float:
    return seconds / 3600


def window(start_s: float, end_s: float) -> dict:
    return {"start_hours": hours(start_s), "end_hours": hours(end_s)}


class BridgeCase(unittest.TestCase):
    """Synthetic store artifacts with known values; no simulator involved."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ArtifactStore(directory.name)
        self.revision = ["reference-r1"]
        self.bridge = AnalysisToolBridge(self.store, reference_revision=lambda: self.revision[0],
                                         clock=lambda: NOW)
        self.specs = {spec.name: spec for spec in self.bridge.tool_specs()}
        self.sequence = 0
        self.requests = {}

    def history(self, columns: dict, *, interval=60, start=0, times=None):
        """Flat HistoryWindowArtifact rows, as the C4 surface writes them."""
        count = len(next(iter(columns.values())))
        stamps = times if times is not None else [start + i * interval for i in range(count)]
        rows = [{"simulation_time_hours": hours(stamps[i]),
                 **{name: values[i] for name, values in columns.items()}}
                for i in range(count)]
        return to_jsonable(self.store.put_records("HistoryWindowArtifact", rows, NOW))

    def request(self, name, arguments):
        self.sequence += 1
        return ToolCallRequest(f"req-{self.sequence}", name, arguments)

    def decide(self, name, arguments):
        return self.bridge.validate_request(self.request(name, arguments), self.specs[name],
                                            None, 0, {})

    def denied(self, name, arguments, code):
        decision = self.decide(name, arguments)
        self.assertEqual(("DENY", code), (decision.decision, decision.reason_code),
                         decision.reason)
        result = self.bridge.execute(self.request(name, arguments), self.specs[name])
        self.assertEqual(code, result.status)  # the executor re-validates and refuses
        self.assertEqual((), result.artifact_refs)
        return decision

    def call(self, name, arguments):
        """Lab consumer path: schema -> validate -> execute -> lab result invariants."""
        request = self.request(name, arguments)
        self.assertEqual([], instance_errors(request.arguments, self.specs[name].input_schema))
        decision = self.bridge.validate_request(request, self.specs[name], None, 0, {})
        self.assertEqual("ALLOW", decision.decision, decision.reason)
        result = self.bridge.execute(request, self.specs[name])
        self.assertEqual("SUCCESS", result.status, result.error)
        deltas = RcaResultIngestor().derive_deltas(result)
        self.bridge.check_result(result, request, self.specs[name], deltas)
        self.assertEqual([], instance_errors(result.structured_output,
                                             self.specs[name].output_schema))
        assert_blind(self, result)
        self.requests[result.request_id] = request
        return result


def features_of(result, variable):
    row = next(item for item in result.structured_output["variables"]
               if item["variable"] == variable)
    return {item["feature"]: to_jsonable(item) for item in row["features"]}


class ResponseFeatureTests(BridgeCase):
    def fixture(self):
        # baseline t=0..120 s at 1.0; analysis t=180..420 s
        return self.history({"XMEAS(1)": [1, 1, 1, 1, 3, 5, 5, 2],
                             "XMV(1)": [0, 0, 0, 0, 2, -2, 0, 0]})

    def test_features_have_known_deterministic_values(self):
        ref = self.fixture()
        arguments = {"trajectory_ref": ref, "variables": ["XMEAS(1)", "XMV(1)"],
                     "baseline_window": window(0, 120), "analysis_window": window(180, 420),
                     "features": [{"feature": "DELTA"},
                                  {"feature": "DIRECTION", "deadband": 0.5},
                                  {"feature": "PEAK"}, {"feature": "MINIMUM"},
                                  {"feature": "ONSET_TIME", "threshold": 1.5},
                                  {"feature": "STEADY_STATE_RANGE"},
                                  {"feature": "INTEGRATED_ERROR", "integrand": "SIGNED"}]}
        result = self.call("compute_response_features", arguments)
        found = features_of(result, "XMEAS(1)")
        self.assertEqual(1.0, found["DELTA"]["value"])                    # 2 - 1
        self.assertEqual("INCREASE", found["DIRECTION"]["value"])
        self.assertEqual((5.0, 120, hours(300)), (found["PEAK"]["value"],
                                                 found["PEAK"]["elapsed_seconds"],
                                                 found["PEAK"]["time_hours"]))  # earliest tie
        self.assertEqual((1.0, 0), (found["MINIMUM"]["value"],
                                    found["MINIMUM"]["elapsed_seconds"]))
        self.assertEqual((60, "s", "DEFINED"), (found["ONSET_TIME"]["value"],
                                                found["ONSET_TIME"]["value_unit"],
                                                found["ONSET_TIME"]["status"]))
        self.assertEqual(4.0, found["STEADY_STATE_RANGE"]["value"])
        self.assertEqual(630.0, found["INTEGRATED_ERROR"]["value"])       # trapezoid, unit*s
        self.assertTrue(found["INTEGRATED_ERROR"]["value_unit"].endswith("*s"))
        row = next(item for item in result.structured_output["variables"]
                   if item["variable"] == "XMEAS(1)")
        self.assertEqual(1.0, row["baseline_mean"])
        self.assertEqual(0.0, features_of(result, "XMV(1)")["INTEGRATED_ERROR"]["value"])
        # identical request -> identical structured output
        again = self.call("compute_response_features", arguments)
        self.assertEqual(to_jsonable(result.structured_output),
                         to_jsonable(again.structured_output))
        self.assertEqual({"analysis_interval_seconds": 60, "baseline_interval_seconds": 60,
                          "resampled": False}, to_jsonable(result.provenance["sampling"]))

    def test_edge_case_semantics_are_explicit(self):
        ref = self.fixture()
        base = {"trajectory_ref": ref, "variables": ["XMEAS(1)", "XMV(1)"],
                "baseline_window": window(0, 120), "analysis_window": window(180, 420)}
        # DIRECTION: |delta| == deadband is UNCHANGED (strict inequality)
        found = features_of(self.call("compute_response_features", {
            **base, "features": [{"feature": "DIRECTION", "deadband": 1.0},
                                 {"feature": "ONSET_TIME", "threshold": 4.0},
                                 {"feature": "INTEGRATED_ERROR", "integrand": "ABSOLUTE"}]}),
            "XMEAS(1)")
        self.assertEqual("UNCHANGED", found["DIRECTION"]["value"])
        # ONSET: strict exceedance; never crossing is NOT_DETECTED with a null value
        self.assertEqual(("NOT_DETECTED", None, None),
                         (found["ONSET_TIME"]["status"], found["ONSET_TIME"]["value"],
                          found["ONSET_TIME"]["elapsed_seconds"]))
        absolute = self.call("compute_response_features", {
            **base, "features": [{"feature": "INTEGRATED_ERROR", "integrand": "ABSOLUTE"}]})
        self.assertEqual(240.0, features_of(absolute, "XMV(1)")["INTEGRATED_ERROR"]["value"])
        # MINIMUM: first occurrence on ties; ONSET is measured from the requested start
        found = features_of(self.call("compute_response_features", {
            "trajectory_ref": ref, "variables": ["XMEAS(1)"],
            "baseline_window": window(0, 0), "analysis_window": window(120, 420),
            "features": [{"feature": "MINIMUM"},
                         {"feature": "ONSET_TIME", "threshold": 0.5}]}), "XMEAS(1)")
        self.assertEqual((1.0, 0, hours(120)), (found["MINIMUM"]["value"],
                                                found["MINIMUM"]["elapsed_seconds"],
                                                found["MINIMUM"]["time_hours"]))
        self.assertEqual(120, found["ONSET_TIME"]["value"])
        # a single-sample window is valid for point features, invalid for integration
        single = {"trajectory_ref": ref, "variables": ["XMEAS(1)"],
                  "analysis_window": window(300, 300)}
        self.assertEqual(5.0, features_of(self.call("compute_response_features", {
            **single, "features": [{"feature": "PEAK"}]}), "XMEAS(1)")["PEAK"]["value"])
        self.denied("compute_response_features", {
            **single, "baseline_window": window(0, 0),
            "features": [{"feature": "INTEGRATED_ERROR", "integrand": "SIGNED"}]},
            "INVALID_REQUEST")
        # windows beyond the data, reversed, or between samples are rejected
        for bad in (window(180, 480), window(300, 180), window(190, 200), window(180.5, 240)):
            self.denied("compute_response_features", {
                **single, "analysis_window": bad, "features": [{"feature": "PEAK"}]},
                "INVALID_REQUEST")

    def test_unsupported_or_underspecified_features_never_guess_a_default(self):
        ref = self.fixture()
        base = {"trajectory_ref": ref, "variables": ["XMEAS(1)"],
                "baseline_window": window(0, 120), "analysis_window": window(180, 420)}
        for feature in ("SETTLING_TIME", "LAG", "TRAJECTORY_DISTANCE", "CORRELATION",
                        "EVENT_OR_SHUTDOWN", "QUALITATIVE_UNSCORED"):
            decision = self.denied("compute_response_features",
                                   {**base, "features": [{"feature": feature}]},
                                   "UNSUPPORTED_CAPABILITY")
            self.assertIn(feature, decision.reason)
        for features in ([{"feature": "ONSET_TIME"}],                 # threshold missing
                         [{"feature": "DIRECTION"}],                  # deadband missing
                         [{"feature": "INTEGRATED_ERROR"}],           # integrand missing
                         [{"feature": "PEAK", "threshold": 1.0}],     # foreign parameter
                         [{"feature": "PEAK"}, {"feature": "PEAK"}]):
            self.denied("compute_response_features", {**base, "features": features},
                        "INVALID_REQUEST")
        no_baseline = {key: value for key, value in base.items() if key != "baseline_window"}
        decision = self.denied("compute_response_features",
                               {**no_baseline, "features": [{"feature": "DELTA"}]},
                               "INVALID_REQUEST")
        self.assertIn("baseline_window", decision.reason)
        # a negative threshold or a free-text feature is not even expressible
        spec = self.specs["compute_response_features"]
        for features in ([{"feature": "ONSET_TIME", "threshold": 0}],
                         [{"feature": "RISE_TIME"}]):
            self.assertTrue(instance_errors({**base, "features": features}, spec.input_schema))

    def test_features_feed_typed_prediction_evaluation(self):
        result = self.call("compute_response_features", {
            "trajectory_ref": self.fixture(), "variables": ["XMEAS(1)"],
            "baseline_window": window(0, 120), "analysis_window": window(180, 420),
            "features": [{"feature": "DELTA"}, {"feature": "DIRECTION", "deadband": 0.5},
                         {"feature": "ONSET_TIME", "threshold": 1.5}]})
        found = features_of(result, "XMEAS(1)")
        checks = ((Feature.DELTA, (0.5, 1.5), MatchStatus.MATCH),
                  (Feature.DIRECTION, "INCREASE", MatchStatus.MATCH),
                  (Feature.DIRECTION, "DECREASE", MatchStatus.CONTRADICT),
                  (Feature.ONSET_TIME, (120.0, 600.0), MatchStatus.CONTRADICT))
        for index, (feature, expected, status) in enumerate(checks):
            prediction = Prediction(f"p{index}", "h1", "XMEAS(1)", feature, 0.5, expected)
            evaluation = evaluate_prediction(prediction, found[feature]["value"])
            self.assertEqual(status, evaluation.match_status)


class CrossCorrelationTests(BridgeCase):
    def lagged(self, shift=3, count=50, trend=0.0):
        signal = numpy.random.default_rng(7).normal(size=count + shift)
        x = signal[shift:shift + count] + trend * numpy.arange(count)
        y = signal[:count]                         # y(t) = x(t - shift): y follows x
        return self.history({"XMEAS(9)": x.tolist(), "XMEAS(21)": y.tolist()})

    def correlate(self, ref, **overrides):
        arguments = {"data_ref": ref, "x": "XMEAS(9)", "y": "XMEAS(21)",
                     "window": window(0, 49 * 60),
                     "lag_range": {"min_lag_seconds": -600, "max_lag_seconds": 600},
                     "min_overlap_samples": 10, "preprocessing": "NONE",
                     "selection": "MAX_CORRELATION", **overrides}
        return self.call("analyze_cross_correlation", arguments)

    def test_known_synthetic_lag_is_recovered_with_documented_sign(self):
        ref = self.lagged()
        output = to_jsonable(self.correlate(ref).structured_output)
        self.assertEqual("DEFINED", output["outcome"])
        self.assertEqual((180, 3, "X_LEADS_Y"), (output["best"]["lag_seconds"],
                                                 output["best"]["lag_samples"],
                                                 output["best"]["lag_interpretation"]))
        self.assertAlmostEqual(1.0, output["best"]["correlation"], places=12)
        self.assertEqual(47, output["best"]["overlap_samples"])
        self.assertIn("L > 0 means y follows x", output["lag_convention"])
        self.assertEqual(("s", 21, 0), (output["lag_unit"], output["evaluated_lags"],
                                        output["undefined_lags"]))
        swapped = to_jsonable(self.correlate(ref, x="XMEAS(21)", y="XMEAS(9)").structured_output)
        self.assertEqual((-180, "Y_LEADS_X"), (swapped["best"]["lag_seconds"],
                                               swapped["best"]["lag_interpretation"]))
        # allowlisted preprocessing keeps the lag on a trending signal
        trending = self.lagged(trend=0.5)
        for policy in ("LINEAR_DETREND", "FIRST_DIFFERENCE"):
            output = to_jsonable(self.correlate(trending, preprocessing=policy).structured_output)
            self.assertEqual(180, output["best"]["lag_seconds"], policy)
        self.assertEqual(49, output["analyzed_samples"])            # FIRST_DIFFERENCE: N - 1

    def test_dense_curve_is_artifact_backed(self):
        result = self.correlate(self.lagged())
        (ref,) = result.artifact_refs
        self.assertEqual((CURVE_KIND, Visibility.AGENT), (ref.kind, ref.visibility))
        self.assertEqual(to_jsonable(ref), to_jsonable(result.structured_output["curve_artifact_ref"]))
        curve = self.store.read(ref)
        self.assertEqual(list(range(-600, 601, 60)), [row["lag_seconds"] for row in curve])
        self.assertEqual(curve[13]["correlation"], result.structured_output["best"]["correlation"])
        # the model-visible output holds no per-lag or per-sample arrays
        lists = {key for key, value in to_jsonable(result.structured_output).items()
                 if isinstance(value, list)}
        self.assertEqual({"constant_signals", "tied_lags_seconds"}, lists)
        self.assertEqual([180], to_jsonable(result.structured_output["tied_lags_seconds"]))

    def test_ties_are_broken_deterministically(self):
        pattern = [0.0, 1.0, 0.0, -1.0] * 6           # x(t + 2) = -x(t)
        ref = self.history({"XMEAS(9)": pattern, "XMEAS(21)": [-value for value in pattern]})
        arguments = {"data_ref": ref, "x": "XMEAS(9)", "y": "XMEAS(21)",
                     "window": window(0, 23 * 60),
                     "lag_range": {"min_lag_seconds": -180, "max_lag_seconds": 180},
                     "min_overlap_samples": 5, "preprocessing": "NONE",
                     "selection": "MAX_CORRELATION"}
        output = to_jsonable(self.call("analyze_cross_correlation", arguments).structured_output)
        self.assertEqual([-120, 120], output["tied_lags_seconds"])
        self.assertEqual(-120, output["best"]["lag_seconds"])       # min(|L|, L)
        absolute = to_jsonable(self.call("analyze_cross_correlation", {
            **arguments, "selection": "MAX_ABS_CORRELATION"}).structured_output)
        self.assertEqual([-120, 0, 120], absolute["tied_lags_seconds"])
        self.assertEqual(0, absolute["best"]["lag_seconds"])
        self.assertAlmostEqual(-1.0, absolute["best"]["correlation"])

    def test_constant_and_degenerate_signals_have_structured_outcomes(self):
        base = {"x": "XMEAS(9)", "y": "XMEAS(21)", "window": window(0, 19 * 60),
                "lag_range": {"min_lag_seconds": -120, "max_lag_seconds": 120},
                "min_overlap_samples": 5, "selection": "MAX_CORRELATION"}
        ramp = [float(i) for i in range(20)]
        constant = self.history({"XMEAS(9)": [2.0] * 20, "XMEAS(21)": ramp})
        output = to_jsonable(self.call("analyze_cross_correlation", {
            **base, "data_ref": constant, "preprocessing": "NONE"}).structured_output)
        self.assertEqual(("UNDEFINED", "CONSTANT_SIGNAL", ["x"], None, None),
                         (output["outcome"], output["undefined_reason"],
                          output["constant_signals"], output["best"],
                          output["zero_lag_correlation"]))
        self.assertEqual(5, output["undefined_lags"])
        # a pure ramp is constant after FIRST_DIFFERENCE: degenerate, not guessed
        ramps = self.history({"XMEAS(9)": ramp, "XMEAS(21)": [2 * v for v in ramp]})
        output = to_jsonable(self.call("analyze_cross_correlation", {
            **base, "data_ref": ramps, "preprocessing": "FIRST_DIFFERENCE"}).structured_output)
        self.assertEqual(["x", "y"], output["constant_signals"])
        # lags whose overlap is constant are individually undefined and excluded
        step = [0.0] * 10 + [1.0] * 10
        stepped = self.history({"XMEAS(9)": step, "XMEAS(21)": step})
        result = self.call("analyze_cross_correlation", {
            **base, "data_ref": stepped, "preprocessing": "NONE",
            "lag_range": {"min_lag_seconds": -900, "max_lag_seconds": 900},
            "min_overlap_samples": 5})
        output = to_jsonable(result.structured_output)
        self.assertEqual(("DEFINED", 0), (output["outcome"], output["best"]["lag_seconds"]))
        curve = self.store.read(result.artifact_refs[0])
        self.assertEqual(output["undefined_lags"],
                         sum(row["correlation"] is None for row in curve))
        self.assertGreater(output["undefined_lags"], 0)

    def test_non_dyadic_constants_are_degenerate_not_rounding_noise(self):
        """An exact `== 0` test would miss constants like 0.1 (mean(v) != v)."""
        base = {"x": "XMEAS(9)", "y": "XMEAS(21)", "window": window(0, 19 * 60),
                "lag_range": {"min_lag_seconds": -120, "max_lag_seconds": 120},
                "min_overlap_samples": 5, "selection": "MAX_CORRELATION"}
        noise = numpy.random.default_rng(3).normal(size=20).tolist()
        for value in (0.1, 63.053, 2705.34):
            constant = self.history({"XMEAS(9)": [value] * 20, "XMEAS(21)": noise})
            output = to_jsonable(self.call("analyze_cross_correlation", {
                **base, "data_ref": constant, "preprocessing": "NONE"}).structured_output)
            self.assertEqual(("UNDEFINED", ["x"]), (output["outcome"],
                                                    output["constant_signals"]), value)
        ramp = [0.1 * i + 5.3 for i in range(20)]
        for policy in ("FIRST_DIFFERENCE", "LINEAR_DETREND"):
            ramps = self.history({"XMEAS(9)": ramp, "XMEAS(21)": noise})
            output = to_jsonable(self.call("analyze_cross_correlation", {
                **base, "data_ref": ramps, "preprocessing": policy}).structured_output)
            self.assertEqual(("UNDEFINED", ["x"]), (output["outcome"],
                                                    output["constant_signals"]), policy)
        # a held-then-stepped actuator: overlaps inside the hold are undefined, not noise
        held = [63.053] * 12 + [70.1] * 8
        stepped = self.history({"XMEAS(9)": held, "XMEAS(21)": held})
        result = self.call("analyze_cross_correlation", {
            **base, "data_ref": stepped, "preprocessing": "NONE",
            "lag_range": {"min_lag_seconds": -900, "max_lag_seconds": 900}})
        curve = self.store.read(result.artifact_refs[0])
        constant_overlap = [row["lag_seconds"] for row in curve if row["correlation"] is None]
        # |k| >= 8 samples: one side of the overlap lies entirely inside a hold
        self.assertEqual([k * 60 for k in range(-15, 16) if abs(k) >= 8], constant_overlap)
        self.assertEqual(0, result.structured_output["best"]["lag_seconds"])

    def test_invalid_lag_ranges_are_rejected_before_execution(self):
        ref = self.lagged()
        base = {"data_ref": ref, "x": "XMEAS(9)", "y": "XMEAS(21)",
                "window": window(0, 49 * 60), "min_overlap_samples": 10,
                "preprocessing": "NONE", "selection": "MAX_CORRELATION"}
        for low, high in ((120, -120),       # reversed
                          (-90, 90),         # not a multiple of the 60 s interval
                          (-2460, 2460),     # leaves < 10 overlapping samples
                          (-14400, 14400)):  # more than 241 lags
            self.denied("analyze_cross_correlation", {
                **base, "lag_range": {"min_lag_seconds": low, "max_lag_seconds": high}},
                "INVALID_REQUEST")
        self.denied("analyze_cross_correlation", {
            **base, "lag_range": {"min_lag_seconds": 0, "max_lag_seconds": 0},
            "min_overlap_samples": 51}, "INVALID_REQUEST")
        self.denied("analyze_cross_correlation", {
            **base, "y": "XMEAS(9)",
            "lag_range": {"min_lag_seconds": 0, "max_lag_seconds": 0}}, "INVALID_REQUEST")
        spec = self.specs["analyze_cross_correlation"]
        for lag_range, overlap in (({"min_lag_seconds": -99999, "max_lag_seconds": 0}, 10),
                                   ({"min_lag_seconds": 0.5, "max_lag_seconds": 60}, 10),
                                   ({"min_lag_seconds": 0, "max_lag_seconds": 60}, 2)):
            self.assertTrue(instance_errors({**base, "lag_range": lag_range,
                                             "min_overlap_samples": overlap},
                                            spec.input_schema))


class TrajectoryComparisonTests(BridgeCase):
    def test_known_metrics_on_aligned_trajectories(self):
        reference = self.history({"XMEAS(7)": [1.0, 2.0, 3.0, 4.0]})
        candidate = self.history({"XMEAS(7)": [2.0, 2.0, 1.0, 4.0]})
        arguments = {"reference_ref": reference, "candidate_ref": candidate,
                     "variables": ["XMEAS(7)"], "alignment_policy": "EXACT_TIMESTAMPS",
                     "reference_window": window(0, 180), "metrics": [
                         "MEAN_ERROR", "MEAN_ABSOLUTE_ERROR", "ROOT_MEAN_SQUARE_ERROR",
                         "MAX_ABSOLUTE_ERROR", "FINAL_ERROR"], "preprocessing": "NONE"}
        result = self.call("compare_trajectories", arguments)
        output = to_jsonable(result.structured_output)
        metrics = {item["metric"]: item for item in output["variables"][0]["metrics"]}
        self.assertEqual(-0.25, metrics["MEAN_ERROR"]["value"])     # e = c - r = [1,0,-2,0]
        self.assertEqual(0.75, metrics["MEAN_ABSOLUTE_ERROR"]["value"])
        self.assertTrue(math.isclose(math.sqrt(1.25), metrics["ROOT_MEAN_SQUARE_ERROR"]["value"]))
        self.assertEqual((2.0, hours(120)), (metrics["MAX_ABSOLUTE_ERROR"]["value"],
                                             metrics["MAX_ABSOLUTE_ERROR"]["time_hours"]))
        self.assertEqual(0.0, metrics["FINAL_ERROR"]["value"])
        self.assertEqual("candidate - reference", output["error_definition"])
        self.assertEqual((False, 4, 60), (output["alignment"]["resampled"],
                                          output["alignment"]["aligned_samples"],
                                          output["alignment"]["sampling_interval_seconds"]))
        # no score/threshold/verdict: metrics only
        text = str(output).lower()
        for word in ("score", "match", "verdict", "threshold", "rank"):
            self.assertNotIn(word, text)
        shifted = self.call("compare_trajectories", {**arguments, "metrics": ["MEAN_ERROR"],
                                                     "preprocessing": "REMOVE_INITIAL_VALUE"})
        self.assertEqual(-1.25, to_jsonable(shifted.structured_output)[
            "variables"][0]["metrics"][0]["value"])                 # e = [0,-1,-3,-1]
        # elapsed alignment pairs equal-length windows of different absolute times
        later = self.history({"XMEAS(7)": [9.0, 2.0, 2.0, 1.0, 4.0]}, start=540)
        elapsed = self.call("compare_trajectories", {
            **arguments, "candidate_ref": later, "alignment_policy": "ELAPSED_FROM_WINDOW_START",
            "candidate_window": window(600, 780)})
        self.assertEqual(output["variables"], to_jsonable(elapsed.structured_output)["variables"])

    def test_incompatible_sampling_is_rejected_never_resampled(self):
        reference = self.history({"XMEAS(7)": [1.0, 2.0, 3.0, 4.0]})
        dense = self.history({"XMEAS(7)": [float(i) for i in range(7)]}, interval=30)
        offset = self.history({"XMEAS(7)": [1.0, 2.0, 3.0, 4.0]}, start=30)
        base = {"reference_ref": reference, "variables": ["XMEAS(7)"],
                "metrics": ["MEAN_ERROR"], "preprocessing": "NONE"}
        exact = {**base, "alignment_policy": "EXACT_TIMESTAMPS",
                 "reference_window": window(0, 180)}
        # a 30 s candidate is not decimated onto the 60 s grid
        self.denied("compare_trajectories", {**exact, "candidate_ref": dense},
                    "SAMPLING_INCOMPATIBLE")
        self.denied("compare_trajectories", {**base, "candidate_ref": offset,
                                             "alignment_policy": "EXACT_TIMESTAMPS",
                                             "reference_window": window(30, 180)},
                    "SAMPLING_INCOMPATIBLE")
        elapsed = {**base, "alignment_policy": "ELAPSED_FROM_WINDOW_START",
                   "reference_window": window(0, 180)}
        self.denied("compare_trajectories", {**elapsed, "candidate_ref": dense,
                                             "candidate_window": window(0, 180)},
                    "SAMPLING_INCOMPATIBLE")
        self.denied("compare_trajectories", {**elapsed, "candidate_ref": offset,
                                             "candidate_window": window(0, 180)},
                    "INVALID_REQUEST")    # window starts before the candidate data
        self.denied("compare_trajectories", {**elapsed, "candidate_ref": offset,
                                             "candidate_window": window(30, 150)},
                    "SAMPLING_INCOMPATIBLE")   # different duration
        # policy/window combinations must be explicit
        self.denied("compare_trajectories", {**exact, "candidate_ref": reference,
                                             "candidate_window": window(0, 180)},
                    "INVALID_REQUEST")
        self.denied("compare_trajectories", {**elapsed, "candidate_ref": reference},
                    "INVALID_REQUEST")
        # an off-grid final record (shutdown or non-multiple horizon) only blocks the
        # windows that contain it; the uniformly sampled part stays analyzable
        shutdown = self.history({"XMEAS(7)": [1.0, 2.0, 3.0, 4.0, 9.0]},
                                times=[0, 60, 120, 180, 214])
        early = self.call("compare_trajectories", {**exact, "candidate_ref": shutdown})
        self.assertEqual(4, early.structured_output["alignment"]["aligned_samples"])
        self.denied("compare_trajectories", {**exact, "reference_ref": shutdown,
                                             "candidate_ref": shutdown,
                                             "reference_window": window(0, 214)},
                    "SAMPLING_INCOMPATIBLE")
        # a single-sample window has no sampling interval, and says so
        single = self.call("compare_trajectories", {**exact, "candidate_ref": dense,
                                                    "reference_window": window(60, 60)})
        self.assertIsNone(single.structured_output["alignment"]["sampling_interval_seconds"])
        # non-uniform windows or non-integral-second source data are rejected
        uneven = self.history({"XMEAS(7)": [1.0, 2.0, 3.0, 4.0]}, times=[0, 60, 150, 180])
        fractional = self.history({"XMEAS(7)": [1.0, 2.0, 3.0, 4.0]},
                                  times=[0, 60.5, 121, 181.5])
        for candidate in (uneven, fractional):
            self.denied("compare_trajectories", {**exact, "candidate_ref": candidate},
                        "SAMPLING_INCOMPATIBLE")
        # no resampling policy exists, and the bridge has no interpolation path
        spec = self.specs["compare_trajectories"]
        self.assertTrue(instance_errors({**exact, "candidate_ref": dense,
                                         "alignment_policy": "LINEAR_INTERPOLATION"},
                                        spec.input_schema))
        source = inspect.getsource(tool_bridge)
        for name in ("numpy.interp", "resample(", "scipy"):
            self.assertNotIn(name, source)


class InputPolicyTests(BridgeCase):
    def arguments(self, ref):
        return {"trajectory_ref": ref, "variables": ["XMEAS(1)"],
                "analysis_window": window(0, 60), "features": [{"feature": "PEAK"}]}

    def test_evaluator_only_or_unissued_refs_are_rejected(self):
        ref = self.history({"XMEAS(1)": [1.0, 2.0]})
        spec = self.specs["compute_response_features"]
        hidden = {**ref, "visibility": "EVALUATOR"}
        self.assertTrue(instance_errors(self.arguments(hidden), spec.input_schema))
        self.denied("compute_response_features", self.arguments(hidden), "POLICY_DENIED")
        # the runtime G0 gate refuses a non-AGENT ref before the lab is consulted
        task = Task("t", "g", (), tuple(self.specs), Budget(5, 5, 0, 0, 5), {"type": "object"})
        pipeline = GatePipeline(task, self.specs, self.bridge_policy(), self.bridge, None, None)
        with self.assertRaises(GateDenied) as denied:
            pipeline.authorize(self.request("compute_response_features", self.arguments(hidden)),
                               {ref["ref_id"]: InformationRef(**hidden)}, {}, 0)
        self.assertEqual("G0_SCHEMA", denied.exception.decision.stage)
        # forged id/checksum or a tampered artifact never resolves
        self.denied("compute_response_features",
                    self.arguments({**ref, "ref_id": "tep-artifact-999999"}), "INVALID_REQUEST")
        self.denied("compute_response_features",
                    self.arguments({**ref, "checksum": "0" * 64}), "INVALID_REQUEST")
        (Path(self.store.root) / f"{ref['ref_id']}.jsonl").write_text("{}\n")
        self.denied("compute_response_features", self.arguments(ref), "ARTIFACT_ERROR")
        # bridge outputs are not analyzable inputs
        curve = to_jsonable(self.store.put_records(CURVE_KIND, [{"lag_seconds": 0}], NOW))
        self.assertTrue(instance_errors(self.arguments(curve), spec.input_schema))
        self.denied("compute_response_features", self.arguments(curve), "INVALID_REQUEST")
        # unknown variables in the artifact or in the registry
        self.denied("compute_response_features",
                    {**self.arguments(self.history({"XMEAS(1)": [1.0, 2.0]})),
                     "variables": ["XMEAS(2)"]}, "INVALID_REQUEST")
        self.assertTrue(instance_errors({**self.arguments(ref), "variables": ["IDV(4)"]},
                                        spec.input_schema))

    def bridge_policy(self):
        from industrial_agent_runtime import GatePolicy
        return GatePolicy("test", frozenset({SideEffectClass.COMPUTE}),
                          frozenset({ANALYSIS_POLICY_TAG}), tool_allowlist=frozenset(self.specs))

    def test_arbitrary_paths_and_code_are_not_expressible(self):
        ref = self.history({"XMEAS(1)": [1.0, 2.0]})
        spec = self.specs["compute_response_features"]
        for bad in ({**self.arguments(ref), "path": "C:/Windows/win.ini"},
                    self.arguments({**ref, "ref_id": "../../etc/passwd"}),
                    self.arguments({**ref, "path": "/etc/passwd"}),
                    self.arguments("runs/world/telemetry.jsonl"),
                    {**self.arguments(ref), "features": [{"feature": "PEAK",
                                                          "function": "numpy.max"}]}):
            self.assertTrue(instance_errors(bad, spec.input_schema), bad)
        # schema bypass: a bare path or module string is still not a resolvable ref
        self.denied("compute_response_features", self.arguments("runs/x.jsonl"),
                    "INVALID_REQUEST")
        correlation = self.specs["analyze_cross_correlation"]
        for code in ("eval", "__import__('os')", "scipy.signal.correlate", "lambda x: x"):
            self.assertTrue(instance_errors({
                "data_ref": ref, "x": "XMEAS(1)", "y": "XMEAS(2)", "window": window(0, 60),
                "lag_range": {"min_lag_seconds": 0, "max_lag_seconds": 0},
                "min_overlap_samples": 3, "preprocessing": code,
                "selection": "MAX_CORRELATION"}, correlation.input_schema))
        for spec in self.specs.values():
            text = str(to_jsonable(spec.input_schema)).lower()
            for field in ("path", "file", "url", "module", "function", "code", "expression"):
                self.assertNotIn(f"'{field}'", text)
        source = inspect.getsource(tool_bridge)
        for call in (r"\beval\(", r"\bexec\(", r"__import__", r"importlib", r"subprocess",
                     r"\bopen\(", r"getattr\(numpy"):
            self.assertIsNone(re.search(call, source), call)


class AuthorityAndProvenanceTests(BridgeCase):
    def run_all(self):
        ref = self.history({"XMEAS(9)": [float(i % 5) for i in range(20)],
                            "XMEAS(21)": [float((i + 2) % 5) for i in range(20)]})
        return [
            self.call("compute_response_features", {
                "trajectory_ref": ref, "variables": ["XMEAS(9)"],
                "analysis_window": window(0, 600), "features": [{"feature": "PEAK"}]}),
            self.call("analyze_cross_correlation", {
                "data_ref": ref, "x": "XMEAS(9)", "y": "XMEAS(21)", "window": window(0, 1140),
                "lag_range": {"min_lag_seconds": -120, "max_lag_seconds": 120},
                "min_overlap_samples": 5, "preprocessing": "NONE",
                "selection": "MAX_CORRELATION"}),
            self.call("compare_trajectories", {
                "reference_ref": ref, "candidate_ref": ref, "variables": ["XMEAS(9)"],
                "alignment_policy": "EXACT_TIMESTAMPS", "reference_window": window(0, 600),
                "metrics": ["ROOT_MEAN_SQUARE_ERROR"], "preprocessing": "NONE"}),
        ]

    def test_toolspecs_are_compute_without_simulation(self):
        self.assertEqual(BRIDGE_TOOLS, set(self.specs))
        for spec in self.specs.values():
            self.assertEqual(SideEffectClass.COMPUTE, spec.side_effect_class)
            self.assertEqual({}, dict(spec.declared_budget_draw))
            self.assertEqual({}, dict(spec.max_budget_draw))
            self.assertIsNone(spec.isolation_guarantee)
            self.assertEqual((ANALYSIS_POLICY_TAG,), spec.required_policy_tags)
            self.assertEqual([], schema_errors(spec.input_schema))
            self.assertEqual([], schema_errors(spec.output_schema))
            metadata = spec.provider_metadata
            self.assertEqual(bridge_tool_version(spec.name), metadata["tool_version"])
            self.assertEqual(("numpy", numpy.__version__),
                             (metadata["library_name"], metadata["library_version"]))
        assert_blind(self, [to_jsonable(spec) for spec in self.specs.values()])
        # executing every tool touches no simulator entry point
        guarded = ("rollout", "fork", "snapshot", "apply", "apply_scenario", "reset", "step")
        patches = [mock.patch(f"tep_sim.TEPEnvironment.{name}",
                              side_effect=AssertionError(f"simulator {name}"), create=True)
                   for name in guarded]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        for result in self.run_all():
            self.assertEqual({}, dict(result.actual_budget_draw))
            self.assertEqual("COMPUTE", result.provenance["side_effect_class"])
        # the bridge module holds no world, sandbox, or simulator handle at all
        for name in ("ReferenceWorld", "SimulationSandbox", "TEPEnvironment"):
            self.assertFalse(hasattr(tool_bridge, name), name)
        self.assertEqual({"artifacts", "limits", "implementation"},
                         {key for key in vars(self.bridge) if not key.startswith("_")})
        with self.assertRaises(TypeError):  # the reference guard is mandatory
            AnalysisToolBridge(self.store)

    def test_results_satisfy_b3_and_provenance_records_the_backend(self):
        pipeline = ResultVerificationPipeline(self.bridge, RcaResultIngestor())
        for result in self.run_all():
            spec = self.specs[result.provenance["tool_name"]]
            self.assertEqual(spec.provider_metadata["tool_version"],
                             result.provenance["tool_version"])
            implementation = to_jsonable(result.provenance["implementation"])
            self.assertEqual(numpy.__version__, implementation["library_version"])
            self.assertEqual(tool_bridge.IMPLEMENTATION_ID, implementation["implementation_id"])
            self.assertFalse(to_jsonable(result.provenance["sampling"])["resampled"])
            self.assertTrue(to_jsonable(result.provenance["inputs"]))
        result = self.run_all()[-1]
        request = self.requests[result.request_id]
        spec = self.specs["compare_trajectories"]
        known = {ref["ref_id"]: InformationRef(**ref)
                 for ref in (request.arguments["reference_ref"],)}
        accounting = reconcile({}, result.actual_budget_draw, Budget(1, 1, 0, 0, 1))
        verified = pipeline.verify(result, request, spec, accounting, known, {}, 0, lambda: 0)
        self.assertEqual("ACCEPT", verified.decision.decision)
        self.assertEqual({"REGISTER_OBSERVATION"}, {d.operation for d in verified.deltas})
        forged = replace(result, provenance={**result.provenance, "tool_version": "other"})
        with self.assertRaises(Exception) as rejected:
            pipeline.verify(forged, request, spec, accounting, known, {}, 0, lambda: 0)
        self.assertEqual("TOOL_VERSION_MISMATCH", rejected.exception.decision.reason_code)

    def test_changing_the_library_version_changes_recorded_provenance(self):
        ref = self.history({"XMEAS(1)": [1.0, 2.0, 3.0]})
        arguments = {"trajectory_ref": ref, "variables": ["XMEAS(1)"],
                     "analysis_window": window(0, 120), "features": [{"feature": "PEAK"}]}
        original = self.call("compute_response_features", arguments)
        with mock.patch.object(numpy, "__version__", "0.0.0+changed"):
            other = AnalysisToolBridge(self.store, reference_revision=lambda: self.revision[0],
                                       clock=lambda: NOW)
        other_specs = {spec.name: spec for spec in other.tool_specs()}
        self.assertNotEqual(checksum(self.bridge.tool_specs()), checksum(other.tool_specs()))
        self.assertEqual("0.0.0+changed", other_specs["compute_response_features"]
                         .provider_metadata["library_version"])
        request = ToolCallRequest("other-1", "compute_response_features", arguments)
        changed = other.execute(request, other_specs["compute_response_features"])
        self.assertEqual("0.0.0+changed",
                         changed.provenance["implementation"]["library_version"])
        self.assertNotEqual(to_jsonable(original.provenance["implementation"]),
                            to_jsonable(changed.provenance["implementation"]))
        self.assertEqual(to_jsonable(original.structured_output),
                         to_jsonable(changed.structured_output))
        # a result recorded under another backend never verifies against this bridge,
        # even when its executor audit entry is present
        self.forge(changed)
        with self.assertRaisesRegex(ResultInvariantError, "implementation"):
            self.bridge.check_result(changed, request, self.specs["compute_response_features"],
                                     ())

    def forge(self, result):
        """Register a tampered result as if this executor produced it, so the checks
        after the audit binding are actually reached."""
        revision = self.revision[0]
        self.bridge._audit[result.request_id] = (revision, revision, checksum(result))

    def test_lab_invariants_reject_tampering_evidence_and_stale_reference(self):
        results = self.run_all()
        result = results[1]                   # cross correlation (artifact-backed)
        request = self.requests[result.request_id]
        spec = self.specs["analyze_cross_correlation"]
        deltas = RcaResultIngestor().derive_deltas(result)
        self.assertEqual(["REGISTER_OBSERVATION", "REGISTER_ARTIFACT_REF"],
                         [delta.operation for delta in deltas])
        output, provenance = result.structured_output, result.provenance
        history_ref = InformationRef(**dict(request.arguments["data_ref"]))
        tampered = (
            (replace(result, provenance={**provenance, "implementation": {
                **provenance["implementation"], "library_version": "9.9"}}), "implementation"),
            (replace(result, provenance={**provenance, "inputs": []}), "provenance inputs"),
            (replace(result, provenance={**provenance, "tool_version": "x"}), "tool_version"),
            (replace(result, actual_budget_draw={"simulation_rollouts": 1}), "draws no budget"),
            (replace(result, provenance={**provenance, "note": "IDV(4)"}), "hidden vocabulary"),
            (replace(result, information_refs=(InformationRef(
                "x", "Note", "lab", "v0", Visibility.AGENT, NOW),)), "information refs"),
            (replace(result, artifact_refs=()), "declared artifact refs"),
            (replace(result, artifact_refs=(history_ref,)), "wrong kind"),
            (replace(result, structured_output={**output, "data_ref": {
                **output["data_ref"], "created_at": "other"}}), "input ref"),
            (replace(result, structured_output=[1, 2]), "must be an object"),
        )
        with self.assertRaises(TypeError):  # runtime ToolResult refuses non-finite numbers
            replace(result, structured_output={**output, "zero_lag_correlation": math.inf})
        for bad, message in tampered:
            # without an executor audit entry every tampering is caught at the binding
            with self.assertRaisesRegex(ResultInvariantError, "unmodified"):
                self.bridge.check_result(bad, request, spec, deltas)
            self.forge(bad)
            with self.assertRaisesRegex(ResultInvariantError, message):
                self.bridge.check_result(bad, request, spec, deltas)
            self.assertFalse(self.bridge.verify_result(bad, request, spec, deltas, 0))
        self.forge(result)
        evidence = StateDelta("ADD_EVIDENCE_LINK", "link", {}, "RESULT_INGESTION")
        with self.assertRaisesRegex(ResultInvariantError, "never evidence"):
            self.bridge.check_result(result, request, spec, (*deltas, evidence))
        fabricated = ToolResult("never-run", "SUCCESS", result.structured_output, {},
                                {**result.provenance, "request_id": "never-run"})
        with self.assertRaisesRegex(ResultInvariantError, "unmodified"):
            self.bridge.check_result(
                fabricated, ToolCallRequest("never-run", request.tool_name, request.arguments),
                spec, ())
        # a reference revision that moved after execution invalidates the result
        self.revision[0] = "reference-r2"
        with self.assertRaisesRegex(ResultInvariantError, "reference world changed"):
            self.bridge.check_result(result, request, spec, deltas)
        self.revision[0] = "reference-r1"
        self.assertTrue(self.bridge.verify_result(result, request, spec, deltas, 0))
        # a reference change during execution yields STALE_STATE, never SUCCESS
        revisions = iter(["reference-a", "reference-b"])
        moving = AnalysisToolBridge(self.store, reference_revision=lambda: next(revisions),
                                    clock=lambda: NOW)
        stale = moving.execute(request, {s.name: s for s in moving.tool_specs()}[
            "analyze_cross_correlation"])
        self.assertEqual(("STALE_STATE", ()), (stale.status, stale.artifact_refs))
        # an unexpected internal error is a generic failure result
        broken = replace(self.bridge._tools["compare_trajectories"],
                         run=mock.Mock(side_effect=KeyError("IDV(4) secret")))
        self.bridge._tools["compare_trajectories"] = broken
        with self.assertLogs("tep_agent_lab.tool_bridge", "ERROR"):  # cause is logged
            failed = self.bridge.execute(ToolCallRequest(
                "b1", "compare_trajectories", self.requests[results[2].request_id].arguments),
                self.specs["compare_trajectories"])
        self.assertEqual(("ANALYSIS_FAILED", "internal analysis error"),
                         (failed.status, failed.error))
        assert_blind(self, failed)


class BridgedPolicyCompositionTests(unittest.TestCase):
    """The bridged policy only adds authority for analysis; it never weakens the base."""

    def test_base_approval_requirements_are_preserved(self):
        from industrial_agent_runtime import GatePolicy
        with TemporaryDirectory() as directory:
            root = Path(directory)
            world = make_world(root, inject=False)
            artifacts = ArtifactStore(root / "artifacts")
            surface = BlindRcaToolSurface(world, artifacts, clock=lambda: NOW)
            bridged = BridgedToolSurface(surface, AnalysisToolBridge(
                artifacts, reference_revision=surface.reference_revision, clock=lambda: NOW))
            base = surface.gate_policy()
            # a stricter base: SIMULATE also requires approval
            strict = GatePolicy(base.policy_version, base.granted_side_effect_classes,
                                base.granted_policy_tags, base.simulation_dimensions,
                                approval_required_for=base.approval_required_for
                                | {SideEffectClass.SIMULATE},
                                tool_allowlist=base.tool_allowlist)
            for source in (base, strict):
                with mock.patch.object(surface, "gate_policy", return_value=source):
                    policy = bridged.gate_policy()
                self.assertEqual(source.approval_required_for, policy.approval_required_for)
                self.assertEqual(source.simulation_dimensions, policy.simulation_dimensions)
                self.assertEqual(source.granted_side_effect_classes | {SideEffectClass.COMPUTE},
                                 policy.granted_side_effect_classes)
                self.assertEqual(source.granted_policy_tags | {ANALYSIS_POLICY_TAG},
                                 policy.granted_policy_tags)
                self.assertTrue(source.tool_allowlist < policy.tool_allowlist)
                self.assertEqual(BRIDGE_TOOLS, policy.tool_allowlist - source.tool_allowlist)
            with mock.patch.object(surface, "gate_policy", return_value=strict):
                self.assertIn(SideEffectClass.SIMULATE,
                              bridged.gate_policy().approval_required_for)
            world.environment.close()


class BridgedSurfaceIngestionTests(unittest.TestCase):
    """Runtime B1-B3 + C4 surface + C5 bridge + C1 ingestion over the real world."""

    def test_bridge_results_become_observations_never_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            world = make_world(root)
            artifacts = ArtifactStore(root / "artifacts")
            surface = BlindRcaToolSurface(world, artifacts, finish_verifier=lambda *a: True,
                                          clock=lambda: NOW)
            with self.assertRaises(ValueError):  # one reference truth for both hooks
                BridgedToolSurface(surface, AnalysisToolBridge(
                    artifacts, reference_revision=lambda: "constant"))
            bridge = AnalysisToolBridge(artifacts, reference_revision=surface.reference_revision,
                                        clock=lambda: NOW)
            bridged = BridgedToolSurface(surface, bridge)
            specs = {spec.name: spec for spec in bridged.tool_specs()}
            # C4 tools are unchanged; the registry adds exactly the three bridge tools
            self.assertEqual({spec.name: checksum(spec) for spec in surface.tool_specs()},
                             {name: checksum(spec) for name, spec in specs.items()
                              if name not in BRIDGE_TOOLS})
            self.assertEqual(BRIDGE_TOOLS, set(specs) - {s.name for s in surface.tool_specs()})
            self.assertEqual(set(), set(specs) & BLIND_RCA_EXCLUDED_TOOLS)
            policy = bridged.gate_policy()
            self.assertEqual({SideEffectClass.READ, SideEffectClass.COMPUTE,
                              SideEffectClass.SIMULATE}, set(policy.granted_side_effect_classes))
            self.assertNotIn(SideEffectClass.MUTATE, policy.granted_side_effect_classes)

            incident = InformationRef("incident-1", "Incident", "fixture", "v1",
                                      Visibility.AGENT, NOW)
            store = RcaStateStore(
                RcaState(investigation_id="i1", goal="find the root cause",
                         incident_ref=incident),
                RunLog(root / "state", {"run_id": "c5", "investigation_id": "i1"}),
                resolve=lambda ref: artifacts.resolve(ref) or (
                    ref if ref == incident else None),
                task_id="task-1")
            before = reference_fingerprint(world)

            def artifact(kind):
                return to_jsonable(next(ref for ref in store.state.artifact_refs
                                        if ref.kind == kind))

            variables = ["XMEAS(9)", "XMEAS(21)"]
            requests = [
                lambda: ToolCallRequest("a-history", "get_history",
                                        {"window_hours": 0.3, "variables": variables}),
                lambda: ToolCallRequest("b-fork", "fork_environment",
                                        {"snapshot_id": "snapshot-0001"}),
                lambda: ToolCallRequest("c-rollout", "run_rollout",
                                        {"branch_id": "branch-0002", "horizon_hours": 0.2}),
                lambda: ToolCallRequest("d-compare", "compare_trajectories", {
                    "reference_ref": artifact("HistoryWindowArtifact"),
                    "candidate_ref": artifact("RolloutTelemetryArtifact"),
                    "variables": variables, "alignment_policy": "EXACT_TIMESTAMPS",
                    "reference_window": {"start_hours": 0.1, "end_hours": 0.3},
                    "metrics": ["ROOT_MEAN_SQUARE_ERROR", "MAX_ABSOLUTE_ERROR"],
                    "preprocessing": "NONE"}),
                lambda: ToolCallRequest("e-xcorr", "analyze_cross_correlation", {
                    "data_ref": artifact("HistoryWindowArtifact"),
                    "x": "XMEAS(21)", "y": "XMEAS(9)",
                    "window": {"start_hours": 0.0, "end_hours": 0.3},
                    "lag_range": {"min_lag_seconds": -300, "max_lag_seconds": 300},
                    "min_overlap_samples": 10, "preprocessing": "LINEAR_DETREND",
                    "selection": "MAX_ABS_CORRELATION"}),
                lambda: ToolCallRequest("f-features", "compute_response_features", {
                    "trajectory_ref": artifact("HistoryWindowArtifact"),
                    "variables": variables,
                    "baseline_window": {"start_hours": 0.0, "end_hours": 0.1},
                    "analysis_window": {"start_hours": 0.1, "end_hours": 0.3},
                    "features": [{"feature": "DELTA"}, {"feature": "PEAK"},
                                 {"feature": "DIRECTION", "deadband": 0.0}]}),
                lambda: ToolCallRequest("g-settling", "compute_response_features", {
                    "trajectory_ref": artifact("HistoryWindowArtifact"),
                    "variables": variables,
                    "analysis_window": {"start_hours": 0.1, "end_hours": 0.3},
                    "features": [{"feature": "SETTLING_TIME"}]}),
            ]

            def turn(make=None):
                def build(projection, limits):
                    turn_id = f"turn-{limits['budget_usage']['model_calls']}"
                    if make is None:
                        return ModelTurn(turn_id, limits["context_projection_ref"],
                                         projection.base_revision, Action.FINISH_PROPOSAL,
                                         finish_proposal=FinishProposal({"done": True}))
                    return ModelTurn(turn_id, limits["context_projection_ref"],
                                     projection.base_revision, Action.TOOL_REQUEST,
                                     tool_request=make())
                return build

            task = Task("task-1", "find the root cause", (incident,), tuple(specs),
                        Budget(12, 12, 0, 0, 30, extra_dimensions=simulation_quota(
                            snapshots=1, branches=1, rollouts=1, horizon_seconds=3600)),
                        {"type": "object"})
            trace = TraceRecorder(root / "trace")
            result = Coordinator(
                task, store, FakeProvider([turn(make) for make in requests] + [turn()]),
                trace, bridged.tool_specs(),
                model_metadata={"provider": "fake", "model": "scripted",
                                "model_version": "v1", "prompt_template_version": "v1"},
                gate=bridged, executor=bridged, verifier=bridged,
                ingestor=RcaResultIngestor(), gate_policy=policy,
                reference_guard=bridged, clock=lambda: NOW).run()

            self.assertEqual(TaskStatus.DONE, result.status, result.errors)
            state = store.state
            self.assertEqual(["observation:a-history", "observation:b-fork",
                              "observation:c-rollout", "observation:d-compare",
                              "observation:e-xcorr", "observation:f-features"],
                             [ref.ref_id for ref in state.observation_refs])
            self.assertEqual((), state.evidence_link_refs)          # observation, not evidence
            self.assertEqual((), state.hypothesis_refs)
            self.assertEqual(["HistoryWindowArtifact", "RolloutTelemetryArtifact", CURVE_KIND],
                             [ref.kind for ref in state.artifact_refs])
            self.assertEqual(before, reference_fingerprint(world))  # no reference mutation
            usage = dict(result.budget_usage)
            self.assertEqual((0, 1, 1, 720), (usage.get("simulation_snapshots", 0),
                                              usage["simulation_branches"],
                                              usage["simulation_rollouts"],
                                              usage["simulated_horizon_seconds"]))
            events = trace.read_events()
            denials = [event["output_summary"]["reason_code"] for event in events
                       if event["type"] == "GATE" and event["status"] == "DENY"]
            self.assertEqual(["UNSUPPORTED_CAPABILITY"], denials)
            accepted = [event for event in events
                        if event["type"] == "VERIFY_RESULT" and event["status"] == "ACCEPTED"]
            self.assertEqual(6, len(accepted))
            projection = store.project({})
            assert_blind(self, projection.content)
            world.environment.close()


if __name__ == "__main__":
    unittest.main()
