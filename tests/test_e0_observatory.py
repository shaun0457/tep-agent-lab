"""E0 Environment Observatory tests (docs/e0-environment-observatory.md)."""

import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from industrial_agent_runtime import InformationRef, canonical_json, to_jsonable

from tep_agent_lab.canonical_context import ProjectionScope
from tep_agent_lab.playground import RunStatus
from tep_agent_lab.playground_views import RunQueries, UnknownArtifact
from tep_agent_lab.tep_world import leakage_findings
from test_playground import lab_revision
from test_tool_surface import NOW, assert_blind

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "e0_environment_observatory", ROOT / "examples" / "e0_environment_observatory.py")
e0 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = e0
_SPEC.loader.exec_module(e0)

REQUESTS = ("e0-1-capabilities", "e0-2-history", "e0-3-fork", "e0-4-rollout", "e0-5-compare")
TURNS = len(REQUESTS) + 1  # one tool request per turn, then finish
GATE_STAGES = {"G0_SCHEMA", "G1_AUTHORITY", "G2_BUDGET", "G3_SIDE_EFFECT", "CONSUMER"}


def agent_block(html: str, block: str) -> dict:
    match = re.search(rf'<script type="application/json" id="{block}">(.*?)</script>', html,
                      re.S)
    return json.loads(match.group(1))


class E0ObservatoryTests(unittest.TestCase):
    """One real demo run: RunManager -> Coordinator -> B2 -> consumer -> Executor -> B3."""

    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name) / "e0"
        cls.demo = e0.run_demo(cls.root, lab_revision=lab_revision(), clock=lambda: NOW)
        cls.queries = cls.demo.manager.queries(cls.demo.run_id)
        cls.payload = e0.build_agent_report(cls.queries)
        cls.developer = cls.demo.harness.developer_setup()
        cls.html = e0.render_html(cls.payload, cls.developer)
        cls.session = cls.demo.manager._runs[cls.demo.run_id].session

    def trace(self):
        return self.session.trace.read_events()

    # 1. real E0 run --------------------------------------------------------------------
    def test_real_run_completes_through_runtime_authority(self):
        self.assertEqual(RunStatus.COMPLETED, self.demo.outcome.terminal_status)
        runtime = self.demo.outcome.runtime_result
        self.assertEqual("DONE", runtime["task_status"], runtime["errors"])
        self.assertEqual(("COMPLETED", "DONE"), (self.payload["world"]["run_status"],
                                                 self.payload["world"]["task_status"]))
        events = self.trace()
        for request_id in REQUESTS:
            with self.subTest(request_id=request_id):
                stages = {event["output_summary"]["stage"] for event in events
                          if event["type"] == "GATE"
                          and event["input_summary"]["request_id"] == request_id}
                self.assertLessEqual(GATE_STAGES, stages)
                dispatched = [event for event in events if event["type"] == "EXECUTE"
                              and event["input_summary"]["request"]["request_id"] == request_id]
                verified = [event["status"] for event in events
                            if event["type"] == "VERIFY_RESULT"
                            and event["input_summary"]["request_id"] == request_id]
                self.assertEqual((1, ["ACCEPTED"]), (len(dispatched), verified))
        self.assertEqual(TURNS, len([event for event in events if event["type"] == "MODEL_TURN"]))
        self.assertEqual(e0.EXPECTED_TOOLS,
                         tuple(call["tool"] for call in self.payload["tool_calls"]))
        self.assertEqual(list(REQUESTS),
                         [call["request_id"] for call in self.payload["tool_calls"]])
        for call in self.payload["tool_calls"]:  # derived from the AGENT feed order
            types = [item["type"] for item in call["trace"]]
            self.assertEqual(["MODEL_TURN", "MODEL_OUTPUT"], types[:2])
            self.assertIn("EXECUTE", types)
            self.assertIn("RESULT_INGESTION", types)
        investigation = self.payload["views"]["investigation"]
        self.assertEqual(5, len(investigation["observations"]))
        self.assertEqual([], investigation["evidence_links"])  # observation, not evidence

    # 2. time series --------------------------------------------------------------------
    def test_time_series_come_from_the_issued_artifacts(self):
        history_ref, rollout_ref = (InformationRef(**ref) for ref in
                                    self.payload["views"]["artifacts"]["artifacts"])
        self.assertEqual(("HistoryWindowArtifact", "RolloutTelemetryArtifact"),
                         (history_ref.kind, rollout_ref.kind))
        history = self.queries.get_artifact(history_ref)["content"]
        rollout = self.queries.get_artifact(rollout_ref)["content"]
        interval = e0.RECORD_INTERVAL_SECONDS / 3600
        fork_time = self.payload["fork"]["simulation_time_hours"]
        end = e0.DEMO_PRE_INCIDENT_HOURS + e0.DEMO_POST_INCIDENT_HOURS
        reference_history = {round(record["simulation_time_hours"] * 3600):
                             record["measurements"]
                             for record in self.payload["views"]["telemetry"]["records"]}
        for name in e0.VARIABLES:
            with self.subTest(variable=name):
                series = self.payload["series"][name]
                reference = series["reference"]["points"]
                counterfactual = series["counterfactual"]["points"]
                for points in (reference, counterfactual):
                    times = [point[0] for point in points]
                    self.assertEqual(sorted(set(times)), times)  # strictly increasing
                self.assertAlmostEqual(0.0, reference[0][0], places=9)
                self.assertAlmostEqual(end, reference[-1][0], places=9)
                self.assertAlmostEqual(fork_time, counterfactual[0][0], places=9)
                self.assertAlmostEqual(end, counterfactual[-1][0], places=9)
                self.assertAlmostEqual(e0.ROLLOUT_HORIZON_HOURS,
                                       counterfactual[-1][0] - counterfactual[0][0], places=9)
                self.assertEqual(round(e0.HISTORY_WINDOW_HOURS / interval) + 1, len(reference))
                self.assertEqual(round(e0.ROLLOUT_HORIZON_HOURS / interval) + 1,
                                 len(counterfactual))
                # verbatim artifact values, and the history artifact matches TelemetryView
                self.assertEqual([[row["simulation_time_hours"], row[name]] for row in history],
                                 reference)
                self.assertEqual([[row["simulation_time_hours"], row["measurements"][name]]
                                  for row in rollout], counterfactual)
                self.assertEqual([reference_history[round(t * 3600)][name] for t, _ in reference],
                                 [value for _, value in reference])
                self.assertEqual((history_ref.ref_id, rollout_ref.ref_id),
                                 (series["reference"]["artifact_id"],
                                  series["counterfactual"]["artifact_id"]))
        comparison = self.payload["comparison"]
        self.assertEqual("EXACT_TIMESTAMPS", comparison["alignment"]["policy"])
        self.assertEqual((to_jsonable(history_ref), to_jsonable(rollout_ref)),
                         (comparison["reference_ref"], comparison["candidate_ref"]))
        self.assertEqual(list(e0.VARIABLES), [row["variable"] for row in comparison["variables"]])
        for row in comparison["variables"]:
            self.assertEqual(list(e0.COMPARISON_METRICS),
                             [metric["metric"] for metric in row["metrics"]])

    # 3. branch tree --------------------------------------------------------------------
    def test_branch_tree_has_connected_baseline_lineage(self):
        tree = self.payload["views"]["branch_tree"]
        nodes = {node["handle"]: node for node in tree["nodes"]}
        baseline = self.demo.harness.baseline_handle
        self.assertEqual(("SNAPSHOT", tree["root"]["handle"], "baseline"),
                         (nodes[baseline]["kind"], nodes[baseline]["parent"],
                          nodes[baseline]["lineage"]))
        branches = [node for node in tree["nodes"] if node["kind"] == "BRANCH"]
        self.assertEqual([(e0.EXPECTED_BRANCH, baseline, "baseline")],
                         [(node["handle"], node["parent"], node["lineage"]) for node in branches])
        self.assertEqual((e0.EXPECTED_BRANCH, baseline),
                         (self.payload["fork"]["branch_id"],
                          self.payload["fork"]["parent_snapshot_id"]))
        self.assertEqual(e0.EXPECTED_BRANCH, self.payload["rollout"]["branch_id"])
        self.assertEqual([{"snapshot_id": baseline}], self.payload["baseline_snapshots"])
        self.assertEqual([], e0.lineage_findings(self.payload, baseline))
        self.assertNotEqual([], e0.lineage_findings(self.payload, "snapshot-9999"))

    # 4. AGENT visibility ---------------------------------------------------------------
    def test_developer_metadata_never_enters_the_agent_payload(self):
        assert_blind(self, self.payload)
        text = canonical_json(self.payload)
        for hidden in ("IDV", "active_disturbances", "evaluator", "disturbance", "fault",
                       "candidate_cause"):
            self.assertNotIn(hidden.lower(), text.lower())
        self.assertEqual(json.loads(json.dumps(self.payload)),
                         agent_block(self.html, "agent-payload"))
        assert_blind(self, agent_block(self.html, "agent-payload"))
        developer = agent_block(self.html, "developer-setup")
        self.assertEqual("IDV(4)", developer["known_injected_cause"])
        self.assertEqual(e0.DEVELOPER_LABEL, developer["label"])
        self.assertNotEqual([], leakage_findings(developer))  # it is hidden truth, kept apart
        with self.assertRaises(ValueError):  # the payload is AGENT-scope only
            e0.build_agent_report(self.demo.manager.queries(
                self.demo.run_id, ProjectionScope.EVALUATOR))
        directory = self.session.directory / "trace" / "projections"
        projections = [json.loads(path.read_bytes()) for path in directory.iterdir()]
        self.assertEqual(TURNS, len(projections))
        for projection in projections:  # the model context never saw the demo setup
            assert_blind(self, projection)

    # 5. exact artifacts ----------------------------------------------------------------
    def test_report_reads_artifacts_by_exact_refs_with_checksums(self):
        listed = [InformationRef(**ref) for ref in self.payload["views"]["artifacts"]["artifacts"]]
        with mock.patch.object(RunQueries, "get_artifact", autospec=True,
                               side_effect=RunQueries.get_artifact) as spy:
            e0.build_agent_report(self.queries)
        self.assertEqual(listed, [call.args[1] for call in spy.call_args_list])
        self.assertTrue(all(type(call.args[1]) is InformationRef for call in spy.call_args_list))
        path = self.session.directory / "lab-artifacts" / f"{listed[0].ref_id}.jsonl"
        original = path.read_bytes()
        try:
            path.write_bytes(original.replace(b"0", b"1", 1))
            with self.assertRaises(UnknownArtifact):  # checksum verification is preserved
                e0.build_agent_report(self.queries)
        finally:
            path.write_bytes(original)
        for form in (str(self.root.resolve()), self.root.resolve().as_posix(),
                     json.dumps(str(self.root.resolve()))[1:-1], str(ROOT), ROOT.as_posix(),
                     str(Path.home()), "lab-artifacts", "prepare-0001", ".worktrees"):
            self.assertNotIn(form, self.html)
        self.assertIsNone(re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/home/|/Users/|/tmp/",
                                    self.html))

    # report derivations ----------------------------------------------------------------
    def test_page_embeds_each_block_once_and_parses(self):
        for block in ("agent-payload", "developer-setup"):
            self.assertEqual(1, self.html.count(f'id="{block}"'))
        self.assertNotIn("__AGENT_JSON__", self.html)
        self.assertNotIn("__DEVELOPER_JSON__", self.html)
        self.assertEqual("\\u003c/script\\u003e \\u0026",
                         e0._script_json("</script> &")[1:-1])
        tricky = {**self.payload, "note": "__DEVELOPER_JSON__ __AGENT_JSON__"}
        spliced = e0.render_html(tricky, self.developer)  # inserted text is never rescanned
        self.assertEqual(json.loads(json.dumps(tricky)), agent_block(spliced, "agent-payload"))
        self.assertEqual(self.developer, agent_block(spliced, "developer-setup"))
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed; inline script syntax not checked")
        script = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertEqual(1, len(script))
        checked = subprocess.run([node, "--check", "-"], input=script[0], text=True,
                                 capture_output=True, encoding="utf-8")
        self.assertEqual(0, checked.returncode, checked.stderr)

    def test_topology_is_read_off_the_graph_edges(self):
        graph = self.payload["views"]["process_graph"]
        topology = self.payload["topology"]
        self.assertEqual(e0.SCHEMATIC_LABEL, topology["label"])
        self.assertEqual(topology, e0._topology(graph))  # deterministic
        ids = {node["node_id"] for node in graph["nodes"]}
        self.assertEqual(ids, set(topology["layout"]))
        self.assertEqual(len(ids), len({tuple(cell) for cell in topology["layout"].values()}))
        pairs = {(edge["source_node"], edge["target_node"]) for edge in graph["edges"]}
        for node, around in topology["neighborhoods"].items():
            self.assertEqual({(node, other) for other in around["downstream"]},
                             {pair for pair in pairs if pair[0] == node})
            self.assertEqual({(other, node) for other in around["upstream"]},
                             {pair for pair in pairs if pair[1] == node})
        reactor = topology["neighborhoods"]["reactor"]
        self.assertIn("XMEAS(9)", reactor["measurements"])  # bound in the ProcessGraph view

    # E0.1: geometry, semantic resolution and P0-only dynamic overlay -------------------
    def test_schematic_covers_actual_p0_graph_exactly_once(self):
        graph = self.payload["views"]["process_graph"]
        svg = e0.SCHEMATIC_ASSET.read_text(encoding="utf-8")
        coverage = e0.validate_schematic(svg, graph)
        self.assertEqual(sorted(n["node_id"] for n in graph["nodes"]), coverage["nodes"])
        self.assertEqual(sorted(e["edge_id"] for e in graph["edges"]), coverage["edges"])
        self.assertEqual("0.2.0", graph["provenance"]["fixture_version"])
        self.assertEqual("HUMAN_VERIFIED", graph["provenance"]["review_status"])
        self.assertEqual("cc8ccc81e9f421238863457438465877850b19d9760740279e54a52468fe9a87",
                         graph["provenance"]["content_sha256"])

    def test_schematic_unknown_and_duplicate_ids_fail_closed(self):
        svg = e0.SCHEMATIC_ASSET.read_text(encoding="utf-8")
        graph = self.payload["views"]["process_graph"]
        for attr, identity in (("data-process-node-id", "reactor"),
                               ("data-process-edge-id", "stream_6")):
            for broken in (svg.replace(f'{attr}="{identity}"', f'{attr}="unknown"'),
                           svg.replace("</svg>", f'<g {attr}="{identity}"/></svg>')):
                with self.subTest(attribute=attr), self.assertRaises(ValueError):
                    e0.validate_schematic(broken, graph)
                with mock.patch.object(Path, "read_text", return_value=broken):
                    with self.assertRaises(ValueError):
                        e0.render_html(self.payload, self.developer)

    def test_schematic_rejects_executable_external_and_hidden_content(self):
        svg = e0.SCHEMATIC_ASSET.read_text(encoding="utf-8")
        for extra in ('<script/>', '<g onclick="run()"/>', '<g href="file.svg"/>',
                      '<path style="fill:url(https://example.com/x)"/>', '<text>IDV(4)</text>'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                e0.validate_schematic(svg.replace("</svg>", extra + "</svg>"),
                                      self.payload["views"]["process_graph"])
        for hidden in ("IDV", "active_disturbances", "fault", "candidate_cause", "evaluator"):
            self.assertNotIn(hidden.lower(), svg.lower())

    def test_highlights_equal_binding_search_and_follow_changed_graph(self):
        graph = self.payload["views"]["process_graph"]
        overlay = self.payload["process_overlay"]
        for name in e0.VARIABLES:
            expected = {category: [entity[key] for entity in graph[category]
                                   if any(b["runtime_variable_id"] == name
                                          for b in entity["bindings"])]
                        for category, key in (("nodes", "node_id"), ("edges", "edge_id"))}
            self.assertEqual(expected, overlay["signal_entities"][name])
        changed = copy.deepcopy(graph)
        source = next(n for n in changed["nodes"]
                      if any(b["runtime_variable_id"] == "XMEAS(9)" for b in n["bindings"]))
        binding = next(b for b in source["bindings"] if b["runtime_variable_id"] == "XMEAS(9)")
        source["bindings"].remove(binding)
        binding["attached_to"] = changed["edges"][0]["edge_id"]
        changed["edges"][0]["bindings"].append(binding)
        derived = e0.process_overlay(changed, self.payload["views"]["telemetry"], e0.VARIABLES)
        self.assertEqual({"nodes": [], "edges": [changed["edges"][0]["edge_id"]]},
                         derived["signal_entities"]["XMEAS(9)"])

    def test_detail_entities_and_plottable_bindings_come_from_p0(self):
        overlay = self.payload["process_overlay"]
        for category, key in (("nodes", "node_id"), ("edges", "edge_id")):
            for entity in self.payload["views"]["process_graph"][category]:
                record = overlay["entities"][category][entity[key]]
                self.assertEqual(entity, record["entity"])
                self.assertEqual(entity["bindings"], [r["binding"] for r in record["signals"]])
                for row in record["signals"]:
                    self.assertEqual(row["binding"]["runtime_variable_id"] in self.payload["series"],
                                     row["plottable"])

    def test_current_values_are_copied_only_from_p0_telemetry(self):
        telemetry = copy.deepcopy(self.payload["views"]["telemetry"])
        telemetry["current"]["measurements"]["XMEAS(9)"] = 123.456
        telemetry["current"]["measurements"].pop("XMEAS(21)")
        with mock.patch.object(RunQueries, "telemetry", return_value=telemetry):
            report = e0.build_agent_report(self.queries)
        current = telemetry["current"]
        values = {**current["measurements"], **current["manipulated_variables"]}
        for category in ("nodes", "edges"):
            for entity in report["process_overlay"]["entities"][category].values():
                for row in entity["signals"]:
                    name = row["binding"]["runtime_variable_id"]
                    self.assertEqual(name in values, row["telemetry_present"])
                    self.assertEqual(values.get(name), row["current_value"])
                    self.assertEqual(current["simulation_time_hours"] if name in values else None,
                                     row["simulation_time_hours"])
        self.assertNotEqual(report["series"]["XMEAS(9)"]["reference"]["points"][-1][1],
                            values["XMEAS(9)"])  # current projection is independent of artifacts

    def test_animation_requires_observed_positive_edge_flow_measurement(self):
        graph = self.payload["views"]["process_graph"]
        self.assertEqual({}, self.payload["process_overlay"]["animated_edges"])
        flow_edge = next(e for e in graph["edges"] if any(
            b["quantity"] == "flow" and b["relation"] == "MEASURES" for b in e["bindings"]))
        flow = next(b for b in flow_edge["bindings"]
                    if b["quantity"] == "flow" and b["relation"] == "MEASURES")
        telemetry = copy.deepcopy(self.payload["views"]["telemetry"])
        for value in (0, -1, 2):
            telemetry["current"]["measurements"][flow["runtime_variable_id"]] = value
            overlay = e0.process_overlay(graph, telemetry, e0.VARIABLES)
            expected = {flow_edge["edge_id"]: [flow["runtime_variable_id"]]} if value > 0 else {}
            self.assertEqual(expected, overlay["animated_edges"])
        telemetry["current"]["measurements"].pop(flow["runtime_variable_id"])
        actuator = next(b for b in flow_edge["bindings"] if b["relation"] == "ACTUATES")
        telemetry["current"]["manipulated_variables"][actuator["runtime_variable_id"]] = 50
        self.assertEqual({}, e0.process_overlay(graph, telemetry, e0.VARIABLES)["animated_edges"])
        self.assertIn("prefers-reduced-motion", self.html)

    def test_schematic_is_embedded_without_asset_dependencies(self):
        self.assertEqual(1, self.html.count('id="graph"'))
        self.assertIn('data-process-node-id="reactor"', self.html)
        self.assertIn('data-process-edge-id="stream_6"', self.html)
        self.assertIn("not authoritative P&amp;ID geometry", self.html)
        self.assertNotIn("__SCHEMATIC_SVG__", self.html)
        self.assertIsNone(re.search(r'\b(?:src|href)\s*=\s*[\"\']', self.html))

    def test_model_turn_matching_counts_operations_and_fails_closed(self):
        def trace(kind):
            return {"source": "runtime_trace", "type": kind, "status": "X", "position": 0}

        lab = {"source": "lab_run_log", "type": "RCA_STATE_UPDATE_ACCEPTED",
               "operations": ["REGISTER_OBSERVATION", "REGISTER_ARTIFACT_REF",
                              "REGISTER_OBSERVATION"]}
        observations = [{"observation_id": "a"}, {"observation_id": "b"}]
        ingestion = {**trace("RESULT_INGESTION"), "status": "ACCEPTED"}
        events = [trace("MODEL_TURN"), ingestion, lab, trace("MODEL_TURN")]
        turns = e0._model_turns(observations, events)
        self.assertEqual([["a", "b"], []],
                         [[item["observation_id"] for item in turn["observations"]]
                          for turn in turns])
        with self.assertRaises(ValueError):  # count mismatch
            e0._model_turns(observations[:1], events)
        with self.assertRaises(ValueError):  # unanchored registration at the feed's end
            e0._model_turns(observations, [trace("MODEL_TURN"), trace("MODEL_TURN"), lab])

    # CLI ------------------------------------------------------------------------------
    def test_cli_refuses_to_overwrite_run_records_or_reports(self):
        lifecycle = self.root / "p0-runs" / self.demo.run_id / "lifecycle" / "events.jsonl"
        before = lifecycle.read_bytes()
        with mock.patch("sys.stderr"):
            code = e0.main(["--output-root", str(self.root), "--run-id", self.demo.run_id,
                            "--lab-revision", lab_revision()])
        self.assertEqual(2, code)
        self.assertEqual(before, lifecycle.read_bytes())
        report = e0.report_path(self.root, "e0-other")
        e0.write_report(report, self.html)
        with self.assertRaises(FileExistsError):
            e0.write_report(report, self.html)
        with mock.patch("sys.stderr"):
            self.assertEqual(2, e0.main(["--output-root", str(self.root),
                                         "--run-id", "e0-other"]))
        self.assertFalse((self.root / "p0-runs" / "e0-other").exists())
        with mock.patch("sys.stderr"):  # rejected before any run record is created
            self.assertEqual(2, e0.main(["--output-root", str(self.root), "--run-id", "E0"]))
            self.assertEqual(2, e0.main(["--output-root", str(self.root), "--run-id",
                                         "e0-third", "--lab-revision", "abc123"]))
        self.assertFalse((self.root / "p0-runs" / "e0-third").exists())

    def test_budget_usage_keys_match_the_page_mapping(self):
        budget = self.payload["views"]["budget"]
        usage = self.demo.outcome.runtime_result["budget_usage"]
        for name in ("model_calls", "tool_calls", "steps"):  # page maps max_<name> -> <name>
            self.assertIn(f"max_{name}", budget["limits"])
            self.assertEqual(usage[name], budget["usage"][name])
        self.assertEqual(TURNS, budget["usage"]["model_calls"])


if __name__ == "__main__":
    unittest.main()
