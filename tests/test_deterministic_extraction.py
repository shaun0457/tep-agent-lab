"""S4 source-only tests; no benchmark, scorer or evaluator inputs."""

from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import subprocess
import sys
import unittest

from tep_agent_lab.context_compiler import (
    CandidateBinding, CandidateKnowledge, CompileSession, FindingCode,
    candidate_from_record, validate_candidate,
)
from tep_agent_lab.deterministic_extraction import (
    ExtractionError, extract_candidates, load_rules, status_flags,
)
from tep_agent_lab.engineering_corpus import prepare_candidate_demo
from tep_agent_lab.persistence import canonical_json, strict_json_loads
from tep_agent_lab.rules import Authority, Origin, Validation

REVISION = "1" * 40
GOLDEN = Path(__file__).with_name("deterministic_extraction_golden.json")


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session, cls.rules = prepare_candidate_demo(
            repository="example/engineering-lab", git_revision=REVISION)
        cls.result = extract_candidates(cls.session, cls.rules)

    def rule_data(self):
        return strict_json_loads(self.rules.raw)

    def altered_session(self, source_suffix, old, new):
        session = self.session
        contents = {ref.path_or_ref: session.source_content(ref) for ref in session.inventory}
        refs = []
        for ref in session.inventory:
            if ref.source_id.endswith(source_suffix):
                contents[ref.path_or_ref] = contents[ref.path_or_ref].replace(old, new)
                ref = replace(ref, content_checksum=hashlib.sha256(
                    contents[ref.path_or_ref]).hexdigest())
            refs.append(ref)

        class Materializer:
            def read_bytes(self, path):
                return contents[path]

        return CompileSession.prepare(
            {ref.repository: ref.git_revision for ref in refs},
            {ref.repository: Materializer() for ref in refs}, refs,
            process_graph_source_id=session.process_graph_source_id,
            input_rule_set_source_id=session.input_rule_set_source_id,
            plant_graph_loader=session.plant_graph_loader, policies=session.policies)

    def test_strict_full_golden_and_roundtrip(self):
        raw = GOLDEN.read_bytes()
        golden = strict_json_loads(raw)
        self.assertEqual(raw, canonical_json(golden) + b"\n")
        self.assertEqual(self.result.record(), golden)
        for record in golden["candidates"]:
            self.assertEqual(candidate_from_record(record).record(), record)

    def test_every_candidate_exact_source_fragments_and_route(self):
        known = frozenset(c.candidate_id for c in self.result.candidates)
        for candidate in self.result.candidates:
            self.assertIsNone(candidate.provenance.model)
            self.assertIsNone(candidate.provenance.confidence)
            self.assertEqual(candidate.provenance.extraction_method, self.rules.method)
            self.assertEqual(candidate.provenance.derived_inputs[0].content_checksum,
                             hashlib.sha256(self.rules.raw).hexdigest())
            for fragment in candidate.fragments():
                data = self.session.source_content(fragment.source_ref)
                self.assertTrue(fragment.verify(data))
                self.assertEqual(fragment.content_checksum, hashlib.sha256(
                    data[fragment.location.start:fragment.location.end]).hexdigest())
                self.assertEqual(fragment.fragmentation_policy,
                                 "markdown.sections-paragraphs-tables-steps@1")
            findings = validate_candidate(self.session, candidate, known_candidate_ids=known)
            codes = {f.code for f in findings.findings}
            self.assertFalse(codes & {FindingCode.FRAGMENT_INVALID,
                                     FindingCode.SOURCE_UNRESOLVED, FindingCode.RULE_INVALID,
                                     FindingCode.CONFLICT_UNRESOLVED})

    def test_unambiguous_outlet_binding(self):
        candidate = next(c for c in self.result.candidates if isinstance(c, CandidateBinding)
                         and c.mention_text == "Reactor cooling-water outlet temperature")
        self.assertEqual(candidate.proposed_target.target_id, "reactor_cooling.outlet_temperature")
        data = self.session.source_content(candidate.mention_fragment.source_ref)
        self.assertIn(b"XMEAS(21)", data[candidate.mention_fragment.location.start:
                                        candidate.mention_fragment.location.end])
        self.assertTrue(validate_candidate(self.session, candidate).passed)
        self.assertEqual(status_flags(self.session, candidate), ("unreviewed",))

    def test_ambiguous_binding_needs_review(self):
        candidate = next(c for c in self.result.candidates if isinstance(c, CandidateBinding)
                         and c.mention_text == "reactor-side temperature")
        self.assertIsNone(candidate.proposed_target)
        self.assertEqual(len(candidate.alternative_targets), 2)
        self.assertIn("ambiguous", status_flags(self.session, candidate))
        self.assertFalse(validate_candidate(self.session, candidate).passed)

    def test_limits_conflict_distinct_shutdown_and_inert_rules(self):
        limits = [c for c in self.result.candidates if c.extraction_kind == "OPERATING_LIMIT"]
        normal = [c for c in limits if "normal_upper <=" in c.rule.extraction.claim
                  and "reactor.temperature_measurement" in c.rule.inputs]
        self.assertEqual(len(normal), 2)
        self.assertEqual({c.rule.extraction.claim.rsplit(" <= ", 1)[1] for c in normal},
                         {"150 degC", "145 degC"})
        for candidate in normal:
            other = next(c for c in normal if c is not candidate)
            self.assertEqual(candidate.conflicting_candidate_ids, (other.candidate_id,))
            self.assertEqual(candidate.conflicting_fragments, other.source_fragments)
            self.assertIn("conflict", status_flags(self.session, candidate))
        shutdown = next(c for c in limits if c.rule.extraction.claim.endswith("175 degC"))
        self.assertIn("shutdown_high <=", shutdown.rule.extraction.claim)
        self.assertFalse(shutdown.conflicting_candidate_ids)
        for candidate in limits:
            self.assertEqual((candidate.rule.origin, candidate.rule.validation,
                              candidate.rule.authority),
                             (Origin.LITERATURE, Validation.NONE, Authority.REFERENCE))

    def test_unregistered_inlet_tag_is_not_invented(self):
        candidate = next(c for c in self.result.candidates if isinstance(c, CandidateBinding)
                         and "RCW-IN-TT" in c.mention_text)
        self.assertIsNone(candidate.proposed_target)
        self.assertFalse(candidate.alternative_targets)
        self.assertIn("no-matching-signal", status_flags(self.session, candidate))
        self.assertFalse(validate_candidate(self.session, candidate).passed)

    def test_control_relation_exact_phrase(self):
        candidate = next(c for c in self.result.candidates if c.extraction_kind == "CONTROL_RELATION")
        self.assertIsInstance(candidate, CandidateKnowledge)
        self.assertIn("Increasing cooling flow", candidate.rule.extraction.claim)
        self.assertEqual(candidate.source_fragments[0].location.semantic_locator,
                         "reactor-and-cooling-water-loop/control-relation/paragraph-1")

    def test_unknown_rows_and_unmatched_cells_are_reported(self):
        session = self.altered_session(".tags-and-limits", b"| XMEAS(7) |", b"| UNKNOWN |")
        result = extract_candidates(session, self.rules)
        self.assertEqual(len(result.candidates), len(self.result.candidates) - 3)
        self.assertTrue(any(u.reason == "unmatched-row" for u in result.unsupported))
        session = self.altered_session(".tags-and-limits", b"normal upper limit 150",
                                       b"normal upper limit unknown")
        result = extract_candidates(session, self.rules)
        self.assertTrue(any(u.cell == "Normal value/range" and "unknown" in u.text
                            for u in result.unsupported))
        self.assertFalse(any(isinstance(c, CandidateKnowledge) and c.rule.extraction.claim.endswith(
            "150 degC") for c in result.candidates))

    def test_missing_phrase_or_locator_fails_closed(self):
        for key, value in (("phrase", "absent exact phrase"), ("locator", "absent/paragraph-1")):
            data = self.rule_data()
            rule = next(r for r in data["rules"] if r["form"] == "phrase")
            rule[key] = value
            with self.assertRaises(ExtractionError):
                extract_candidates(self.session, load_rules(canonical_json(data)))

    def test_version_changes_version_not_content(self):
        data = self.rule_data()
        data["version"] = "2"
        result = extract_candidates(self.session, load_rules(canonical_json(data)))
        for left, right in zip(self.result.candidates, result.candidates, strict=True):
            self.assertEqual(left.candidate_id, right.candidate_id)
            self.assertEqual(left.content_key, right.content_key)
            self.assertNotEqual(left.candidate_version, right.candidate_version)

    def test_determinism_and_immutability(self):
        result = extract_candidates(self.session, self.rules)
        self.assertEqual(canonical_json(result.record()), canonical_json(self.result.record()))
        with self.assertRaises(FrozenInstanceError):
            result.candidates[0].mention_text = "changed"

    def test_strict_schema(self):
        mutations = [lambda d: d.update(extra=True), lambda d: d.update(version=True),
                     lambda d: d["rules"][0].update(extra=True),
                     lambda d: d["rules"][0]["item"].update(kind="UNKNOWN"),
                     lambda d: d["rules"].append(d["rules"][0]),
                     lambda d: d["rules"][0]["matches"].update(Tag="["),
                     lambda d: d["rules"][0]["item"]["scope"][0].update(extra=True)]
        for mutate in mutations:
            data = self.rule_data()
            mutate(data)
            with self.assertRaises(ExtractionError):
                load_rules(canonical_json(data))
        for raw in (b'{"version":"1","version":"2"}', b'{"version":NaN}'):
            with self.assertRaises(ExtractionError):
                load_rules(raw)

    def test_invalid_units_and_numbers_remain_unsupported(self):
        for old, new in ((b"| degC | Base 120.40", b"| unknown-unit | Base 120.40"),
                         (b"normal upper limit 150", b"normal upper limit NaN"),
                         (b"normal upper limit 150", b"normal upper limit 9007199254740993")):
            session = self.altered_session(".tags-and-limits", old, new)
            result = extract_candidates(session, self.rules)
            self.assertTrue(any(u.reason == "unmatched-cell" and u.text.find(
                new.decode().split("|")[1].strip() if b"|" in new else new.decode()) >= 0
                for u in result.unsupported))
            self.assertFalse(any(isinstance(c, CandidateKnowledge) and
                                 c.rule.extraction.claim.endswith("150 degC")
                                 for c in result.candidates))

    def test_entity_mention_uses_existing_binding_route(self):
        data = self.rule_data()
        item = data["rules"][0]["item"]
        item.update(kind="ENTITY_MENTION", target_kind="ENTITY", targets=["reactor"])
        result = extract_candidates(self.session, load_rules(canonical_json(data)))
        candidate = result.candidates[0]
        self.assertIsInstance(candidate, CandidateBinding)
        self.assertEqual(candidate.extraction_kind, "ENTITY_MENTION")
        self.assertEqual(candidate.proposed_target.target_id, "reactor")

    def test_changed_exact_bytes_fail_before_extraction(self):
        session = self.session
        refs = session.inventory
        contents = {ref.path_or_ref: session.source_content(ref) for ref in refs}

        class Materializer:
            def read_bytes(self, path):
                return contents[path]

        prepared = CompileSession.prepare(
            {ref.repository: ref.git_revision for ref in refs},
            {ref.repository: Materializer() for ref in refs}, refs,
            process_graph_source_id=session.process_graph_source_id,
            input_rule_set_source_id=session.input_rule_set_source_id,
            plant_graph_loader=session.plant_graph_loader, policies=session.policies)
        ref = next(ref for ref in refs if ref.source_id.endswith(".tags-and-limits"))
        contents[ref.path_or_ref] += b"changed bytes"
        with self.assertRaises(ExtractionError):
            extract_candidates(prepared, self.rules)

    def test_generic_engine_no_domain_literals(self):
        import tep_agent_lab.deterministic_extraction as engine
        text = Path(engine.__file__).read_text(encoding="utf-8")
        for literal in ("XMEAS", "XMV", "IDV", "tep_sim", "reactor"):
            self.assertNotIn(literal, text)

    def test_demo(self):
        result = subprocess.run([sys.executable, "-m", "tep_agent_lab.engineering_corpus",
                                 "candidates", "--git-revision", REVISION],
                                text=True, capture_output=True, check=True)
        for phrase in ("SIGNAL_MENTION", "OPERATING_LIMIT", "CONTROL_RELATION", "ambiguous",
                       "conflict", "no-matching-signal", "UNSUPPORTED", "fragment:"):
            self.assertIn(phrase, result.stdout)


if __name__ == "__main__":
    unittest.main()
