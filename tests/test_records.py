import tempfile
import unittest
from dataclasses import replace

from industrial_agent_runtime import InformationRef, Visibility
from tep_agent_lab.persistence import RunLog
from tep_agent_lab.records import (
    DecisionRecord, EngineeringArchive, ExperimentRecord, InvestigationReport, RecordStatus,
)


class EngineeringRecordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = RunLog(tmp.name, {"run_id": "run1", "investigation_id": "i1"})
        self.refs = {name: InformationRef(name, name, "fixture", "v1", Visibility.AGENT, "2026-09-17")
                     for name in ("incident", "hypothesis", "evidence", "experiment", "run-spec", "result", "trace")}
        self.archive = EngineeringArchive(self.log, lambda ref: self.refs[ref.ref_id])

    def report(self, **changes):
        return InvestigationReport(**(dict(
            report_id="report", investigation_id="i1", generated_from_state_revision=7,
            incident_ref=self.refs["incident"], conclusion="Uncertain mechanism",
            selected_causal_claim="Candidate only", ranked_hypothesis_refs=(self.refs["hypothesis"],),
            supporting_evidence_link_refs=(self.refs["evidence"],), contradicting_evidence_link_refs=(),
            experiment_refs=(self.refs["experiment"],), decision_record_refs=(),
            uncertainty="Insufficient discrimination", unresolved_questions=("Which mechanism?",),
            trace_ref=self.refs["trace"], artifact_refs=(),
            revisions={"environment": "a1", "runtime": "9841347", "lab": "c1"},
            policy_versions={"model": "fake", "prompt": "v0", "tool": "v0", "rule_policy": "v0"},
            created_at="2026-09-17") | changes))

    def test_report_archive_has_exact_refs_revision_and_no_context_side_effects(self):
        report = self.report()
        ref = self.archive.store(report, version="1")
        saved = self.log.read_artifact(ref.checksum)
        self.assertEqual(7, saved["generated_from_state_revision"])
        self.assertEqual("VERIFIED", saved["status"])
        self.assertEqual("evidence", saved["supporting_evidence_link_refs"][0]["ref_id"])
        self.assertEqual(RecordStatus.DRAFT, report.status)
        self.assertEqual(["ENGINEERING_RECORD"], [e["type"] for e in self.log.events()])

    def test_unknown_ref_hidden_ref_and_metadata_spoof_fail_before_archive(self):
        missing = replace(self.refs["evidence"], ref_id="nonexistent")
        for ref, error in ((missing, KeyError),
                           (replace(self.refs["evidence"], visibility=Visibility.EVALUATOR), ValueError),
                           (replace(self.refs["evidence"], version="wrong"), ValueError)):
            with self.assertRaises(error):
                self.archive.store(self.report(supporting_evidence_link_refs=(ref,)), version="1")
        self.assertEqual((), self.log.events())

    def test_corrected_record_preserves_original_version(self):
        first = self.archive.store(self.report(), version="1")
        second = self.archive.store(self.report(conclusion="Corrected interpretation"),
                                    version="2", supersedes=first)
        self.assertNotEqual(first.checksum, second.checksum)
        self.assertEqual("Uncertain mechanism", self.log.read_artifact(first.checksum)["conclusion"])
        with self.assertRaises(ValueError):
            self.archive.store(self.report(), version="1")
        self.assertEqual(2, len(self.log.events()))

    def test_decision_links_exact_state_revision(self):
        record = DecisionRecord(decision_id="d1", investigation_id="i1", decision_type="EXPERIMENT",
                                decision="Test candidate", alternatives=("No experiment",),
                                evidence_refs=(self.refs["evidence"],), rule_or_policy_refs=(),
                                tradeoffs=("Uses one rollout",), uncertainty="Open", decided_by="fake",
                                state_revision=3, created_at="2026-09-17")
        ref = self.archive.store(record, version="1")
        self.assertEqual(3, self.log.read_artifact(ref.checksum)["state_revision"])

    def test_failed_experiment_record_references_numeric_truth_without_copying_it(self):
        budget = {"rollouts": 1}
        record = ExperimentRecord(record_id="er1", experiment_ref=self.refs["experiment"],
                                  hypothesis_refs=(self.refs["hypothesis"],), prediction_refs=(),
                                  run_spec_ref=self.refs["run-spec"], result_ref=self.refs["result"],
                                  outcome_summary="Failed execution retained", actual_budget_usage=budget,
                                  artifact_refs=(), provenance={"status": "FAILED", "scorer": "v0"},
                                  created_at="2026-09-17")
        budget["rollouts"] = 999
        ref = self.archive.store(record, version="1")
        saved = self.log.read_artifact(ref.checksum)
        self.assertEqual(1, saved["actual_budget_usage"]["rollouts"])
        self.assertEqual("result", saved["result_ref"]["ref_id"])
        self.assertNotIn("metric_values", saved)

    def test_invalid_record_schema_and_wrong_run_rejected(self):
        for changes in ({"generated_from_state_revision": -1}, {"revisions": {}},
                        {"status": "APPROVED"}, {"incident_ref": None}, {"conclusion": 10},
                        {"unresolved_questions": [{"mutable": []}]}):
            with self.assertRaises((ValueError, TypeError)):
                self.report(**changes)
        with self.assertRaises(ValueError):
            self.archive.store(self.report(investigation_id="other-run"), version="1")

    def test_report_status_cannot_silently_escalate(self):
        rejected = self.report(status=RecordStatus.REJECTED)
        with self.assertRaisesRegex(ValueError, "Only a DRAFT"):
            self.archive.store(rejected, version="1")
        self.assertEqual((), self.log.events())

    def test_optional_strings_reject_mutable_or_non_string_values(self):
        mutable = {"secret": []}
        with self.assertRaisesRegex(ValueError, "case_id requires nonempty text"):
            self.report(case_id=mutable)
        mutable["secret"].append("changed")
        with self.assertRaisesRegex(ValueError, "accepted_rejected_neutral"):
            ExperimentRecord(
                record_id="er-bad", experiment_ref=self.refs["experiment"],
                hypothesis_refs=(), prediction_refs=(),
                run_spec_ref=self.refs["run-spec"], result_ref=self.refs["result"],
                outcome_summary="Bad optional field", actual_budget_usage={},
                artifact_refs=(), provenance={"status": "FAILED"},
                created_at="2026-09-17", accepted_rejected_neutral=mutable,
            )

    def test_nested_information_ref_cannot_bypass_visibility_validation(self):
        hidden = replace(self.refs["evidence"], visibility=Visibility.EVALUATOR)
        with self.assertRaisesRegex(ValueError, "explicit InformationRef fields"):
            ExperimentRecord(
                record_id="er-hidden", experiment_ref=self.refs["experiment"],
                hypothesis_refs=(), prediction_refs=(),
                run_spec_ref=self.refs["run-spec"], result_ref=self.refs["result"],
                outcome_summary="Must reject hidden provenance ref",
                actual_budget_usage={}, artifact_refs=(),
                provenance={"input": {"ref": hidden}}, created_at="2026-09-17",
            )


if __name__ == "__main__":
    unittest.main()
