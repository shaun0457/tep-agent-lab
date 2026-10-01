"""tep-sim 0.2.0 adoption: exact pins and the promoted HUMAN_VERIFIED ProcessGraph.

The promotion asserts that ProcessGraph 0.2.0 is semantically identical to 0.1.0;
only provenance (version, content hash, review status/record, binding method) differs.
Any Agent-visible engineering difference here is a SPEC_CONFLICT, not a test update.
"""

from importlib import resources
import json
from pathlib import Path
import re
import tomllib
import unittest

from tep_sim import load_process_graph
from tep_sim.process import PACKAGED_GRAPH_FIXTURES

ROOT = Path(__file__).resolve().parents[1]
TEP_SIM_SHA = "ea0b7304090017b14ac13665595b6f5e9d195250"
RUNTIME_SHA = "92651cf305ad735871f26006a790f561fff82654"
PROMOTED_GRAPH_SHA256 = "cc8ccc81e9f421238863457438465877850b19d9760740279e54a52468fe9a87"


def agent_visible_semantics(graph) -> dict:
    """Everything C4 can project from a graph, minus graph provenance."""
    nodes = [node.node_id for node in graph.nodes()]

    def described(found):
        return sorted((binding.describe() for binding in found),
                      key=lambda item: json.dumps(item, sort_keys=True))

    view = {}
    for node_id in nodes:
        local = graph.project_local(node_id).as_dict()
        local.pop("graph")
        view[node_id] = {
            "local": local,
            "upstream": sorted(graph.upstream(node_id, max_depth=None)),
            "downstream": sorted(graph.downstream(node_id, max_depth=None)),
            "measurements": described(graph.measurements(node_id,
                                                         include_incident_streams=True)),
            "actuators": described(graph.actuators(node_id, include_incident_streams=True)),
        }
    return {"nodes": view,
            "edges": sorted((edge.edge_id, edge.kind.value, edge.source_node,
                             edge.target_node, edge.name, edge.stream_number)
                            for edge in graph.edges()),
            "bindings": described(graph.bindings())}


class DependencyPinTests(unittest.TestCase):
    def test_exact_tep_sim_and_runtime_pins(self):
        pins = json.loads((ROOT / "dependency-pins.json").read_text())
        self.assertEqual(TEP_SIM_SHA, pins["tep-sim"])
        self.assertEqual(RUNTIME_SHA, pins["industrial-agent-runtime"])
        with open(ROOT / "pyproject.toml", "rb") as file:
            declared = tomllib.load(file)["project"]["dependencies"]
        self.assertIn("tep-sim==0.2.0", declared)
        self.assertIn("industrial-agent-runtime==0.1.0", declared)
        # each CI checkout step pins its own repository to the pins-file revision
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        refs = dict(re.findall(r"^\s*repository:\s*shaun0457/(\S+)\s*\n\s*ref:\s*(\S+)\s*$",
                               ci, re.MULTILINE))
        self.assertEqual({name: pins[name] for name in ("industrial-agent-runtime", "tep-sim")},
                         refs)


class PromotedProcessGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_process_graph()
        historical = resources.files("tep_sim.fixtures") / PACKAGED_GRAPH_FIXTURES["0.1.0"]
        with resources.as_file(historical) as path:
            cls.historical = load_process_graph(path)

    def test_default_graph_is_the_pinned_human_verified_0_2_0(self):
        provenance = self.graph.provenance
        self.assertEqual(("tep-process-graph", "0.2.0"),
                         (provenance.fixture_id, provenance.fixture_version))
        self.assertEqual("HUMAN_VERIFIED", provenance.review_status)
        self.assertTrue(provenance.pinned)
        self.assertEqual(PROMOTED_GRAPH_SHA256, provenance.content_sha256)
        self.assertIsNotNone(provenance.review_record)

    def test_promotion_is_semantically_identical_to_0_1_0(self):
        old = self.historical.provenance
        self.assertEqual(("0.1.0", "PENDING_HUMAN_REVIEW"), (old.fixture_version,
                                                             old.review_status))
        self.assertNotEqual(old.content_sha256, self.graph.provenance.content_sha256)
        self.assertEqual(agent_visible_semantics(self.historical),
                         agent_visible_semantics(self.graph))


if __name__ == "__main__":
    unittest.main()
