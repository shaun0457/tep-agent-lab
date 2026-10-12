"""Generic, source-attested deterministic extraction into the compiler's candidates.

Rules are trusted, versioned declarative data, not executable code or acceptance
policies. Extraction creates no reviews, canonical sources or publication artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
import hashlib
import re
from typing import Any

from industrial_agent_runtime.serialization import freeze_json

from .context_compiler import (
    Candidate, CandidateBinding, CandidateKnowledge, CompileSession, CompilerError,
    DerivedInput, DerivationProvenance, ExtractionKind, SourceFragmentRef, TargetKind,
    PlantTargetRef, TypedValue,
)
from .markdown_fragments import TABLES_STEPS_POLICY, fragment_markdown, table_row_cells
from .persistence import content_checksum, strict_json_loads
from .rules import ExtractionMetadata, literature_candidate

SCHEMA = "deterministic-extraction-rules/v1"
GROUNDING = "exact-selected-plant-ids@1"
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_NUMBER = r"[+-]?[0-9]+(?:\.[0-9]+)?"


class ExtractionError(CompilerError):
    """Invalid rule data or missing exact phrase/anchor; no partial result is returned."""


def _keys(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ExtractionError("rule object must contain exactly its schema keys")


def _text(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ExtractionError("expected nonempty rule text")


def _strings(value: Any, *, nonempty: bool = True) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        raise ExtractionError("expected rule string array")
    for item in value:
        _text(item)
    if len(set(value)) != len(value):
        raise ExtractionError("duplicate rule string")


def _validate(data: Any) -> None:
    _keys(data, {"schema_version", "rule_set_id", "version", "authored_at", "units", "rules"})
    if data["schema_version"] != SCHEMA:
        raise ExtractionError("unsupported rule schema")
    for field, pattern in (("rule_set_id", _ID), ("version", _VERSION)):
        _text(data[field])
        if not pattern.fullmatch(data[field]):
            raise ExtractionError(f"invalid {field}")
    _text(data["authored_at"])
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data["authored_at"]):
        raise ExtractionError("authored_at requires an exact calendar date")
    date.fromisoformat(data["authored_at"])
    _strings(data["units"])
    if not isinstance(data["rules"], list) or not data["rules"]:
        raise ExtractionError("rules requires a nonempty array")
    ids: set[str] = set()
    for rule in data["rules"]:
        if not isinstance(rule, dict) or rule.get("form") not in ("table", "phrase"):
            raise ExtractionError("only table and phrase rules are supported")
        form = rule["form"]
        _keys(rule, {"id", "form", "source_id", "locator", "item",
                     "matches" if form == "table" else "phrase"})
        for field in ("id", "source_id", "locator"):
            _text(rule[field])
        if not _ID.fullmatch(rule["id"]) or rule["id"] in ids:
            raise ExtractionError("invalid or duplicate rule id")
        ids.add(rule["id"])
        item = rule["item"]
        if not isinstance(item, dict):
            raise ExtractionError("item requires an object")
        kind = ExtractionKind(item.get("kind"))
        base = {"kind", "scope"}
        if kind in (ExtractionKind.SIGNAL_MENTION, ExtractionKind.ENTITY_MENTION):
            _keys(item, base | {"target_kind", "targets"} |
                  ({"mention_column"} if form == "table" else set()))
            target_kind = TargetKind(item["target_kind"])
            if ((kind == ExtractionKind.SIGNAL_MENTION and target_kind != TargetKind.SIGNAL)
                    or (kind == ExtractionKind.ENTITY_MENTION
                        and target_kind == TargetKind.SIGNAL)):
                raise ExtractionError("binding kind/target kind mismatch")
            _strings(item["targets"], nonempty=False)
            if form == "table":
                _text(item["mention_column"])
        elif kind == ExtractionKind.OPERATING_LIMIT:
            _keys(item, base | {"variable", "constraint", "conditions", "uncertainties"} |
                  ({"number_column", "unit_column"} if form == "table" else {"value_text"}))
            for field in set(item) - base:
                _text(item[field])
            if form == "phrase":
                if item["value_text"] not in rule["phrase"]:
                    raise ExtractionError("limit value must occur in the exact phrase")
                _quantity(item["value_text"], data["units"])
        elif kind == ExtractionKind.CONTROL_RELATION and form == "phrase":
            _keys(item, base | {"variables", "conditions", "uncertainties", "units"})
            _strings(item["variables"])
            for field in ("conditions", "uncertainties", "units"):
                _text(item[field])
        else:
            raise ExtractionError("unsupported typed item for this rule form")
        if not isinstance(item["scope"], list) or not item["scope"]:
            raise ExtractionError("an explicit nonempty scope is required")
        for target in item["scope"]:
            _keys(target, {"kind", "id"})
            TargetKind(target["kind"])
            _text(target["id"])
        if len({(t["kind"], t["id"]) for t in item["scope"]}) != len(item["scope"]):
            raise ExtractionError("duplicate scope target")
        if form == "phrase":
            _text(rule["phrase"])
        else:
            matches = rule["matches"]
            if not isinstance(matches, dict) or not matches:
                raise ExtractionError("table rules need header-keyed fullmatch patterns")
            for header, pattern in matches.items():
                _text(header)
                _text(pattern)
                re.compile(pattern)
            if kind in (ExtractionKind.ENTITY_MENTION, ExtractionKind.SIGNAL_MENTION):
                required = {item["mention_column"]}
            else:
                required = {item["number_column"], item["unit_column"]}
                groups = re.compile(matches.get(item["number_column"], "")).groupindex
                if set(groups) != {"value"}:
                    raise ExtractionError("limit number pattern needs exactly named group value")
            if not required <= matches.keys():
                raise ExtractionError("every consumed cell requires an explicit match pattern")


@dataclass(frozen=True, init=False)
class ExtractionRuleSet:
    raw: bytes
    data: Mapping[str, Any]

    def __init__(self, raw: bytes) -> None:
        try:
            if not isinstance(raw, bytes):
                raise ExtractionError("rules require exact bytes")
            data = strict_json_loads(raw)
            _validate(data)
        except (ValueError, TypeError, KeyError, re.error) as exc:
            raise ExtractionError(f"invalid extraction rules: {exc}") from exc
        object.__setattr__(self, "raw", raw)
        object.__setattr__(self, "data", freeze_json(data))

    @property
    def method(self) -> str:
        return f"{self.data['rule_set_id']}@{self.data['version']}"


def load_rules(raw: bytes) -> ExtractionRuleSet:
    return ExtractionRuleSet(raw)


def _quantity(text: str, units: Any) -> TypedValue:
    match = re.fullmatch(f"({_NUMBER}) (.+)", text)
    if match is None or match[2] not in units:
        raise ExtractionError("unsupported number or unit")
    value = float(match[1]) if "." in match[1] else int(match[1])
    return TypedValue("limit", value, match[2])


@dataclass(frozen=True)
class UnsupportedExtraction:
    fragment: SourceFragmentRef
    cell: str | None
    text: str
    reason: str

    def record(self) -> dict[str, Any]:
        return {"fragment": self.fragment.record(), "cell": self.cell,
                "text": self.text, "reason": self.reason}


@dataclass(frozen=True)
class ExtractionResult:
    candidates: tuple[Candidate, ...]
    unsupported: tuple[UnsupportedExtraction, ...]

    def record(self) -> dict[str, Any]:
        return {"candidates": [c.record() for c in self.candidates],
                "unsupported": [u.record() for u in self.unsupported]}


def _provenance(session: CompileSession, rules: ExtractionRuleSet,
                fragments: tuple[SourceFragmentRef, ...]) -> DerivationProvenance:
    sources = {f.source_ref.source_id: f.source_ref for f in fragments}
    sources[session.plant.source_ref.source_id] = session.plant.source_ref
    return DerivationProvenance(
        extraction_method=rules.method, grounding_method=GROUNDING,
        influencing_sources=tuple(sources[k] for k in sorted(sources)),
        derived_inputs=(DerivedInput(
            ref=rules.method, role="deterministic-extraction-rule-set",
            visibility=session.visibility, content_checksum=hashlib.sha256(rules.raw).hexdigest()),))


def _candidate(session: CompileSession, rules: ExtractionRuleSet, rule: Mapping[str, Any],
               fragment: SourceFragmentRef, mention: str,
               quantity: TypedValue | None) -> Candidate:
    item = rule["item"]
    kind = ExtractionKind(item["kind"])
    scope = tuple(session.plant.target(TargetKind(t["kind"]), t["id"]) for t in item["scope"])
    candidate_id = "dc-" + content_checksum({"rule_id": rule["id"],
                                            "fragment_id": fragment.fragment_id})
    common = dict(candidate_id=candidate_id, session_id=session.session_id,
                  extraction_kind=kind, scope_refs=scope,
                  provenance=_provenance(session, rules, (fragment,)))
    if kind in (ExtractionKind.SIGNAL_MENTION, ExtractionKind.ENTITY_MENTION):
        target_kind = TargetKind(item["target_kind"])
        # Missing targets remain unresolved; never create a plant identity or alias.
        targets = tuple(session.plant.target(target_kind, target) for target in item["targets"]
                        if session.plant.contains(PlantTargetRef(
                            session.plant.source_ref, target_kind, target)))
        return CandidateBinding(
            **common, mention_fragment=fragment, mention_text=mention, target_kind=target_kind,
            proposed_target=targets[0] if len(targets) == 1 else None,
            alternative_targets=targets if len(targets) > 1 else ())
    if kind == ExtractionKind.OPERATING_LIMIT:
        assert quantity is not None
        number = str(quantity.value).removesuffix(".0")
        claim = f"{item['variable']} {item['constraint']} <= {number} {quantity.unit}"
        variables = (item["variable"],)
        units = quantity.unit
        relationship = item["constraint"]
    else:
        claim, variables, units = mention, tuple(item["variables"]), item["units"]
        relationship = "CONTROL_RELATION"
    known = session.plant.entities | session.plant.signals | session.plant.relationships
    if not set(variables) <= known:
        raise ExtractionError("rule claim variables must exist in the selected plant")
    extraction = ExtractionMetadata(
        source=fragment.citation(), claim=claim, variables_entities=variables,
        conditions_assumptions=item["conditions"], units=units,
        relationship_type=relationship, uncertainties=item["uncertainties"])
    inert = literature_candidate(
        rule_id=candidate_id, version="1", title=kind.value,
        scope=",".join(t.target_id for t in scope), extraction=extraction,
        created_by="deterministic-extraction", created_at=rules.data["authored_at"])
    return CandidateKnowledge(**common, source_fragments=(fragment,), rule=inert)


def extract_candidates(session: CompileSession, rules: ExtractionRuleSet) -> ExtractionResult:
    """Read exact session sources only; a failed phrase aborts the whole batch.

    All rows, paragraphs and steps are accounted for. Sections and whole tables are
    containers, not extraction items. A matched cell is consumed in full; all other
    cells/rows and unselected prose remain explicitly unsupported.
    """
    if not isinstance(session, CompileSession) or not isinstance(rules, ExtractionRuleSet):
        raise ExtractionError("expected a frozen compile session and validated rules")
    fragments: list[SourceFragmentRef] = []
    contents: dict[str, bytes] = {}
    for ref in session.inventory:
        if ref.kind != "ENGINEERING_DOCUMENT":
            continue
        content = session.source_content(ref)
        if content is None:
            raise ExtractionError("engineering source no longer resolves exactly")
        contents[ref.source_id] = content
        fragments.extend(fragment_markdown(ref, content, policy=TABLES_STEPS_POLICY))
    candidates: list[Candidate] = []
    consumed: dict[str, set[str]] = {}
    selected: set[str] = set()
    groups: dict[str, tuple[Any, ...]] = {}
    for rule in rules.data["rules"]:
        anchors = [f for f in fragments if f.source_ref.source_id == rule["source_id"]
                   and f.location.semantic_locator == rule["locator"]]
        expected_kind = "TABLE" if rule["form"] == "table" else None
        if len(anchors) != 1 or (expected_kind and anchors[0].fragment_kind != expected_kind):
            raise ExtractionError(f"{rule['id']}: exact fragment locator missing or wrong kind")
        anchor = anchors[0]
        item = rule["item"]
        if rule["form"] == "phrase":
            if anchor.fragment_kind not in ("PARAGRAPH", "PROCEDURE_STEP"):
                raise ExtractionError("phrase rules require a paragraph or procedure step")
            raw = contents[anchor.source_ref.source_id]
            text = raw[anchor.location.start:anchor.location.end].decode("utf-8")
            if rule["phrase"] not in text:
                raise ExtractionError(f"{rule['id']}: exact phrase not found; extraction aborted")
            quantity = (_quantity(item["value_text"], rules.data["units"])
                        if item["kind"] == "OPERATING_LIMIT" else None)
            matches = [(anchor, rule["phrase"], quantity, set())]
        else:
            matches = []
            rows = [f for f in fragments if f.fragment_kind == "TABLE_ROW"
                    and f.source_ref == anchor.source_ref
                    and f.location.section_path == anchor.location.section_path
                    and f.location.table_index == anchor.location.table_index]
            for row in rows:
                cells = table_row_cells(row, contents[row.source_ref.source_id])
                checks = {header: re.fullmatch(pattern, cells.get(header, ""))
                          for header, pattern in rule["matches"].items()}
                if not all(checks.values()):
                    continue
                quantity = None
                if item["kind"] == "OPERATING_LIMIT":
                    try:
                        quantity = _quantity(
                            checks[item["number_column"]]["value"] + " " + cells[item["unit_column"]],
                            rules.data["units"])
                    except CompilerError:
                        continue  # matched syntax but invalid number/unit remains unsupported
                    mention = cells[item["number_column"]]
                else:
                    mention = cells[item["mention_column"]]
                matches.append((row, mention, quantity, set(checks)))
        for fragment, mention, quantity, headers in matches:
            candidate = _candidate(session, rules, rule, fragment, mention, quantity)
            candidates.append(candidate)
            selected.add(fragment.fragment_id)
            consumed.setdefault(fragment.fragment_id, set()).update(headers)
            if quantity is not None:
                groups[candidate.candidate_id] = (
                    item["variable"], item["constraint"], quantity.unit, item["conditions"],
                    tuple((t.kind, t.target_id) for t in candidate.scope_refs))
    # Stable candidate ids allow bidirectional links without a cyclic version hash.
    if len({c.candidate_id for c in candidates}) != len(candidates):
        raise ExtractionError("duplicate extracted candidate id")
    linked: list[Candidate] = []
    for candidate in candidates:
        partners = [c for c in candidates if c.candidate_id != candidate.candidate_id
                    and candidate.candidate_id in groups
                    and groups.get(c.candidate_id) == groups[candidate.candidate_id]
                    and c.rule.extraction.claim != candidate.rule.extraction.claim]
        if partners:
            conflict_fragments = tuple(f for c in partners for f in c.source_fragments)
            candidate = replace(candidate,
                                conflicting_candidate_ids=tuple(c.candidate_id for c in partners),
                                conflicting_fragments=conflict_fragments,
                                provenance=_provenance(session, rules,
                                                       candidate.source_fragments + conflict_fragments))
        linked.append(candidate)
    unsupported: list[UnsupportedExtraction] = []
    for fragment in fragments:
        raw = contents[fragment.source_ref.source_id]
        text = raw[fragment.location.start:fragment.location.end].decode("utf-8")
        if fragment.fragment_kind == "TABLE_ROW":
            if fragment.fragment_id not in selected:
                unsupported.append(UnsupportedExtraction(fragment, None, text, "unmatched-row"))
            for header, cell in table_row_cells(fragment, raw).items():
                if header not in consumed.get(fragment.fragment_id, set()):
                    unsupported.append(UnsupportedExtraction(fragment, header, cell, "unmatched-cell"))
        elif fragment.fragment_kind in ("PARAGRAPH", "PROCEDURE_STEP"):
            if fragment.fragment_id not in selected:
                unsupported.append(UnsupportedExtraction(fragment, None, text, "no-phrase-rule"))
    return ExtractionResult(tuple(linked), tuple(unsupported))


def status_flags(session: CompileSession, candidate: Candidate) -> tuple[str, ...]:
    """Derived display flags, never mutable candidate/review state or acceptance."""
    flags = ["unreviewed"]
    if isinstance(candidate, CandidateBinding):
        if candidate.alternative_targets:
            flags.append("ambiguous")
        if candidate.proposed_target is None:
            flags.append("needs-review")
            if not candidate.alternative_targets:
                flags.append("no-matching-signal" if candidate.target_kind == TargetKind.SIGNAL
                             else "no-matching-target")
    elif candidate.conflicting_candidate_ids:
        flags.extend(("conflict", "needs-review"))
    return tuple(flags)
