"""Rule-set source revision tests (knowledge-rule-registry-v0 §Rule-set source revision)."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from industrial_agent_runtime import Visibility

from tep_agent_lab.canonical_context import (
    CanonicalContextRegistry, ContextSourceRef, DirectorySourceMaterializer, ProjectionScope,
    most_restrictive,
)
from tep_agent_lab.rule_sets import (
    RULE_SET_KIND, RULE_SET_SCHEMA_VERSION, RuleSetError, RuleSetRevision, check_extends,
    conflict_from_record, conflict_record, load_rule_set, load_rule_set_source,
    rule_from_record, rule_record,
)
from tep_agent_lab.rules import (
    Authority, Behavior, ExtractionMetadata, Origin, Rule, RuleConflict, RuleRegistry,
    SourceVersion, Validation, literature_candidate, reviewed_blind_rca_policy,
)

LAB = "shaun0457/tep-agent-lab"
REVISION = "b" * 40
SOURCE = SourceVersion("fixture://manual", "revision-1", "section 4, page 9")
STAMP = "2026-10-10T00:00:00Z"


def full_rule(**changes: object) -> Rule:
    """A rule with every optional field populated, including extraction metadata."""
    extraction = ExtractionMetadata(SOURCE, "cooling relation", ("temperature", "flow"),
                                    "steady state", "degC", "monotonic", "fixture only")
    base = Rule("cooling-guidance", "1", "Illustrative relation", Origin.LITERATURE,
                Validation.CORROBORATED, Authority.ADVISORY, "test/cooling",
                "more flow lowers temperature", ("temperature", "flow"), (SOURCE,),
                (SourceVersion("fixture://campaign", "c-1", "held-out"),), "active",
                "host-fixture", STAMP, STAMP, expected_effect_or_constraint="lower T",
                operating_envelope="normal", units="degC", support_summary="two sources",
                reviewed_by="fixture-reviewer", behavior=Behavior.SCORE, extraction=extraction)
    return replace(base, **changes)


def registry() -> RuleRegistry:
    first = full_rule()
    second = replace(first, rule_id="other-guidance", extraction=None, behavior=None,
                     expected_effect_or_constraint=None, operating_envelope=None,
                     units=None, support_summary=None, reviewed_by=None)
    conflict = RuleConflict((first.ref, second.ref), "test/cooling", "opposing trend",
                            "second is narrower", "unresolved")
    return RuleRegistry((second, first, reviewed_blind_rca_policy()), (conflict,))


class RuleRecordTests(unittest.TestCase):
    def test_every_rule_field_round_trips(self) -> None:
        for rule in registry().rules():
            record = rule_record(rule)
            self.assertEqual(rule, rule_from_record(json.loads(json.dumps(record))))
        self.assertEqual(set(Rule.__dataclass_fields__), set(rule_record(full_rule())))

    def test_conflict_round_trips(self) -> None:
        conflict = registry().conflicts()[0]
        self.assertEqual(conflict, conflict_from_record(conflict_record(conflict)))

    def test_strict_records(self) -> None:
        record = rule_record(full_rule())
        for broken in ({**record, "extra": 1}, {k: v for k, v in record.items() if k != "units"},
                       {**record, "origin": "MODEL"}, {**record, "units": 3},
                       {**record, "inputs": "temperature"},
                       {**record, "authority": "HARD_GATE"}):
            with self.subTest(broken=sorted(set(broken) ^ set(record)) or "value"):
                with self.assertRaises((RuleSetError, ValueError, TypeError)):
                    rule_from_record(broken)


class RuleSetRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def commit(self, revision: RuleSetRevision, path: str,
               visibility: Visibility = Visibility.AGENT) -> ContextSourceRef:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(revision.serialize())
        return ContextSourceRef(source_id="rules.r0", repository=LAB, git_revision=REVISION,
                                path_or_ref=path, content_checksum=revision.checksum,
                                kind=RULE_SET_KIND, schema_version=RULE_SET_SCHEMA_VERSION,
                                visibility=visibility)

    def source_registry(self, ref: ContextSourceRef) -> CanonicalContextRegistry:
        context = CanonicalContextRegistry({LAB: REVISION},
                                           {LAB: DirectorySourceMaterializer(self.root)})
        context.register(ref)
        context.freeze()
        return context

    def test_round_trip_preserves_rules_and_conflicts(self) -> None:
        original = registry()
        revision = RuleSetRevision(original, Visibility.AGENT)
        loaded = load_rule_set(revision.serialize(), revision.checksum)
        self.assertEqual(original, loaded.registry)
        for rule in original.rules():
            self.assertEqual(original.find_rule_conflicts(rule.ref),
                             loaded.registry.find_rule_conflicts(rule.ref))
            self.assertEqual(rule, loaded.registry.get_rule(rule.ref))
        self.assertEqual(revision, loaded)

    def test_serialization_is_deterministic_and_order_independent(self) -> None:
        rules = registry().rules()
        conflicts = registry().conflicts()
        a = RuleSetRevision(RuleRegistry(rules, conflicts), Visibility.AGENT)
        b = RuleSetRevision(RuleRegistry(tuple(reversed(rules)), conflicts), Visibility.AGENT)
        self.assertEqual(a.serialize(), b.serialize())
        self.assertEqual(a.checksum, b.checksum)
        document = json.loads(a.serialize())
        self.assertEqual((RULE_SET_SCHEMA_VERSION, RULE_SET_KIND, None),
                         (document["schema_version"], document["kind"], document["parent"]))

    def test_loader_verifies_checksum_and_schema(self) -> None:
        revision = RuleSetRevision(registry(), Visibility.AGENT)
        data = revision.serialize()
        with self.assertRaises(RuleSetError):
            load_rule_set(data, "0" * 64)
        tampered = json.loads(data)
        tampered["rules"][0]["title"] = "edited"
        changed = json.dumps(tampered).encode()
        from tep_agent_lab.canonical_context import content_checksum
        checksum = content_checksum(changed, "sha256:canonical-json")
        self.assertNotEqual(revision.checksum, checksum)
        for broken in ({**json.loads(data), "kind": "OTHER"},
                       {**json.loads(data), "extra": True},
                       {**json.loads(data), "conflicts": [
                           {**json.loads(data)["conflicts"][0],
                            "rule_refs": [{"rule_id": "missing", "version": "1"},
                                          {"rule_id": "other-guidance", "version": "1"}]}]}):
            encoded = json.dumps(broken).encode()
            with self.assertRaises(RuleSetError):
                load_rule_set(encoded, content_checksum(encoded, "sha256:canonical-json"))

    def test_same_ref_loads_same_immutable_registry(self) -> None:
        revision = RuleSetRevision(registry(), Visibility.AGENT)
        ref = self.commit(revision, "context/rule-sets/r0.json")
        context = self.source_registry(ref)
        first = load_rule_set_source(context, ref.source_id, ProjectionScope.AGENT)
        second = load_rule_set_source(context, ref.source_id, ProjectionScope.AGENT)
        self.assertEqual(first, second)
        self.assertEqual(revision.registry, first.registry)
        with self.assertRaises(FrozenInstanceError):
            first.registry._rules = ()
        with self.assertRaises(FrozenInstanceError):
            first.visibility = Visibility.EVALUATOR
        (self.root / "context/rule-sets/r0.json").write_bytes(b"{}")
        with self.assertRaises(Exception):
            load_rule_set_source(context, ref.source_id, ProjectionScope.AGENT)

    def test_source_kind_and_visibility_are_checked(self) -> None:
        hidden = RuleSetRevision(registry(), Visibility.EVALUATOR)
        ref = self.commit(hidden, "context/rule-sets/hidden.json", Visibility.AGENT)
        with self.assertRaises(RuleSetError):
            load_rule_set_source(self.source_registry(ref), ref.source_id,
                                 ProjectionScope.EVALUATOR)
        wrong_kind = replace(self.commit(RuleSetRevision(registry(), Visibility.AGENT),
                                         "context/rule-sets/k.json"), kind="PROCESS_GRAPH")
        with self.assertRaises(RuleSetError):
            load_rule_set_source(self.source_registry(wrong_kind), wrong_kind.source_id,
                                 ProjectionScope.AGENT)

    def test_child_revision_extends_parent_without_editing(self) -> None:
        parent = RuleSetRevision(registry(), Visibility.AGENT)
        parent_ref = self.commit(parent, "context/rule-sets/r0.json")
        extraction = ExtractionMetadata(SOURCE, "limit", ("temperature",), "normal", "degC",
                                        "upper_limit", "fixture")
        candidate = literature_candidate(rule_id="compiled-limit", version="1",
                                         title="Compiled limit", scope="test/cooling",
                                         extraction=extraction, created_by="compiler",
                                         created_at=STAMP)
        child = parent.derive(parent_ref, (candidate,))
        self.assertEqual(parent_ref, child.parent)
        check_extends(parent, child)
        loaded = load_rule_set(child.serialize(), child.checksum)
        self.assertEqual(parent_ref, loaded.parent)
        self.assertEqual((Origin.LITERATURE, Validation.NONE, Authority.REFERENCE),
                         (loaded.registry.get_rule(candidate.ref).origin,
                          loaded.registry.get_rule(candidate.ref).validation,
                          loaded.registry.get_rule(candidate.ref).authority))
        self.assertEqual(parent.registry.conflicts(), loaded.registry.conflicts())
        # An existing (rule_id, version) is never re-published with different content.
        edited = replace(parent.registry.rules()[0], title="edited")
        with self.assertRaises((RuleSetError, ValueError)):
            parent.derive(parent_ref, (edited,))
        # The parent ref must be exactly the parent content.
        with self.assertRaises(RuleSetError):
            parent.derive(replace(parent_ref, content_checksum="0" * 64), (candidate,))
        forged = RuleSetRevision(RuleRegistry((candidate,)), Visibility.AGENT, parent_ref)
        with self.assertRaises(RuleSetError):
            check_extends(parent, forged)

    def test_visibility_taint(self) -> None:
        parent = RuleSetRevision(registry(), Visibility.EVALUATOR)
        parent_ref = self.commit(parent, "context/rule-sets/hidden.json", Visibility.EVALUATOR)
        with self.assertRaises(RuleSetError):
            parent.derive(parent_ref, (), visibility=Visibility.AGENT)
        self.assertEqual(Visibility.EVALUATOR, parent.derive(parent_ref, ()).visibility)
        self.assertEqual(Visibility.EVALUATOR,
                         most_restrictive(Visibility.AGENT, Visibility.EVALUATOR))
        self.assertEqual(Visibility.AGENT, most_restrictive(Visibility.AGENT))


if __name__ == "__main__":
    unittest.main()
