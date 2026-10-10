"""Context Compiler v0 contracts (docs/specs/context-compiler-v0.md, milestone P1.2A).

Candidate Context and canonical context are different trust domains::

    exact ContextSourceRef (CompileSession: its own frozen CanonicalContextRegistry)
      -> SourceFragmentRef (exact location + content checksum)
      -> CandidateBinding / CandidateKnowledge (immutable, versioned candidates)
      -> CompilerWorkspace: append-only, non-canonical; deterministic validation
      -> ReviewRecord log (HUMAN or a registered DETERMINISTIC_POLICY principal only)
      -> CompilerWorkspace.publish: EngineeringContextRevision (+ rule-set revision R1)
         with its review-log prefix, committed by a per-lineage head compare-and-set

Review and publication are Application API operations (ADR-002): nothing here is an
Agent tool, a ToolSpec or part of a ContextProjection. Confidence is review-routing
metadata only and never establishes acceptance. Until P1.3, plant identities are the
exact AGENT-visible ``PROCESS_GRAPH`` source selected for the session (ADR-004
Decision 4). Rule-routed items reuse ``Rule``/``RuleRegistry`` through
``literature_candidate`` carriers and keep LITERATURE/NONE/REFERENCE. The module is
generic: it never imports tep_sim; callers supply the ProcessGraph loader.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Any, Protocol

from industrial_agent_runtime import Visibility
from industrial_agent_runtime.serialization import freeze_json

from .canonical_context import (
    CANONICAL_JSON_SHA256, CanonicalContextRegistry, ContextSourceError, ContextSourceRef,
    ProjectionScope, SourceMaterializer, UnknownContextSource, checked_relative_path,
    content_checksum, most_restrictive,
)
from .persistence import canonical_json
from .persistence import content_checksum as json_digest
from .rule_sets import (
    RULE_SET_KIND, RuleSetError, RuleSetRevision, load_rule_set, load_rule_set_source,
    rule_record,
)
from .rules import Authority, Origin, Rule, RuleRef, SourceVersion, Validation

COMPILER_CONTRACT_VERSION = "tep-agent-lab.context-compiler/v0"
ROUTING_POLICY = "tep-agent-lab.context-compiler.routing/v0"
VALIDATION_POLICY = "tep-agent-lab.context-compiler.validation/v0"
PROCESS_GRAPH_KIND = "PROCESS_GRAPH"
ENGINEERING_CONTEXT_REVISION_KIND = "ENGINEERING_CONTEXT_REVISION"
REVISION_SCHEMA_VERSION = "tep-agent-lab.engineering-context-revision/v0"
REVIEW_LOG_SCHEMA_VERSION = "tep-agent-lab.compiler-review-log/v0"
HEAD_SCHEMA_VERSION = "tep-agent-lab.engineering-context-head/v0"
ENGINEERING_CONTEXT_ROOT = "context/engineering-context"
RULE_SET_ROOT = "context/rule-sets"
KNOWLEDGE_VALIDATION_STATUS = "VERIFIED"  # scope/extraction fidelity only; no truth/authority

_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_VERSIONED = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*@[A-Za-z0-9][A-Za-z0-9._+-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KIND = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class CompilerError(ValueError):
    """A compiler contract was violated at the trusted application boundary."""


class WorkspaceAppendError(CompilerError):
    """An append-only or immutability rule of the compiler workspace was violated."""


class PublicationError(CompilerError):
    """Revision-level failure: nothing is committed and the previous head stays usable."""


class StaleHead(PublicationError):
    """The lineage head no longer names the intended parent revision (CAS lost)."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompilerError(f"{name} must be nonempty text")
    return value


def _optional_text(value: Any, name: str) -> None:
    if value is not None:
        _text(value, name)


def _pattern(value: Any, pattern: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CompilerError(f"invalid {name}")
    return value


def _typed_tuple(value: Any, kind: type | tuple[type, ...], name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, kind) for item in value):
        raise CompilerError(f"{name} must be an immutable typed tuple")


def _source_ref(value: Any, name: str) -> ContextSourceRef:
    if type(value) is not ContextSourceRef:
        raise CompilerError(f"{name} must be an exact ContextSourceRef")
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- source fragments -----------------------------------------------------------------
class LocationKind(StrEnum):
    BYTE_RANGE = "BYTE_RANGE"      # exact bytes [start, end) of the source revision
    JSON_POINTER = "JSON_POINTER"  # RFC 6901 subtree of a JSON source, canonical JSON


@dataclass(frozen=True)
class FragmentLocation:
    kind: LocationKind
    start: int | None = None
    end: int | None = None
    pointer: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", LocationKind(self.kind))
        if self.kind is LocationKind.BYTE_RANGE:
            if (type(self.start) is not int or type(self.end) is not int or self.start < 0
                    or self.end <= self.start or self.pointer is not None):
                raise CompilerError("BYTE_RANGE needs integers 0 <= start < end, no pointer")
        elif (self.start is not None or self.end is not None
              or not isinstance(self.pointer, str)
              or (self.pointer and not self.pointer.startswith("/"))):
            raise CompilerError("JSON_POINTER needs an RFC 6901 pointer and no byte range")

    @classmethod
    def byte_range(cls, start: int, end: int) -> "FragmentLocation":
        return cls(LocationKind.BYTE_RANGE, start, end)

    @classmethod
    def json_pointer(cls, pointer: str) -> "FragmentLocation":
        return cls(LocationKind.JSON_POINTER, pointer=pointer)

    def extract(self, content: bytes) -> bytes:
        if self.kind is LocationKind.BYTE_RANGE:
            if self.end > len(content):
                raise CompilerError("fragment range exceeds the source")
            return content[self.start:self.end]
        node = json.loads(content)
        for token in self.pointer.split("/")[1:]:
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and token in node:
                node = node[token]
            elif (isinstance(node, list) and re.fullmatch(r"0|[1-9][0-9]*", token)
                  and int(token) < len(node)):
                node = node[int(token)]
            else:
                raise CompilerError("fragment pointer does not resolve")
        return canonical_json(node)

    def record(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "start": self.start, "end": self.end,
                "pointer": self.pointer}


@dataclass(frozen=True, kw_only=True)
class SourceFragmentRef:
    """A bounded exact portion of one immutable source; identity is deterministic."""

    source_ref: ContextSourceRef
    location: FragmentLocation
    content_checksum: str
    fragment_kind: str
    fragmentation_policy: str
    label: str | None = None
    fragment_id: str = field(init=False)

    def __post_init__(self) -> None:
        _source_ref(self.source_ref, "source_ref")
        if not isinstance(self.location, FragmentLocation):
            raise CompilerError("location must be a FragmentLocation")
        _pattern(self.content_checksum, _SHA256, "fragment content_checksum")
        _pattern(self.fragment_kind, _KIND, "fragment_kind")
        _pattern(self.fragmentation_policy, _VERSIONED, "fragmentation_policy")
        _optional_text(self.label, "label")
        object.__setattr__(self, "fragment_id", "frag-" + json_digest(self._identity()))

    def _identity(self) -> dict[str, Any]:
        return {"source_ref": self.source_ref.record(), "location": self.location.record(),
                "content_checksum": self.content_checksum,
                "fragment_kind": self.fragment_kind,
                "fragmentation_policy": self.fragmentation_policy, "label": self.label}

    @classmethod
    def from_content(cls, source_ref: ContextSourceRef, content: bytes,
                     location: FragmentLocation, *, fragment_kind: str,
                     fragmentation_policy: str, label: str | None = None
                     ) -> "SourceFragmentRef":
        return cls(source_ref=source_ref, location=location,
                   content_checksum=_sha256(location.extract(content)),
                   fragment_kind=fragment_kind, fragmentation_policy=fragmentation_policy,
                   label=label)

    def __hash__(self) -> int:  # ContextSourceRef holds unhashable frozen mappings
        return hash(self.fragment_id)

    @property
    def visibility(self) -> Visibility:
        return self.source_ref.visibility

    def verify(self, content: bytes) -> bool:
        try:
            return _sha256(self.location.extract(content)) == self.content_checksum
        except ValueError:
            return False

    def citation(self) -> SourceVersion:
        """The Rule provenance citation (``ExtractionMetadata.source``) of this fragment."""
        ref = self.source_ref
        return SourceVersion(f"{ref.repository}/{ref.path_or_ref}", ref.git_revision,
                             self.fragment_id)

    def record(self) -> dict[str, Any]:
        return {**self._identity(), "fragment_id": self.fragment_id}


# -- plant-context targets ------------------------------------------------------------
class TargetKind(StrEnum):
    ENTITY = "ENTITY"              # ProcessGraph node id
    SIGNAL = "SIGNAL"              # ProcessGraph binding semantic entity id (P1.1 signal_id)
    RELATIONSHIP = "RELATIONSHIP"  # ProcessGraph edge id


@dataclass(frozen=True)
class PlantTargetRef:
    """``(selected PROCESS_GRAPH source ref, ProcessGraph id)``; P1.3 preserves the ids."""

    plant_context: ContextSourceRef
    kind: TargetKind
    target_id: str

    def __post_init__(self) -> None:
        if _source_ref(self.plant_context, "plant_context").kind != PROCESS_GRAPH_KIND:
            raise CompilerError("plant targets resolve against a PROCESS_GRAPH source")
        object.__setattr__(self, "kind", TargetKind(self.kind))
        _text(self.target_id, "target_id")

    def __hash__(self) -> int:  # consistent with field equality
        return hash((self.plant_context.source_id, self.plant_context.content_checksum,
                     self.kind, self.target_id))

    def record(self) -> dict[str, Any]:
        return {"plant_context": self.plant_context.record(), "kind": self.kind.value,
                "target_id": self.target_id}

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "PlantTargetRef":
        if not isinstance(record, Mapping) or set(record) != {"plant_context", "kind",
                                                               "target_id"}:
            raise CompilerError("PlantTargetRef record must have exactly the v0 fields")
        return cls(ContextSourceRef(**dict(record["plant_context"])), record["kind"],
                   record["target_id"])


@dataclass(frozen=True)
class PlantContextIndex:
    """Identity-preserving id sets of the selected ProcessGraph; no new plant semantics."""

    source_ref: ContextSourceRef
    entities: frozenset[str]
    signals: frozenset[str]
    relationships: frozenset[str]

    def __post_init__(self) -> None:
        ref = _source_ref(self.source_ref, "source_ref")
        if ref.kind != PROCESS_GRAPH_KIND or ref.visibility != Visibility.AGENT:
            raise CompilerError("the plant context is an AGENT-visible PROCESS_GRAPH source")
        for name in ("entities", "signals", "relationships"):
            values = getattr(self, name)
            if not isinstance(values, frozenset) or any(
                    not isinstance(item, str) or not item for item in values):
                raise CompilerError(f"{name} must be a frozenset of ids")

    @classmethod
    def from_process_graph(cls, source_ref: ContextSourceRef, graph: Any
                           ) -> "PlantContextIndex":
        return cls(source_ref, frozenset(node.node_id for node in graph.nodes()),
                   frozenset(binding.semantic_entity_id for binding in graph.bindings()),
                   frozenset(edge.edge_id for edge in graph.edges()))

    def _ids(self, kind: TargetKind) -> frozenset[str]:
        return {TargetKind.ENTITY: self.entities, TargetKind.SIGNAL: self.signals,
                TargetKind.RELATIONSHIP: self.relationships}[TargetKind(kind)]

    def contains(self, target: PlantTargetRef) -> bool:
        return (target.plant_context == self.source_ref
                and target.target_id in self._ids(target.kind))

    def target(self, kind: TargetKind, target_id: str) -> PlantTargetRef:
        ref = PlantTargetRef(self.source_ref, kind, target_id)
        if not self.contains(ref):
            raise CompilerError("no such target in the selected plant context")
        return ref


# -- typed extraction routing ---------------------------------------------------------
class ExtractionKind(StrEnum):
    """Minimal v0 vocabulary; unknown material stays unclassified, never forced in."""

    ENTITY_MENTION = "ENTITY_MENTION"
    SIGNAL_MENTION = "SIGNAL_MENTION"
    OPERATING_LIMIT = "OPERATING_LIMIT"
    CONTROL_RELATION = "CONTROL_RELATION"
    PROCEDURE_GUIDANCE = "PROCEDURE_GUIDANCE"
    TROUBLESHOOTING_GUIDANCE = "TROUBLESHOOTING_GUIDANCE"
    MAINTENANCE_OBSERVATION = "MAINTENANCE_OBSERVATION"


class Route(StrEnum):
    BINDING = "BINDING"      # CandidateBinding
    RULE = "RULE"            # inert literature-candidate Rule carrier
    KNOWLEDGE = "KNOWLEDGE"  # non-rule CandidateKnowledge -> KnowledgeRef


_ROUTES = {
    ExtractionKind.ENTITY_MENTION: Route.BINDING,
    ExtractionKind.SIGNAL_MENTION: Route.BINDING,
    ExtractionKind.OPERATING_LIMIT: Route.RULE,
    ExtractionKind.CONTROL_RELATION: Route.RULE,
    ExtractionKind.PROCEDURE_GUIDANCE: Route.KNOWLEDGE,
    ExtractionKind.TROUBLESHOOTING_GUIDANCE: Route.KNOWLEDGE,
    ExtractionKind.MAINTENANCE_OBSERVATION: Route.KNOWLEDGE,
}
_COMPATIBLE_TARGETS = {
    ExtractionKind.ENTITY_MENTION: frozenset({TargetKind.ENTITY, TargetKind.RELATIONSHIP}),
    ExtractionKind.SIGNAL_MENTION: frozenset({TargetKind.SIGNAL}),
}


def route(kind: Any) -> Route:
    """The deterministic, versioned (``ROUTING_POLICY``) v0 routing decision."""
    try:
        return _ROUTES[ExtractionKind(kind)]
    except ValueError:
        raise CompilerError("unsupported extraction kind stays unclassified") from None


# -- provenance -----------------------------------------------------------------------
@dataclass(frozen=True)
class Confidence:
    """Review-routing metadata only: not truth, authority, evidence or permission."""

    score: float
    method: str

    def __post_init__(self) -> None:
        if (type(self.score) not in (int, float) or not math.isfinite(self.score)
                or not 0 <= self.score <= 1):
            raise CompilerError("confidence score must be a finite number in [0, 1]")
        _pattern(self.method, _VERSIONED, "confidence method")

    def record(self) -> dict[str, Any]:
        return {"score": self.score, "method": self.method}


@dataclass(frozen=True)
class DerivedInput:
    """A non-source influencing input (e.g. a recorded model call) and its visibility."""

    ref: str
    role: str
    visibility: Visibility

    def __post_init__(self) -> None:
        _text(self.ref, "derived input ref")
        _text(self.role, "derived input role")
        object.__setattr__(self, "visibility", Visibility(self.visibility))

    def record(self) -> dict[str, Any]:
        return {"ref": self.ref, "role": self.role, "visibility": self.visibility.value}


@dataclass(frozen=True)
class DerivedAnnotation:
    """Model-normalized text: derived annotation with generator provenance, never content."""

    text: str
    generator: str

    def __post_init__(self) -> None:
        _text(self.text, "annotation text")
        _pattern(self.generator, _VERSIONED, "annotation generator")

    def record(self) -> dict[str, Any]:
        return {"text": self.text, "generator": self.generator, "derived": True}


@dataclass(frozen=True)
class TypedValue:
    name: str
    value: int | float | str
    unit: str | None = None

    def __post_init__(self) -> None:
        _text(self.name, "value name")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float, str)):
            raise CompilerError("typed value must be a number or text")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise CompilerError("typed value must be finite")
        _optional_text(self.unit, "unit")

    def record(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "unit": self.unit}


@dataclass(frozen=True, kw_only=True)
class DerivationProvenance:
    """Methods, versions and every influencing input of one candidate version."""

    extraction_method: str
    grounding_method: str
    routing_policy: str = ROUTING_POLICY
    model: str | None = None
    influencing_sources: tuple[ContextSourceRef, ...] = ()
    derived_inputs: tuple[DerivedInput, ...] = ()
    confidence: Confidence | None = None

    def __post_init__(self) -> None:
        _pattern(self.extraction_method, _VERSIONED, "extraction_method")
        _pattern(self.grounding_method, _VERSIONED, "grounding_method")
        if self.routing_policy != ROUTING_POLICY:
            raise CompilerError("unknown routing policy version")
        _optional_text(self.model, "model")
        _typed_tuple(self.influencing_sources, ContextSourceRef, "influencing_sources")
        _typed_tuple(self.derived_inputs, DerivedInput, "derived_inputs")
        if self.confidence is not None and not isinstance(self.confidence, Confidence):
            raise CompilerError("confidence must be a Confidence")

    def visibilities(self) -> tuple[Visibility, ...]:
        return (*(ref.visibility for ref in self.influencing_sources),
                *(item.visibility for item in self.derived_inputs))

    def record(self) -> dict[str, Any]:
        return {"extraction_method": self.extraction_method,
                "grounding_method": self.grounding_method,
                "routing_policy": self.routing_policy, "model": self.model,
                "influencing_sources": [ref.record() for ref in self.influencing_sources],
                "derived_inputs": [item.record() for item in self.derived_inputs],
                "confidence": None if self.confidence is None else self.confidence.record()}


# -- candidates -----------------------------------------------------------------------
@dataclass(frozen=True, order=True)
class CandidateRef:
    """Binds an exact candidate version, never only its content key."""

    candidate_id: str
    candidate_version: str

    def __post_init__(self) -> None:
        _pattern(self.candidate_id, _ID, "candidate_id")
        _pattern(self.candidate_version, _SHA256, "candidate_version")

    def record(self) -> dict[str, str]:
        return {"candidate_id": self.candidate_id,
                "candidate_version": self.candidate_version}

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "CandidateRef":
        if not isinstance(record, Mapping) or set(record) != {"candidate_id",
                                                               "candidate_version"}:
            raise CompilerError("CandidateRef record must have exactly the v0 fields")
        return cls(record["candidate_id"], record["candidate_version"])


class _Candidate:
    candidate_id: str
    session_id: str
    extraction_kind: ExtractionKind
    provenance: DerivationProvenance
    scope_refs: tuple[PlantTargetRef, ...]

    def _common(self, routes: frozenset[Route]) -> None:
        _pattern(self.candidate_id, _ID, "candidate_id")
        _text(self.session_id, "session_id")
        try:
            object.__setattr__(self, "extraction_kind", ExtractionKind(self.extraction_kind))
        except ValueError:
            raise CompilerError("unsupported extraction kind stays unclassified") from None
        if route(self.extraction_kind) not in routes:
            raise CompilerError("extraction kind is routed to a different candidate type")
        if not isinstance(self.provenance, DerivationProvenance):
            raise CompilerError("provenance must be a DerivationProvenance")
        _typed_tuple(self.scope_refs, PlantTargetRef, "scope_refs")

    def _identify(self, content: Mapping[str, Any], version_extra: Mapping[str, Any]) -> None:
        contexts = [target.plant_context for target in self.targets()]
        if any(context != contexts[0] for context in contexts):
            raise CompilerError("a candidate resolves against exactly one plant context")
        key = json_digest(dict(content))
        object.__setattr__(self, "content_key", key)
        object.__setattr__(self, "candidate_version", json_digest({
            "content_key": key, "candidate_id": self.candidate_id,
            "session_id": self.session_id, "provenance": self.provenance.record(),
            **version_extra}))

    def fragments(self) -> tuple[SourceFragmentRef, ...]:
        raise NotImplementedError

    def targets(self) -> tuple[PlantTargetRef, ...]:
        raise NotImplementedError

    @property
    def ref(self) -> CandidateRef:
        return CandidateRef(self.candidate_id, self.candidate_version)  # type: ignore[attr-defined]

    @property
    def route(self) -> Route:
        return route(self.extraction_kind)

    @property
    def visibility(self) -> Visibility:
        """Most restrictive visibility of every input that influenced this candidate."""
        return most_restrictive(Visibility.AGENT,
                                *(item.visibility for item in self.fragments()),
                                *(item.plant_context.visibility for item in self.targets()),
                                *self.provenance.visibilities())


def _fragment_ids(fragments: Iterable[SourceFragmentRef]) -> list[str]:
    return [item.fragment_id for item in fragments]


@dataclass(frozen=True, kw_only=True)
class CandidateBinding(_Candidate):
    """A proposed mapping from a source mention to an existing plant identity."""

    candidate_id: str
    session_id: str
    extraction_kind: ExtractionKind
    mention_fragment: SourceFragmentRef
    mention_text: str
    target_kind: TargetKind
    proposed_target: PlantTargetRef | None
    scope_refs: tuple[PlantTargetRef, ...]
    provenance: DerivationProvenance
    normalized_mention: str | None = None
    alternative_targets: tuple[PlantTargetRef, ...] = ()
    supporting_fragments: tuple[SourceFragmentRef, ...] = ()
    conflicting_fragments: tuple[SourceFragmentRef, ...] = ()
    content_key: str = field(init=False)
    candidate_version: str = field(init=False)

    def __post_init__(self) -> None:
        self._common(frozenset({Route.BINDING}))
        if not isinstance(self.mention_fragment, SourceFragmentRef):
            raise CompilerError("mention_fragment must be a SourceFragmentRef")
        _text(self.mention_text, "mention_text")
        _optional_text(self.normalized_mention, "normalized_mention")
        object.__setattr__(self, "target_kind", TargetKind(self.target_kind))
        _typed_tuple(self.alternative_targets, PlantTargetRef, "alternative_targets")
        _typed_tuple(self.supporting_fragments, SourceFragmentRef, "supporting_fragments")
        _typed_tuple(self.conflicting_fragments, SourceFragmentRef, "conflicting_fragments")
        proposed = () if self.proposed_target is None else (self.proposed_target,)
        _typed_tuple(proposed, PlantTargetRef, "proposed_target")
        if any(item.kind != self.target_kind for item in (*proposed, *self.alternative_targets)):
            raise CompilerError("proposed and alternative targets must be of target_kind")
        if len(set((*proposed, *self.alternative_targets))) != len(
                proposed) + len(self.alternative_targets):
            raise CompilerError("alternative targets must be distinct from the proposal")
        self._identify(
            {"route": Route.BINDING.value, "extraction_kind": self.extraction_kind.value,
             "mention_fragment": self.mention_fragment.fragment_id,
             "supporting_fragments": _fragment_ids(self.supporting_fragments),
             "conflicting_fragments": _fragment_ids(self.conflicting_fragments),
             "mention_text": self.mention_text, "normalized_mention": self.normalized_mention,
             "target_kind": self.target_kind.value,
             "proposed_target": None if self.proposed_target is None
             else self.proposed_target.record(),
             "scope_refs": [item.record() for item in self.scope_refs]},
            {"alternative_targets": [item.record() for item in self.alternative_targets]})

    def fragments(self) -> tuple[SourceFragmentRef, ...]:
        return (self.mention_fragment, *self.supporting_fragments, *self.conflicting_fragments)

    def targets(self) -> tuple[PlantTargetRef, ...]:
        proposed = () if self.proposed_target is None else (self.proposed_target,)
        return (*proposed, *self.alternative_targets, *self.scope_refs)


@dataclass(frozen=True, kw_only=True)
class CandidateKnowledge(_Candidate):
    """A proposed engineering statement; RULE-routed items carry an inert candidate Rule."""

    candidate_id: str
    session_id: str
    extraction_kind: ExtractionKind
    source_fragments: tuple[SourceFragmentRef, ...]
    scope_refs: tuple[PlantTargetRef, ...]
    provenance: DerivationProvenance
    rule: Rule | None = None
    normalized_claim: DerivedAnnotation | None = None
    conditions: str | None = None
    values: tuple[TypedValue, ...] = ()
    conflicting_fragments: tuple[SourceFragmentRef, ...] = ()
    conflicting_candidate_ids: tuple[str, ...] = ()
    content_key: str = field(init=False)
    candidate_version: str = field(init=False)

    def __post_init__(self) -> None:
        self._common(frozenset({Route.RULE, Route.KNOWLEDGE}))
        _typed_tuple(self.source_fragments, SourceFragmentRef, "source_fragments")
        if not self.source_fragments:
            raise CompilerError("knowledge candidates cite at least one source fragment")
        _typed_tuple(self.conflicting_fragments, SourceFragmentRef, "conflicting_fragments")
        _typed_tuple(self.values, TypedValue, "values")
        _typed_tuple(self.conflicting_candidate_ids, str, "conflicting_candidate_ids")
        for item in self.conflicting_candidate_ids:
            _pattern(item, _ID, "conflicting candidate id")
        _optional_text(self.conditions, "conditions")
        if self.normalized_claim is not None and not isinstance(self.normalized_claim,
                                                                DerivedAnnotation):
            raise CompilerError("normalized_claim must be a DerivedAnnotation")
        if self.route is Route.RULE:
            if not isinstance(self.rule, Rule):
                raise CompilerError("RULE-routed items use the existing Rule carrier")
            if (self.rule.origin, self.rule.validation, self.rule.authority) != (
                    Origin.LITERATURE, Validation.NONE, Authority.REFERENCE) or (
                    self.rule.status != "candidate" or self.rule.extraction is None):
                raise CompilerError("compiler rules stay inert LITERATURE/NONE/REFERENCE "
                                    "literature candidates with extraction metadata")
            if self.normalized_claim is not None or self.conditions is not None or self.values:
                raise CompilerError("RULE-routed claims live in the Rule carrier only")
        elif self.rule is not None:
            raise CompilerError("only RULE-routed items carry a Rule")
        self._identify(
            {"route": self.route.value, "extraction_kind": self.extraction_kind.value,
             "source_fragments": _fragment_ids(self.source_fragments),
             "conflicting_fragments": _fragment_ids(self.conflicting_fragments),
             "conflicting_candidate_ids": list(self.conflicting_candidate_ids),
             "scope_refs": [item.record() for item in self.scope_refs],
             "rule": None if self.rule is None else rule_record(self.rule),
             "normalized_claim": None if self.normalized_claim is None
             else self.normalized_claim.record(),
             "conditions": self.conditions,
             "values": [item.record() for item in self.values]}, {})

    def fragments(self) -> tuple[SourceFragmentRef, ...]:
        return (*self.source_fragments, *self.conflicting_fragments)

    def targets(self) -> tuple[PlantTargetRef, ...]:
        return self.scope_refs


Candidate = CandidateBinding | CandidateKnowledge


# -- compile session --------------------------------------------------------------------
@dataclass(frozen=True)
class CompilerPolicies:
    review_policy: str
    compiler_version: str = COMPILER_CONTRACT_VERSION
    validation_policy: str = VALIDATION_POLICY
    # Explicitly registered deterministic acceptance policies (policy_id@version). None
    # exist in v0; a policy may never depend on model confidence or model judgment.
    acceptance_policies: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _pattern(self.review_policy, _VERSIONED, "review_policy")
        _text(self.compiler_version, "compiler_version")
        _text(self.validation_policy, "validation_policy")
        if not isinstance(self.acceptance_policies, frozenset):
            raise CompilerError("acceptance_policies must be a frozenset")
        for item in self.acceptance_policies:
            _pattern(item, _VERSIONED, "acceptance policy")

    def record(self) -> dict[str, Any]:
        return {"review_policy": self.review_policy, "compiler_version": self.compiler_version,
                "validation_policy": self.validation_policy,
                "acceptance_policies": sorted(self.acceptance_policies)}


@dataclass(frozen=True, eq=False)
class CompileSession:
    """Frozen inputs of one compilation; build it with ``CompileSession.prepare``.

    The session owns its CanonicalContextRegistry instance over exactly the attested
    sources it reads; it never reuses or mutates a run's READY registry.
    """

    registry: CanonicalContextRegistry
    inventory: tuple[ContextSourceRef, ...]
    plant: PlantContextIndex
    input_rule_set_ref: ContextSourceRef
    input_rule_set: RuleSetRevision
    policies: CompilerPolicies
    visibility: Visibility
    session_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.registry.frozen:
            raise CompilerError("a compile session reads a frozen source inventory")
        object.__setattr__(self, "session_id", "cs-" + json_digest({
            "inventory": [ref.record() for ref in self.inventory],
            "plant_context": self.plant.source_ref.record(),
            "input_rule_set": self.input_rule_set_ref.record(),
            "policies": self.policies.record(), "visibility": self.visibility.value}))

    @classmethod
    def prepare(cls, revisions: Mapping[str, str],
                materializers: Mapping[str, SourceMaterializer],
                sources: Sequence[ContextSourceRef], *, process_graph_source_id: str,
                input_rule_set_source_id: str, plant_graph_loader: Callable[[Any], Any],
                policies: CompilerPolicies,
                visibility: Visibility = Visibility.AGENT) -> "CompileSession":
        visibility = Visibility(visibility)
        if not isinstance(policies, CompilerPolicies):
            raise CompilerError("policies must be CompilerPolicies")
        registry = CanonicalContextRegistry(revisions, materializers)
        try:
            for source in sources:
                registry.register(source)
        except ContextSourceError as exc:
            raise CompilerError(f"compile input is not an exact attested source: {exc}") from exc
        inventory = registry.freeze()
        if any(most_restrictive(ref.visibility, visibility) != visibility for ref in inventory):
            raise CompilerError("a compilation uses only inputs visible at its visibility")
        graphs = [ref for ref in inventory if ref.kind == PROCESS_GRAPH_KIND]
        if (len(graphs) != 1 or graphs[0].source_id != process_graph_source_id
                or graphs[0].visibility != Visibility.AGENT):
            raise CompilerError("select exactly one AGENT-visible PROCESS_GRAPH source")
        rule_sets = [ref for ref in inventory if ref.kind == RULE_SET_KIND]
        if len(rule_sets) != 1 or rule_sets[0].source_id != input_rule_set_source_id:
            raise CompilerError("select exactly one input ENGINEERING_RULE_SET source")
        try:
            input_rule_set = load_rule_set_source(registry, input_rule_set_source_id,
                                                  ProjectionScope.EVALUATOR)
        except RuleSetError as exc:
            raise CompilerError(f"input rule set is invalid: {exc}") from exc
        graph = plant_graph_loader(registry.resolve(process_graph_source_id,
                                                    ProjectionScope.EVALUATOR).json())
        return cls(registry, inventory, PlantContextIndex.from_process_graph(graphs[0], graph),
                   rule_sets[0], input_rule_set, policies, visibility)

    def source_content(self, ref: ContextSourceRef) -> bytes | None:
        """Re-attested bytes of an exact inventory source; None for anything else."""
        try:
            if self.registry.ref(ref.source_id, ProjectionScope.EVALUATOR) != ref:
                return None
            return self.registry.resolve(ref.source_id, ProjectionScope.EVALUATOR).content
        except (UnknownContextSource, ContextSourceError):
            return None


# -- deterministic validation -------------------------------------------------------------
class FindingCode(StrEnum):
    SESSION_MISMATCH = "SESSION_MISMATCH"
    SOURCE_UNRESOLVED = "SOURCE_UNRESOLVED"
    FRAGMENT_INVALID = "FRAGMENT_INVALID"
    INFLUENCE_UNKNOWN = "INFLUENCE_UNKNOWN"
    PLANT_CONTEXT_MISMATCH = "PLANT_CONTEXT_MISMATCH"
    TARGET_UNRESOLVED = "TARGET_UNRESOLVED"
    TARGET_UNKNOWN = "TARGET_UNKNOWN"
    TARGET_KIND_INCOMPATIBLE = "TARGET_KIND_INCOMPATIBLE"
    SCOPE_EMPTY = "SCOPE_EMPTY"
    SCOPE_UNKNOWN = "SCOPE_UNKNOWN"
    VISIBILITY_WIDENED = "VISIBILITY_WIDENED"
    RULE_INVALID = "RULE_INVALID"
    RULE_VERSION_EXISTS = "RULE_VERSION_EXISTS"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"    # non-blocking review-routing finding
    CONFLICT_DECLARED = "CONFLICT_DECLARED"  # non-blocking; conflicts stay explicit


_NON_BLOCKING = frozenset({FindingCode.AMBIGUOUS_TARGET, FindingCode.CONFLICT_DECLARED})


@dataclass(frozen=True)
class ValidationFinding:
    code: FindingCode
    detail: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", FindingCode(self.code))
        _text(self.detail, "finding detail")

    @property
    def blocking(self) -> bool:
        return self.code not in _NON_BLOCKING

    def record(self) -> dict[str, Any]:
        return {"code": self.code.value, "detail": self.detail, "blocking": self.blocking}


@dataclass(frozen=True, kw_only=True)
class ValidationResult:
    candidate_ref: CandidateRef
    session_id: str
    validation_policy: str
    selected_target: PlantTargetRef | None
    selected_scope_refs: tuple[PlantTargetRef, ...]
    findings: tuple[ValidationFinding, ...]
    result_id: str = field(init=False)

    def __post_init__(self) -> None:
        _typed_tuple(self.findings, ValidationFinding, "findings")
        object.__setattr__(self, "result_id", "vr-" + json_digest(self._body()))

    def _body(self) -> dict[str, Any]:
        return {"candidate_ref": self.candidate_ref.record(), "session_id": self.session_id,
                "validation_policy": self.validation_policy,
                "selected_target": None if self.selected_target is None
                else self.selected_target.record(),
                "selected_scope_refs": [item.record() for item in self.selected_scope_refs],
                "findings": [item.record() for item in self.findings]}

    @property
    def passed(self) -> bool:
        return not any(item.blocking for item in self.findings)

    def record(self) -> dict[str, Any]:
        return {**self._body(), "result_id": self.result_id, "passed": self.passed}


def validate_candidate(session: CompileSession, candidate: Candidate,
                       review: "ReviewRecord | None" = None) -> ValidationResult:
    """Deterministic validators; a REMAP review is revalidated against its selection."""
    findings: list[ValidationFinding] = []

    def add(code: FindingCode, detail: str) -> None:
        findings.append(ValidationFinding(code, detail))

    if candidate.session_id != session.session_id:
        add(FindingCode.SESSION_MISMATCH, "candidate was compiled in another session")
    contents: dict[str, bytes | None] = {}
    for fragment in candidate.fragments():
        key = fragment.source_ref.source_id
        if key not in contents:
            contents[key] = session.source_content(fragment.source_ref)
        if contents[key] is None:
            add(FindingCode.SOURCE_UNRESOLVED, f"{fragment.fragment_id}: source is not an "
                "exact attested session source")
        elif not fragment.verify(contents[key]):
            add(FindingCode.FRAGMENT_INVALID, f"{fragment.fragment_id}: location/checksum")
    for ref in candidate.provenance.influencing_sources:
        if session.source_content(ref) is None:
            add(FindingCode.INFLUENCE_UNKNOWN, f"{ref.source_id}: not a session source")
    remap = review is not None and review.decision is ReviewDecision.REMAP
    scope = review.selected_scope_refs if remap else candidate.scope_refs
    target = None
    if isinstance(candidate, CandidateBinding):
        target = review.selected_target if remap else candidate.proposed_target
        for label, item in (("target", target),
                            *(("alternative", alt) for alt in candidate.alternative_targets)):
            if item is None:
                add(FindingCode.TARGET_UNRESOLVED, "mention has no existing plant target")
            elif item.plant_context != session.plant.source_ref:
                add(FindingCode.PLANT_CONTEXT_MISMATCH, f"{label} uses another plant context")
            elif not session.plant.contains(item):
                add(FindingCode.TARGET_UNKNOWN, f"{label} {item.target_id} does not exist")
            elif (item.kind not in _COMPATIBLE_TARGETS[candidate.extraction_kind]
                  or item.kind != candidate.target_kind):
                add(FindingCode.TARGET_KIND_INCOMPATIBLE,
                    f"{label} kind {item.kind.value} is incompatible")
        if candidate.alternative_targets:
            add(FindingCode.AMBIGUOUS_TARGET, f"{len(candidate.alternative_targets)} "
                "alternative targets need focused review")
    if not scope:
        add(FindingCode.SCOPE_EMPTY, "an empty scope is never read as plant-wide")
    for item in scope:
        if item.plant_context != session.plant.source_ref:
            add(FindingCode.PLANT_CONTEXT_MISMATCH, "scope uses another plant context")
        elif not session.plant.contains(item):
            add(FindingCode.SCOPE_UNKNOWN, f"scope {item.target_id} does not exist")
    taint = [candidate.visibility]
    if review is not None:
        taint.append(review.visibility)
    if most_restrictive(session.visibility, *taint) != session.visibility:
        add(FindingCode.VISIBILITY_WIDENED, "an influencing input is more restrictive than "
            "the compilation visibility")
    if isinstance(candidate, CandidateKnowledge):
        if candidate.rule is not None:
            citations = {item.citation() for item in candidate.source_fragments}
            if candidate.rule.extraction.source not in citations:
                add(FindingCode.RULE_INVALID, "rule extraction must cite a source fragment")
            try:
                session.input_rule_set.registry.get_rule(candidate.rule.ref)
                add(FindingCode.RULE_VERSION_EXISTS, "an existing rule version is never edited")
            except KeyError:
                pass
        if candidate.conflicting_fragments or candidate.conflicting_candidate_ids:
            add(FindingCode.CONFLICT_DECLARED, "conflicting sources/candidates are recorded")
    elif candidate.conflicting_fragments:
        add(FindingCode.CONFLICT_DECLARED, "conflicting source fragments are recorded")
    return ValidationResult(candidate_ref=candidate.ref, session_id=session.session_id,
                            validation_policy=session.policies.validation_policy,
                            selected_target=target, selected_scope_refs=tuple(scope),
                            findings=tuple(findings))


# -- human review -----------------------------------------------------------------------
class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REMAP = "REMAP"
    KEEP_CANDIDATE = "KEEP_CANDIDATE"


class PrincipalKind(StrEnum):
    """Allowed review decision principals; a model/Agent is not representable."""

    HUMAN = "HUMAN"
    DETERMINISTIC_POLICY = "DETERMINISTIC_POLICY"


@dataclass(frozen=True)
class ReviewPrincipal:
    kind: PrincipalKind
    identifier: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PrincipalKind(self.kind))
        if self.kind is PrincipalKind.HUMAN:
            _text(self.identifier, "reviewer_id")
        else:
            _pattern(self.identifier, _VERSIONED, "policy_id@version")

    @classmethod
    def human(cls, reviewer_id: str) -> "ReviewPrincipal":
        return cls(PrincipalKind.HUMAN, reviewer_id)

    def record(self) -> dict[str, str]:
        return {"kind": self.kind.value, "identifier": self.identifier}


_REVIEW_FIELDS = frozenset({
    "review_id", "candidate_ref", "decision", "principal", "reason", "reviewed_at",
    "review_policy", "validation_policy", "session_id", "plant_context",
    "review_log_position", "supersedes", "selected_target", "selected_scope_refs",
    "visibility"})


@dataclass(frozen=True, kw_only=True)
class ReviewRecord:
    """One immutable review decision at its append-only log position."""

    review_id: str
    candidate_ref: CandidateRef
    decision: ReviewDecision
    principal: ReviewPrincipal
    reason: str
    reviewed_at: str
    review_policy: str
    validation_policy: str
    session_id: str
    plant_context: ContextSourceRef
    review_log_position: int
    supersedes: tuple[str, ...] = ()
    selected_target: PlantTargetRef | None = None
    selected_scope_refs: tuple[PlantTargetRef, ...] | None = None
    visibility: Visibility = Visibility.AGENT  # of the review metadata / rationale

    def __post_init__(self) -> None:
        _pattern(self.review_id, _ID, "review_id")
        if not isinstance(self.candidate_ref, CandidateRef):
            raise CompilerError("candidate_ref must be a CandidateRef")
        object.__setattr__(self, "decision", ReviewDecision(self.decision))
        if not isinstance(self.principal, ReviewPrincipal):
            raise CompilerError("principal must be a ReviewPrincipal")
        for name in ("reason", "reviewed_at", "review_policy", "validation_policy",
                     "session_id"):
            _text(getattr(self, name), name)
        _source_ref(self.plant_context, "plant_context")
        if type(self.review_log_position) is not int or self.review_log_position < 1:
            raise CompilerError("review_log_position is a positive integer")
        _typed_tuple(self.supersedes, str, "supersedes")
        if len(set(self.supersedes)) != len(self.supersedes) or self.review_id in self.supersedes:
            raise CompilerError("supersedes names distinct earlier records")
        object.__setattr__(self, "visibility", Visibility(self.visibility))
        if self.decision is ReviewDecision.REMAP:
            _typed_tuple(self.selected_scope_refs, PlantTargetRef, "selected_scope_refs")
            if self.selected_target is not None and not isinstance(self.selected_target,
                                                                   PlantTargetRef):
                raise CompilerError("selected_target must be a PlantTargetRef")
        elif self.selected_target is not None or self.selected_scope_refs is not None:
            raise CompilerError("only REMAP stores a selected binding")

    def record(self) -> dict[str, Any]:
        return {"review_id": self.review_id, "candidate_ref": self.candidate_ref.record(),
                "decision": self.decision.value, "principal": self.principal.record(),
                "reason": self.reason, "reviewed_at": self.reviewed_at,
                "review_policy": self.review_policy,
                "validation_policy": self.validation_policy, "session_id": self.session_id,
                "plant_context": self.plant_context.record(),
                "review_log_position": self.review_log_position,
                "supersedes": list(self.supersedes),
                "selected_target": None if self.selected_target is None
                else self.selected_target.record(),
                "selected_scope_refs": None if self.selected_scope_refs is None
                else [item.record() for item in self.selected_scope_refs],
                "visibility": self.visibility.value}

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ReviewRecord":
        if not isinstance(record, Mapping) or set(record) != _REVIEW_FIELDS:
            raise CompilerError("ReviewRecord record must have exactly the v0 fields")
        principal = record["principal"]
        if not isinstance(principal, Mapping) or set(principal) != {"kind", "identifier"}:
            raise CompilerError("invalid review principal record")
        scope = record["selected_scope_refs"]
        if not isinstance(record["supersedes"], list) or not (
                scope is None or isinstance(scope, list)):
            raise CompilerError("supersedes/selected_scope_refs must be lists")
        return cls(
            review_id=record["review_id"],
            candidate_ref=CandidateRef.from_record(record["candidate_ref"]),
            decision=record["decision"],
            principal=ReviewPrincipal(principal["kind"], principal["identifier"]),
            reason=record["reason"], reviewed_at=record["reviewed_at"],
            review_policy=record["review_policy"],
            validation_policy=record["validation_policy"], session_id=record["session_id"],
            plant_context=ContextSourceRef(**dict(record["plant_context"])),
            review_log_position=record["review_log_position"],
            supersedes=tuple(record["supersedes"]),
            selected_target=None if record["selected_target"] is None
            else PlantTargetRef.from_record(record["selected_target"]),
            selected_scope_refs=None if scope is None
            else tuple(PlantTargetRef.from_record(item) for item in scope),
            visibility=record["visibility"])


class ReviewStatus(StrEnum):
    """Derived projection of the review log; never independently mutable."""

    UNREVIEWED = "UNREVIEWED"
    APPROVED = "APPROVED"
    REMAPPED = "REMAPPED"
    REJECTED = "REJECTED"
    KEEP_CANDIDATE = "KEEP_CANDIDATE"
    UNRESOLVED = "UNRESOLVED"  # more than one terminal record, even when they agree


_DECISION_STATUS = {ReviewDecision.APPROVE: ReviewStatus.APPROVED,
                    ReviewDecision.REMAP: ReviewStatus.REMAPPED,
                    ReviewDecision.REJECT: ReviewStatus.REJECTED,
                    ReviewDecision.KEEP_CANDIDATE: ReviewStatus.KEEP_CANDIDATE}


@dataclass(frozen=True)
class EffectiveReview:
    candidate_ref: CandidateRef
    review_log_position: int
    status: ReviewStatus
    record: ReviewRecord | None
    terminal_review_ids: tuple[str, ...]


def effective_review(records: Iterable[ReviewRecord], candidate_ref: CandidateRef,
                     position: int) -> EffectiveReview:
    """Computed over every record of the candidate version in the prefix <= position."""
    prefix = [item for item in records if item.candidate_ref == candidate_ref
              and item.review_log_position <= position]
    superseded = {review_id for item in prefix for review_id in item.supersedes}
    terminal = [item for item in prefix if item.review_id not in superseded]
    ids = tuple(item.review_id for item in terminal)
    if not prefix:
        return EffectiveReview(candidate_ref, position, ReviewStatus.UNREVIEWED, None, ids)
    if len(terminal) != 1:
        return EffectiveReview(candidate_ref, position, ReviewStatus.UNRESOLVED, None, ids)
    record = terminal[0]
    return EffectiveReview(candidate_ref, position, _DECISION_STATUS[record.decision],
                           record, ids)


# -- publication --------------------------------------------------------------------------
@dataclass(frozen=True)
class ArtifactPathRef:
    """A same-commit reference: repository path plus content checksum, never HEAD/latest."""

    path: str
    content_checksum: str

    def __post_init__(self) -> None:
        try:
            checked_relative_path(self.path)
        except ValueError as exc:
            raise CompilerError(str(exc)) from exc
        _pattern(self.content_checksum, _SHA256, "artifact content_checksum")

    def record(self) -> dict[str, str]:
        return {"path": self.path, "content_checksum": self.content_checksum}

    @classmethod
    def from_record(cls, record: Any) -> "ArtifactPathRef":
        if not isinstance(record, Mapping) or set(record) != {"path", "content_checksum"}:
            raise CompilerError("artifact ref must have exactly path and content_checksum")
        return cls(record["path"], record["content_checksum"])

    @classmethod
    def of(cls, path: str, data: bytes) -> "ArtifactPathRef":
        return cls(path, content_checksum(data, CANONICAL_JSON_SHA256))


def head_path(lineage_id: str) -> str:
    return f"{ENGINEERING_CONTEXT_ROOT}/{_pattern(lineage_id, _ID, 'lineage_id')}/HEAD.json"


@dataclass(frozen=True, kw_only=True)
class PublicationRequest:
    """Publisher input selects candidate versions only; targets, scopes, reviews and the
    review-log position always come from the workspace."""

    lineage_id: str
    candidate_refs: tuple[CandidateRef, ...]
    published_by: str
    published_at: str
    parent_revision: ContextSourceRef | None = None

    def __post_init__(self) -> None:
        _pattern(self.lineage_id, _ID, "lineage_id")
        _typed_tuple(self.candidate_refs, CandidateRef, "candidate_refs")
        if len(set(self.candidate_refs)) != len(self.candidate_refs):
            raise CompilerError("candidate_refs must be distinct")
        _text(self.published_by, "published_by")
        _text(self.published_at, "published_at")
        if self.parent_revision is not None:
            parent = _source_ref(self.parent_revision, "parent_revision")
            if (parent.kind != ENGINEERING_CONTEXT_REVISION_KIND
                    or parent.checksum_method != CANONICAL_JSON_SHA256):
                raise CompilerError("parent_revision is a committed revision ContextSourceRef")

    @property
    def expected_head(self) -> ArtifactPathRef | None:
        parent = self.parent_revision
        return None if parent is None else ArtifactPathRef(parent.path_or_ref,
                                                           parent.content_checksum)


class ExclusionReason(StrEnum):
    STALE_SESSION = "STALE_SESSION"
    STALE_REVIEW = "STALE_REVIEW"
    UNREVIEWED = "UNREVIEWED"
    UNRESOLVED_REVIEW = "UNRESOLVED_REVIEW"
    REJECTED = "REJECTED"
    KEEP_CANDIDATE = "KEEP_CANDIDATE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    REMAP_INVALID = "REMAP_INVALID"


_STATUS_EXCLUSION = {ReviewStatus.UNREVIEWED: ExclusionReason.UNREVIEWED,
                     ReviewStatus.UNRESOLVED: ExclusionReason.UNRESOLVED_REVIEW,
                     ReviewStatus.REJECTED: ExclusionReason.REJECTED,
                     ReviewStatus.KEEP_CANDIDATE: ExclusionReason.KEEP_CANDIDATE}


@dataclass(frozen=True)
class Exclusion:
    candidate_ref: CandidateRef
    reason: ExclusionReason
    detail: str

    def record(self) -> dict[str, Any]:
        return {"candidate_ref": self.candidate_ref.record(), "reason": self.reason.value,
                "detail": self.detail}


@dataclass(frozen=True)
class PublicationBundle:
    """Everything one publication commit adds, plus the head compare-and-set."""

    lineage_id: str
    expected_head: ArtifactPathRef | None
    new_head: ArtifactPathRef
    files: tuple[tuple[str, bytes], ...]

    @property
    def head_path(self) -> str:
        return head_path(self.lineage_id)

    def head_record(self) -> bytes:
        return canonical_json({"schema_version": HEAD_SCHEMA_VERSION,
                               "lineage_id": self.lineage_id,
                               "revision": self.new_head.record()})


@dataclass(frozen=True)
class PublishedRevision:
    revision: ArtifactPathRef
    review_log: ArtifactPathRef
    published_rule_set: ArtifactPathRef | None
    review_log_position: int
    document: Mapping[str, Any]
    exclusions: tuple[Exclusion, ...]
    files: tuple[tuple[str, bytes], ...]


class PublicationTarget(Protocol):
    """Repository-backed publication store with a per-lineage head record."""

    def head(self, lineage_id: str) -> ArtifactPathRef | None:
        ...

    def commit(self, bundle: PublicationBundle) -> None:
        """Atomically add the files and advance the head iff it equals expected_head."""
        ...


class DirectoryPublicationTarget:
    """A repository working tree: artifacts are created once, then the head moves by CAS.

    Creating the git commit from these files is the operator's step (P1.2D). A failed
    commit removes the files it created and leaves the head unchanged.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._lock = threading.Lock()

    def _path(self, relative: str) -> Path:
        target = (self.root / checked_relative_path(relative)).resolve()
        if not target.is_relative_to(self.root):
            raise PublicationError("artifact path escapes the repository root")
        return target

    def head(self, lineage_id: str) -> ArtifactPathRef | None:
        path = self._path(head_path(lineage_id))
        if not path.exists():
            return None
        record = json.loads(path.read_bytes())
        if (not isinstance(record, Mapping) or record.get("schema_version") != HEAD_SCHEMA_VERSION
                or record.get("lineage_id") != lineage_id):
            raise PublicationError("invalid lineage head record")
        return ArtifactPathRef.from_record(record.get("revision"))

    def commit(self, bundle: PublicationBundle) -> None:
        with self._lock:
            if self.head(bundle.lineage_id) != bundle.expected_head:
                raise StaleHead("lineage head moved; rebuild and review against the new parent")
            created: list[Path] = []
            try:
                for relative, data in bundle.files:
                    path = self._path(relative)
                    if path.exists():
                        if path.read_bytes() != data:
                            raise PublicationError(f"{relative} exists with other content")
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        created.append(path)
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                head = self._path(bundle.head_path)
                head.parent.mkdir(parents=True, exist_ok=True)
                staged = head.with_name(head.name + ".staged")
                staged.write_bytes(bundle.head_record())
                os.replace(staged, head)
            except BaseException:
                for path in created:
                    path.unlink(missing_ok=True)
                raise


@dataclass(frozen=True)
class _Entry:
    candidate: Candidate
    session: CompileSession
    validation: ValidationResult


@dataclass(frozen=True)
class _Approved:
    candidate: Candidate
    review: ReviewRecord
    validation: ValidationResult
    target: PlantTargetRef | None
    scope_refs: tuple[PlantTargetRef, ...]


def _scope_records(scope: Iterable[PlantTargetRef]) -> list[dict[str, Any]]:
    return [item.record() for item in scope]


def _traced(item: _Approved) -> dict[str, Any]:
    candidate, provenance = item.candidate, item.candidate.provenance
    return {"candidate_ref": candidate.ref.record(), "content_key": candidate.content_key,
            "extraction_kind": candidate.extraction_kind.value, "route": candidate.route.value,
            "decision": item.review.decision.value, "review_id": item.review.review_id,
            "scope_refs": _scope_records(item.scope_refs),
            "fragments": [fragment.record() for fragment in candidate.fragments()],
            "extraction_method": provenance.extraction_method,
            "grounding_method": provenance.grounding_method, "model": provenance.model,
            "confidence": None if provenance.confidence is None
            else provenance.confidence.record(),
            "validation": item.validation.record()}


class CompilerWorkspace:
    """Append-only, non-canonical compiler store (spec §Compiler workspace).

    In memory in P1.2A. It is not a ContextSourceRef kind and is never registered in a
    CanonicalContextRegistry, so consumers cannot resolve it. Candidate versions and
    ReviewRecords are immutable and the review log is append-only. One workspace lock
    serializes review appends and publication, from reading the log tail until the
    publication commit completes. These methods are Application API operations only.
    """

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = _pattern(workspace_id, _ID, "workspace_id")
        self._lock = threading.RLock()
        self._entries: dict[CandidateRef, _Entry] = {}
        self._reviews: list[ReviewRecord] = []

    # -- candidates --------------------------------------------------------------------
    def add_candidate(self, session: CompileSession, candidate: Candidate) -> ValidationResult:
        if not isinstance(session, CompileSession):
            raise TypeError("CompileSession required")
        if not isinstance(candidate, (CandidateBinding, CandidateKnowledge)):
            raise TypeError("CandidateBinding or CandidateKnowledge required")
        with self._lock:
            if candidate.session_id != session.session_id:
                raise WorkspaceAppendError("candidate belongs to another compile session")
            if candidate.ref in self._entries:
                raise WorkspaceAppendError("a candidate version is appended exactly once")
            validation = validate_candidate(session, candidate)
            self._entries[candidate.ref] = _Entry(candidate, session, validation)
            return validation

    def candidate(self, ref: CandidateRef) -> Candidate:
        return self._entry(ref).candidate

    def candidates(self) -> tuple[Candidate, ...]:
        with self._lock:
            return tuple(entry.candidate for entry in self._entries.values())

    def validation(self, ref: CandidateRef) -> ValidationResult:
        return self._entry(ref).validation

    def _entry(self, ref: CandidateRef) -> _Entry:
        with self._lock:
            entry = self._entries.get(ref)
        if entry is None:
            raise KeyError(ref)
        return entry

    # -- review log ----------------------------------------------------------------------
    @property
    def review_log_tail(self) -> int:
        with self._lock:
            return len(self._reviews)

    def review_log(self) -> tuple[ReviewRecord, ...]:
        with self._lock:
            return tuple(self._reviews)

    def append_review(self, candidate_ref: CandidateRef, decision: ReviewDecision, *,
                      review_id: str, principal: ReviewPrincipal, reason: str,
                      reviewed_at: str, supersedes: Sequence[str] = (),
                      selected_target: PlantTargetRef | None = None,
                      selected_scope_refs: Sequence[PlantTargetRef] | None = None,
                      visibility: Visibility = Visibility.AGENT) -> ReviewRecord:
        decision = ReviewDecision(decision)
        with self._lock:
            entry = self._entries.get(candidate_ref)
            if entry is None:
                raise WorkspaceAppendError("reviews bind an existing exact candidate version")
            session, candidate = entry.session, entry.candidate
            if not isinstance(principal, ReviewPrincipal):
                raise WorkspaceAppendError("an allowed decision principal is required")
            if (principal.kind is PrincipalKind.DETERMINISTIC_POLICY
                    and principal.identifier not in session.policies.acceptance_policies):
                raise WorkspaceAppendError("deterministic acceptance needs a registered policy")
            if any(item.review_id == review_id for item in self._reviews):
                raise WorkspaceAppendError("review ids are unique in the append-only log")
            earlier = {item.review_id: item for item in self._reviews}
            for review in supersedes:
                if review not in earlier or earlier[review].candidate_ref != candidate_ref:
                    raise WorkspaceAppendError("supersedes names only earlier records of the "
                                               "same candidate version")
            scope = None if selected_scope_refs is None else tuple(selected_scope_refs)
            if decision is ReviewDecision.REMAP:
                if scope is None:
                    raise WorkspaceAppendError("REMAP stores the full selected binding")
                if isinstance(candidate, CandidateBinding):
                    if selected_target is None:
                        raise WorkspaceAppendError("a binding REMAP selects a target")
                    original = (candidate.proposed_target, candidate.scope_refs)
                else:
                    if selected_target is not None:
                        raise WorkspaceAppendError("a knowledge REMAP changes scope refs only")
                    original = (None, candidate.scope_refs)
                if (selected_target, scope) == original:
                    raise WorkspaceAppendError("REMAP selects a different binding")
            record = ReviewRecord(
                review_id=review_id, candidate_ref=candidate_ref, decision=decision,
                principal=principal, reason=reason, reviewed_at=reviewed_at,
                review_policy=session.policies.review_policy,
                validation_policy=session.policies.validation_policy,
                session_id=session.session_id, plant_context=session.plant.source_ref,
                review_log_position=len(self._reviews) + 1, supersedes=tuple(supersedes),
                selected_target=selected_target, selected_scope_refs=scope,
                visibility=visibility)
            self._reviews.append(record)
            return record

    def effective_review(self, candidate_ref: CandidateRef, *,
                         position: int | None = None) -> EffectiveReview:
        """Current effective decision; an older ``position`` is audit replay only."""
        with self._lock:
            tail = len(self._reviews)
            position = tail if position is None else position
            if type(position) is not int or not 0 <= position <= tail:
                raise CompilerError("review log position outside the log")
            return effective_review(self._reviews, candidate_ref, position)

    def review_status(self, candidate_ref: CandidateRef, *,
                      position: int | None = None) -> ReviewStatus:
        return self.effective_review(candidate_ref, position=position).status

    # -- publication ---------------------------------------------------------------------
    def publish(self, session: CompileSession, request: PublicationRequest,
                target: PublicationTarget) -> PublishedRevision:
        """Publish the complete approved set; pinned to the review-log tail under lock."""
        if not isinstance(session, CompileSession) or not isinstance(request,
                                                                     PublicationRequest):
            raise TypeError("CompileSession and PublicationRequest required")
        with self._lock:
            position = len(self._reviews)
            records = tuple(self._reviews)
            if target.head(request.lineage_id) != request.expected_head:
                raise StaleHead("lineage head moved; rebuild and review against the new parent")
            self._check_dependency_closure(session)
            approved: list[_Approved] = []
            exclusions: list[Exclusion] = []
            for ref in request.candidate_refs:
                entry = self._entries.get(ref)
                if entry is None:
                    raise PublicationError("selection names an unknown candidate version")
                # Everything about a considered candidate (its exclusion record, its
                # review-log prefix) enters the revision, so a more restrictive input
                # cannot be excluded quietly: it would leak membership. Fail instead.
                taint = most_restrictive(entry.candidate.visibility, *(
                    item.visibility for item in records if item.candidate_ref == ref))
                if most_restrictive(taint, session.visibility) != session.visibility:
                    raise PublicationError("selection includes inputs more restrictive than "
                                           "the publication; compile an allowed-input one")
                outcome = self._decide(session, entry.candidate, records, position)
                (exclusions if isinstance(outcome, Exclusion) else approved).append(outcome)
            bundle, result = self._build(session, request, records, position, approved,
                                         tuple(exclusions))
            verify_publication(dict(bundle.files), result.revision)
            target.commit(bundle)
            return result

    @staticmethod
    def _check_dependency_closure(session: CompileSession) -> None:
        try:
            for ref in session.inventory:
                session.registry.resolve(ref.source_id, ProjectionScope.EVALUATOR)
            reloaded = load_rule_set_source(session.registry, session.input_rule_set_ref.source_id,
                                            ProjectionScope.EVALUATOR)
        except (ContextSourceError, RuleSetError) as exc:
            raise PublicationError(f"publication dependency failed to re-attest: {exc}") from exc
        if reloaded != session.input_rule_set:
            raise PublicationError("input rule set changed after the session froze it")

    @staticmethod
    def _decide(session: CompileSession, candidate: Candidate,
                records: tuple[ReviewRecord, ...], position: int) -> _Approved | Exclusion:
        ref = candidate.ref
        if candidate.session_id != session.session_id:
            return Exclusion(ref, ExclusionReason.STALE_SESSION,
                             "candidate was compiled under another source/plant/policy tuple")
        effective = effective_review(records, ref, position)
        if effective.record is None or effective.status in _STATUS_EXCLUSION:
            return Exclusion(ref, _STATUS_EXCLUSION[effective.status],
                             f"effective review status {effective.status.value}")
        review = effective.record
        if ((review.session_id, review.review_policy, review.validation_policy,
             review.plant_context) != (session.session_id, session.policies.review_policy,
                                       session.policies.validation_policy,
                                       session.plant.source_ref)):
            return Exclusion(ref, ExclusionReason.STALE_REVIEW,
                             "review was made against an incompatible tuple")
        remap = review.decision is ReviewDecision.REMAP
        validation = validate_candidate(session, candidate, review)
        if not validation.passed:
            codes = sorted({item.code.value for item in validation.findings if item.blocking})
            return Exclusion(ref, ExclusionReason.REMAP_INVALID if remap
                             else ExclusionReason.VALIDATION_FAILED, ",".join(codes))
        return _Approved(candidate, review, validation, validation.selected_target,
                         validation.selected_scope_refs)

    def _build(self, session: CompileSession, request: PublicationRequest,
               records: tuple[ReviewRecord, ...], position: int, approved: list[_Approved],
               exclusions: tuple[Exclusion, ...]) -> tuple[PublicationBundle, PublishedRevision]:
        lineage = request.lineage_id
        bindings, knowledge, rules = [], [], []
        for item in approved:
            if item.candidate.route is Route.BINDING:
                bindings.append(item)
            elif item.candidate.route is Route.RULE:
                rules.append(item)
            else:
                knowledge.append(item)
        files: list[tuple[str, bytes]] = []
        published_rule_set: ArtifactPathRef | None = None
        pinned: list[RuleRef] = []
        if rules:
            try:
                r1 = session.input_rule_set.derive(
                    session.input_rule_set_ref, tuple(item.candidate.rule for item in rules),
                    visibility=session.visibility)
            except RuleSetError as exc:
                raise PublicationError(f"published rule set is invalid: {exc}") from exc
            pinned = sorted((item.candidate.rule.ref for item in rules),
                            key=lambda ref: (ref.rule_id, ref.version))
            if r1.parent != session.input_rule_set_ref:
                raise PublicationError("published rule set parent is not the input rule set")
            for ref in pinned:
                try:
                    r1.registry.get_rule(ref)
                except KeyError:
                    raise PublicationError("a pinned RuleRef does not resolve") from None
            data = r1.serialize()
            published_rule_set = ArtifactPathRef(f"{RULE_SET_ROOT}/{r1.checksum}.json",
                                                 r1.checksum)
            files.append((published_rule_set.path, data))
        considered = set(request.candidate_refs)
        log_bytes = canonical_json({
            "schema_version": REVIEW_LOG_SCHEMA_VERSION, "workspace_id": self.workspace_id,
            "review_log_position": position,
            "records": [item.record() for item in records if item.candidate_ref in considered]})
        log_ref = ArtifactPathRef.of(
            f"{ENGINEERING_CONTEXT_ROOT}/{lineage}/review-logs/"
            f"{content_checksum(log_bytes, CANONICAL_JSON_SHA256)}.json", log_bytes)
        files.append((log_ref.path, log_bytes))
        conflicts = [{"candidate_ref": item.candidate.ref.record(),
                      "conflicting_fragments": _fragment_ids(item.candidate.conflicting_fragments),
                      "conflicting_candidate_ids": list(getattr(
                          item.candidate, "conflicting_candidate_ids", ()))}
                     for item in approved if item.candidate.conflicting_fragments
                     or getattr(item.candidate, "conflicting_candidate_ids", ())]
        body = {
            "schema_version": REVISION_SCHEMA_VERSION,
            "kind": ENGINEERING_CONTEXT_REVISION_KIND, "lineage_id": lineage,
            "parent_revision": None if request.parent_revision is None
            else request.parent_revision.record(),
            "visibility": session.visibility.value, "compile_session_id": session.session_id,
            "plant_context": session.plant.source_ref.record(),
            "source_inventory": [ref.record() for ref in session.inventory],
            "compiler_version": session.policies.compiler_version,
            "routing_policy": ROUTING_POLICY,
            "validation_policy": session.policies.validation_policy,
            "review_policy": session.policies.review_policy,
            "extraction_methods": sorted({item.candidate.provenance.extraction_method
                                          for item in approved}),
            "grounding_methods": sorted({item.candidate.provenance.grounding_method
                                         for item in approved}),
            "approved_bindings": [
                {**_traced(item), "target": item.target.record(),
                 "proposed_target": None if item.candidate.proposed_target is None
                 else item.candidate.proposed_target.record(),
                 "mention_text": item.candidate.mention_text} for item in bindings],
            "approved_knowledge": [
                {**_traced(item),
                 "knowledge_ref": {
                     "knowledge_id": f"{item.candidate.candidate_id}@"
                                     f"{item.candidate.candidate_version}",
                     "validation_status": KNOWLEDGE_VALIDATION_STATUS,
                     "validation_evidence_review_id": item.review.review_id,
                     "content": [fragment.record()
                                 for fragment in item.candidate.source_fragments]},
                 "derived_annotations": [] if item.candidate.normalized_claim is None
                 else [item.candidate.normalized_claim.record()],
                 "conditions": item.candidate.conditions,
                 "values": [value.record() for value in item.candidate.values]}
                for item in knowledge],
            "input_rule_set": session.input_rule_set_ref.record(),
            "published_rule_set": {"revision": "INPUT"} if published_rule_set is None
            else {"revision": "SAME_COMMIT", **published_rule_set.record()},
            "pinned_rule_refs": [{"rule_id": ref.rule_id, "version": ref.version}
                                 for ref in pinned],
            "rule_scope_bindings": [
                {**_traced(item), "rule_ref": {"rule_id": item.candidate.rule.rule_id,
                                               "version": item.candidate.rule.version},
                 "rule_scope": item.candidate.rule.scope} for item in rules],
            "review_log": {"review_log_position": position, **log_ref.record()},
            "review_ids": sorted(item.review.review_id for item in approved),
            "conflicts": conflicts,
            "exclusions": [item.record() for item in exclusions],
            "provenance": {"published_by": request.published_by,
                           "published_at": request.published_at,
                           "workspace_id": self.workspace_id},
        }
        document = {**body, "revision_id": "ecr-" + json_digest(body)}
        data = canonical_json(document)
        revision = ArtifactPathRef.of(
            f"{ENGINEERING_CONTEXT_ROOT}/{lineage}/revisions/{document['revision_id']}.json",
            data)
        files.append((revision.path, data))
        bundle = PublicationBundle(lineage, request.expected_head, revision, tuple(files))
        return bundle, PublishedRevision(revision, log_ref, published_rule_set, position,
                                         freeze_json(document), exclusions, tuple(files))


# -- verification from repository content alone ------------------------------------------
@dataclass(frozen=True)
class VerifiedRevision:
    document: Mapping[str, Any]
    review_log_position: int
    effective_reviews: tuple[EffectiveReview, ...]
    published_rule_set: RuleSetRevision | None


def _artifact(files: Mapping[str, bytes], ref: ArtifactPathRef) -> Any:
    data = files.get(ref.path)
    if data is None:
        raise PublicationError(f"{ref.path} is missing from the publication")
    try:
        actual = content_checksum(data, CANONICAL_JSON_SHA256)
    except (TypeError, ValueError) as exc:
        raise PublicationError(f"{ref.path} is not JSON") from exc
    if actual != ref.content_checksum:
        raise PublicationError(f"{ref.path} content checksum mismatch")
    return json.loads(data)


def verify_publication(files: Mapping[str, bytes], revision: ArtifactPathRef
                       ) -> VerifiedRevision:
    """Recompute every effective decision and rule-set link from committed content."""
    document = _artifact(files, revision)
    try:
        body = {key: value for key, value in document.items() if key != "revision_id"}
        if (document["schema_version"] != REVISION_SCHEMA_VERSION
                or document["kind"] != ENGINEERING_CONTEXT_REVISION_KIND
                or document["revision_id"] != "ecr-" + json_digest(body)):
            raise PublicationError("revision schema, kind or id is invalid")
        log_meta = dict(document["review_log"])
        position = log_meta.pop("review_log_position")
        log = _artifact(files, ArtifactPathRef.from_record(log_meta))
        if log["schema_version"] != REVIEW_LOG_SCHEMA_VERSION or log[
                "review_log_position"] != position:
            raise PublicationError("review log does not match the pinned position")
        records = tuple(ReviewRecord.from_record(item) for item in log["records"])
        positions = [item.review_log_position for item in records]
        if positions != sorted(set(positions)) or any(item > position for item in positions):
            raise PublicationError("review log prefix is out of order or past its position")
        effective = []
        items = (*document["approved_bindings"], *document["approved_knowledge"],
                 *document["rule_scope_bindings"])
        for item in items:
            result = effective_review(records, CandidateRef.from_record(item["candidate_ref"]),
                                      position)
            review = result.record
            if (result.status not in (ReviewStatus.APPROVED, ReviewStatus.REMAPPED)
                    or review.review_id != item["review_id"]
                    or review.session_id != document["compile_session_id"]):
                raise PublicationError("approved item lacks an effective APPROVE/REMAP")
            if review.decision is ReviewDecision.REMAP:
                if item["scope_refs"] != _scope_records(review.selected_scope_refs) or (
                        "target" in item and item["target"] != review.selected_target.record()):
                    raise PublicationError("published REMAP binding differs from its review")
            effective.append(result)
        pinned = [RuleRef(**ref) for ref in document["pinned_rule_refs"]]
        bound = [RuleRef(**item["rule_ref"]) for item in document["rule_scope_bindings"]]
        if sorted(bound, key=lambda ref: (ref.rule_id, ref.version)) != pinned:
            raise PublicationError("rule scope bindings and pinned RuleRefs differ")
        published = dict(document["published_rule_set"])
        rule_set = None
        if published.pop("revision") == "SAME_COMMIT":
            location = ArtifactPathRef.from_record(published)
            rule_set = load_rule_set(files[location.path], location.content_checksum)
            if rule_set.parent is None or rule_set.parent.record() != document["input_rule_set"]:
                raise PublicationError("published rule set parent is not the input rule set")
            for ref in pinned:
                rule = rule_set.registry.get_rule(ref)
                if (rule.origin, rule.validation, rule.authority) != (
                        Origin.LITERATURE, Validation.NONE, Authority.REFERENCE):
                    raise PublicationError("a compiler-published rule changed its axes")
        elif pinned or published:
            raise PublicationError("RuleRefs need a published rule-set revision")
    except PublicationError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PublicationError(f"publication does not verify: {exc!r}") from exc
    return VerifiedRevision(freeze_json(document), position, tuple(effective), rule_set)
