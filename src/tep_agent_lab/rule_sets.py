"""Rule-set source revision (knowledge-rule-registry-v0.md §Rule-set source revision).

A ``RuleSetRevision`` is one complete, immutable ``RuleRegistry`` serialized
deterministically as an ``ENGINEERING_RULE_SET`` source artifact: every Rule field and
every RuleConflict, so that loading round-trips ``find_rule_conflicts``. Its identity
is the repository revision plus content checksum (a ``ContextSourceRef``); inside the
commit that introduces it, it is referenced by path plus checksum. A revision may name
a parent for lineage only. Serialization changes no Rule axis and grants no authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
import json
from typing import Any

from industrial_agent_runtime import Visibility

from .canonical_context import (
    CANONICAL_JSON_SHA256, CanonicalContextRegistry, ContextSourceRef, ProjectionScope,
    content_checksum, most_restrictive,
)
from .persistence import canonical_json
from .rules import (
    Authority, Behavior, ExtractionMetadata, Origin, Rule, RuleConflict, RuleRef,
    RuleRegistry, SourceVersion, Validation,
)

RULE_SET_KIND = "ENGINEERING_RULE_SET"
RULE_SET_SCHEMA_VERSION = "tep-agent-lab.engineering-rule-set/v0"
_DOCUMENT_KEYS = frozenset({"schema_version", "kind", "visibility", "parent", "rules",
                            "conflicts"})
_ENUMS = {"origin": Origin, "validation": Validation, "authority": Authority}
_TUPLE_FIELDS = ("inputs", "source_refs", "validation_refs")
_OPTIONAL_TEXT = ("expected_effect_or_constraint", "operating_envelope", "units",
                  "support_summary", "reviewed_by")


class RuleSetError(ValueError):
    """A rule-set artifact failed schema, checksum, lineage or visibility validation."""


def _exact_keys(record: Any, expected: Iterable[str], what: str) -> Mapping[str, Any]:
    if not isinstance(record, Mapping) or set(record) != set(expected):
        raise RuleSetError(f"{what} must have exactly the v0 fields")
    return record


def _source_version_record(source: SourceVersion) -> dict[str, str]:
    return {"source_ref": source.source_ref, "version": source.version,
            "source_location": source.source_location}


def _source_version(record: Any) -> SourceVersion:
    record = _exact_keys(record, ("source_ref", "version", "source_location"),
                         "SourceVersion")
    return SourceVersion(record["source_ref"], record["version"], record["source_location"])


def _extraction_record(extraction: ExtractionMetadata) -> dict[str, Any]:
    return {"source": _source_version_record(extraction.source), "claim": extraction.claim,
            "variables_entities": list(extraction.variables_entities),
            "conditions_assumptions": extraction.conditions_assumptions,
            "units": extraction.units, "relationship_type": extraction.relationship_type,
            "uncertainties": extraction.uncertainties}


def _extraction(record: Any) -> ExtractionMetadata:
    record = _exact_keys(record, (item.name for item in fields(ExtractionMetadata)),
                         "ExtractionMetadata")
    entities = record["variables_entities"]
    if not isinstance(entities, list):
        raise RuleSetError("variables_entities must be a list")
    return ExtractionMetadata(_source_version(record["source"]), record["claim"],
                              tuple(entities), record["conditions_assumptions"],
                              record["units"], record["relationship_type"],
                              record["uncertainties"])


def rule_record(rule: Rule) -> dict[str, Any]:
    """Every Rule field, JSON-compatible; enums by value, absent optionals as null."""
    if not isinstance(rule, Rule):
        raise TypeError("Expected Rule")
    record: dict[str, Any] = {}
    for item in fields(Rule):
        value = getattr(rule, item.name)
        if item.name in _ENUMS or item.name == "behavior":
            value = None if value is None else value.value
        elif item.name in ("source_refs", "validation_refs"):
            value = [_source_version_record(source) for source in value]
        elif item.name == "inputs":
            value = list(value)
        elif item.name == "extraction":
            value = None if value is None else _extraction_record(value)
        record[item.name] = value
    return record


def rule_from_record(record: Any) -> Rule:
    record = _exact_keys(record, (item.name for item in fields(Rule)), "Rule")
    values = dict(record)
    for name, kind in _ENUMS.items():
        values[name] = kind(values[name])
    values["behavior"] = None if values["behavior"] is None else Behavior(values["behavior"])
    for name in _TUPLE_FIELDS:
        if not isinstance(values[name], list):
            raise RuleSetError(f"{name} must be a list")
    values["inputs"] = tuple(values["inputs"])
    values["source_refs"] = tuple(_source_version(item) for item in values["source_refs"])
    values["validation_refs"] = tuple(_source_version(item)
                                      for item in values["validation_refs"])
    for name in _OPTIONAL_TEXT:
        if values[name] is not None and not isinstance(values[name], str):
            raise RuleSetError(f"{name} must be text or null")
    if values["extraction"] is not None:
        values["extraction"] = _extraction(values["extraction"])
    return Rule(**values)


def _rule_ref_record(ref: RuleRef) -> dict[str, str]:
    return {"rule_id": ref.rule_id, "version": ref.version}


def conflict_record(conflict: RuleConflict) -> dict[str, Any]:
    if not isinstance(conflict, RuleConflict):
        raise TypeError("Expected RuleConflict")
    return {"rule_refs": [_rule_ref_record(ref) for ref in conflict.rule_refs],
            "overlapping_scope": conflict.overlapping_scope,
            "differing_relation_or_constraint": conflict.differing_relation_or_constraint,
            "operating_envelope_differences": conflict.operating_envelope_differences,
            "resolution_status": conflict.resolution_status}


def conflict_from_record(record: Any) -> RuleConflict:
    record = _exact_keys(record, (item.name for item in fields(RuleConflict)),
                         "RuleConflict")
    refs = record["rule_refs"]
    if not isinstance(refs, list):
        raise RuleSetError("rule_refs must be a list")
    return RuleConflict(
        tuple(RuleRef(**_exact_keys(ref, ("rule_id", "version"), "RuleRef")) for ref in refs),
        record["overlapping_scope"], record["differing_relation_or_constraint"],
        record["operating_envelope_differences"], record["resolution_status"])


def _rule_set_ref(ref: Any) -> ContextSourceRef:
    if type(ref) is not ContextSourceRef or ref.kind != RULE_SET_KIND:
        raise RuleSetError("a rule-set parent is an ENGINEERING_RULE_SET ContextSourceRef")
    if ref.checksum_method != CANONICAL_JSON_SHA256:
        raise RuleSetError("rule-set artifacts use the canonical JSON checksum")
    return ref


@dataclass(frozen=True)
class RuleSetRevision:
    """One complete rule set; a parent ref records lineage only, never inheritance."""

    registry: RuleRegistry
    visibility: Visibility
    parent: ContextSourceRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.registry, RuleRegistry):
            raise TypeError("Expected RuleRegistry")
        object.__setattr__(self, "visibility", Visibility(self.visibility))
        if self.parent is not None:
            _rule_set_ref(self.parent)

    def document(self) -> dict[str, Any]:
        return {"schema_version": RULE_SET_SCHEMA_VERSION, "kind": RULE_SET_KIND,
                "visibility": self.visibility.value,
                "parent": None if self.parent is None else self.parent.record(),
                "rules": [rule_record(rule) for rule in self.registry.rules()],
                "conflicts": [conflict_record(item) for item in self.registry.conflicts()]}

    def serialize(self) -> bytes:
        return canonical_json(self.document())

    @property
    def checksum(self) -> str:
        return content_checksum(self.serialize(), CANONICAL_JSON_SHA256)

    def derive(self, parent_ref: ContextSourceRef, added: Iterable[Rule], *,
               visibility: Visibility | None = None) -> "RuleSetRevision":
        """A complete child set: every rule/conflict of this set unchanged plus ``added``.

        ``parent_ref`` must address exactly this content. The child is never less
        restrictive than this set, since its rules derive from this set's inputs.
        """
        _rule_set_ref(parent_ref)
        if parent_ref.content_checksum != self.checksum:
            raise RuleSetError("parent ref does not address this rule-set content")
        floor = most_restrictive(self.visibility, parent_ref.visibility)
        target = floor if visibility is None else Visibility(visibility)
        if most_restrictive(floor, target) != target:
            raise RuleSetError("a derived rule set cannot widen its parent's visibility")
        try:
            registry = RuleRegistry((*self.registry.rules(), *added),
                                    self.registry.conflicts())
        except (TypeError, ValueError, KeyError) as exc:
            raise RuleSetError(f"derived rule set is invalid: {exc}") from exc
        child = RuleSetRevision(registry, target, parent_ref)
        check_extends(self, child)
        return child


def check_extends(parent: RuleSetRevision, child: RuleSetRevision) -> None:
    """The child names this parent and keeps every parent rule and conflict unchanged."""
    if child.parent is None or child.parent.content_checksum != parent.checksum:
        raise RuleSetError("child rule set does not name this parent")
    for rule in parent.registry.rules():
        try:
            kept = child.registry.get_rule(rule.ref)
        except KeyError:
            kept = None
        if kept != rule:
            raise RuleSetError(f"child rule set drops or edits {rule.rule_id}@{rule.version}")
    missing = [item for item in parent.registry.conflicts()
               if item not in child.registry.conflicts()]
    if missing:
        raise RuleSetError("child rule set drops a parent conflict record")


def load_rule_set(data: bytes, expected_checksum: str) -> RuleSetRevision:
    """Verify the checksum and schema, then return the immutable rule-set revision."""
    try:
        actual = content_checksum(data, CANONICAL_JSON_SHA256)
        document = json.loads(data)
    except (TypeError, ValueError) as exc:
        raise RuleSetError("rule-set artifact is not canonical JSON") from exc
    if actual != expected_checksum:
        raise RuleSetError("rule-set artifact checksum mismatch")
    document = _exact_keys(document, _DOCUMENT_KEYS, "rule-set artifact")
    if (document["schema_version"] != RULE_SET_SCHEMA_VERSION
            or document["kind"] != RULE_SET_KIND):
        raise RuleSetError("unsupported rule-set schema or kind")
    if not isinstance(document["rules"], list) or not isinstance(document["conflicts"], list):
        raise RuleSetError("rules and conflicts must be lists")
    try:
        parent = (None if document["parent"] is None
                  else ContextSourceRef(**dict(document["parent"])))
        registry = RuleRegistry(tuple(rule_from_record(item) for item in document["rules"]),
                                tuple(conflict_from_record(item)
                                      for item in document["conflicts"]))
        revision = RuleSetRevision(registry, Visibility(document["visibility"]), parent)
    except RuleSetError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise RuleSetError(f"invalid rule-set artifact: {exc}") from exc
    if revision.serialize() != canonical_json(document):
        raise RuleSetError("rule-set artifact is not in deterministic canonical order")
    return revision


def load_rule_set_source(registry: CanonicalContextRegistry, source_id: str,
                         scope: ProjectionScope) -> RuleSetRevision:
    """Load through a frozen source inventory, which re-attests revision and checksum."""
    resolved = registry.resolve(source_id, scope)
    ref = _rule_set_ref(resolved.ref)
    revision = load_rule_set(resolved.content, ref.content_checksum)
    if most_restrictive(ref.visibility, revision.visibility) != ref.visibility:
        raise RuleSetError("source ref visibility is wider than the rule set it addresses")
    return revision
