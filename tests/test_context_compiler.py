"""P1.2A Context Compiler contract tests (docs/specs/context-compiler-v0.md).

Acceptance criteria are cited as ``AC<n>`` (spec §Acceptance criteria); review findings
of the P1.2A review as ``H<n>``/``M<n>``. P1.2A proves the contracts over a hand-built
fixture and stops at a staged (non-canonical) publication bundle; the parser corpus
(P1.2B), model-assisted extraction (P1.2C) and the repository transaction plus review
demo (P1.2D) come later.
"""

import ast
from dataclasses import FrozenInstanceError, replace
import hashlib
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unicodedata
import unittest
from unittest import mock

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
    Confidence, DerivationProvenance, DerivedAnnotation, DerivedInput, ExclusionReason,
    ExtractionKind, FindingCode, FragmentLocation, PlantTargetRef, PrincipalKind,
    PublicationError, PublicationRequest, ReviewDecision, ReviewPrincipal, ReviewRecord,
    ReviewStatus, SourceFragmentRef, StagedPublication, TargetKind, TypedValue,
    WorkspaceAppendError, effective_review, head_path, validate_candidate,
    verify_publication,
)
from tep_agent_lab.persistence import canonical_json
from tep_agent_lab.playground import (LAB_REPOSITORY, TEP_SIM_REPOSITORY,
                                      load_dependency_pins, pinned_tep_sim_sources)
from tep_agent_lab.rule_sets import (RULE_SET_KIND, RULE_SET_SCHEMA_VERSION, RuleSetError,
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
MODEL_CALL = b'{"call":"fixture-model-call-1"}'


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


def model_input(visibility: Visibility = Visibility.AGENT, data: bytes = MODEL_CALL,
                ref: str = "model-call:1") -> DerivedInput:
    return DerivedInput(ref, "model_context", visibility, hashlib.sha256(data).hexdigest())


def resolver(item: DerivedInput) -> bytes:
    if item.ref != "model-call:1":
        raise LookupError(item.ref)
    return MODEL_CALL


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

    def materializers(self):
        return {LAB_REPOSITORY: DirectorySourceMaterializer(self.root),
                TEP_SIM_REPOSITORY: PackageSourceMaterializer("tep_sim")}

    def prepare(self, sources, *, review_policy="lab-review@1",
                visibility=Visibility.AGENT, acceptance=frozenset()) -> CompileSession:
        return CompileSession.prepare(
            REVISIONS, self.materializers(), sources, process_graph_source_id=GRAPH.source_id,
            input_rule_set_source_id=R0_ID, plant_graph_loader=build_process_graph,
            policies=CompilerPolicies(review_policy, acceptance_policies=acceptance),
            visibility=visibility)

    def fragment(self, index: int, *, ref=None, content: bytes = SOP) -> SourceFragmentRef:
        return SourceFragmentRef.from_content(
            ref or self.sop_ref, content, line_location(content, index),
            fragment_kind="PARAGRAPH", fragmentation_policy=POLICY)

    def provenance(self, **changes) -> DerivationProvenance:
        return DerivationProvenance(extraction_method="fixture-extractor@1",
                                    grounding_method="exact-id@1", **changes)

    def binding(self, candidate_id: str, line: int, mention: str, kind: TargetKind,
                target_id: str | None, *, extraction=ExtractionKind.ENTITY_MENTION,
                alternatives=(), scope=(REACTOR,), provenance=None, session=None,
                **changes) -> CandidateBinding:
        return CandidateBinding(
            candidate_id=candidate_id, session_id=(session or self.session).session_id,
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


def committed(staged) -> dict:
    """The revision document exactly as staged (plain JSON)."""
    return json.loads(dict(staged.files)[staged.revision.path])


def reseal(files: dict, document: dict, log: dict | None = None) -> ArtifactPathRef:
    """Re-package an edited revision (and optionally log) with valid checksums."""
    if log is not None:
        data = canonical_json(log)
        path = f"context/engineering-context/x/review-logs/{hashlib.sha256(data).hexdigest()}"
        files[path] = data
        document["review_log"] = {"review_log_position": log["review_log_position"],
                                  "path": path,
                                  "content_checksum": hashlib.sha256(data).hexdigest()}
    document.pop("revision_id", None)
    document["revision_id"] = "ecr-" + context_compiler._digest(document)
    data = canonical_json(document)
    ref = ArtifactPathRef.of(f"context/engineering-context/x/revisions/"
                             f"{document['revision_id']}.json", data)
    files[ref.path] = data
    return ref


class CompilerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.c = Compilation(self.root / "sources")
        self.session = self.c.session
        self.workspace = CompilerWorkspace("ws-1")

    def add(self, candidate, session=None, workspace=None):
        (workspace or self.workspace).add_candidate(session or self.session, candidate)
        return candidate.ref

    def review(self, ref, decision=ReviewDecision.APPROVE, review_id=None, workspace=None,
               **changes):
        workspace = workspace or self.workspace
        count = workspace.review_log_tail + 1
        return workspace.append_review(
            ref, decision, review_id=review_id or f"review-{count}",
            principal=changes.pop("principal", HUMAN),
            reason=changes.pop("reason", "matches the cited fragment"), reviewed_at=STAMP,
            **changes)

    def request(self, refs, *, parent=None, lineage="reactor-cooling"):
        return PublicationRequest(lineage_id=lineage, candidate_refs=tuple(refs),
                                  published_by="app-user-1", published_at=STAMP,
                                  parent_revision=parent)

    def stage(self, refs, session=None, workspace=None, **changes):
        return (workspace or self.workspace).stage_publication(
            session or self.session, self.request(refs, **changes),
            derived_input_resolver=resolver)

    def verify(self, files, revision):
        return verify_publication(files, revision, self.c.r0)


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

    def test_m6_direct_construction_keeps_the_invariants(self) -> None:
        unfrozen = CanonicalContextRegistry(REVISIONS, self.c.materializers())
        for source in (GRAPH, self.c.sop_ref, self.c.r0_ref):
            unfrozen.register(source)
        args = (GRAPH.source_id, R0_ID, build_process_graph, CompilerPolicies("lab-review@1"))
        with self.assertRaises(CompilerError):
            CompileSession(unfrozen, *args)
        unfrozen.freeze()
        direct = CompileSession(unfrozen, *args)
        self.assertEqual(self.session.session_id, direct.session_id)
        with self.assertRaises(CompilerError):
            CompileSession(unfrozen, "wrong.graph", R0_ID, build_process_graph,
                           CompilerPolicies("lab-review@1"))
        with self.assertRaises(CompilerError):
            CompileSession(unfrozen, *args, visibility="PUBLIC")

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
        self.assertEqual({first}, {again})
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
                    lambda: FragmentLocation("SECTION"),
                    lambda: replace(first, fragmentation_policy="no-version")):
            with self.assertRaises(CompilerError):
                bad()

    def test_h2_byte_ranges_need_exact_bytes_sources(self) -> None:
        with self.assertRaises(CompilerError):  # canonical-JSON checksum ignores CRLF
            SourceFragmentRef(source_ref=GRAPH, location=FragmentLocation.byte_range(0, 10),
                              content_checksum="0" * 64, fragment_kind="PARAGRAPH",
                              fragmentation_policy=POLICY)
        crlf = SOP.replace(b"\n", b"\r\n")  # a CRLF checkout of the same SOP
        self.c.write(SOP_PATH, crlf)
        with self.assertRaises(CompilerError):  # exact-bytes attestation refuses it
            self.c.prepare([GRAPH, self.c.sop_ref, self.c.r0_ref])
        self.assertFalse(self.c.fragment(3).verify(crlf))
        forged = self.c.fragment(3)
        object.__setattr__(forged, "source_ref", replace(
            self.c.sop_ref, checksum_method="sha256:canonical-json"))
        self.assertFalse(forged.verify(SOP))

    def test_m7_fragment_extraction_wraps_malformed_sources(self) -> None:
        pointer = FragmentLocation.json_pointer("/a")
        for content in (b"{\"a\": 1, \"a\": 2}", b"\xff\xfe", b"[" * 100000, b"NaN"):
            with self.assertRaises(CompilerError):
                pointer.extract(content)
        with self.assertRaises(CompilerError):
            FragmentLocation.json_pointer("/99999999999").extract(b"[1]")

    def test_ac3_candidates_are_immutable_versions(self) -> None:
        candidate = self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor")
        with self.assertRaises(FrozenInstanceError):
            candidate.proposed_target = target(TargetKind.ENTITY, "condenser")
        retry = self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor",
                               provenance=self.c.provenance(model="fixture/model@2"))
        self.assertEqual(candidate.content_key, retry.content_key)
        self.assertNotEqual(candidate.candidate_version, retry.candidate_version)
        self.assertEqual(2, len({candidate, retry, candidate}))
        ref = self.add(candidate)
        with self.assertRaises(WorkspaceAppendError):
            self.add(candidate)
        self.add(retry)  # a new version is appended; the historical one is kept
        self.assertEqual((candidate, retry), self.workspace.candidates())
        self.assertIs(candidate, self.workspace.candidate(ref))
        foreign = replace(self.c.knowledge(), session_id="cs-other")
        with self.assertRaises(WorkspaceAppendError):
            self.workspace.add_candidate(self.session, foreign)

    def test_low_identity_normalization(self) -> None:
        nfc = self.c.binding("cafe", 2, "café reactor", TargetKind.ENTITY, "reactor")
        nfd = self.c.binding("cafe", 2, unicodedata.normalize("NFD", "café reactor"),
                             TargetKind.ENTITY, "reactor")
        self.assertEqual(nfc.content_key, nfd.content_key)
        self.assertEqual(TypedValue("limit", 130, "degC"), TypedValue("limit", 130.0, "degC"))
        self.assertEqual(1.0, Confidence(1, "fixture-score@1").score)
        for bad in (True, float("nan"), 2 ** 60):
            with self.assertRaises(CompilerError):
                TypedValue("limit", bad)
        with self.assertRaises(CompilerError):
            Confidence(1.5, "fixture-score@1")

    def test_versioned_routing_and_unclassified_material(self) -> None:
        with self.assertRaises(CompilerError):  # rule-shaped kind cannot become a binding
            self.c.binding("x", 2, "reactor", TargetKind.ENTITY, "reactor",
                           extraction=ExtractionKind.OPERATING_LIMIT)
        with self.assertRaises(CompilerError):  # unknown material is never forced in
            self.c.binding("x", 2, "reactor", TargetKind.ENTITY, "reactor",
                           extraction="acme:VALVE_LORE")
        with self.assertRaises(CompilerError):
            self.c.binding("x", 2, "reactor", "BAD_KIND", "reactor")
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


class ValidationTests(CompilerTestCase):
    def codes(self, candidate, review=None, **changes):
        return {item.code for item in validate_candidate(self.session, candidate,
                                                         review, **changes).findings}

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

    def test_h3_same_source_id_with_another_identity_is_attested_separately(self) -> None:
        good = self.c.fragment(2)
        for change in ({"git_revision": "d" * 40}, {"path_or_ref": "docs/not-in-inventory.md"},
                       {"content_checksum": "3" * 64}):
            with self.subTest(change=change):
                forged = self.c.fragment(4, ref=replace(self.c.sop_ref, **change))
                candidate = replace(self.c.knowledge(conflicting_fragments=(forged,)),
                                    source_fragments=(good,))
                self.assertIn(FindingCode.SOURCE_UNRESOLVED, self.codes(candidate))
                self.assertFalse(validate_candidate(self.session, candidate).passed)

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
        self.assertIn(FindingCode.CONFLICT_UNRESOLVED,
                      self.codes(conflicted, known_candidate_ids=frozenset()))


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
        self.assertEqual(4, len({approve, reject, keep, remap}))
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

    def test_m8_review_timestamps_are_strict_utc(self) -> None:
        ref = self.add(self.c.binding("b", 2, "reactor", TargetKind.ENTITY, "reactor"))
        for stamp in ("2026-10-10", "2026-10-10T00:00:00+08:00", "2026-13-10T00:00:00Z",
                      "2026-10-10 00:00:00Z"):
            with self.subTest(stamp=stamp):
                with self.assertRaises(CompilerError):
                    self.workspace.append_review(ref, ReviewDecision.APPROVE, review_id="r1",
                                                 principal=HUMAN, reason="x",
                                                 reviewed_at=stamp)
                with self.assertRaises(CompilerError):
                    PublicationRequest(lineage_id="l", candidate_refs=(), published_by="u",
                                       published_at=stamp)
        self.assertEqual(0, self.workspace.review_log_tail)

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
        with self.assertRaises(CompilerError):
            ReviewPrincipal("AGENT", "planner")
        with self.assertRaises(ValueError):
            PrincipalKind("MODEL")
        with self.assertRaises(WorkspaceAppendError):  # no acceptance policy registered
            self.review(ref, principal=ReviewPrincipal(PrincipalKind.DETERMINISTIC_POLICY,
                                                       "auto@1"))
        with self.assertRaises(WorkspaceAppendError):
            self.review(ref, principal="model")

    def test_ac16_ac22_multiple_terminal_records_block_until_superseded(self) -> None:
        ref = self.add(self.c.binding("b", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref, review_id="r1")
        self.review(ref, review_id="r2")
        effective = self.workspace.effective_review(ref)
        self.assertEqual((ReviewStatus.UNRESOLVED, ("r1", "r2")),
                         (effective.status, effective.terminal_review_ids))
        blocked = self.stage([ref])  # AC16: two agreeing terminal reviews still block
        self.assertEqual([(ref, ExclusionReason.UNRESOLVED_REVIEW)],
                         [(item.candidate_ref, item.reason) for item in blocked.exclusions])
        self.assertEqual([], committed(blocked)["approved_bindings"])
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
                provenance=c.provenance(confidence=Confidence(0.97, "fixture-score@1"),
                                        derived_inputs=(model_input(),)))),
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

    def test_vertical_staged_publication_contract(self) -> None:
        refs = self.vertical_slice()
        before = self.workspace.candidates()
        staged = self.stage(refs.values())
        document = committed(staged)
        self.assertIsInstance(staged, StagedPublication)
        self.assertFalse(staged.canonical)
        # AC8 / AC5 / AC12: rejected, unresolved and unreviewed candidates stay out.
        self.assertEqual({(refs["temperature"], ExclusionReason.UNREVIEWED),
                          (refs["pump"], ExclusionReason.KEEP_CANDIDATE),
                          (refs["wrong"], ExclusionReason.REJECTED)},
                         {(item.candidate_ref, item.reason) for item in staged.exclusions})
        bindings = {item["candidate_ref"]["candidate_id"]: item
                    for item in document["approved_bindings"]}
        self.assertEqual({"reactor", "actuator"}, set(bindings))
        # AC7 / AC25: the REMAP target comes from the review; the proposal is preserved.
        actuator = bindings["actuator"]
        self.assertEqual("reactor_cooling.flow_actuator", actuator["target"]["target_id"])
        self.assertEqual(("REMAP", "r-actuator"), (actuator["decision"], actuator["review_id"]))
        # AC11 / H5: full provenance, original mapping and alternatives are retained.
        self.assertEqual({"score": 0.97, "method": "fixture-score@1"},
                         actuator["provenance"]["confidence"])
        self.assertEqual([model_input().record()], actuator["provenance"]["derived_inputs"])
        candidates = json.loads(dict(staged.files)[staged.candidates.path])["candidates"]
        stored = {item["candidate_id"]: item for item in candidates}
        self.assertEqual({"reactor", "actuator", "temperature", "pump", "wrong", "limit",
                          "sticky-valve"}, set(stored))
        self.assertEqual("condenser_cooling.flow_actuator",
                         stored["actuator"]["content"]["proposed_target"]["target_id"])
        self.assertEqual("reactor_cooling.flow_actuator", stored["actuator"]["version_extra"][
            "alternative_targets"][0]["target_id"])
        reactor = bindings["reactor"]
        self.assertEqual(self.c.fragment(2).fragment_id, reactor["fragments"][0]["fragment_id"])
        self.assertEqual(("fixture-extractor@1", "exact-id@1", True),
                         (reactor["provenance"]["extraction_method"],
                          reactor["provenance"]["grounding_method"],
                          reactor["validation"]["passed"]))
        self.assertEqual(sorted(["r-reactor", "r-actuator", "r-limit", "r-valve"]),
                         document["review_ids"])
        (valve,) = document["approved_knowledge"]
        self.assertEqual(("VERIFIED", "r-valve"),
                         (valve["knowledge_ref"]["validation_status"],
                          valve["knowledge_ref"]["validation_evidence_review_id"]))
        self.assertEqual(self.c.fragment(4).record(), valve["knowledge_ref"]["content"][0])
        self.assertEqual("sticky-valve", document["conflicts"][0]["candidate_ref"]["candidate_id"])
        # AC9 / AC19 / AC23: R1 = R0 unchanged + the approved inert candidate Rule.
        files = dict(staged.files)
        r1 = load_rule_set(files[staged.published_rule_set.path],
                           staged.published_rule_set.content_checksum)
        self.assertEqual(self.c.r0_ref, r1.parent)
        rule = self.workspace.candidate(refs["limit"]).rule
        self.assertEqual(rule, r1.registry.get_rule(rule.ref))
        self.assertEqual((rule,), r1.registry.query_rules("tep/reactor"))
        self.assertEqual(reviewed_blind_rca_policy(),
                         r1.registry.get_rule(reviewed_blind_rca_policy().ref))
        (scope_binding,) = document["rule_scope_bindings"]
        self.assertEqual(("tep/reactor", "r-limit", [REACTOR.record()]),
                         (scope_binding["rule_scope"], scope_binding["review_id"],
                          scope_binding["scope_refs"]))
        # AC22: pinned to the tail and recomputable from bundle content alone.
        self.assertEqual(self.workspace.review_log_tail, staged.review_log_position)
        verified = self.verify(files, staged.revision)
        self.assertEqual({ReviewStatus.APPROVED, ReviewStatus.REMAPPED},
                         {item.status for item in verified.effective_reviews})
        with self.assertRaises(TypeError):
            staged.document["approved_bindings"] = ()
        self.assertEqual(before, self.workspace.candidates())

    def test_ac10_staging_advances_no_head_and_records_the_expected_parent(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref)
        first = self.stage([ref])
        self.assertEqual(head_path("reactor-cooling"), first.head_path)
        self.assertNotIn(first.head_path, dict(first.files))
        self.assertIsNone(json.loads(first.head_record)["expected_parent"])
        self.assertFalse(hasattr(context_compiler, "DirectoryPublicationTarget"))
        self.assertFalse(hasattr(context_compiler, "PublishedRevision"))
        self.assertFalse(hasattr(CompilerWorkspace, "publish"))
        parent = ContextSourceRef(
            source_id="lab.ecr.parent", repository=LAB_REPOSITORY, git_revision="e" * 40,
            path_or_ref=first.revision.path, content_checksum=first.revision.content_checksum,
            kind=ENGINEERING_CONTEXT_REVISION_KIND,
            schema_version=context_compiler.REVISION_SCHEMA_VERSION,
            visibility=Visibility.AGENT)
        second = self.stage([ref], parent=parent)
        self.assertEqual(first.revision.record(),
                         json.loads(second.head_record)["expected_parent"])
        self.assertEqual(parent.record(), committed(second)["parent_revision"])
        self.assertNotEqual(first.revision, second.revision)

    def test_m3_bundle_is_validated(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref)
        staged = self.stage([ref])
        outside = (*staged.files, ("docs/elsewhere.json", b"{}"))
        missing = tuple(item for item in staged.files if item[0] != staged.revision.path)
        tampered = tuple((path, data + b" " if path == staged.revision.path else data)
                         for path, data in staged.files)
        head = (*staged.files, (staged.head_path, staged.head_record))
        for files in (outside, missing, tampered, head):
            with self.assertRaises(PublicationError):
                replace(staged, files=files)

    def test_ac20_staged_revision_registers_by_ref_once_written(self) -> None:
        refs = self.vertical_slice()
        staged = self.stage(refs.values())
        repo = self.root / "repo"
        for path, data in staged.files:  # what the P1.2D commit will add
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_bytes(data)
        ref = ContextSourceRef(
            source_id="lab.engineering-context.reactor-cooling", repository=LAB_REPOSITORY,
            git_revision="e" * 40, path_or_ref=staged.revision.path,
            content_checksum=staged.revision.content_checksum,
            kind=ENGINEERING_CONTEXT_REVISION_KIND,
            schema_version=context_compiler.REVISION_SCHEMA_VERSION,
            visibility=Visibility.AGENT)
        materializer = {LAB_REPOSITORY: DirectorySourceMaterializer(repo)}
        registry = CanonicalContextRegistry({LAB_REPOSITORY: "e" * 40}, materializer)
        registry.register(ref)
        (repo / staged.revision.path).write_bytes(b'{"tampered": true}')
        with self.assertRaises(ContextSourceError):
            CanonicalContextRegistry({LAB_REPOSITORY: "e" * 40}, materializer).register(ref)

    def test_ac17_remap_targets_and_scope_are_revalidated(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "condenser"))
        self.review(ref, ReviewDecision.REMAP,
                    selected_target=target(TargetKind.SIGNAL, "reactor.temperature_measurement"),
                    selected_scope_refs=(REACTOR,))
        scope = self.add(self.c.knowledge())
        self.review(scope, ReviewDecision.REMAP,
                    selected_scope_refs=(target(TargetKind.ENTITY, "no-such-unit"),))
        conflict = self.add(self.c.knowledge("conflicted", conflicting_candidate_ids=("ghost",)))
        self.review(conflict, ReviewDecision.REMAP,
                    selected_scope_refs=(target(TargetKind.ENTITY, "stripper"),))
        staged = self.stage([ref, scope, conflict])
        details = {item.candidate_ref: (item.reason, item.detail) for item in staged.exclusions}
        self.assertEqual((ExclusionReason.REMAP_INVALID, "TARGET_KIND_INCOMPATIBLE"),
                         details[ref])
        self.assertEqual((ExclusionReason.REMAP_INVALID, "SCOPE_UNKNOWN"), details[scope])
        self.assertEqual((ExclusionReason.REMAP_INVALID, "CONFLICT_UNRESOLVED"),
                         details[conflict])

    def test_ac22_staging_uses_the_tail_and_cannot_skip_a_later_reject(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref, review_id="r1")
        self.review(ref, ReviewDecision.REJECT, review_id="r2", supersedes=("r1",))
        self.assertNotIn("position", " ".join(inspect.signature(
            CompilerWorkspace.stage_publication).parameters))
        self.assertNotIn("review_log_position", PublicationRequest.__dataclass_fields__)
        staged = self.stage([ref])
        self.assertEqual(2, staged.review_log_position)
        self.assertEqual(ExclusionReason.REJECTED, staged.exclusions[0].reason)
        self.assertEqual(ReviewStatus.APPROVED, self.workspace.review_status(ref, position=1))

    def test_ac22_workspace_lock_serializes_review_and_staging(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref, review_id="r1")
        entered, release = threading.Event(), threading.Event()
        real = context_compiler.verify_publication

        def blocking(*args, **kwargs):
            entered.set()
            release.wait(10)
            return real(*args, **kwargs)

        staged, appended = {}, {}
        with mock.patch.object(context_compiler, "verify_publication", blocking):
            stager = threading.Thread(target=lambda: staged.update(result=self.stage([ref])))
            stager.start()
            self.assertTrue(entered.wait(10))
            reviewer = threading.Thread(target=lambda: appended.update(record=self.review(
                ref, ReviewDecision.REJECT, review_id="r2", supersedes=("r1",))))
            reviewer.start()
            reviewer.join(0.3)
            self.assertTrue(reviewer.is_alive())  # blocked until staging completes
            release.set()
            stager.join(10)
            reviewer.join(10)
        self.assertEqual(1, staged["result"].review_log_position)
        self.assertEqual(2, appended["record"].review_log_position)
        self.assertEqual(1, len(committed(staged["result"])["approved_bindings"]))
        self.assertIsInstance(self.workspace._lock, type(threading.Lock()))

    def test_ac21_dependency_failure_stages_nothing(self) -> None:
        refs = self.vertical_slice()
        with self.assertRaises(PublicationError):
            self.stage([*refs.values(), CandidateRef("ghost", "0" * 64)])
        twin = self.add(self.c.rule_candidate("limit-twin"))  # same RuleRef, other candidate
        self.review(twin)
        with self.assertRaisesRegex(PublicationError, "same RuleRef: reactor-limit@1"):
            self.stage([refs["limit"], twin])
        (self.c.root / SOP_PATH).write_bytes(SOP.replace(b"130", b"135"))
        with self.assertRaises(PublicationError):
            self.stage(refs.values())

    def test_h5_derived_inputs_must_resolve_to_their_exact_bytes(self) -> None:
        phantom = model_input(ref="nonexistent:model-call")
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor",
                                      provenance=self.c.provenance(derived_inputs=(phantom,))))
        self.review(ref)
        with self.assertRaises(PublicationError):  # the resolver cannot produce it
            self.stage([ref])
        with self.assertRaises(PublicationError):  # no resolver at all
            self.workspace.stage_publication(self.session, self.request([ref]))
        wrong = self.add(self.c.binding("wrong", 2, "reactor", TargetKind.ENTITY, "reactor",
                                        provenance=self.c.provenance(derived_inputs=(
                                            model_input(data=b"other bytes"),))))
        self.review(wrong)
        with self.assertRaises(PublicationError):
            self.stage([wrong])

    def test_ac14_new_source_or_policy_tuple_needs_new_review_and_revision(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref)
        first = self.stage([ref])
        changed = self.c.prepare([GRAPH, self.c.sop_ref, self.c.r0_ref],
                                 review_policy="lab-review@2")
        stale = self.stage([ref], session=changed)
        self.assertEqual(ExclusionReason.STALE_SESSION, stale.exclusions[0].reason)
        redone = replace(self.workspace.candidate(ref), session_id=changed.session_id)
        self.workspace.add_candidate(changed, redone)
        self.assertEqual(ReviewStatus.UNREVIEWED, self.workspace.review_status(redone.ref))
        self.review(redone.ref)
        third = self.stage([redone.ref], session=changed)
        self.assertEqual(1, len(committed(third)["approved_bindings"]))
        self.assertEqual(3, len({first.revision, stale.revision, third.revision}))
        self.assertEqual("lab-review@2", committed(third)["review_policy"])

    def test_m4_revision_is_independent_of_selection_order(self) -> None:
        refs = self.vertical_slice()
        forward = self.stage(list(refs.values()))
        backward = self.stage(list(reversed(refs.values())))
        self.assertEqual(forward.files, backward.files)
        for section in ("approved_bindings", "exclusions", "considered_candidate_refs"):
            listed = [CandidateRef.from_record(item.get("candidate_ref", item))
                      for item in committed(forward)[section]]
            self.assertEqual(sorted(listed), listed)

    def test_m2_lineage_ids_are_strict(self) -> None:
        for lineage in ("a:b", "trailing.", "Upper", "a/b", "nul", "", "a" * 65):
            with self.subTest(lineage=lineage):
                with self.assertRaises(CompilerError):
                    self.request([], lineage=lineage)
        self.request([], lineage="reactor-cooling.v2")


class VisibilityTests(CompilerTestCase):
    def test_ac13_ac18_visibility_and_derivation_taint(self) -> None:
        hidden = Compilation(self.root / "hidden-sources", hidden=True,
                             visibility=Visibility.EVALUATOR)
        hidden_workspace = CompilerWorkspace("ws-hidden", Visibility.EVALUATOR)
        tainted = hidden.binding("valve", 4, "cooling valve", TargetKind.ENTITY, "reactor",
                                 conflicting_fragments=(hidden.fragment(
                                     0, ref=hidden.hidden_ref, content=HIDDEN),))
        self.assertEqual(Visibility.EVALUATOR, tainted.visibility)
        with self.assertRaises(WorkspaceAppendError):  # wrong-visibility workspace
            self.workspace.add_candidate(hidden.session, tainted)
        self.add(tainted, session=hidden.session, workspace=hidden_workspace)
        self.review(tainted.ref, workspace=hidden_workspace)
        hidden_staged = self.stage([tainted.ref], session=hidden.session,
                                   workspace=hidden_workspace, lineage="hidden")
        self.assertEqual("EVALUATOR", committed(hidden_staged)["visibility"])
        with self.assertRaises(PublicationError):  # no cross-visibility staging
            self.stage([tainted.ref], session=self.session, workspace=hidden_workspace)
        # Hidden model context or review rationale cannot enter the AGENT workspace.
        leaky = self.c.binding("leaky", 2, "reactor", TargetKind.ENTITY, "reactor",
                               provenance=self.c.provenance(
                                   derived_inputs=(model_input(Visibility.EVALUATOR),)))
        with self.assertRaises(WorkspaceAppendError):
            self.add(leaky)
        self.assertIn(FindingCode.VISIBILITY_WIDENED, {
            item.code for item in validate_candidate(self.session, leaky).findings})
        rationale = self.add(self.c.binding("rationale", 2, "reactor", TargetKind.ENTITY,
                                            "reactor"))
        with self.assertRaises(WorkspaceAppendError):
            self.review(rationale, visibility=Visibility.EVALUATOR)
        # Removing the hidden ref is a new candidate version needing its own review.
        stripped = replace(leaky, provenance=self.c.provenance())
        self.assertNotEqual(leaky.ref, stripped.ref)
        self.add(stripped)
        staged = self.stage([stripped.ref])
        self.assertEqual([(stripped.ref, ExclusionReason.UNREVIEWED)],
                         [(item.candidate_ref, item.reason) for item in staged.exclusions])
        self.assertEqual("AGENT", committed(staged)["visibility"])
        self.assertNotIn(HIDDEN_ID, json.dumps(committed(staged)))

    def test_h4_hidden_activity_never_changes_agent_revision_bytes(self) -> None:
        ref = self.add(self.c.binding("reactor", 2, "reactor", TargetKind.ENTITY, "reactor"))
        self.review(ref)
        other = self.add(self.c.binding("other", 2, "reactor", TargetKind.ENTITY, "reactor"))
        before = self.stage([ref]).files
        with self.assertRaises(WorkspaceAppendError):  # refused, so the tail does not move
            self.review(other, visibility=Visibility.EVALUATOR)
        hidden = Compilation(self.root / "hidden-sources", hidden=True,
                             visibility=Visibility.EVALUATOR)
        hidden_workspace = CompilerWorkspace("ws-1", Visibility.EVALUATOR)
        elsewhere = self.add(hidden.binding("x", 2, "reactor", TargetKind.ENTITY, "reactor"),
                             session=hidden.session, workspace=hidden_workspace)
        self.review(elsewhere, workspace=hidden_workspace, visibility=Visibility.EVALUATOR)
        self.assertEqual(before, self.stage([ref]).files)


class VerifierForgeryTests(CompilerTestCase):
    """H6: each forgery is re-packaged with valid checksums and must still fail."""

    vertical_slice = PublicationTests.vertical_slice

    def setUp(self) -> None:
        super().setUp()
        self.refs = self.vertical_slice()
        self.staged = self.stage(self.refs.values())
        self.files = dict(self.staged.files)
        self.document = committed(self.staged)
        self.log = json.loads(self.files[self.staged.review_log.path])

    def assertForged(self, document, log=None) -> None:
        files = dict(self.files)
        ref = reseal(files, document, log)
        with self.assertRaises(PublicationError):
            self.verify(files, ref)

    def test_resealed_unchanged_bundle_still_verifies(self) -> None:
        files = dict(self.files)  # so each forgery below fails for its edit alone
        self.verify(files, reseal(files, self.document, self.log))

    def test_changed_approve_target(self) -> None:
        item = next(item for item in self.document["approved_bindings"]
                    if item["decision"] == "APPROVE")
        item["target"]["target_id"] = "condenser"
        self.assertForged(self.document)

    def test_unregistered_deterministic_policy_principal(self) -> None:
        record = next(item for item in self.log["records"] if item["review_id"] == "r-reactor")
        record["principal"] = {"kind": "DETERMINISTIC_POLICY", "identifier": "unregistered@1"}
        self.assertForged(self.document, self.log)

    def test_r1_dropping_r0_rules_keeps_parent(self) -> None:
        r1 = load_rule_set(self.files[self.staged.published_rule_set.path],
                           self.staged.published_rule_set.content_checksum)
        rule = self.workspace.candidate(self.refs["limit"]).rule
        forged = RuleSetRevision(RuleRegistry((rule,)), r1.visibility, r1.parent)
        path = f"context/rule-sets/{forged.checksum}.json"
        self.files[path] = forged.serialize()
        self.document["published_rule_set"] = {"revision": "SAME_COMMIT", "path": path,
                                               "content_checksum": forged.checksum}
        self.assertForged(self.document)

    def test_decision_and_review_id_mismatches(self) -> None:
        document = json.loads(json.dumps(self.document))
        item = document["approved_bindings"][0]
        item["decision"] = "APPROVE" if item["decision"] == "REMAP" else "REMAP"
        self.assertForged(document)
        document = json.loads(json.dumps(self.document))
        document["approved_bindings"][0]["review_id"] = "r-wrong"
        self.assertForged(document)
        document = json.loads(json.dumps(self.document))
        document["review_ids"] = document["review_ids"][1:]
        self.assertForged(document)

    def test_incompatible_review_tuple_and_extra_fields(self) -> None:
        log = json.loads(json.dumps(self.log))
        next(item for item in log["records"] if item["review_id"] == "r-reactor")[
            "review_policy"] = "other-review@9"
        self.assertForged(json.loads(json.dumps(self.document)), log)
        document = json.loads(json.dumps(self.document))
        document["extra"] = True
        self.assertForged(document)
        document = json.loads(json.dumps(self.document))
        document["acceptance_policies"] = ["unregistered@1"]
        self.assertForged(document)  # compile_session_id no longer matches the tuple

    def test_m7_duplicate_keys_are_rejected(self) -> None:
        data = self.files[self.staged.revision.path]
        forged = b'{"visibility":"EVALUATOR",' + data[1:]
        files = dict(self.files)
        files[self.staged.revision.path] = forged
        with self.assertRaises(PublicationError):
            self.verify(files, ArtifactPathRef.of(self.staged.revision.path, forged))
        r0 = self.c.r0.serialize()
        with self.assertRaises(RuleSetError):
            load_rule_set(b'{"visibility":"EVALUATOR",' + r0[1:], self.c.r0.checksum)

    def test_wrong_input_rule_set(self) -> None:
        other = RuleSetRevision(RuleRegistry(()), Visibility.AGENT)
        with self.assertRaises(PublicationError):
            verify_publication(self.files, self.staged.revision, other)


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
        for runtime_id in ("XMEAS", "XMV", "IDV"):
            self.assertNotIn(runtime_id, self.SOURCE)

    def test_staging_writes_no_files(self) -> None:
        for call in ("open(", "write_bytes", "write_text", "os.replace", "mkdir"):
            self.assertNotIn(call, self.SOURCE)

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
        workspace_api = {"add_candidate", "append_review", "stage", "publish", "review",
                         "approve", "remap", "reject", "candidate"}
        for name in names | allowlist:
            self.assertFalse(any(word in name for word in workspace_api), name)
        for module in ("tool_surface", "tool_bridge", "playground", "investigation"):
            text = (Path(context_compiler.__file__).with_name(f"{module}.py")
                    .read_text(encoding="utf-8"))
            self.assertNotIn("context_compiler", text, module)


if __name__ == "__main__":
    unittest.main()
