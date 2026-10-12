"""Source-only S5 evidence; no benchmark fixtures or evaluator inputs."""

import copy
import hashlib
import inspect
import json
from pathlib import Path
import unittest

from industrial_agent_runtime import Visibility
from industrial_agent_runtime.serialization import to_jsonable

from tep_agent_lab import staged_revision_demo as pipeline
from tep_agent_lab.context_compiler import (
    PublicationError, candidate_from_record, verify_publication,
)
from tep_agent_lab.engineering_corpus import prepare_candidate_demo
from tep_agent_lab.persistence import canonical_json, strict_json_loads


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "src/tep_agent_lab/engineering_corpus/tep_reactor_cooling/demo-review.json"
GOLDEN = Path(__file__).with_name("staged_revision_demo_golden.json")
REPOSITORY = "shaun0457/tep-agent-lab"
PLANT_REPOSITORY = "shaun0457/tep-sim"


class StagedRevisionDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = REVIEW.read_bytes()
        cls.data = strict_json_loads(cls.raw)
        cls.session, cls.rules = prepare_candidate_demo(
            repository=REPOSITORY,
            git_revision=cls.data["input_revisions"][REPOSITORY],
            plant_git_revision=cls.data["input_revisions"][PLANT_REPOSITORY],
            review_policy=cls.data["review_policy"])

    def stage(self, data=None):
        raw = self.raw if data is None else canonical_json(data)
        return pipeline.stage_revision(self.session, self.rules, raw)

    def verify(self, staged, summary, files=None):
        return verify_publication(
            dict(staged.files) if files is None else files, staged.revision, self.session,
            anchor=summary.anchor,
            derived_input_resolver=pipeline.extraction_input_resolver(self.rules))

    @staticmethod
    def items(staged):
        return (*staged.document["approved_bindings"],
                *staged.document["approved_knowledge"],
                *staged.document["rule_scope_bindings"])

    def test_end_to_end_and_rule_axes(self):
        staged, summary = self.stage()
        verified = self.verify(staged, summary)
        self.assertEqual(summary.verification, "PASS")
        self.assertFalse(staged.canonical)
        self.assertIsNone(staged.expected_head)
        self.assertEqual((len(summary.approved), len(summary.rejected),
                          len(summary.deferred)), (10, 2, 0))
        self.assertEqual(len(verified.published_rule_set.registry.rules()), 5)
        for rule in verified.published_rule_set.registry.rules():
            self.assertEqual((rule.origin.value, rule.validation.value, rule.authority.value),
                             ("LITERATURE", "NONE", "REFERENCE"))

    def test_rejections_only_in_audit_not_approved_content(self):
        staged, summary = self.stage()
        refs = {tuple(item["candidate_ref"].values()) for item in self.items(staged)}
        for row in summary.rejected:
            self.assertNotIn(tuple(row["candidate_ref"].values()), refs)
            self.assertTrue(row["reason"])
        claims = [rule.extraction.claim for rule in
                  self.verify(staged, summary).published_rule_set.registry.rules()]
        self.assertFalse(any("145 degC" in claim for claim in claims))
        self.assertTrue(any("150 degC" in claim for claim in claims))
        self.assertTrue(any("175 degC" in claim for claim in claims))
        self.assertFalse(any("RCW-IN-TT" in str(item) for item in self.items(staged)))

    def test_reviewer_remap_keeps_original_and_revalidates(self):
        staged, summary = self.stage()
        row = next(item for item in self.items(staged) if item["decision"] == "REMAP")
        self.assertEqual(row["target"]["target_id"], "reactor.temperature_measurement")
        log = strict_json_loads(dict(staged.files)[staged.review_log.path])
        candidate = next(candidate_from_record(c) for c in log["candidate_log"]
                         if c["candidate_id"] == row["candidate_ref"]["candidate_id"])
        self.assertIsNone(candidate.proposed_target)
        self.assertEqual(len(candidate.alternative_targets), 2)
        self.assertEqual(row["validation"]["passed"], True)
        self.assertEqual(next(item for item in summary.approved
                              if item["decision"] == "REMAP")["decision"], "REMAP")

    def test_explicit_deferred_never_enters_approved_content(self):
        data = copy.deepcopy(self.data)
        row = next(row for row in data["decisions"] if row["decision"] == "REMAP")
        data["decisions"].remove(row)
        data["deferred"].append({"candidate_ref": row["candidate_ref"],
                                 "reason": "Await a further human source review."})
        staged, summary = self.stage(data)
        self.verify(staged, summary)
        self.assertEqual(len(summary.deferred), 1)
        self.assertFalse(any(item["candidate_ref"] == row["candidate_ref"]
                             for item in self.items(staged)))

    def test_keep_candidate_is_unresolved(self):
        data = copy.deepcopy(self.data)
        row = next(row for row in data["decisions"] if row["decision"] == "REMAP")
        row.update(decision="KEEP_CANDIDATE", selected_target=None, selected_scope_refs=None)
        staged, summary = self.stage(data)
        self.assertEqual(len(summary.deferred), 1)
        self.assertFalse(any(item["candidate_ref"] == row["candidate_ref"]
                             for item in self.items(staged)))

    def test_exact_fragment_and_review_trace_for_every_item(self):
        staged, summary = self.stage()
        log = strict_json_loads(dict(staged.files)[staged.review_log.path])
        records = {row["review_id"]: row for row in log["records"]}
        candidates = {c.ref: c for c in
                      (candidate_from_record(row) for row in log["candidate_log"])}
        for item in self.items(staged):
            review = records[item["review_id"]]
            self.assertEqual(review["candidate_ref"], item["candidate_ref"])
            self.assertEqual(review["principal"], self.data["principal"])
            self.assertTrue(item["fragments"])
            candidate = next(c for ref, c in candidates.items()
                             if ref.record() == item["candidate_ref"])
            self.assertEqual([f.record() for f in candidate.fragments()],
                             to_jsonable(item["fragments"]))
            for fragment in candidate.fragments():
                self.assertTrue(fragment.verify(self.session.source_content(fragment.source_ref)))
        self.assertEqual(summary.anchor.review_log_position, len(self.data["decisions"]))

    def test_byte_identical_runs_and_golden(self):
        first, summary = self.stage()
        second, other = self.stage()
        self.assertEqual(first.files, second.files)
        self.assertEqual(first.revision, second.revision)
        self.assertEqual(first.head_record, second.head_record)
        self.assertEqual(summary.record(), other.record())
        golden = canonical_json({
            "summary": summary.record(),
            "files": {path: hashlib.sha256(raw).hexdigest() for path, raw in first.files}})
        self.assertEqual(golden, GOLDEN.read_bytes())

    def test_every_tampered_artifact_fails_verification(self):
        staged, summary = self.stage()
        for path, raw in staged.files:
            with self.subTest(path=path):
                files = dict(staged.files)
                files[path] = raw + b" "
                with self.assertRaises(PublicationError):
                    self.verify(staged, summary, files)

    def test_stale_or_unknown_candidate_version_fails_closed(self):
        for field, value in (("candidate_version", "0" * 64),
                             ("candidate_id", "unknown-demo-candidate")):
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                data["decisions"][0]["candidate_ref"][field] = value
                with self.assertRaises(pipeline.RecordedReviewError):
                    self.stage(data)

    def test_missing_duplicate_and_decided_plus_deferred_fail_closed(self):
        for mode in ("missing", "duplicate", "overlap"):
            data = copy.deepcopy(self.data)
            row = data["decisions"][0]
            if mode == "missing":
                data["decisions"].pop(0)
            elif mode == "duplicate":
                data["decisions"].append(copy.deepcopy(row))
            else:
                data["deferred"].append({"candidate_ref": row["candidate_ref"],
                                         "reason": "Deferred."})
            with self.subTest(mode=mode), self.assertRaises(pipeline.RecordedReviewError):
                self.stage(data)

    def test_strict_schema_and_principal(self):
        for mutate in (
                lambda d: d.update(extra=True),
                lambda d: d.update(schema_version="recorded-demo-review/v2"),
                lambda d: d["principal"].update(kind="MODEL"),
                lambda d: d["principal"].update(kind="DETERMINISTIC_POLICY"),
                lambda d: d["decisions"][0].update(extra=True),
                lambda d: d.update(session_id="stale")):
            data = copy.deepcopy(self.data)
            mutate(data)
            with self.assertRaises(pipeline.RecordedReviewError):
                self.stage(data)
        with self.assertRaises(pipeline.RecordedReviewError):
            pipeline.load_recorded_review(b'{"schema_version": 1, "schema_version": 1}')

    def test_changed_review_reason_changes_revision(self):
        first, _ = self.stage()
        data = copy.deepcopy(self.data)
        data["decisions"][0]["reason"] += " Recorded rationale clarified."
        second, summary = self.stage(data)
        self.verify(second, summary)
        self.assertNotEqual(first.revision, second.revision)

    def test_deferred_rationale_is_audited_and_changes_revision(self):
        data = copy.deepcopy(self.data)
        row = data["decisions"].pop(8)
        data["deferred"].append({"candidate_ref": row["candidate_ref"],
                                 "reason": "Await human clarification."})
        first, summary = self.stage(data)
        log = strict_json_loads(dict(first.files)[first.review_log.path])
        deferred = next(r for r in log["records"] if r["candidate_ref"] == row["candidate_ref"])
        self.assertEqual(deferred["decision"], "KEEP_CANDIDATE")
        self.assertEqual(deferred["reason"], data["deferred"][0]["reason"])
        self.assertEqual(summary.deferred[0]["review_id"], deferred["review_id"])
        data["deferred"][0]["reason"] += " A further source is needed."
        second, _ = self.stage(data)
        self.assertNotEqual(first.revision, second.revision)

    def test_hidden_review_metadata_cannot_widen_visibility(self):
        data = copy.deepcopy(self.data)
        data["visibility"] = Visibility.EVALUATOR.value
        with self.assertRaises(pipeline.RecordedReviewError):
            self.stage(data)

    def test_invalid_remap_is_excluded_by_contract_validation(self):
        data = copy.deepcopy(self.data)
        row = next(r for r in data["decisions"] if r["decision"] == "REMAP")
        row["selected_target"]["target_id"] = "nonexistent-signal"
        staged, summary = self.stage(data)
        self.verify(staged, summary)
        self.assertFalse(any(item["candidate_ref"] == row["candidate_ref"]
                             for item in self.items(staged)))
        self.assertEqual(summary.deferred[0]["exclusion"]["reason"], "REMAP_INVALID")

    def test_generic_pipeline_has_no_domain_tag_literals(self):
        source = inspect.getsource(pipeline)
        for tag in ("XMEAS", "XMV", "IDV"):
            self.assertNotIn(tag, source)

    def test_demo_cli(self):
        import contextlib
        import io
        from tep_agent_lab.engineering_corpus import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["stage"]), 0)
        text = output.getvalue()
        for marker in ("APPROVE", "REMAP", "REJECT", "DEFERRED", "145", "RCW-IN-TT",
                       "fragment:", "revision_id=", "checksum=", "verify_publication: PASS",
                       "canonical=False"):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
