"""P1.2A Context Compiler contract tests (docs/specs/context-compiler-v0.md).

Acceptance criteria are cited as ``AC<n>`` (spec §Acceptance criteria). P1.2A proves
them at the contract level over a hand-built fixture; the parser corpus (P1.2B),
model-assisted extraction (P1.2C) and the review demo (P1.2D) come later.
"""

import ast
from dataclasses import FrozenInstanceError, replace
import hashlib
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from industrial_agent_runtime import Visibility
from tep_sim import build_process_graph

from tep_agent_lab import context_compiler
from tep_agent_lab.canonical_context import (
    BYTES_SHA256, CanonicalContextRegistry, ContextSourceError, ContextSourceRef,
    DirectorySourceMaterializer, PackageSourceMaterializer,
)
from tep_agent_lab.context_compiler import (
    ENGINEERING_CONTEXT_REVISION_KIND, ArtifactPathRef, CandidateBinding, CandidateKnowledge,
    CandidateRef, CompileSession, CompilerError, CompilerPolicies, CompilerWorkspace,
    Confidence, DerivationProvenance, DerivedAnnotation, DerivedInput,
    DirectoryPublicationTarget, ExclusionReason, ExtractionKind, FindingCode,
    FragmentLocation, PlantTargetRef, PrincipalKind, PublicationError, PublicationRequest,
    ReviewDecision, ReviewPrincipal, ReviewRecord, ReviewStatus, SourceFragmentRef,
    StaleHead, TargetKind, WorkspaceAppendError, effective_review, head_path,
    validate_candidate, verify_publication,
)
from tep_agent_lab.persistence import canonical_json
from tep_agent_lab.persistence import content_checksum as json_digest
from tep_agent_lab.playground import (LAB_REPOSITORY, TEP_SIM_REPOSITORY,
                                      load_dependency_pins, pinned_tep_sim_sources)
from tep_agent_lab.rule_sets import (RULE_SET_KIND, RULE_SET_SCHEMA_VERSION,
                                     RuleSetRevision, load_rule_set)
from tep_agent_lab.rules import (Authority, ExtractionMetadata, Origin, RuleRegistry,
                                 Validation, literature_candidate, reviewed_blind_rca_policy)

ROOT = Path(__file__).resolve().parents[1]
PINS = load_dependency_pins(ROOT / "dependency-pins.json")
LAB_REVISION = "c" * 40
GRAPH = pinned_tep_sim_sources(PINS["tep-sim"])[0]
REVISIONS = {LAB_REPOSITORY: LAB_REVISION, TEP_SIM_REPOSITORY: PINS["tep-sim"]}
STAMP = "2026-10-10T00:00:00Z"
SOP_ID, SOP_PATH = "lab.sop.reactor-cooling", "docs/engineering/reactor-cooling-sop.md"
HIDDEN_ID, HIDDEN_PATH = "lab.evaluator-notes", "docs/engineering/evaluator-notes.md"
R0_ID, R0_PATH = "lab.rules.r0", "context/rule-sets/r0.json"
SOP = (b"# Reactor cooling SOP (P1.2A test fixture)\n"
       b"\n"
       b"Reactor cooling water flow is raised when the reactor temperature climbs.\n"
       b"Keep the reactor temperature at or below 130 degC in normal operation.\n"
       b"If the cooling water valve sticks, inspect the reactor cooling flow actuator.\n"
       b"The reactor coolant pump was serviced last quarter.\n")
HIDDEN = b"Evaluator-only note: the scored root cause is the reactor cooling valve.\n"
POLICY = "fixture-lines@1"
HUMAN = ReviewPrincipal.human("reviewer-1")


def line_location(content: bytes, index: int) -> FragmentLocation:
    lines = content.split(b"\n")
    start = sum(len(line) + 1 for line in lines[:index])
    return FragmentLocation.byte_range(start, start + len(lines[index]))


def source_ref(source_id: str, path: str, content: bytes,
               visibility: Visibility) -> ContextSourceRef:
    return ContextSourceRef(source_id=source_id, repository=LAB_REPOSITORY,
                            git_revision=LAB_REVISION, path_or_ref=path,
                            content_checksum=hashlib.sha256(content).hexdigest(),
                            kind="ENGINEERING_DOCUMENT", schema_version="text/markdown",
                            visibility=visibility, checksum_method=BYTES_SHA256)


def target(kind: TargetKind, target_id: str) -> PlantTargetRef:
    return PlantTargetRef(GRAPH, kind, target_id)


REACTOR = target(TargetKind.ENTITY, "reactor")


class Compilation:
    """One compile session over the pinned ProcessGraph, a fixture SOP and R0."""

    def __init__(self, root: Path, *, review_policy: str = "lab-review@1",
                 visibility: Visibility = Visibility.AGENT, hidden: bool = False) -> None:
        self.root = root
        self.write(SOP_PATH, SOP)
        self.r0 = RuleSetRevision(RuleRegistry((reviewed_blind_rca_policy(),)),
                                  Visibility.AGENT)
        self.write(R0_PATH, self.r0.serialize())
        self.sop_ref = source_ref(SOP_ID, SOP_PATH, SOP, Visibility.AGENT)
        self.r0_ref = ContextSourceRef(
            source_id=R0_ID, repository=LAB_REPOSITORY, git_revision=LAB_REVISION,
            path_or_ref=R0_PATH, content_checksum=self.r0.checksum, kind=RULE_SET_KIND,
            schema_version=RULE_SET_SCHEMA_VERSION, visibility=Visibility.AGENT)
        sources = [GRAPH, self.sop_ref, self.r0_ref]
        if hidden:
            self.write(HIDDEN_PATH, HIDDEN)
            self.hidden_ref = source_ref(HIDDEN_ID, HIDDEN_PATH, HIDDEN, Visibility.EVALUATOR)
            sources.append(self.hidden_ref)
        self.session = self.prepare(sources, review_policy=review_policy,
                                    visibility=visibility)

    def write(self, path: str, data: bytes) -> None:
        target_path = self.root / path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)

    def prepare(self, sources, *, review_policy="lab-review@1",
                visibility=Visibility.AGENT) -> CompileSession:
        return CompileSession.prepare(
            REVISIONS, {LAB_REPOSITORY: DirectorySourceMaterializer(self.root),
                        TEP_SIM_REPOSITORY: PackageSourceMaterializer("tep_sim")},
            sources, process_graph_source_id=GRAPH.source_id, input_rule_set_source_id=R0_ID,
            plant_graph_loader=build_process_graph,
            policies=CompilerPolicies(review_policy), visibility=visibility)

    def fragment(self, index: int, *, ref=None, content: bytes = SOP) -> SourceFragmentRef:
        return SourceFragmentRef.from_content(
            ref or self.sop_ref, content, line_location(content, index),
            fragment_kind="PARAGRAPH", fragmentation_policy=POLICY)

    def provenance(self, **changes) -> DerivationProvenance:
        return DerivationProvenance(extraction_method="fixture-extractor@1",
                                    grounding_method="exact-id@1", **changes)

    def binding(self, candidate_id: str, line: int, mention: str, kind: TargetKind,
                target_id: str | None, *, extraction=ExtractionKind.ENTITY_MENTION,
                alternatives=(), scope=(REACTOR,), provenance=None, **changes
                ) -> CandidateBinding:
        return CandidateBinding(
            candidate_id=candidate_id, session_id=self.session.session_id,
            extraction_kind=extraction, mention_fragment=self.fragment(line),
            mention_text=mention, target_kind=kind,
            proposed_target=None if target_id is None else target(kind, target_id),
            alternative_targets=tuple(target(kind, item) for item in alternatives),
            scope_refs=tuple(scope), provenance=provenance or self.provenance(), **changes)

    def rule_candidate(self, candidate_id: str = "limit", *, rule_id: str = "reactor-limit",
                       version: str = "1") -> CandidateKnowledge:
        fragment = self.fragment(3)
        extraction = ExtractionMetadata(
            fragment.citation(), "reactor temperature <= 130 degC",
            ("reactor.temperature_measurement",), "normal operation", "degC",
            "upper_limit", "fixture SOP statement")
        rule = literature_candidate(rule_id=rule_id, version=version,
                                    title="Reactor temperature limit", scope="tep/reactor",
                                    extraction=extraction, created_by="context-compiler",
                                    created_at=STAMP)
        return CandidateKnowledge(
            candidate_id=candidate_id, session_id=self.session.session_id,
            extraction_kind=ExtractionKind.OPERATING_LIMIT, source_fragments=(fragment,),
            scope_refs=(REACTOR,), provenance=self.provenance(), rule=rule)

    def knowledge(self, candidate_id: str = "sticky-valve", *, provenance=None,
                  **changes) -> CandidateKnowledge:
        return CandidateKnowledge(
            candidate_id=candidate_id, session_id=self.session.session_id,
            extraction_kind=ExtractionKind.TROUBLESHOOTING_GUIDANCE,
            source_fragments=(self.fragment(4),), scope_refs=(REACTOR,),
            provenance=provenance or self.provenance(),
            normalized_claim=DerivedAnnotation("Inspect the reactor cooling flow actuator",
                                               "fixture-normalizer@1"),
            conditions="cooling water valve sticks", **changes)


class CompilerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.c = Compilation(self.root / "sources")
        self.session = self.c.session
        self.workspace = CompilerWorkspace("ws-1")
        self.repo = self.root / "repo"
        self.target = DirectoryPublicationTarget(self.repo)

    def add(self, candidate, session=None):
        self.workspace.add_candidate(session or self.session, candidate)
        return candidate.ref

    def review(self, ref, decision=ReviewDecision.APPROVE, review_id=None, **changes):
        count = self.workspace.review_log_tail + 1
        return self.workspace.append_review(
            ref, decision, review_id=review_id or f"review-{count}", principal=HUMAN,
            reason=changes.pop("reason", "matches the cited fragment"), reviewed_at=STAMP,
            **changes)

    def request(self, refs, *, parent=None, lineage="reactor-cooling"):
        return PublicationRequest(lineage_id=lineage, candidate_refs=tuple(refs),
                                  published_by="app-user-1", published_at=STAMP,
                                  parent_revision=parent)

    def publish(self, refs, session=None, **changes):
        return self.workspace.publish(session or self.session, self.request(refs, **changes),
                                      self.target)

    def repo_files(self) -> dict[str, bytes]:
        return {path.relative_to(self.repo).as_posix(): path.read_bytes()
                for path in self.repo.rglob("*") if path.is_file()}


class CompileSessionTests(CompilerTestCase):
    def test_ac1_exact_registered_sources_in_an_own_frozen_registry(self) -> None:
        self.assertTrue(self.session.registry.frozen)
        self.assertEqual(sorted([GRAPH.source_id, SOP_ID, R0_ID]),
                         [ref.source_id for ref in self.session.inventory])
        self.assertEqual(GRAPH, self.session.plant.source_ref)
        self.assertEqual(self.c.r0, self.session.input_rule_set)
        with self.assertRaises(ContextSourceError):
            self.session.registry.register(replace(self.c.sop_ref, source_id="late"))
        stale = replace(self.c.sop_ref, content_checksum="0" * 64)
        with self.assertRaises(CompilerError):
            self.c.prepare([GRAPH, stale, self.c.r0_ref])
        with self.assertRaises(CompilerError):  # an arbitrary local path is never canonical
            self.c.prepare([GRAPH, str(self.root / "sources" / SOP_PATH), self.c.r0_ref])

    def test_session_selects_one_agent_graph_and_one_input_rule_set(self) -> None:
        with self.assertRaises(CompilerError):
            self.c.prepare([self.c.sop_ref, self.c.r0_ref])
        with self.assertRaises(CompilerError):
            self.c.prepare([GRAPH, self.c.sop_ref])
        hidden_graph = replace(GRAPH, visibility=Visibility.EVALUATOR)
        with self.assertRaises(CompilerError):
            self.c.prepare([hidden_graph, self.c.r0_ref], visibility=Visibility.EVALUATOR)

    def test_ac13_agent_compilation_rejects_evaluator_inputs(self) -> None:
        self.c.write(HIDDEN_PATH, HIDDEN)
        hidden = source_ref(HIDDEN_ID, HIDDEN_PATH, HIDDEN, Visibility.EVALUATOR)
        with self.assertRaises(CompilerError):
            self.c.prepare([GRAPH, self.c.sop_ref, self.c.r0_ref, hidden])
        evaluator = self.c.prepare([GRAPH, self.c.sop_ref, self.c.r0_ref, hidden],
                                   visibility=Visibility.EVALUATOR)
        self.assertEqual(Visibility.EVALUATOR, evaluator.visibility)

    def test_ac14_policy_change_is_a_new_session_tuple(self) -> None:
        other = self.c.prepare([GRAPH, self.c.sop_ref, self.c.r0_ref],
                               review_policy="lab-review@2")
        same = self.c.prepare([GRAPH, self.c.sop_ref, self.c.r0_ref])
        self.assertEqual(self.session.session_id, same.session_id)
        self.assertNotEqual(self.session.session_id, other.session_id)


class FragmentAndCandidateTests(CompilerTestCase):
    def test_ac2_fragment_identity_is_deterministic_and_exact(self) -> None:
        first, again = self.c.fragment(3), self.c.fragment(3)
        self.assertEqual(first.fragment_id, again.fragment_id)
        self.assertNotEqual(first.fragment_id, self.c.fragment(4).fragment_id)
        other_policy = replace(first, fragmentation_policy="fixture-lines@2")
        self.assertNotEqual(first.fragment_id, other_policy.fragment_id)
        self.assertEqual(self.c.sop_ref, first.source_ref)
        self.assertTrue(first.verify(SOP))
        self.assertFalse(first.verify(SOP.replace(b"130", b"140")))
        self.assertEqual((f"{LAB_REPOSITORY}/{SOP_PATH}", LAB_REVISION, first.fragment_id),
                         (first.citation().source_ref, first.citation().version,
                          first.citation().source_location))
        graph_bytes = PackageSourceMaterializer("tep_sim").read_bytes(GRAPH.path_or_ref)
        subtree = SourceFragmentRef.from_content(
            GRAPH, graph_bytes, FragmentLocation.json_pointer("/nodes/0"),
            fragment_kind="JSON_SUBTREE", fragmentation_policy="json-pointer@1")
        self.assertTrue(subtree.verify(graph_bytes))
        for bad in (lambda: FragmentLocation.byte_range(5, 5),
                    lambda: FragmentLocation.json_pointer("nodes"),
                    lambda: replace(first, fragmentation_policy="no-version")):
            with self.assertRaises(CompilerError):
                bad()

    def test_ac3_candidates_are_immutable_versions(self) -> None:
        candidate = self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor")
        with self.assertRaises(FrozenInstanceError):
            candidate.proposed_target = target(TargetKind.ENTITY, "condenser")
        retry = self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor",
                               provenance=self.c.provenance(model="fixture/model@2"))
        self.assertEqual(candidate.content_key, retry.content_key)
        self.assertNotEqual(candidate.candidate_version, retry.candidate_version)
        ref = self.add(candidate)
        with self.assertRaises(WorkspaceAppendError):
            self.add(candidate)
        self.add(retry)  # a new version is appended; the historical one is kept
        self.assertEqual((candidate, retry), self.workspace.candidates())
        self.assertIs(candidate, self.workspace.candidate(ref))
        foreign = replace(self.c.knowledge(), session_id="cs-other")
        with self.assertRaises(WorkspaceAppendError):
            self.workspace.add_candidate(self.session, foreign)

    def test_versioned_routing_and_unclassified_material(self) -> None:
        with self.assertRaises(CompilerError):  # rule-shaped kind cannot become a binding
            self.c.binding("x", 2, "reactor", TargetKind.ENTITY, "reactor",
                           extraction=ExtractionKind.OPERATING_LIMIT)
        with self.assertRaises(CompilerError):  # unknown material is never forced in
            self.c.binding("x", 2, "reactor", TargetKind.ENTITY, "reactor",
                           extraction="acme:VALVE_LORE")
        with self.assertRaises(CompilerError):  # non-rule guidance does not carry a Rule
            replace(self.c.knowledge(), rule=self.c.rule_candidate().rule)
        with self.assertRaises(CompilerError):
            replace(self.c.rule_candidate(), conditions="duplicated claim schema")

    def test_ac9_ac12_compiler_rules_keep_literature_candidate_axes(self) -> None:
        candidate = self.c.rule_candidate()
        promoted = replace(candidate.rule, authority=Authority.ADVISORY)
        with self.assertRaises(CompilerError):
            replace(candidate, rule=promoted)
        confident = replace(candidate, provenance=self.c.provenance(
            confidence=Confidence(0.999, "fixture-score@1")))
        self.assertEqual(candidate.content_key, confident.content_key)
        self.assertEqual((Origin.LITERATURE, Validation.NONE, Authority.REFERENCE),
                         (confident.rule.origin, confident.rule.validation,
                          confident.rule.authority))
        with self.assertRaises(CompilerError):
            Confidence(1.5, "fixture-score@1")


class ValidationTests(CompilerTestCase):
    def codes(self, candidate, review=None):
        return {item.code for item in validate_candidate(self.session, candidate,
                                                         review).findings}

    def test_ac4_targets_bind_to_the_exact_plant_context(self) -> None:
        good = self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor")
        self.assertTrue(validate_candidate(self.session, good).passed)
        other_graph = replace(GRAPH, git_revision="d" * 40)
        moved = replace(good, proposed_target=PlantTargetRef(other_graph, TargetKind.ENTITY,
                                                             "reactor"),
                        scope_refs=(PlantTargetRef(other_graph, TargetKind.ENTITY,
                                                   "reactor"),))
        self.assertIn(FindingCode.PLANT_CONTEXT_MISMATCH, self.codes(moved))
        self.assertIn(FindingCode.TARGET_UNKNOWN, self.codes(
            self.c.binding("x", 2, "reactor", TargetKind.ENTITY, "reactor-9")))
        self.assertIn(FindingCode.TARGET_UNRESOLVED, self.codes(
            self.c.binding("x", 5, "pump", TargetKind.ENTITY, None)))
        self.assertIn(FindingCode.TARGET_KIND_INCOMPATIBLE, self.codes(
            self.c.binding("x", 3, "temperature", TargetKind.SIGNAL,
                           "reactor.temperature_measurement")))
        self.assertIn(FindingCode.SCOPE_EMPTY, self.codes(
            self.c.binding("x", 2, "reactor", TargetKind.ENTITY, "reactor", scope=())))
        relationship = self.c.binding("x", 2, "cooling water", TargetKind.RELATIONSHIP,
                                      "reactor_cooling_water_in")
        self.assertTrue(validate_candidate(self.session, relationship).passed)

    def test_ac5_ambiguity_is_explicit_and_sources_must_be_exact(self) -> None:
        ambiguous = self.c.binding(
            "actuator", 4, "cooling flow actuator", TargetKind.SIGNAL,
            "condenser_cooling.flow_actuator", extraction=ExtractionKind.SIGNAL_MENTION,
            alternatives=("reactor_cooling.flow_actuator",))
        result = validate_candidate(self.session, ambiguous)
        self.assertTrue(result.passed)
        self.assertIn(FindingCode.AMBIGUOUS_TARGET, {item.code for item in result.findings})
        outside = source_ref("lab.unregistered", "docs/other.md", SOP, Visibility.AGENT)
        stray = replace(ambiguous, mention_fragment=self.c.fragment(4, ref=outside))
        self.assertIn(FindingCode.SOURCE_UNRESOLVED, self.codes(stray))
        forged = replace(self.c.fragment(4), content_checksum="1" * 64)
        self.assertIn(FindingCode.FRAGMENT_INVALID,
                      self.codes(replace(ambiguous, mention_fragment=forged)))
        influenced = replace(ambiguous, provenance=self.c.provenance(
            influencing_sources=(outside,)))
        self.assertIn(FindingCode.INFLUENCE_UNKNOWN, self.codes(influenced))

    def test_rule_candidates_satisfy_the_existing_rule_contracts(self) -> None:
        self.assertTrue(validate_candidate(self.session, self.c.rule_candidate()).passed)
        existing = reviewed_blind_rca_policy()
        clash = self.c.rule_candidate(rule_id=existing.rule_id, version=existing.version)
        self.assertIn(FindingCode.RULE_VERSION_EXISTS, self.codes(clash))
        uncited = replace(self.c.rule_candidate(), source_fragments=(self.c.fragment(2),))
        self.assertIn(FindingCode.RULE_INVALID, self.codes(uncited))
        conflicted = self.c.knowledge(conflicting_candidate_ids=("other",))
        result = validate_candidate(self.session, conflicted)
        self.assertTrue(result.passed)
        self.assertIn(FindingCode.CONFLICT_DECLARED, {item.code for item in result.findings})


class ReviewTests(CompilerTestCase):
    def test_ac6_ac7_decisions_are_immutable_append_only_records(self) -> None:
        refs = [self.add(self.c.binding(f"b{i}", 2, "reactor", TargetKind.ENTITY, "reactor"))
                for i in range(3)]
        knowledge = self.add(self.c.knowledge())
        approve = self.review(refs[0])
        reject = self.review(refs[1], ReviewDecision.REJECT, reason="wrong mention")
        keep = self.review(refs[2], ReviewDecision.KEEP_CANDIDATE)
        remap = self.review(knowledge, ReviewDecision.REMAP, reason="applies to stripper",
                            selected_scope_refs=(target(TargetKind.ENTITY, "stripper"),))
        self.assertEqual([1, 2, 3, 4], [item.review_log_position
                                        for item in self.workspace.review_log()])
        self.assertEqual((approve, reject, keep, remap), self.workspace.review_log())
        with self.assertRaises(FrozenInstanceError):
            approve.decision = ReviewDecision.REJECT
        self.assertEqual("applies to stripper", remap.reason)
        self.assertEqual((REACTOR,), self.workspace.candidate(knowledge).scope_refs)
        self.assertEqual([ReviewStatus.APPROVED, ReviewStatus.REJECTED,
                          ReviewStatus.KEEP_CANDIDATE, ReviewStatus.REMAPPED],
                         [self.workspace.review_status(ref) for ref in (*refs, knowledge)])
        self.assertEqual(remap, ReviewRecord.from_record(json.loads(json.dumps(
            remap.record()))))
        with self.assertRaises(WorkspaceAppendError):
            self.review(refs[0], review_id="review-1")
        with self.assertRaises(WorkspaceAppendError):
            self.review(refs[0], supersedes=("review-2",))  # another candidate's record
        with self.assertRaises(WorkspaceAppendError):
            self.review(refs[0], supersedes=("review-99",))
        with self.assertRaises(WorkspaceAppendError):
            self.review(CandidateRef("ghost", "0" * 64))
        self.assertEqual(4, self.workspace.review_log_tail)

    def test_remap_structure(self) -> None:
        binding = self.add(self.c.binding("b", 2, "reactor", TargetKind.ENTITY, "reactor"))
        knowledge = self.add(self.c.knowledge())
        for ref, changes in (
                (binding, {}),
                (binding, {"selected_scope_refs": (REACTOR,)}),
                (binding, {"selected_target": REACTOR, "selected_scope_refs": (REACTOR,)}),
                (knowledge, {"selected_target": REACTOR, "selected_scope_refs": (REACTOR,)})):
            with self.assertRaises(WorkspaceAppendError):
                self.review(ref, ReviewDecision.REMAP, **changes)
        with self.assertRaises(CompilerError):
            self.review(binding, selected_target=REACTOR)
        self.assertEqual(0, self.workspace.review_log_tail)

    def test_only_allowed_decision_principals(self) -> None:
        ref = self.add(self.c.binding("b", 2, "reactor", TargetKind.ENTITY, "reactor"))
        with self.assertRaises(ValueError):
            ReviewPrincipal("AGENT", "planner")
        with self.assertRaises(ValueError):
            PrincipalKind("MODEL")
        with self.assertRaises(WorkspaceAppendError):  # no acceptance policy registered
            self.workspace.append_review(
                ref, ReviewDecision.APPROVE, review_id="r1",
                principal=ReviewPrincipal(PrincipalKind.DETERMINISTIC_POLICY, "auto@1"),
                reason="policy", reviewed_at=STAMP)
        with self.assertRaises(WorkspaceAppendError):
            self.workspace.append_review(ref, ReviewDecision.APPROVE, review_id="r1",
                                         principal="model", reason="x", reviewed_at=STAMP)

    def test_ac16_ac22_multiple_terminal_records_block_until_superseded(self) -> None:
        ref = self.add(self.c.binding("b", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref, review_id="r1")
        self.review(ref, review_id="r2")
        effective = self.workspace.effective_review(ref)
        self.assertEqual((ReviewStatus.UNRESOLVED, ("r1", "r2")),
                         (effective.status, effective.terminal_review_ids))
        self.review(ref, ReviewDecision.REJECT, review_id="r3", supersedes=("r1", "r2"))
        self.assertEqual(ReviewStatus.REJECTED, self.workspace.review_status(ref))
        # audit replay at an older position recomputes, it never publishes
        self.assertEqual(ReviewStatus.APPROVED,
                         self.workspace.review_status(ref, position=1))
        self.assertEqual(ReviewStatus.UNREVIEWED,
                         effective_review(self.workspace.review_log(), ref, 0).status)
        with self.assertRaises(CompilerError):
            self.workspace.review_status(ref, position=4)


class PublicationTests(CompilerTestCase):
    def vertical_slice(self):
        c = self.c
        refs = {
            "reactor": self.add(c.binding("reactor", 2, "reactor", TargetKind.ENTITY,
                                          "reactor")),
            "actuator": self.add(c.binding(
                "actuator", 4, "reactor cooling flow actuator", TargetKind.SIGNAL,
                "condenser_cooling.flow_actuator", extraction=ExtractionKind.SIGNAL_MENTION,
                alternatives=("reactor_cooling.flow_actuator",),
                provenance=c.provenance(confidence=Confidence(0.97, "fixture-score@1")))),
            "temperature": self.add(c.binding(
                "temperature", 3, "reactor temperature", TargetKind.SIGNAL,
                "reactor.temperature_measurement", extraction=ExtractionKind.SIGNAL_MENTION,
                provenance=c.provenance(confidence=Confidence(0.999, "fixture-score@1")))),
            "pump": self.add(c.binding("pump", 5, "reactor coolant pump", TargetKind.ENTITY,
                                       None)),
            "wrong": self.add(c.binding("wrong", 2, "cooling water", TargetKind.ENTITY,
                                        "condenser")),
            "limit": self.add(c.rule_candidate()),
            "valve": self.add(c.knowledge(conflicting_candidate_ids=("wrong",))),
        }
        self.review(refs["reactor"], review_id="r-reactor")
        self.review(refs["actuator"], ReviewDecision.REMAP, review_id="r-actuator",
                    reason="the SOP names the reactor loop",
                    selected_target=target(TargetKind.SIGNAL, "reactor_cooling.flow_actuator"),
                    selected_scope_refs=(REACTOR,))
        self.review(refs["pump"], ReviewDecision.KEEP_CANDIDATE, review_id="r-pump")
        self.review(refs["wrong"], ReviewDecision.REJECT, review_id="r-wrong")
        self.review(refs["limit"], review_id="r-limit")
        self.review(refs["valve"], review_id="r-valve")
        return refs

    def test_vertical_publication_contract(self) -> None:
        refs = self.vertical_slice()
        before = self.workspace.candidates()
        result = self.publish(refs.values())
        document = result.document
        # AC8 / AC5 / AC12: rejected, unresolved and unreviewed candidates stay out.
        self.assertEqual({(refs["temperature"], ExclusionReason.UNREVIEWED),
                          (refs["pump"], ExclusionReason.KEEP_CANDIDATE),
                          (refs["wrong"], ExclusionReason.REJECTED)},
                         {(item.candidate_ref, item.reason) for item in result.exclusions})
        bindings = {item["candidate_ref"]["candidate_id"]: item
                    for item in document["approved_bindings"]}
        self.assertEqual({"reactor", "actuator"}, set(bindings))
        # AC7 / AC25: the REMAP target comes from the review; the proposal is preserved.
        actuator = bindings["actuator"]
        self.assertEqual("reactor_cooling.flow_actuator", actuator["target"]["target_id"])
        self.assertEqual("condenser_cooling.flow_actuator",
                         actuator["proposed_target"]["target_id"])
        self.assertEqual(("REMAP", "r-actuator"), (actuator["decision"], actuator["review_id"]))
        self.assertEqual({"score": 0.97, "method": "fixture-score@1"}, actuator["confidence"])
        # AC11: traced to fragments, methods, validation findings and review records.
        reactor = bindings["reactor"]
        self.assertEqual(self.c.fragment(2).fragment_id, reactor["fragments"][0]["fragment_id"])
        self.assertEqual(("fixture-extractor@1", "exact-id@1", True),
                         (reactor["extraction_method"], reactor["grounding_method"],
                          reactor["validation"]["passed"]))
        self.assertEqual(sorted(["r-reactor", "r-actuator", "r-limit", "r-valve"]),
                         list(document["review_ids"]))
        # Non-rule knowledge: VERIFIED only as scope/extraction fidelity, content = fragment.
        (valve,) = document["approved_knowledge"]
        self.assertEqual(("VERIFIED", "r-valve"),
                         (valve["knowledge_ref"]["validation_status"],
                          valve["knowledge_ref"]["validation_evidence_review_id"]))
        self.assertEqual(self.c.fragment(4).record(),
                         dict(valve["knowledge_ref"]["content"][0]))
        self.assertTrue(valve["derived_annotations"][0]["derived"])
        self.assertEqual("sticky-valve", document["conflicts"][0]["candidate_ref"]["candidate_id"])
        # AC9 / AC19 / AC23: R1 = R0 unchanged + the approved inert candidate Rule.
        files = self.repo_files()
        self.assertEqual(files, dict(result.files) | {head_path("reactor-cooling"):
                                                       files[head_path("reactor-cooling")]})
        r1 = load_rule_set(files[result.published_rule_set.path],
                           result.published_rule_set.content_checksum)
        self.assertEqual(self.c.r0_ref, r1.parent)
        rule = self.workspace.candidate(refs["limit"]).rule
        self.assertEqual(rule, r1.registry.get_rule(rule.ref))
        self.assertEqual((rule,), r1.registry.query_rules("tep/reactor"))
        self.assertEqual(reviewed_blind_rca_policy(),
                         r1.registry.get_rule(reviewed_blind_rca_policy().ref))
        self.assertEqual([{"rule_id": rule.rule_id, "version": rule.version}],
                         [dict(item) for item in document["pinned_rule_refs"]])
        (scope_binding,) = committed(result)["rule_scope_bindings"]
        self.assertEqual(("tep/reactor", "r-limit", [REACTOR.record()]),
                         (scope_binding["rule_scope"], scope_binding["review_id"],
                          scope_binding["scope_refs"]))
        # AC10 / AC22: pinned to the tail and recomputable from repository content alone.
        self.assertEqual(self.workspace.review_log_tail, result.review_log_position)
        verified = verify_publication(files, result.revision)
        self.assertEqual({ReviewStatus.APPROVED, ReviewStatus.REMAPPED},
                         {item.status for item in verified.effective_reviews})
        with self.assertRaises(TypeError):
            document["approved_bindings"] = ()
        self.assertEqual(before, self.workspace.candidates())
        self.assertEqual(result.revision, self.target.head("reactor-cooling"))

    def test_ac20_revision_is_a_repository_artifact_registered_by_ref(self) -> None:
        refs = self.vertical_slice()
        result = self.publish(refs.values())
        ref = ContextSourceRef(
            source_id="lab.engineering-context.reactor-cooling", repository=LAB_REPOSITORY,
            git_revision="e" * 40, path_or_ref=result.revision.path,
            content_checksum=result.revision.content_checksum,
            kind=ENGINEERING_CONTEXT_REVISION_KIND,
            schema_version=context_compiler.REVISION_SCHEMA_VERSION,
            visibility=Visibility.AGENT)
        registry = CanonicalContextRegistry({LAB_REPOSITORY: "e" * 40},
                                            {LAB_REPOSITORY: DirectorySourceMaterializer(
                                                self.repo)})
        registry.register(ref)  # prepare-time registration attests path + checksum
        registry.freeze()
        (self.repo / result.revision.path).write_bytes(b'{"tampered": true}')
        fresh = CanonicalContextRegistry({LAB_REPOSITORY: "e" * 40},
                                         {LAB_REPOSITORY: DirectorySourceMaterializer(
                                             self.repo)})
        with self.assertRaises(ContextSourceError):
            fresh.register(ref)

    def test_ac17_remap_targets_are_revalidated(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "condenser"))
        self.review(ref, ReviewDecision.REMAP,
                    selected_target=target(TargetKind.SIGNAL, "reactor.temperature_measurement"),
                    selected_scope_refs=(REACTOR,))
        result = self.publish([ref])
        self.assertEqual([(ref, ExclusionReason.REMAP_INVALID)],
                         [(item.candidate_ref, item.reason) for item in result.exclusions])
        self.assertIn("TARGET_KIND_INCOMPATIBLE", result.exclusions[0].detail)
        self.assertEqual((), result.document["approved_bindings"])

    def test_ac22_publication_uses_the_tail_and_cannot_skip_a_later_reject(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref, review_id="r1")
        self.review(ref, ReviewDecision.REJECT, review_id="r2", supersedes=("r1",))
        self.assertNotIn("position", " ".join(inspect.signature(
            CompilerWorkspace.publish).parameters))
        self.assertNotIn("review_log_position", PublicationRequest.__dataclass_fields__)
        result = self.publish([ref])
        self.assertEqual(2, result.review_log_position)
        self.assertEqual(ExclusionReason.REJECTED, result.exclusions[0].reason)
        self.assertEqual(ReviewStatus.APPROVED, self.workspace.review_status(ref, position=1))

    def test_ac22_workspace_lock_serializes_review_and_publication(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref, review_id="r1")
        target_ = BlockingTarget(self.repo)
        published, appended = {}, {}
        publisher = threading.Thread(target=lambda: published.update(
            result=self.workspace.publish(self.session, self.request([ref]), target_)))
        publisher.start()
        self.assertTrue(target_.entered.wait(10))
        reviewer = threading.Thread(target=lambda: appended.update(record=self.review(
            ref, ReviewDecision.REJECT, review_id="r2", supersedes=("r1",))))
        reviewer.start()
        reviewer.join(0.3)
        self.assertTrue(reviewer.is_alive())  # blocked until the publication commit ends
        target_.release.set()
        publisher.join(10)
        reviewer.join(10)
        self.assertEqual(1, published["result"].review_log_position)
        self.assertEqual(2, appended["record"].review_log_position)
        self.assertEqual(1, len(published["result"].document["approved_bindings"]))

    def test_ac10_per_lineage_head_compare_and_set(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref)
        first = self.publish([ref])
        files = self.repo_files()
        with self.assertRaises(StaleHead):  # someone else already advanced the head
            self.publish([ref])
        self.assertEqual(files, self.repo_files())
        parent = ContextSourceRef(
            source_id="lab.ecr.parent", repository=LAB_REPOSITORY, git_revision="e" * 40,
            path_or_ref=first.revision.path, content_checksum=first.revision.content_checksum,
            kind=ENGINEERING_CONTEXT_REVISION_KIND,
            schema_version=context_compiler.REVISION_SCHEMA_VERSION,
            visibility=Visibility.AGENT)
        wrong_parent = replace(parent, content_checksum="2" * 64)
        with self.assertRaises(StaleHead):
            self.publish([ref], parent=wrong_parent)
        stale_bundle = context_compiler.PublicationBundle(
            "reactor-cooling", None, first.revision, ())
        with self.assertRaises(StaleHead):  # the target re-checks atomically at commit
            self.target.commit(stale_bundle)
        second = self.publish([ref], parent=parent)
        self.assertEqual(parent.record(), committed(second)["parent_revision"])
        self.assertNotEqual(first.revision, second.revision)
        self.assertEqual(second.revision, self.target.head("reactor-cooling"))
        self.assertTrue((self.repo / first.revision.path).exists())  # history is kept
        self.assertIsNone(self.target.head("other-lineage"))

    def test_ac21_dependency_failure_publishes_nothing(self) -> None:
        refs = self.vertical_slice()
        with self.assertRaises(PublicationError):
            self.publish([*refs.values(), CandidateRef("ghost", "0" * 64)])
        self.assertEqual({}, self.repo_files())
        twin = self.add(self.c.rule_candidate("limit-twin"))  # same RuleRef, other candidate
        self.review(twin)
        with self.assertRaises(PublicationError):
            self.publish([refs["limit"], twin])
        self.assertEqual({}, self.repo_files())
        (self.c.root / SOP_PATH).write_bytes(SOP.replace(b"130", b"135"))
        with self.assertRaises(PublicationError):
            self.publish(refs.values())
        self.assertEqual({}, self.repo_files())
        self.assertIsNone(self.target.head("reactor-cooling"))

    def test_ac14_new_source_or_policy_tuple_needs_new_review_and_revision(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref)
        first = self.publish([ref])
        changed = self.c.prepare([GRAPH, self.c.sop_ref, self.c.r0_ref],
                                 review_policy="lab-review@2")
        parent = ContextSourceRef(
            source_id="lab.ecr.parent", repository=LAB_REPOSITORY, git_revision="e" * 40,
            path_or_ref=first.revision.path, content_checksum=first.revision.content_checksum,
            kind=ENGINEERING_CONTEXT_REVISION_KIND,
            schema_version=context_compiler.REVISION_SCHEMA_VERSION,
            visibility=Visibility.AGENT)
        stale = self.publish([ref], session=changed, parent=parent)
        self.assertEqual(ExclusionReason.STALE_SESSION, stale.exclusions[0].reason)
        redone = replace(self.workspace.candidate(ref), session_id=changed.session_id)
        self.workspace.add_candidate(changed, redone)
        self.assertEqual(ReviewStatus.UNREVIEWED, self.workspace.review_status(redone.ref))
        self.review(redone.ref)
        parent2 = replace(parent, path_or_ref=stale.revision.path,
                          content_checksum=stale.revision.content_checksum)
        third = self.publish([redone.ref], session=changed, parent=parent2)
        self.assertEqual(1, len(third.document["approved_bindings"]))
        self.assertEqual(3, len({first.revision, stale.revision, third.revision}))
        self.assertEqual("lab-review@2", third.document["review_policy"])

    def test_ac13_ac18_visibility_and_derivation_taint(self) -> None:
        # An evaluator-visible compilation taints every derived output.
        hidden = Compilation(self.root / "hidden-sources", hidden=True,
                             visibility=Visibility.EVALUATOR)
        tainted = hidden.binding("valve", 4, "cooling valve", TargetKind.ENTITY, "reactor",
                                 conflicting_fragments=(hidden.fragment(
                                     0, ref=hidden.hidden_ref, content=HIDDEN),))
        self.assertEqual(Visibility.EVALUATOR, tainted.visibility)
        self.workspace.add_candidate(hidden.session, tainted)
        self.review(tainted.ref)
        hidden_result = self.workspace.publish(
            hidden.session, self.request([tainted.ref], lineage="hidden"), self.target)
        self.assertEqual("EVALUATOR", hidden_result.document["visibility"])
        published = self.repo_files()
        # Hidden model context taints an otherwise Agent-visible candidate.
        model_context = DerivedInput("model-call:1", "model_context", Visibility.EVALUATOR)
        leaky = self.c.binding("leaky", 2, "reactor", TargetKind.ENTITY, "reactor",
                               provenance=self.c.provenance(derived_inputs=(model_context,)))
        self.assertIn(FindingCode.VISIBILITY_WIDENED,
                      {item.code for item in self.workspace.validation(self.add(leaky))
                       .findings})
        self.review(leaky.ref)
        # Review rationale is an influencing input too.
        rationale = self.add(self.c.binding("rationale", 2, "reactor", TargetKind.ENTITY,
                                            "reactor"))
        self.review(rationale, visibility=Visibility.EVALUATOR)
        # None of them can enter an Agent-visible publication, not even as an exclusion
        # record (which would expose hidden membership); nothing is written.
        for ref in (tainted.ref, leaky.ref, rationale):
            with self.assertRaises(PublicationError):
                self.publish([ref], lineage="agent")
        self.assertEqual(published, self.repo_files())
        # Removing the hidden ref is a new candidate version needing its own review,
        # never a downgrade of the tainted one.
        stripped = replace(leaky, provenance=self.c.provenance())
        self.assertNotEqual(leaky.ref, stripped.ref)
        self.add(stripped)
        result = self.publish([stripped.ref], lineage="agent")
        self.assertEqual([(stripped.ref, ExclusionReason.UNREVIEWED)],
                         [(item.candidate_ref, item.reason) for item in result.exclusions])
        self.assertEqual("AGENT", result.document["visibility"])
        self.assertNotIn(HIDDEN_ID, json.dumps(committed(result)))
        self.assertEqual([], committed(result)["approved_bindings"])

    def test_ac19_verification_rejects_a_forged_rule_set_parent(self) -> None:
        refs = self.vertical_slice()
        result = self.publish(refs.values())
        files = dict(result.files)
        document = committed(result)
        forged_parent = replace(self.c.r0_ref, git_revision="f" * 40)
        r1 = load_rule_set(files[result.published_rule_set.path],
                           result.published_rule_set.content_checksum)
        forged = RuleSetRevision(r1.registry, r1.visibility, forged_parent)
        forged_ref = ArtifactPathRef(f"context/rule-sets/{forged.checksum}.json",
                                     forged.checksum)
        document["published_rule_set"] = {"revision": "SAME_COMMIT", **forged_ref.record()}
        files[forged_ref.path] = forged.serialize()
        resealed = reseal(document)
        files[resealed.path] = canonical(document)
        with self.assertRaises(PublicationError):
            verify_publication(files, resealed)
        # Claiming an approved item whose effective review is not APPROVE/REMAP fails too.
        document = committed(result)
        document["approved_bindings"][0]["review_id"] = "r-wrong"
        files = dict(result.files)
        resealed = reseal(document)
        files[resealed.path] = canonical(document)
        with self.assertRaises(PublicationError):
            verify_publication(files, resealed)


class BlockingTarget(DirectoryPublicationTarget):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.entered, self.release = threading.Event(), threading.Event()

    def commit(self, bundle) -> None:
        self.entered.set()
        self.release.wait(10)
        super().commit(bundle)


def committed(result) -> dict:
    """The revision document exactly as committed (plain JSON)."""
    return json.loads(dict(result.files)[result.revision.path])


def canonical(document) -> bytes:
    return canonical_json(document)


def reseal(document) -> ArtifactPathRef:
    """Recompute the revision id after a test edit, as a forger would."""
    document.pop("revision_id", None)
    document["revision_id"] = "ecr-" + json_digest(document)
    return ArtifactPathRef.of(f"context/engineering-context/x/revisions/"
                              f"{document['revision_id']}.json", canonical(document))


class BoundaryTests(unittest.TestCase):
    SOURCE = Path(context_compiler.__file__).read_text(encoding="utf-8")

    def imports(self) -> set[str]:
        found = set()
        for node in ast.walk(ast.parse(self.SOURCE)):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add(("." * node.level) + (node.module or ""))
                found.update(f"{node.module}.{alias.name}" for alias in node.names)
        return found

    def test_ac15_no_owner_is_duplicated(self) -> None:
        imported = self.imports()
        for owner in (".telemetry", ".investigation", ".tool_surface", ".tool_bridge",
                      ".playground", "tep_sim"):
            self.assertFalse({item for item in imported if item.startswith(owner)}, owner)
        defined = {node.name for node in ast.walk(ast.parse(self.SOURCE))
                   if isinstance(node, ast.ClassDef)}
        self.assertFalse(defined & {"RuleRegistry", "Rule", "ProcessGraph", "RcaState",
                                    "TaskStateStore", "CanonicalContextRegistry"})
        self.assertNotIn("XMEAS", self.SOURCE)
        self.assertNotIn("XMV", self.SOURCE)
        self.assertNotIn("IDV", self.SOURCE)

    def test_ac24_review_and_publication_are_not_agent_tools(self) -> None:
        self.assertFalse({item for item in self.imports()
                          if "ToolSpec" in item or "GatePolicy" in item})
        from tep_agent_lab.tep_world import ArtifactStore
        from tep_agent_lab.tool_bridge import AnalysisToolBridge, BridgedToolSurface
        from tep_agent_lab.tool_surface import BlindRcaToolSurface
        from test_tool_surface import make_world
        with TemporaryDirectory() as directory:
            world = make_world(Path(directory), inject=False)
            store = ArtifactStore(Path(directory) / "artifacts")
            blind = BlindRcaToolSurface(world, store)
            surface = BridgedToolSurface(blind, AnalysisToolBridge(
                store, reference_revision=blind.reference_revision))
            names = {spec.name for spec in surface.tool_specs()}
            allowlist = set(surface.gate_policy().tool_allowlist)
        workspace_api = {"add_candidate", "append_review", "publish", "review", "approve",
                         "remap", "reject", "candidate"}
        for name in names | allowlist:
            self.assertFalse(any(word in name for word in workspace_api), name)
        for module in ("tool_surface", "tool_bridge", "playground", "investigation"):
            text = (Path(context_compiler.__file__).with_name(f"{module}.py")
                    .read_text(encoding="utf-8"))
            self.assertNotIn("context_compiler", text, module)


if __name__ == "__main__":
    unittest.main()
