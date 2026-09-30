"""Versioned lab rule metadata; no simulator evaluation or promotion engine.

The host constructs a registry from reviewed configuration. Agents receive only
the read-only view. Scope matching is exact; predicates are descriptive data,
never executable Python. SourceVersion is a citation, not runtime InformationRef.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class Origin(StrEnum):
    SIMULATOR = "SIMULATOR"
    FORMAL_DERIVATION = "FORMAL_DERIVATION"
    POLICY = "POLICY"
    LITERATURE = "LITERATURE"
    EXPERIMENT = "EXPERIMENT"
    AGENT = "AGENT"


class Validation(StrEnum):
    NONE = "NONE"
    CORROBORATED = "CORROBORATED"
    SIMULATION_VALIDATED = "SIMULATION_VALIDATED"
    ROBUST_VALIDATED = "ROBUST_VALIDATED"
    REVIEWED = "REVIEWED"


class Authority(StrEnum):
    REFERENCE = "REFERENCE"
    ADVISORY = "ADVISORY"
    PLANNING = "PLANNING"
    OPERATIONAL_PROPOSAL = "OPERATIONAL_PROPOSAL"
    HARD_GATE = "HARD_GATE"


class Behavior(StrEnum):
    PRIOR = "PRIOR"
    ANNOTATE = "ANNOTATE"
    SCORE = "SCORE"
    WARN = "WARN"
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


def _text(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text")


@dataclass(frozen=True)
class RuleRef:
    rule_id: str
    version: str

    def __post_init__(self) -> None:
        _text(self.rule_id)
        _text(self.version)


@dataclass(frozen=True)
class SourceVersion:
    source_ref: str
    version: str
    source_location: str

    def __post_init__(self) -> None:
        for value in (self.source_ref, self.version, self.source_location):
            _text(value)


@dataclass(frozen=True)
class ExtractionMetadata:
    source: SourceVersion
    claim: str
    variables_entities: tuple[str, ...]
    conditions_assumptions: str
    units: str
    relationship_type: str
    uncertainties: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceVersion):
            raise TypeError("Extraction requires a versioned citation")
        if not isinstance(self.variables_entities, tuple):
            raise TypeError("variables_entities must be immutable")
        for value in (self.claim, self.conditions_assumptions, self.units,
                      self.relationship_type, self.uncertainties,
                      *self.variables_entities):
            _text(value)


@dataclass(frozen=True)
class Rule:
    rule_id: str
    version: str
    title: str
    origin: Origin
    validation: Validation
    authority: Authority
    scope: str
    predicate_or_relation: str
    inputs: tuple[str, ...]
    source_refs: tuple[SourceVersion, ...]
    validation_refs: tuple[SourceVersion, ...]
    status: str
    created_by: str
    created_at: str
    updated_at: str
    expected_effect_or_constraint: str | None = None
    operating_envelope: str | None = None
    units: str | None = None
    support_summary: str | None = None
    reviewed_by: str | None = None
    behavior: Behavior | None = None
    extraction: ExtractionMetadata | None = None

    def __post_init__(self) -> None:
        for name in ("rule_id", "version", "title", "scope",
                     "predicate_or_relation", "status", "created_by",
                     "created_at", "updated_at"):
            _text(getattr(self, name))
        for value, kind in ((self.origin, Origin), (self.validation, Validation),
                            (self.authority, Authority)):
            if not isinstance(value, kind):
                raise TypeError(f"Expected {kind.__name__}")
        for name, kind in (("inputs", str), ("source_refs", SourceVersion),
                           ("validation_refs", SourceVersion)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                not isinstance(value, kind) for value in values
            ):
                raise TypeError(f"{name} must be an immutable typed tuple")
        if not self.source_refs:
            raise ValueError("Rules require exact provenance")
        if self.behavior is not None and not isinstance(self.behavior, Behavior):
            raise TypeError("Expected Behavior")
        if self.behavior in (Behavior.ALLOW, Behavior.BLOCK):
            if self.authority != Authority.HARD_GATE:
                raise ValueError("Only HARD_GATE rules can allow/block")
        if self.authority == Authority.REFERENCE and self.behavior not in (
            None, Behavior.PRIOR, Behavior.ANNOTATE
        ):
            raise ValueError("REFERENCE can only provide priors/annotations")
        if self.authority == Authority.HARD_GATE:
            if self.origin not in (Origin.SIMULATOR, Origin.POLICY,
                                   Origin.FORMAL_DERIVATION):
                raise ValueError("Evidence maturity cannot grant HARD_GATE")
            if self.origin != Origin.SIMULATOR:
                if self.validation != Validation.REVIEWED or not self.reviewed_by:
                    raise ValueError("Policy/formal hard gates require review")
            if self.origin == Origin.FORMAL_DERIVATION and not self.validation_refs:
                raise ValueError("Formal hard gates require test/validation refs")
        if self.extraction is not None:
            if not isinstance(self.extraction, ExtractionMetadata):
                raise TypeError("Expected ExtractionMetadata")
            if self.extraction.source not in self.source_refs:
                raise ValueError("Extraction source must appear in provenance")

    @property
    def ref(self) -> RuleRef:
        return RuleRef(self.rule_id, self.version)


@dataclass(frozen=True)
class RuleConflict:
    rule_refs: tuple[RuleRef, ...]
    overlapping_scope: str
    differing_relation_or_constraint: str
    operating_envelope_differences: str
    resolution_status: str

    def __post_init__(self) -> None:
        if not isinstance(self.rule_refs, tuple) or any(
            not isinstance(ref, RuleRef) for ref in self.rule_refs
        ) or len(set(self.rule_refs)) < 2:
            raise ValueError("Conflict requires at least two distinct pinned rules")
        for value in (self.overlapping_scope, self.differing_relation_or_constraint,
                      self.operating_envelope_differences, self.resolution_status):
            _text(value)


@dataclass(frozen=True, init=False)
class RuleRegistry:
    """Immutable host snapshot: revisions produce a new registry, not edits.

    No latest-version inference or implicit status policy is used. Consumers pin
    configured refs explicitly, including when replaying historical runs.
    """

    _rules: tuple[Rule, ...]
    _conflicts: tuple[RuleConflict, ...]

    def __init__(self, rules: Iterable[Rule],
                 conflicts: Iterable[RuleConflict] = ()) -> None:
        entries = tuple(rules)
        if any(not isinstance(rule, Rule) for rule in entries):
            raise TypeError("Expected Rule entries")
        refs = [rule.ref for rule in entries]
        if len(set(refs)) != len(refs):
            raise ValueError("A rule version cannot be overwritten")
        object.__setattr__(self, "_rules", tuple(sorted(
            entries, key=lambda r: (r.rule_id, r.version))))
        object.__setattr__(self, "_conflicts", tuple(conflicts))
        for conflict in self._conflicts:
            if not isinstance(conflict, RuleConflict):
                raise TypeError("Expected RuleConflict")
            for ref in conflict.rule_refs:
                self.get_rule(ref)

    def get_rule(self, ref: RuleRef) -> Rule:
        for rule in self._rules:
            if rule.ref == ref:
                return rule
        raise KeyError(ref)

    def query_rules(self, scope: str, *, origin: Origin | None = None,
                    validation: Validation | None = None,
                    authority: Authority | None = None) -> tuple[Rule, ...]:
        return tuple(rule for rule in self._rules if rule.scope == scope
                     and (origin is None or rule.origin == origin)
                     and (validation is None or rule.validation == validation)
                     and (authority is None or rule.authority == authority))

    def get_rule_provenance(self, ref: RuleRef) -> tuple[SourceVersion, ...]:
        return self.get_rule(ref).source_refs

    def get_rule_validation(self, ref: RuleRef) -> tuple[SourceVersion, ...]:
        return self.get_rule(ref).validation_refs

    def find_rule_conflicts(self, ref: RuleRef) -> tuple[RuleConflict, ...]:
        self.get_rule(ref)
        return tuple(c for c in self._conflicts if ref in c.rule_refs)

    def gate_rules(self, scope: str, configured_refs: Iterable[RuleRef]
                   ) -> tuple[Rule, ...]:
        """Select authority; actual typed request validation belongs to consumer."""
        selected = (self.get_rule(ref) for ref in set(configured_refs))
        return tuple(sorted((r for r in selected if r.scope == scope
                             and r.authority == Authority.HARD_GATE),
                            key=lambda r: (r.rule_id, r.version)))

    def request_agent_metadata_update(self, ref: RuleRef, **changes: object) -> None:
        """Explicit rejection surface; not a registered tool or promotion path."""
        self.get_rule(ref)
        raise PermissionError("Agents cannot edit rule metadata or authority")


def literature_candidate(*, rule_id: str, version: str, title: str, scope: str,
                         extraction: ExtractionMetadata, created_by: str,
                         created_at: str) -> Rule:
    """LLM extraction yields inert candidate data, never installs a rule."""
    return Rule(rule_id, version, title, Origin.LITERATURE, Validation.NONE,
                Authority.REFERENCE, scope, extraction.claim,
                extraction.variables_entities, (extraction.source,), (),
                "candidate", created_by, created_at, created_at,
                units=extraction.units, behavior=Behavior.ANNOTATE,
                extraction=extraction)


def reviewed_blind_rca_policy() -> Rule:
    """Represent the accepted ADR policy; this function grants no tool access."""
    source = SourceVersion(
        "tep-agent-lab/docs/decisions/ADR-001-simulate-before-reference-mutation.md",
        "a0a761a92ddae79c8af767483cc260792833752b", "Initial implementation policy")
    return Rule(
        rule_id="blind-rca-reference-mutation-disabled", version="1",
        title="Reference MUTATE is unavailable in blind RCA",
        origin=Origin.POLICY, validation=Validation.REVIEWED,
        authority=Authority.HARD_GATE, scope="blind-rca",
        predicate_or_relation="Reject reference-world MUTATE requests",
        inputs=("side_effect_class",), source_refs=(source,),
        validation_refs=(source,), status="active", created_by="lab-policy",
        created_at="2026-09-15", updated_at="2026-09-15",
        reviewed_by="ADR-001 accepted decision", behavior=Behavior.BLOCK)
