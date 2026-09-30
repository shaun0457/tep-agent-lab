"""Acceptance and negative tests for frozen C2 metadata contracts."""

from dataclasses import FrozenInstanceError, replace
import unittest

from tep_agent_lab.rules import (
    Authority, Behavior, ExtractionMetadata, Origin, Rule, RuleConflict,
    RuleRef, RuleRegistry, SourceVersion, Validation, literature_candidate,
    reviewed_blind_rca_policy,
)


SOURCE = SourceVersion("fixture://manual", "revision-1", "section 4, page 9")
VALIDATION = SourceVersion("fixture://campaign", "campaign-1", "held-out results")
STAMP = "2026-09-17T00:00:00Z"


def rule(**changes: object) -> Rule:
    base = Rule("cooling-guidance", "1", "Illustrative test relation",
                Origin.LITERATURE, Validation.NONE, Authority.REFERENCE,
                "test/cooling", "fixture relation; not simulator truth",
                ("temperature",), (SOURCE,), (), "active", "host-fixture",
                STAMP, STAMP, behavior=Behavior.ANNOTATE)
    return replace(base, **changes)


class RuleRegistryTests(unittest.TestCase):
    def test_1_literature_candidate_exact_provenance(self) -> None:
        extraction = ExtractionMetadata(
            SOURCE, "Illustrative cooling relation", ("temperature",),
            "Fixture conditions only", "K", "qualitative", "Not validated")
        candidate = literature_candidate(
            rule_id="literature", version="v1", title="Candidate",
            scope="test/cooling", extraction=extraction,
            created_by="extractor", created_at=STAMP)
        registry = RuleRegistry((candidate,))
        self.assertEqual((candidate.origin, candidate.validation, candidate.authority),
                         (Origin.LITERATURE, Validation.NONE, Authority.REFERENCE))
        self.assertEqual(registry.get_rule_provenance(candidate.ref), (SOURCE,))
        self.assertEqual(candidate.extraction, extraction)

    def test_2_agent_cannot_escalate_or_edit_validation(self) -> None:
        candidate = rule(origin=Origin.AGENT)
        registry = RuleRegistry((candidate,))
        for changes in ({"authority": Authority.HARD_GATE},
                        {"validation": Validation.REVIEWED},
                        {"origin": Origin.POLICY}):
            with self.assertRaises(PermissionError):
                registry.request_agent_metadata_update(candidate.ref, **changes)
        self.assertEqual(registry.get_rule(candidate.ref), candidate)

    def test_3_reviewed_scoped_policy_limit(self) -> None:
        policy = rule(rule_id="intervention-limit", origin=Origin.POLICY,
                      validation=Validation.REVIEWED, authority=Authority.HARD_GATE,
                      reviewed_by="fixture-policy-review", behavior=Behavior.BLOCK,
                      predicate_or_relation="fixture maximum delta <= 2",
                      scope="test/isolated-intervention")
        registry = RuleRegistry((policy,))
        self.assertEqual(registry.gate_rules(policy.scope, (policy.ref,)), (policy,))
        self.assertEqual(registry.gate_rules("different-scope", (policy.ref,)), ())

    def test_4_validation_does_not_promote_authority(self) -> None:
        original = rule(origin=Origin.EXPERIMENT, authority=Authority.ADVISORY)
        validated = replace(original, version="2",
                            validation=Validation.SIMULATION_VALIDATED,
                            validation_refs=(VALIDATION,))
        registry = RuleRegistry((original, validated))
        self.assertEqual(validated.authority, Authority.ADVISORY)
        self.assertEqual(registry.get_rule_validation(validated.ref), (VALIDATION,))
        self.assertEqual(registry.gate_rules(validated.scope, (validated.ref,)), ())

    def test_5_preserve_conflicting_rules_and_metadata(self) -> None:
        first = rule()
        second = rule(rule_id="other-guidance", origin=Origin.EXPERIMENT,
                      authority=Authority.PLANNING,
                      predicate_or_relation="opposing illustrative relation",
                      operating_envelope="different fixture envelope")
        conflict = RuleConflict((first.ref, second.ref), first.scope,
                                "opposing trend", "second is narrower", "unresolved")
        registry = RuleRegistry((second, first), (conflict,))
        self.assertEqual(len(registry.query_rules(first.scope)), 2)
        self.assertEqual(registry.find_rule_conflicts(first.ref), (conflict,))
        resolved_rules = tuple(registry.get_rule(ref) for ref in conflict.rule_refs)
        self.assertEqual(resolved_rules, (first, second))

    def test_6_consumer_blocks_only_configured_authoritative_rules(self) -> None:
        simulator = rule(rule_id="external-capability", origin=Origin.SIMULATOR,
                         authority=Authority.HARD_GATE, behavior=Behavior.BLOCK,
                         source_refs=(SourceVersion("fixture://tep-sim/capability",
                                                    "pinned-commit", "bounds"),))
        literature = rule()
        registry = RuleRegistry((literature, simulator))
        configured = (literature.ref, simulator.ref)
        checked = []

        def fake_validate_request() -> bool:
            for selected in registry.gate_rules(simulator.scope, configured):
                checked.append(selected.ref)
                # Predicate implementation belongs to the real consumer/A1.
                return False
            return True

        self.assertFalse(fake_validate_request())
        self.assertEqual(checked, [simulator.ref])
        self.assertEqual(registry.gate_rules(simulator.scope, (literature.ref,)), ())

    def test_7_old_run_uses_pinned_versions_and_axes(self) -> None:
        historical = rule(authority=Authority.ADVISORY, behavior=Behavior.SCORE)
        newer = replace(historical, version="2", authority=Authority.REFERENCE,
                        behavior=Behavior.ANNOTATE, status="deprecated")
        registry = RuleRegistry((newer, historical))
        historical_run_refs = (historical.ref,)

        def rescore(refs: tuple[RuleRef, ...]) -> tuple[tuple, ...]:
            return tuple((r.rule_id, r.version, r.origin, r.validation, r.authority)
                         for r in (registry.get_rule(ref) for ref in refs))

        self.assertEqual(rescore(historical_run_refs),
                         ((historical.rule_id, "1", Origin.LITERATURE,
                           Validation.NONE, Authority.ADVISORY),))
        self.assertEqual(registry.get_rule(newer.ref), newer)

    def test_duplicate_version_and_missing_refs_fail(self) -> None:
        first = rule()
        with self.assertRaises(ValueError):
            RuleRegistry((first, replace(first, title="overwrite")))
        with self.assertRaises(KeyError):
            RuleRegistry((first,)).get_rule(RuleRef(first.rule_id, "missing"))
        conflict = RuleConflict((first.ref, RuleRef("missing", "1")), "test",
                                "different", "none", "unresolved")
        with self.assertRaises(KeyError):
            RuleRegistry((first,), (conflict,))

    def test_hard_gate_eligibility_review_and_behavior(self) -> None:
        for origin in (Origin.AGENT, Origin.EXPERIMENT, Origin.LITERATURE):
            with self.assertRaises(ValueError):
                rule(origin=origin, validation=Validation.ROBUST_VALIDATED,
                     authority=Authority.HARD_GATE)
        with self.assertRaises(ValueError):
            rule(origin=Origin.POLICY, authority=Authority.HARD_GATE)
        with self.assertRaises(ValueError):
            rule(origin=Origin.FORMAL_DERIVATION, validation=Validation.REVIEWED,
                 reviewed_by="reviewer", authority=Authority.HARD_GATE)
        formal = rule(origin=Origin.FORMAL_DERIVATION,
                      validation=Validation.REVIEWED, reviewed_by="reviewer",
                      validation_refs=(VALIDATION,), authority=Authority.HARD_GATE)
        self.assertEqual(formal.authority, Authority.HARD_GATE)
        for authority in (Authority.REFERENCE, Authority.ADVISORY,
                          Authority.PLANNING, Authority.OPERATIONAL_PROPOSAL):
            with self.assertRaises(ValueError):
                rule(authority=authority, behavior=Behavior.BLOCK)
        with self.assertRaises(ValueError):
            rule(behavior=Behavior.SCORE)

    def test_data_are_immutable_and_typed(self) -> None:
        first = rule()
        with self.assertRaises(FrozenInstanceError):
            first.authority = Authority.HARD_GATE
        with self.assertRaises(TypeError):
            rule(source_refs=[SOURCE])
        with self.assertRaises(TypeError):
            rule(origin="LITERATURE")
        with self.assertRaises(ValueError):
            rule(source_refs=())
        with self.assertRaises(ValueError):
            rule(scope="")
        with self.assertRaises(ValueError):
            SourceVersion("source", "", "page")

    def test_deterministic_selection_and_unknown_pin(self) -> None:
        first = rule(rule_id="a", origin=Origin.SIMULATOR,
                     authority=Authority.HARD_GATE)
        second = replace(first, rule_id="b")
        expected = (first, second)
        for entries in ((first, second), (second, first)):
            registry = RuleRegistry(entries)
            self.assertEqual(registry.gate_rules(first.scope,
                             (second.ref, first.ref, first.ref)), expected)
            with self.assertRaises(KeyError):
                registry.gate_rules(first.scope, (RuleRef("missing", "1"),))

    def test_reviewed_real_policy_is_pinned_to_accepted_adr(self) -> None:
        policy = reviewed_blind_rca_policy()
        registry = RuleRegistry((policy,))
        self.assertEqual(policy.origin, Origin.POLICY)
        self.assertEqual(policy.validation, Validation.REVIEWED)
        self.assertEqual(registry.gate_rules("blind-rca", (policy.ref,)), (policy,))
        self.assertEqual(len(policy.source_refs[0].version), 40)
        with self.assertRaises(FrozenInstanceError):
            registry._rules = ()


if __name__ == "__main__":
    unittest.main()
