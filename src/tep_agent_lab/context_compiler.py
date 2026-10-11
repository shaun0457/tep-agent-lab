"""Context Compiler v0 contracts (docs/specs/context-compiler-v0.md, milestone P1.2A).

Candidate Context and canonical context are different trust domains::

    exact ContextSourceRef (CompileSession: its own frozen CanonicalContextRegistry)
      -> SourceFragmentRef (exact location + content checksum)
      -> CandidateBinding / CandidateKnowledge (immutable, versioned candidates)
      -> CompilerWorkspace: append-only, non-canonical, one visibility; validation
      -> ReviewRecord log (HUMAN or a registered DETERMINISTIC_POLICY principal only)
      -> CompilerWorkspace.stage_publication: a validated StagedPublication bundle
         (EngineeringContextRevision, review-log prefix, candidate records, R1, head)

P1.2A stops at staging. A ``StagedPublication`` is NOT canonical and advances no head:
the repository transaction (one git commit, serialized writers, ref/head
compare-and-set, crash safety) is P1.2D. Review and publication are Application API
operations (ADR-002): nothing here is an Agent tool, a ToolSpec or part of a
ContextProjection. Confidence is review-routing metadata only. Until P1.3, plant
identities are the exact AGENT-visible ``PROCESS_GRAPH`` source selected for the
session (ADR-004 Decision 4). Rule-routed items reuse ``Rule``/``RuleRegistry`` through
``literature_candidate`` carriers and keep LITERATURE/NONE/REFERENCE. The module is
generic: it never imports tep_sim; callers supply the ProcessGraph loader.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import hashlib
import math
import re
import threading
from typing import Any, NoReturn

from industrial_agent_runtime import Visibility
from industrial_agent_runtime.serialization import freeze_json

from .canonical_context import (
    BYTES_SHA256, CANONICAL_JSON_SHA256, CanonicalContextRegistry, ContextSourceError,
    ContextSourceRef, ProjectionScope, SourceMaterializer, UnknownContextSource,
    checked_relative_path, content_checksum, most_restrictive,
)
from .persistence import canonical_json, strict_json_loads
from .persistence import content_checksum as json_digest
from .rule_sets import (
    RULE_SET_KIND, RuleSetError, RuleSetRevision, load_rule_set, load_rule_set_source,
    rule_from_record, rule_record,
)
from .rules import Authority, Origin, Rule, RuleRef, SourceVersion, Validation

COMPILER_CONTRACT_VERSION = "tep-agent-lab.context-compiler/v0"
ROUTING_POLICY = "tep-agent-lab.context-compiler.routing/v0"
VALIDATION_POLICY = "tep-agent-lab.context-compiler.validation/v0"
PROCESS_GRAPH_KIND = "PROCESS_GRAPH"
ENGINEERING_CONTEXT_REVISION_KIND = "ENGINEERING_CONTEXT_REVISION"
REVISION_SCHEMA_VERSION = "tep-agent-lab.engineering-context-revision/v0"
REVIEW_LOG_SCHEMA_VERSION = "tep-agent-lab.compiler-review-log/v0"
CANDIDATES_SCHEMA_VERSION = "tep-agent-lab.compiler-candidates/v0"
HEAD_SCHEMA_VERSION = "tep-agent-lab.engineering-context-head/v0"
ENGINEERING_CONTEXT_ROOT = "context/engineering-context"
RULE_SET_ROOT = "context/rule-sets"
KNOWLEDGE_VALIDATION_STATUS = "VERIFIED"  # scope/extraction fidelity only; no truth/authority

_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_LINEAGE = re.compile(r"(?=.{1,64}\Z)[a-z0-9]+(?:[._-][a-z0-9]+)*")
_VERSIONED = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*@[A-Za-z0-9][A-Za-z0-9._+-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KIND = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_MAX_EXACT_INT = 2 ** 53
_JSON_ERRORS = (TypeError, ValueError, RecursionError)


class CompilerError(ValueError):
    """A compiler contract was violated at the trusted application boundary."""


class WorkspaceAppendError(CompilerError):
    """An append-only, immutability or visibility rule of the workspace was violated."""


class PublicationError(CompilerError):
    """Revision-level failure: nothing is staged and no partial revision exists."""


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


def _enum(kind: type[StrEnum], value: Any, name: str) -> Any:
    try:
        return kind(value)
    except (TypeError, ValueError):
        raise CompilerError(f"invalid {name}") from None


def _visibility(value: Any) -> Visibility:
    return _enum(Visibility, value, "visibility")


def _utc(value: Any, name: str) -> str:
    _pattern(value, _UTC, f"{name} (UTC ISO-8601 ending in Z)")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise CompilerError(f"invalid {name}") from None
    return value


def _lineage(value: Any) -> str:
    _pattern(value, _LINEAGE, "lineage_id")
    try:
        checked_relative_path(value)
    except ValueError:
        raise CompilerError("invalid lineage_id") from None
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


def _number(value: Any, name: str) -> float:
    """One numeric rule: finite numbers become floats; ints beyond 2**53 are rejected."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompilerError(f"{name} must be a number")
    if isinstance(value, int) and abs(value) > _MAX_EXACT_INT:
        raise CompilerError(f"{name} exceeds the exactly representable range")
    number = float(value)
    if not math.isfinite(number):
        raise CompilerError(f"{name} must be finite")
    return number


def _json(data: bytes, what: str) -> Any:
    try:
        return strict_json_loads(data)
    except _JSON_ERRORS as exc:
        raise CompilerError(f"{what} is not strict JSON") from exc


# -- source fragments -----------------------------------------------------------------
class LocationKind(StrEnum):
    BYTE_RANGE = "BYTE_RANGE"      # exact bytes [start, end); BYTES_SHA256 sources only
    JSON_POINTER = "JSON_POINTER"  # RFC 6901 subtree of a JSON source, canonical JSON


@dataclass(frozen=True)
class FragmentLocation:
    kind: LocationKind
    start: int | None = None
    end: int | None = None
    pointer: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(LocationKind, self.kind, "location kind"))
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
        if not isinstance(content, bytes):
            raise CompilerError("fragment content must be bytes")
        if self.kind is LocationKind.BYTE_RANGE:
            start, end = self.start, self.end
            if start is None or end is None or end > len(content):
                raise CompilerError("fragment range exceeds the source")
            return content[start:end]
        node = _json(content, "JSON_POINTER source")
        for token in (self.pointer or "").split("/")[1:]:
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and token in node:
                node = node[token]
            elif (isinstance(node, list) and re.fullmatch(r"0|[1-9][0-9]{0,9}", token)
                  and int(token) < len(node)):
                node = node[int(token)]
            else:
                raise CompilerError("fragment pointer does not resolve")
        try:
            return canonical_json(node)
        except _JSON_ERRORS as exc:
            raise CompilerError("fragment subtree is not canonical JSON") from exc

    def record(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "start": self.start, "end": self.end,
                "pointer": self.pointer}


def _location_fits(location: FragmentLocation, source: ContextSourceRef) -> bool:
    """Byte offsets are exact only over exact bytes; a canonical-JSON checksum is line-
    ending independent, so a CRLF checkout would silently shift BYTE_RANGE offsets."""
    return location.kind is not LocationKind.BYTE_RANGE or source.checksum_method == BYTES_SHA256


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
        if not _location_fits(self.location, self.source_ref):
            raise CompilerError("BYTE_RANGE fragments need a BYTES_SHA256 source")
        _pattern(self.content_checksum, _SHA256, "fragment content_checksum")
        _pattern(self.fragment_kind, _KIND, "fragment_kind")
        _pattern(self.fragmentation_policy, _VERSIONED, "fragmentation_policy")
        _optional_text(self.label, "label")
        object.__setattr__(self, "fragment_id", "frag-" + json_digest(self._identity()))

    def __hash__(self) -> int:  # ContextSourceRef holds unhashable frozen mappings
        return hash(self.fragment_id)

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
        if not isinstance(location, FragmentLocation):
            raise CompilerError("location must be a FragmentLocation")
        return cls(source_ref=source_ref, location=location,
                   content_checksum=_sha256(location.extract(content)),
                   fragment_kind=fragment_kind, fragmentation_policy=fragmentation_policy,
                   label=label)

    @property
    def visibility(self) -> Visibility:
        return self.source_ref.visibility

    def verify(self, content: bytes) -> bool:
        if not _location_fits(self.location, self.source_ref):
            return False
        try:
            return _sha256(self.location.extract(content)) == self.content_checksum
        except CompilerError:
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


def _context_source(record: Any) -> ContextSourceRef:
    try:
        return ContextSourceRef(**dict(record))
    except (TypeError, ValueError) as exc:
        raise CompilerError(f"invalid ContextSourceRef record: {exc}") from exc


@dataclass(frozen=True)
class PlantTargetRef:
    """``(selected PROCESS_GRAPH source ref, ProcessGraph id)``; P1.3 preserves the ids."""

    plant_context: ContextSourceRef
    kind: TargetKind
    target_id: str

    def __post_init__(self) -> None:
        if _source_ref(self.plant_context, "plant_context").kind != PROCESS_GRAPH_KIND:
            raise CompilerError("plant targets resolve against a PROCESS_GRAPH source")
        object.__setattr__(self, "kind", _enum(TargetKind, self.kind, "target kind"))
        _text(self.target_id, "target_id")

    def __hash__(self) -> int:  # consistent with field equality
        return hash((self.plant_context.source_id, self.plant_context.content_checksum,
                     self.kind, self.target_id))

    def record(self) -> dict[str, Any]:
        return {"plant_context": self.plant_context.record(), "kind": self.kind.value,
                "target_id": self.target_id}

    @classmethod
    def from_record(cls, record: Any) -> "PlantTargetRef":
        if not isinstance(record, Mapping) or set(record) != {"plant_context", "kind",
                                                               "target_id"}:
            raise CompilerError("PlantTargetRef record must have exactly the v0 fields")
        return cls(_context_source(record["plant_context"]), record["kind"],
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

    def __hash__(self) -> int:
        return hash((self.source_ref.content_checksum, self.entities, self.signals,
                     self.relationships))

    @classmethod
    def from_process_graph(cls, source_ref: ContextSourceRef, graph: Any
                           ) -> "PlantContextIndex":
        return cls(source_ref, frozenset(node.node_id for node in graph.nodes()),
                   frozenset(binding.semantic_entity_id for binding in graph.bindings()),
                   frozenset(edge.edge_id for edge in graph.edges()))

    def _ids(self, kind: TargetKind) -> frozenset[str]:
        return {TargetKind.ENTITY: self.entities, TargetKind.SIGNAL: self.signals,
                TargetKind.RELATIONSHIP: self.relationships}[kind]

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
    except (TypeError, ValueError):
        raise CompilerError("unsupported extraction kind stays unclassified") from None


# -- provenance -----------------------------------------------------------------------
@dataclass(frozen=True)
class Confidence:
    """Review-routing metadata only: not truth, authority, evidence or permission."""

    score: float
    method: str

    def __post_init__(self) -> None:
        score = _number(self.score, "confidence score")
        if not 0 <= score <= 1:
            raise CompilerError("confidence score must be in [0, 1]")
        object.__setattr__(self, "score", score)
        _pattern(self.method, _VERSIONED, "confidence method")

    def record(self) -> dict[str, Any]:
        return {"score": self.score, "method": self.method}


@dataclass(frozen=True)
class DerivedInput:
    """A non-source influencing input (e.g. a recorded model call, P1.2C) by exact ref.

    ``content_checksum`` is the SHA-256 of the exact recorded artifact; staging must
    resolve it, and an input that no resolver can produce blocks publication.
    """

    ref: str
    role: str
    visibility: Visibility
    content_checksum: str

    def __post_init__(self) -> None:
        _text(self.ref, "derived input ref")
        _text(self.role, "derived input role")
        object.__setattr__(self, "visibility", _visibility(self.visibility))
        _pattern(self.content_checksum, _SHA256, "derived input content_checksum")

    def record(self) -> dict[str, Any]:
        return {"ref": self.ref, "role": self.role, "visibility": self.visibility.value,
                "content_checksum": self.content_checksum}


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
    """Numbers are normalized to float (see ``_number``); text values stay text."""

    name: str
    value: float | str
    unit: str | None = None

    def __post_init__(self) -> None:
        _text(self.name, "value name")
        if not isinstance(self.value, str):
            object.__setattr__(self, "value", _number(self.value, "typed value"))
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

    def __hash__(self) -> int:  # equal provenances share these fields
        return hash((self.extraction_method, self.grounding_method, self.model,
                     self.derived_inputs, self.confidence))

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
    def from_record(cls, record: Any) -> "CandidateRef":
        if not isinstance(record, Mapping) or set(record) != {"candidate_id",
                                                               "candidate_version"}:
            raise CompilerError("CandidateRef record must have exactly the v0 fields")
        return cls(record["candidate_id"], record["candidate_version"])


_CANDIDATE_RECORD_KEYS = frozenset({"candidate_type", "candidate_id", "session_id",
                                    "content", "content_key", "version_extra", "provenance",
                                    "candidate_version", "fragments", "visibility"})


def _version_of(candidate_id: str, session_id: str, content_key: str,
                provenance: Any, version_extra: Mapping[str, Any]) -> str:
    return json_digest({"content_key": content_key, "candidate_id": candidate_id,
                        "session_id": session_id, "provenance": provenance, **version_extra})


class _Candidate:
    candidate_id: str
    session_id: str
    extraction_kind: ExtractionKind
    provenance: DerivationProvenance
    scope_refs: tuple[PlantTargetRef, ...]
    content_key: str
    candidate_version: str
    _TYPE = ""

    def _common(self, routes: frozenset[Route]) -> None:
        _pattern(self.candidate_id, _ID, "candidate_id")
        _text(self.session_id, "session_id")
        object.__setattr__(self, "extraction_kind", _enum(
            ExtractionKind, self.extraction_kind, "extraction kind (unclassified)"))
        if route(self.extraction_kind) not in routes:
            raise CompilerError("extraction kind is routed to a different candidate type")
        if not isinstance(self.provenance, DerivationProvenance):
            raise CompilerError("provenance must be a DerivationProvenance")
        _typed_tuple(self.scope_refs, PlantTargetRef, "scope_refs")

    def _identify(self) -> None:
        contexts = [target.plant_context for target in self.targets()]
        if any(context != contexts[0] for context in contexts):
            raise CompilerError("a candidate resolves against exactly one plant context")
        key = json_digest(self._content())
        object.__setattr__(self, "content_key", key)
        object.__setattr__(self, "candidate_version", _version_of(
            self.candidate_id, self.session_id, key, self.provenance.record(),
            self._version_extra()))

    def __hash__(self) -> int:
        return hash(self.candidate_version)

    def _content(self) -> dict[str, Any]:
        raise NotImplementedError

    def _version_extra(self) -> dict[str, Any]:
        return {}

    def fragments(self) -> tuple[SourceFragmentRef, ...]:
        raise NotImplementedError

    def targets(self) -> tuple[PlantTargetRef, ...]:
        raise NotImplementedError

    @property
    def ref(self) -> CandidateRef:
        return CandidateRef(self.candidate_id, self.candidate_version)

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

    def record(self) -> dict[str, Any]:
        """The complete immutable candidate and derivation record (hash-recomputable)."""
        return {"candidate_type": self._TYPE, "candidate_id": self.candidate_id,
                "session_id": self.session_id, "content": self._content(),
                "content_key": self.content_key, "version_extra": self._version_extra(),
                "provenance": self.provenance.record(),
                "candidate_version": self.candidate_version,
                "fragments": [item.record() for item in self.fragments()],
                "visibility": self.visibility.value}


def _fragment_ids(fragments: Iterable[SourceFragmentRef]) -> list[str]:
    return [item.fragment_id for item in fragments]


@dataclass(frozen=True, kw_only=True, eq=True)
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
    _TYPE = "BINDING"
    __hash__ = _Candidate.__hash__

    def __post_init__(self) -> None:
        self._common(frozenset({Route.BINDING}))
        if not isinstance(self.mention_fragment, SourceFragmentRef):
            raise CompilerError("mention_fragment must be a SourceFragmentRef")
        _text(self.mention_text, "mention_text")
        _optional_text(self.normalized_mention, "normalized_mention")
        object.__setattr__(self, "target_kind", _enum(TargetKind, self.target_kind,
                                                      "target kind"))
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
        self._identify()

    def _content(self) -> dict[str, Any]:
        return {"route": Route.BINDING.value, "extraction_kind": self.extraction_kind.value,
                "mention_fragment": self.mention_fragment.fragment_id,
                "supporting_fragments": _fragment_ids(self.supporting_fragments),
                "conflicting_fragments": _fragment_ids(self.conflicting_fragments),
                "mention_text": self.mention_text,
                "normalized_mention": self.normalized_mention,
                "target_kind": self.target_kind.value,
                "proposed_target": None if self.proposed_target is None
                else self.proposed_target.record(),
                "scope_refs": [item.record() for item in self.scope_refs]}

    def _version_extra(self) -> dict[str, Any]:
        return {"alternative_targets": [item.record() for item in self.alternative_targets]}

    def fragments(self) -> tuple[SourceFragmentRef, ...]:
        return (self.mention_fragment, *self.supporting_fragments, *self.conflicting_fragments)

    def targets(self) -> tuple[PlantTargetRef, ...]:
        proposed = () if self.proposed_target is None else (self.proposed_target,)
        return (*proposed, *self.alternative_targets, *self.scope_refs)


@dataclass(frozen=True, kw_only=True, eq=True)
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
    _TYPE = "KNOWLEDGE"
    __hash__ = _Candidate.__hash__

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
        self._identify()

    def _content(self) -> dict[str, Any]:
        return {"route": self.route.value, "extraction_kind": self.extraction_kind.value,
                "source_fragments": _fragment_ids(self.source_fragments),
                "conflicting_fragments": _fragment_ids(self.conflicting_fragments),
                "conflicting_candidate_ids": list(self.conflicting_candidate_ids),
                "scope_refs": [item.record() for item in self.scope_refs],
                "rule": None if self.rule is None else rule_record(self.rule),
                "normalized_claim": None if self.normalized_claim is None
                else self.normalized_claim.record(),
                "conditions": self.conditions,
                "values": [item.record() for item in self.values]}

    def fragments(self) -> tuple[SourceFragmentRef, ...]:
        return (*self.source_fragments, *self.conflicting_fragments)

    def targets(self) -> tuple[PlantTargetRef, ...]:
        return self.scope_refs


Candidate = CandidateBinding | CandidateKnowledge


def _conflicting_ids(candidate: Candidate) -> tuple[str, ...]:
    return candidate.conflicting_candidate_ids if isinstance(candidate,
                                                             CandidateKnowledge) else ()


def _fragment_from_record(record: Any) -> SourceFragmentRef:
    record = dict(record)
    location = dict(record["location"])
    fragment = SourceFragmentRef(
        source_ref=_context_source(record["source_ref"]),
        location=FragmentLocation(location["kind"], location["start"], location["end"],
                                  location["pointer"]),
        content_checksum=record["content_checksum"], fragment_kind=record["fragment_kind"],
        fragmentation_policy=record["fragmentation_policy"], label=record["label"])
    if fragment.record() != record:
        raise CompilerError("fragment record does not reconstruct exactly")
    return fragment


def _provenance_from_record(record: Any) -> DerivationProvenance:
    confidence = record["confidence"]
    return DerivationProvenance(
        extraction_method=record["extraction_method"],
        grounding_method=record["grounding_method"], routing_policy=record["routing_policy"],
        model=record["model"],
        influencing_sources=tuple(_context_source(item)
                                  for item in record["influencing_sources"]),
        derived_inputs=tuple(DerivedInput(item["ref"], item["role"], item["visibility"],
                                          item["content_checksum"])
                             for item in record["derived_inputs"]),
        confidence=None if confidence is None
        else Confidence(confidence["score"], confidence["method"]))


def candidate_from_record(record: Any) -> "Candidate":
    """Rebuild a candidate from its complete record; it must re-serialize identically."""
    if not isinstance(record, Mapping) or set(record) != _CANDIDATE_RECORD_KEYS:
        raise CompilerError("candidate record must have exactly the v0 fields")
    fragments = {item["fragment_id"]: _fragment_from_record(item)
                 for item in record["fragments"]}
    content, provenance = record["content"], _provenance_from_record(record["provenance"])

    def pick(ids: Iterable[str]) -> tuple[SourceFragmentRef, ...]:
        return tuple(fragments[item] for item in ids)

    def targets(items: Iterable[Any]) -> tuple[PlantTargetRef, ...]:
        return tuple(PlantTargetRef.from_record(item) for item in items)

    candidate: Candidate
    if record["candidate_type"] == "BINDING":
        candidate = CandidateBinding(
            candidate_id=record["candidate_id"], session_id=record["session_id"],
            extraction_kind=content["extraction_kind"],
            mention_fragment=fragments[content["mention_fragment"]],
            mention_text=content["mention_text"], target_kind=content["target_kind"],
            proposed_target=None if content["proposed_target"] is None
            else PlantTargetRef.from_record(content["proposed_target"]),
            scope_refs=targets(content["scope_refs"]), provenance=provenance,
            normalized_mention=content["normalized_mention"],
            alternative_targets=targets(record["version_extra"]["alternative_targets"]),
            supporting_fragments=pick(content["supporting_fragments"]),
            conflicting_fragments=pick(content["conflicting_fragments"]))
    elif record["candidate_type"] == "KNOWLEDGE":
        claim = content["normalized_claim"]
        candidate = CandidateKnowledge(
            candidate_id=record["candidate_id"], session_id=record["session_id"],
            extraction_kind=content["extraction_kind"],
            source_fragments=pick(content["source_fragments"]),
            scope_refs=targets(content["scope_refs"]), provenance=provenance,
            rule=None if content["rule"] is None else rule_from_record(content["rule"]),
            normalized_claim=None if claim is None
            else DerivedAnnotation(claim["text"], claim["generator"]),
            conditions=content["conditions"],
            values=tuple(TypedValue(item["name"], item["value"], item["unit"])
                         for item in content["values"]),
            conflicting_fragments=pick(content["conflicting_fragments"]),
            conflicting_candidate_ids=tuple(content["conflicting_candidate_ids"]))
    else:
        raise CompilerError("unknown candidate type")
    if canonical_json(candidate.record()) != canonical_json(record):
        raise CompilerError("candidate record does not reconstruct exactly")
    return candidate


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
    """Frozen inputs of one compilation; ``prepare`` registers the sources first.

    Every invariant is checked here, so direct construction cannot skip them. The
    session reads its own frozen CanonicalContextRegistry over exactly the attested
    inputs; it never reuses or mutates a run's READY registry.
    """

    registry: CanonicalContextRegistry
    process_graph_source_id: str
    input_rule_set_source_id: str
    plant_graph_loader: Callable[[Any], Any]
    policies: CompilerPolicies
    visibility: Visibility = Visibility.AGENT
    # Resolves the input rule set's ancestors by exact ref (content checksum); a parent
    # that cannot be resolved fails closed.
    rule_set_parent_resolver: Callable[[ContextSourceRef], bytes] | None = None
    inventory: tuple[ContextSourceRef, ...] = field(init=False)
    plant: PlantContextIndex = field(init=False)
    input_rule_set_ref: ContextSourceRef = field(init=False)
    input_rule_set: RuleSetRevision = field(init=False)
    session_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.registry, CanonicalContextRegistry) or not self.registry.frozen:
            raise CompilerError("a compile session reads a frozen source inventory")
        if not isinstance(self.policies, CompilerPolicies):
            raise CompilerError("policies must be CompilerPolicies")
        if not callable(self.plant_graph_loader):
            raise CompilerError("a ProcessGraph loader is required")
        visibility = _visibility(self.visibility)
        object.__setattr__(self, "visibility", visibility)
        inventory = self.registry.inventory(ProjectionScope.EVALUATOR)
        if any(most_restrictive(ref.visibility, visibility) != visibility for ref in inventory):
            raise CompilerError("a compilation uses only inputs visible at its visibility")
        graphs = [ref for ref in inventory if ref.kind == PROCESS_GRAPH_KIND]
        if (len(graphs) != 1 or graphs[0].source_id != self.process_graph_source_id
                or graphs[0].visibility != Visibility.AGENT):
            raise CompilerError("select exactly one AGENT-visible PROCESS_GRAPH source")
        rule_sets = [ref for ref in inventory if ref.kind == RULE_SET_KIND]
        if len(rule_sets) != 1 or rule_sets[0].source_id != self.input_rule_set_source_id:
            raise CompilerError("select exactly one input ENGINEERING_RULE_SET source")
        try:
            input_rule_set = load_rule_set_source(
                self.registry, rule_sets[0].source_id, ProjectionScope.EVALUATOR,
                resolve_parent=self.rule_set_parent_resolver)
            graph_json = _json(self.registry.resolve(graphs[0].source_id,
                                                     ProjectionScope.EVALUATOR).content,
                               "PROCESS_GRAPH source")
        except (RuleSetError, ContextSourceError) as exc:
            raise CompilerError(f"compile input failed to load: {exc}") from exc
        try:
            plant = PlantContextIndex.from_process_graph(graphs[0],
                                                         self.plant_graph_loader(graph_json))
        except CompilerError:
            raise
        except Exception as exc:  # the caller-supplied loader owns its error types
            raise CompilerError(f"PROCESS_GRAPH source does not load: {exc}") from exc
        object.__setattr__(self, "inventory", inventory)
        object.__setattr__(self, "plant", plant)
        object.__setattr__(self, "input_rule_set_ref", rule_sets[0])
        object.__setattr__(self, "input_rule_set", input_rule_set)
        object.__setattr__(self, "session_id", "cs-" + json_digest({
            "inventory": [ref.record() for ref in inventory],
            "plant_context": graphs[0].record(), "input_rule_set": rule_sets[0].record(),
            "policies": self.policies.record(), "visibility": visibility.value}))

    @classmethod
    def prepare(cls, revisions: Mapping[str, str],
                materializers: Mapping[str, SourceMaterializer],
                sources: Sequence[ContextSourceRef], *, process_graph_source_id: str,
                input_rule_set_source_id: str, plant_graph_loader: Callable[[Any], Any],
                policies: CompilerPolicies,
                visibility: Visibility = Visibility.AGENT) -> "CompileSession":
        registry = CanonicalContextRegistry(revisions, materializers)
        try:
            for source in sources:
                registry.register(source)
        except ContextSourceError as exc:
            raise CompilerError(f"compile input is not an exact attested source: {exc}") from exc
        registry.freeze()
        return cls(registry, process_graph_source_id, input_rule_set_source_id,
                   plant_graph_loader, policies, visibility,
                   rule_set_parent_resolver=_materialized(materializers))

    def source_content(self, ref: ContextSourceRef) -> bytes | None:
        """Re-attested bytes of an exact inventory source; None for anything else."""
        try:
            if self.registry.ref(ref.source_id, ProjectionScope.EVALUATOR) != ref:
                return None
            return self.registry.resolve(ref.source_id, ProjectionScope.EVALUATOR).content
        except (UnknownContextSource, ContextSourceError):
            return None


def _materialized(materializers: Mapping[str, SourceMaterializer]
                 ) -> Callable[[ContextSourceRef], bytes]:
    """Read an exact ancestor artifact; load_rule_set then checks its content checksum."""
    def resolve(ref: ContextSourceRef) -> bytes:
        materializer = materializers.get(ref.repository)
        if materializer is None:
            raise LookupError(f"no materialization for {ref.repository}")
        return materializer.read_bytes(ref.path_or_ref)
    return resolve


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
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"    # non-blocking review-routing finding
    CONFLICT_DECLARED = "CONFLICT_DECLARED"  # non-blocking; conflicts stay explicit


_NON_BLOCKING = frozenset({FindingCode.AMBIGUOUS_TARGET, FindingCode.CONFLICT_DECLARED})


@dataclass(frozen=True)
class ValidationFinding:
    code: FindingCode
    detail: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _enum(FindingCode, self.code, "finding code"))
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
        _typed_tuple(self.selected_scope_refs, PlantTargetRef, "selected_scope_refs")
        object.__setattr__(self, "result_id", "vr-" + json_digest(self._body()))

    def __hash__(self) -> int:
        return hash(self.result_id)

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


def _source_findings(session: CompileSession, candidate: Candidate
                     ) -> list[ValidationFinding]:
    """Exact-source integrity of every fragment and influencing source of a candidate."""
    findings: list[ValidationFinding] = []
    contents: dict[str, bytes | None] = {}
    for fragment in candidate.fragments():
        # Cache by the complete source identity: a same-id ref with another revision,
        # path or checksum must be attested on its own and fail.
        key = json_digest(fragment.source_ref.record())
        if key not in contents:
            contents[key] = session.source_content(fragment.source_ref)
        content = contents[key]
        if content is None:
            findings.append(ValidationFinding(
                FindingCode.SOURCE_UNRESOLVED,
                f"{fragment.fragment_id}: source is not an exact attested session source"))
        elif not fragment.verify(content):
            findings.append(ValidationFinding(FindingCode.FRAGMENT_INVALID,
                                              f"{fragment.fragment_id}: location/checksum"))
    for ref in candidate.provenance.influencing_sources:
        if session.source_content(ref) is None:
            findings.append(ValidationFinding(FindingCode.INFLUENCE_UNKNOWN,
                                              f"{ref.source_id}: not a session source"))
    return findings


def validate_candidate(session: CompileSession, candidate: Candidate,
                       review: "ReviewRecord | None" = None, *,
                       known_candidate_ids: frozenset[str] | None = None) -> ValidationResult:
    """Deterministic validators; a REMAP review is revalidated against its selection.

    ``known_candidate_ids`` (given at staging) resolves declared conflicting candidates.
    """
    findings: list[ValidationFinding] = []

    def add(code: FindingCode, detail: str) -> None:
        findings.append(ValidationFinding(code, detail))

    if candidate.session_id != session.session_id:
        add(FindingCode.SESSION_MISMATCH, "candidate was compiled in another session")
    findings.extend(_source_findings(session, candidate))
    remap = review is not None and review.decision is ReviewDecision.REMAP
    scope = (review.selected_scope_refs or ()) if review is not None and remap \
        else candidate.scope_refs
    target = None
    if isinstance(candidate, CandidateBinding):
        target = review.selected_target if review is not None and remap \
            else candidate.proposed_target
        checks: list[tuple[str, PlantTargetRef | None]] = [("target", target)]
        checks += [("alternative", alt) for alt in candidate.alternative_targets]
        for label, item in checks:
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
        taint.append(review.effective_visibility)
    if most_restrictive(session.visibility, *taint) != session.visibility:
        add(FindingCode.VISIBILITY_WIDENED, "an influencing input is more restrictive than "
            "the compilation visibility")
    if isinstance(candidate, CandidateKnowledge) and candidate.rule is not None:
        citations = {item.citation() for item in candidate.source_fragments}
        if candidate.rule.extraction is None or candidate.rule.extraction.source not in citations:
            add(FindingCode.RULE_INVALID, "rule extraction must cite a source fragment")
        try:
            session.input_rule_set.registry.get_rule(candidate.rule.ref)
            add(FindingCode.RULE_VERSION_EXISTS, "an existing rule version is never edited")
        except KeyError:
            pass
    conflicting_ids = _conflicting_ids(candidate)
    if known_candidate_ids is not None:
        for conflict_id in conflicting_ids:
            if conflict_id not in known_candidate_ids:
                add(FindingCode.CONFLICT_UNRESOLVED,
                    f"conflicting candidate {conflict_id} unknown")
    if candidate.conflicting_fragments or conflicting_ids:
        add(FindingCode.CONFLICT_DECLARED, "conflicting sources/candidates are recorded")
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
        object.__setattr__(self, "kind", _enum(PrincipalKind, self.kind, "principal kind"))
        if self.kind is PrincipalKind.HUMAN:
            _text(self.identifier, "reviewer_id")
        else:
            _pattern(self.identifier, _VERSIONED, "policy_id@version")

    @classmethod
    def human(cls, reviewer_id: str) -> "ReviewPrincipal":
        return cls(PrincipalKind.HUMAN, reviewer_id)

    def allowed_by(self, acceptance_policies: Iterable[str]) -> bool:
        return (self.kind is PrincipalKind.HUMAN
                or self.identifier in set(acceptance_policies))

    def record(self) -> dict[str, str]:
        return {"kind": self.kind.value, "identifier": self.identifier}


_REVIEW_FIELDS = frozenset({
    "review_id", "candidate_ref", "decision", "principal", "reason", "reviewed_at",
    "review_policy", "validation_policy", "session_id", "plant_context",
    "review_log_position", "supersedes", "selected_target", "selected_scope_refs",
    "visibility", "previous_hash"})


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
    # Hash of the record at the previous log position (None at position 1): the log is
    # a hash chain whose tail hash a trusted anchor pins.
    previous_hash: str | None = None

    def __post_init__(self) -> None:
        _pattern(self.review_id, _ID, "review_id")
        if not isinstance(self.candidate_ref, CandidateRef):
            raise CompilerError("candidate_ref must be a CandidateRef")
        object.__setattr__(self, "decision", _enum(ReviewDecision, self.decision, "decision"))
        if not isinstance(self.principal, ReviewPrincipal):
            raise CompilerError("principal must be a ReviewPrincipal")
        for name in ("reason", "review_policy", "validation_policy", "session_id"):
            _text(getattr(self, name), name)
        _utc(self.reviewed_at, "reviewed_at")
        _source_ref(self.plant_context, "plant_context")
        if type(self.review_log_position) is not int or self.review_log_position < 1:
            raise CompilerError("review_log_position is a positive integer")
        _typed_tuple(self.supersedes, str, "supersedes")
        if len(set(self.supersedes)) != len(self.supersedes) or self.review_id in self.supersedes:
            raise CompilerError("supersedes names distinct earlier records")
        object.__setattr__(self, "visibility", _visibility(self.visibility))
        if self.decision is ReviewDecision.REMAP:
            _typed_tuple(self.selected_scope_refs, PlantTargetRef, "selected_scope_refs")
            if self.selected_target is not None and not isinstance(self.selected_target,
                                                                   PlantTargetRef):
                raise CompilerError("selected_target must be a PlantTargetRef")
        elif self.selected_target is not None or self.selected_scope_refs is not None:
            raise CompilerError("only REMAP stores a selected binding")
        if (self.review_log_position == 1) != (self.previous_hash is None):
            raise CompilerError("only the first log record has no previous hash")
        if self.previous_hash is not None:
            _pattern(self.previous_hash, _SHA256, "previous_hash")

    def __hash__(self) -> int:
        return hash((self.review_id, self.review_log_position, self.candidate_ref))

    @property
    def record_hash(self) -> str:
        return json_digest(self.record())

    @property
    def effective_visibility(self) -> Visibility:
        """Taint over every ref the record carries, not only its declared visibility."""
        targets = (*(() if self.selected_target is None else (self.selected_target,)),
                   *(self.selected_scope_refs or ()))
        return most_restrictive(self.visibility, self.plant_context.visibility,
                                *(item.plant_context.visibility for item in targets))

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
                "visibility": self.visibility.value, "previous_hash": self.previous_hash}

    @classmethod
    def from_record(cls, record: Any) -> "ReviewRecord":
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
            plant_context=_context_source(record["plant_context"]),
            review_log_position=record["review_log_position"],
            supersedes=tuple(record["supersedes"]),
            selected_target=None if record["selected_target"] is None
            else PlantTargetRef.from_record(record["selected_target"]),
            selected_scope_refs=None if scope is None
            else tuple(PlantTargetRef.from_record(item) for item in scope),
            visibility=record["visibility"], previous_hash=record["previous_hash"])


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


@dataclass(frozen=True)
class WorkspaceAnchor:
    """Trusted pin of a workspace's two append-only logs.

    ``review_log_position`` / ``review_tail_hash`` pin the hash-chained review log;
    ``candidate_count`` / ``candidate_tail_hash`` pin the hash chain of candidate
    appends (which candidate versions exist). The verifier never trusts a bundle's own
    copy of either log: the anchor comes from the caller (the workspace in P1.2A, the
    repository history in P1.2D).
    """

    workspace_id: str
    review_log_position: int
    review_tail_hash: str | None
    candidate_count: int
    candidate_tail_hash: str | None

    def __post_init__(self) -> None:
        _pattern(self.workspace_id, _ID, "anchor workspace_id")
        for count, tail in ((self.review_log_position, self.review_tail_hash),
                            (self.candidate_count, self.candidate_tail_hash)):
            if type(count) is not int or count < 0:
                raise CompilerError("anchor counts are nonnegative integers")
            if (count == 0) != (tail is None):
                raise CompilerError("only an empty log has no tail hash")
            if tail is not None:
                _pattern(tail, _SHA256, "anchor tail hash")


def _candidate_chain(refs: Sequence["CandidateRef"]) -> str | None:
    tail = None
    for ref in refs:
        tail = json_digest({"previous": tail, "candidate_ref": ref.record()})
    return tail


def _anchor_of(workspace_id: str, records: Sequence[ReviewRecord],
               candidate_log: Sequence["CandidateRef"]) -> WorkspaceAnchor:
    return WorkspaceAnchor(workspace_id, len(records),
                           records[-1].record_hash if records else None,
                           len(candidate_log), _candidate_chain(candidate_log))


# -- staged publication -------------------------------------------------------------------
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
        return cls(path, _sha256(data))


def head_path(lineage_id: str) -> str:
    return f"{ENGINEERING_CONTEXT_ROOT}/{_lineage(lineage_id)}/HEAD.json"


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
        _lineage(self.lineage_id)
        _typed_tuple(self.candidate_refs, CandidateRef, "candidate_refs")
        if len(set(self.candidate_refs)) != len(self.candidate_refs):
            raise CompilerError("candidate_refs must be distinct")
        _text(self.published_by, "published_by")
        _utc(self.published_at, "published_at")
        if self.parent_revision is not None:
            parent = _source_ref(self.parent_revision, "parent_revision")
            if (parent.kind != ENGINEERING_CONTEXT_REVISION_KIND
                    or parent.checksum_method != CANONICAL_JSON_SHA256):
                raise CompilerError("parent_revision is a committed revision ContextSourceRef")

    def __hash__(self) -> int:
        return hash((self.lineage_id, self.candidate_refs, self.published_at))

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
class StagedPublication:
    """A self-consistent publication bundle. It is NOT canonical and advances no head.

    Construction parses the revision strictly and checks that every metadata field
    (lineage, refs, position, parent/expected head, exclusions) agrees with it; the
    full dependency verification is ``verify_publication``.

    Committing it (one git commit that adds ``files`` and moves ``head_path`` from
    ``expected_head`` to ``revision`` by compare-and-set) is the P1.2D repository
    transaction. Until then nothing here is consumer-visible trusted context.
    """

    lineage_id: str
    expected_head: ArtifactPathRef | None
    revision: ArtifactPathRef
    review_log: ArtifactPathRef
    candidates: ArtifactPathRef
    published_rule_set: ArtifactPathRef | None
    review_log_position: int
    exclusions: tuple[Exclusion, ...]
    files: tuple[tuple[str, bytes], ...]
    document: Mapping[str, Any] = field(init=False)
    head_path: str = field(init=False)
    head_record: bytes = field(init=False)

    canonical = False  # staging only; see the class docstring

    def __post_init__(self) -> None:
        try:
            self._validate()
        except PublicationError:
            raise
        except (KeyError, TypeError, AttributeError, ValueError, RecursionError) as exc:
            raise PublicationError(f"malformed staged publication: {exc!r}") from exc

    def _validate(self) -> None:
        lineage = _lineage(self.lineage_id)
        allowed = (f"{ENGINEERING_CONTEXT_ROOT}/{lineage}/", f"{RULE_SET_ROOT}/")
        paths = [path for path, _ in self.files]
        if len(set(paths)) != len(paths):
            raise PublicationError("bundle paths must be distinct")
        files = dict(self.files)
        for path, data in self.files:
            try:
                checked_relative_path(path)
            except ValueError as exc:
                raise PublicationError(str(exc)) from exc
            if not path.startswith(allowed) or not isinstance(data, bytes):
                raise PublicationError(f"{path} is outside the publication roots")
        head = head_path(lineage)
        if head in files:
            raise PublicationError("the head record is not a bundle artifact")
        for ref in (self.revision, self.review_log, self.candidates, self.published_rule_set):
            if ref is not None and (ref.path not in files
                                    or _sha256(files[ref.path]) != ref.content_checksum):
                raise PublicationError(f"{ref.path} is missing or its checksum differs")
        document = _keys(_artifact(files, self.revision), _REVISION_KEYS, "revision")
        consistent = self._agrees(document, lineage)
        if not consistent:
            raise PublicationError("bundle metadata disagrees with its revision")
        object.__setattr__(self, "document", freeze_json(document))
        object.__setattr__(self, "head_path", head)
        object.__setattr__(self, "head_record", canonical_json({
            "schema_version": HEAD_SCHEMA_VERSION, "lineage_id": lineage,
            "expected_parent": None if self.expected_head is None
            else self.expected_head.record(),
            "revision": self.revision.record()}))

    def _agrees(self, document: Mapping[str, Any], lineage: str) -> bool:
        parent = document["parent_revision"]
        expected_parent = None if parent is None else ArtifactPathRef(
            parent["path_or_ref"], parent["content_checksum"])
        rule_set = ({"revision": "INPUT"} if self.published_rule_set is None
                    else {"revision": "SAME_COMMIT", **self.published_rule_set.record()})
        return not (document["lineage_id"] != lineage
                or {key: value for key, value in document["review_log"].items()
                    if key in ("review_log_position", "path", "content_checksum")} != {
                        "review_log_position": self.review_log_position,
                        **self.review_log.record()}
                or document["candidates"] != self.candidates.record()
                or document["published_rule_set"] != rule_set
                or expected_parent != self.expected_head
                or document["exclusions"] != [item.record() for item in self.exclusions])

    def __hash__(self) -> int:
        return hash(self.revision)


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
    candidate = item.candidate
    return {"candidate_ref": candidate.ref.record(), "content_key": candidate.content_key,
            "extraction_kind": candidate.extraction_kind.value, "route": candidate.route.value,
            "decision": item.review.decision.value, "review_id": item.review.review_id,
            "scope_refs": _scope_records(item.scope_refs),
            "original_scope_refs": _scope_records(candidate.scope_refs),
            "fragments": [fragment.record() for fragment in candidate.fragments()],
            "provenance": candidate.provenance.record(),
            "validation": item.validation.record()}


def _item_record(item: _Approved) -> dict[str, Any]:
    """The published record of one approved item; the verifier rebuilds it exactly."""
    candidate = item.candidate
    if candidate.route is Route.BINDING:
        return {**_traced(item),
                "target": None if item.target is None else item.target.record()}
    if not isinstance(candidate, CandidateKnowledge):
        raise PublicationError("knowledge routes need a CandidateKnowledge")
    if candidate.route is Route.RULE:
        if candidate.rule is None:
            raise PublicationError("RULE-routed item without a Rule carrier")
        return {**_traced(item),
                "rule_ref": {"rule_id": candidate.rule.rule_id,
                             "version": candidate.rule.version},
                "rule_scope": candidate.rule.scope}
    return {**_traced(item),
            "knowledge_ref": {
                "knowledge_id": f"{candidate.candidate_id}@{candidate.candidate_version}",
                "validation_status": KNOWLEDGE_VALIDATION_STATUS,
                "validation_evidence_review_id": item.review.review_id,
                "content": [fragment.record() for fragment in candidate.source_fragments]}}


def _conflict_summary(candidates: Iterable[Candidate]) -> list[dict[str, Any]]:
    """Explicit conflicts of approved items, in canonical candidate order."""
    return [{"candidate_ref": candidate.ref.record(),
             "conflicting_fragments": _fragment_ids(candidate.conflicting_fragments),
             "conflicting_candidate_ids": list(_conflicting_ids(candidate))}
            for candidate in sorted(candidates, key=lambda item: item.ref)
            if candidate.conflicting_fragments or _conflicting_ids(candidate)]


def _check_rule_set_visibility(rule_set: RuleSetRevision, visibility: Visibility) -> None:
    if most_restrictive(rule_set.visibility, visibility) != visibility:
        raise PublicationError("a referenced rule set is more restricted than the revision")


@dataclass(frozen=True)
class _Closure:
    input_rule_set: RuleSetRevision
    known_candidate_ids: frozenset[str]
    integrity_checked_refs: frozenset[CandidateRef]


def _dependency_refs(candidate_log: Sequence[Candidate], considered: Sequence[Candidate],
                     session_id: str) -> list[CandidateRef]:
    """Canonical partners: latest append per cited id in the publication session.

    Selection never chooses a partner version; other sessions never participate.
    Missing current-session partners remain unresolved conflicts.
    """
    referenced = {item for candidate in considered for item in _conflicting_ids(candidate)}
    latest: dict[str, CandidateRef] = {}
    for candidate in candidate_log:
        if candidate.candidate_id in referenced and candidate.session_id == session_id:
            latest[candidate.candidate_id] = candidate.ref
    return sorted(latest.values())


def _dependency_closure(session: CompileSession, *, considered: Sequence[Candidate],
                        dependencies: Sequence[Candidate], partners: Sequence[Candidate],
                        reviews: Sequence[ReviewRecord],
                        lineage: str, parent: ContextSourceRef | None,
                        visibility: Visibility,
                        derived_input_resolver: Callable[[DerivedInput], bytes] | None,
                        parent_resolver: Callable[[ContextSourceRef], bytes] | None
                        ) -> _Closure:
    """Shared revision-level integrity checks for staging and verification.

    Partner selection is already checked against ``_dependency_refs`` by the caller.
    All session sources, R0 lineage, parent, and carried visibility re-attest. Every
    canonical partner must pass exact source, fragment, influencing-source and
    derived-input integrity, whatever its decision. Failure aborts the revision per
    spec Publication atomicity. Resolved partner refs form a per-call cache so the
    approved loop does not resolve them again. Missing partners are not known ids.
    """
    try:
        for ref in session.inventory:
            session.registry.resolve(ref.source_id, ProjectionScope.EVALUATOR)
        input_rule_set = load_rule_set_source(
            session.registry, session.input_rule_set_ref.source_id, ProjectionScope.EVALUATOR,
            resolve_parent=session.rule_set_parent_resolver)
    except (ContextSourceError, RuleSetError) as exc:
        raise PublicationError(f"publication dependency failed to re-attest: {exc}") from exc
    if input_rule_set != session.input_rule_set:
        raise PublicationError("input rule set changed after the session froze it")
    _check_rule_set_visibility(input_rule_set, visibility)
    _check_parent_revision(parent, parent_resolver, lineage, visibility)
    for review in reviews:
        if most_restrictive(review.effective_visibility, visibility) != visibility:
            raise PublicationError("a review record carries refs more restricted than the "
                                   "publication")
    for candidate in (*considered, *dependencies):
        if most_restrictive(candidate.visibility, visibility) != visibility:
            raise PublicationError("selection includes inputs more restrictive than the "
                                   "publication; compile an allowed-input one")
    for candidate in partners:
        _partner_intact(session, candidate, derived_input_resolver)
    return _Closure(input_rule_set,
                    frozenset(candidate.candidate_id for candidate in partners),
                    frozenset(candidate.ref for candidate in partners))


def _partner_intact(session: CompileSession, candidate: Candidate,
                    derived_input_resolver: Callable[[DerivedInput], bytes] | None) -> None:
    """Exact partner integrity; failures and resolver exceptions abort publication."""
    findings = _source_findings(session, candidate)
    if findings:
        codes = ",".join(sorted({finding.code.value for finding in findings}))
        raise PublicationError(f"conflict partner {candidate.candidate_id} integrity failed: "
                               f"{codes}")
    _resolve_derived_inputs(candidate, derived_input_resolver)


def _check_parent_revision(parent: ContextSourceRef | None,
                           resolver: Callable[[ContextSourceRef], bytes] | None,
                           lineage: str, visibility: Visibility) -> None:
    """A parent revision resolves exactly, belongs to the lineage and is not hidden."""
    if parent is None:
        return
    if most_restrictive(parent.visibility, visibility) != visibility:
        raise PublicationError("parent revision is more restrictive than the publication")
    if resolver is None:
        raise PublicationError("parent revision cannot be resolved")
    try:
        data = resolver(parent)
    except Exception as exc:  # any resolver failure leaves the parent unresolved
        raise PublicationError("parent revision cannot be resolved") from exc
    if not isinstance(data, bytes):
        raise PublicationError("parent revision resolver must return bytes")
    document = _keys(_artifact({parent.path_or_ref: data},
                               ArtifactPathRef(parent.path_or_ref, parent.content_checksum)),
                     _REVISION_KEYS, "parent revision")
    body = {key: value for key, value in document.items() if key != "revision_id"}
    if (document["schema_version"] != REVISION_SCHEMA_VERSION
            or document["kind"] != ENGINEERING_CONTEXT_REVISION_KIND
            or document["revision_id"] != "ecr-" + json_digest(body)):
        raise PublicationError("parent is not a valid engineering-context revision")
    if document["lineage_id"] != lineage:
        raise PublicationError("parent revision belongs to another lineage")
    if most_restrictive(_visibility(document["visibility"]), visibility) != visibility:
        raise PublicationError("parent revision is more restrictive than the publication")


def _by_ref(items: Iterable[Any], ref: Callable[[Any], CandidateRef]) -> list[Any]:
    return sorted(items, key=lambda item: ref(item))


class CompilerWorkspace:
    """Append-only, non-canonical compiler store bound to one visibility.

    In memory in P1.2A. It is not a ContextSourceRef kind and is never registered in a
    CanonicalContextRegistry, so consumers cannot resolve it. Candidate versions and
    ReviewRecords are immutable and the review log is append-only. Every session,
    candidate and review must be visible at the workspace visibility, so a hidden
    record never moves the log tail that a less restrictive revision pins (use a
    separate workspace per visibility). One lock serializes review appends and
    staging. These methods are Application API operations only.
    """

    def __init__(self, workspace_id: str, visibility: Visibility = Visibility.AGENT) -> None:
        self.workspace_id = _pattern(workspace_id, _ID, "workspace_id")
        self.visibility = _visibility(visibility)
        # Re-entrant so a misbehaving resolver called during staging cannot deadlock
        # the workspace; staging detects and rejects any such mutation instead.
        self._lock = threading.RLock()
        self._entries: dict[CandidateRef, _Entry] = {}
        self._reviews: list[ReviewRecord] = []

    def _visible(self, visibility: Visibility) -> bool:
        return most_restrictive(visibility, self.visibility) == self.visibility

    # -- candidates --------------------------------------------------------------------
    def add_candidate(self, session: CompileSession, candidate: Candidate) -> ValidationResult:
        if not isinstance(session, CompileSession):
            raise TypeError("CompileSession required")
        if not isinstance(candidate, (CandidateBinding, CandidateKnowledge)):
            raise TypeError("CandidateBinding or CandidateKnowledge required")
        if session.visibility != self.visibility:
            raise WorkspaceAppendError("a workspace holds sessions of its own visibility only")
        if not self._visible(candidate.visibility):
            raise WorkspaceAppendError("candidate is more restrictive than the workspace")
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
        decision = _enum(ReviewDecision, decision, "decision")
        if not self._visible(_visibility(visibility)):
            raise WorkspaceAppendError("review metadata is more restrictive than the "
                                       "workspace; use a workspace of that visibility")
        with self._lock:
            entry = self._entries.get(candidate_ref)
            if entry is None:
                raise WorkspaceAppendError("reviews bind an existing exact candidate version")
            session, candidate = entry.session, entry.candidate
            if not isinstance(principal, ReviewPrincipal):
                raise WorkspaceAppendError("an allowed decision principal is required")
            if not principal.allowed_by(session.policies.acceptance_policies):
                raise WorkspaceAppendError("deterministic acceptance needs a registered policy")
            earlier = {item.review_id: item for item in self._reviews}
            if review_id in earlier:
                raise WorkspaceAppendError("review ids are unique in the append-only log")
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
                visibility=visibility,
                previous_hash=self._reviews[-1].record_hash if self._reviews else None)
            if not self._visible(record.effective_visibility):
                raise WorkspaceAppendError("review carries refs more restrictive than the "
                                           "workspace")
            self._reviews.append(record)
            return record

    def anchor(self) -> WorkspaceAnchor:
        """The current trusted anchor of this workspace's review and candidate logs."""
        with self._lock:
            return _anchor_of(self.workspace_id, self._reviews, tuple(self._entries))

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

    # -- staged publication ----------------------------------------------------------------
    def stage_publication(self, session: CompileSession, request: PublicationRequest, *,
                          derived_input_resolver: Callable[[DerivedInput], bytes] | None = None,
                          parent_resolver: Callable[[ContextSourceRef], bytes] | None = None
                          ) -> StagedPublication:
        """Build and fully validate the complete approved set, pinned to the log tail.

        The result is staging only (``StagedPublication``); no head moves. The derived
        inputs of every approved candidate must resolve to their exact recorded bytes
        through ``derived_input_resolver`` (otherwise staging fails); every canonical
        conflict partner must also pass exact integrity or the whole revision aborts.
        An uncited excluded candidate needs none. A parent revision must resolve
        through ``parent_resolver`` to the same lineage at no more restricted visibility.
        The bundle is then verified exactly as a consumer would, against this
        workspace's anchor. Resolvers run while the workspace lock is held and must not
        modify the workspace. The guard runs before and after verification and raises
        ``PublicationError`` on mutation. Append-only mutations are never rolled back.
        """
        if not isinstance(session, CompileSession) or not isinstance(request,
                                                                     PublicationRequest):
            raise TypeError("CompileSession and PublicationRequest required")
        if session.visibility != self.visibility:
            raise PublicationError("stage from a session of the workspace's visibility")
        with self._lock:
            records = tuple(self._reviews)
            entries: list[_Entry] = []
            for ref in sorted(request.candidate_refs):
                entry = self._entries.get(ref)
                if entry is None:
                    raise PublicationError("selection names an unknown candidate version")
                entries.append(entry)
            considered = tuple(entry.candidate for entry in entries)
            candidate_log = tuple(entry.candidate for entry in self._entries.values())
            candidate_refs = tuple(candidate.ref for candidate in candidate_log)
            anchor = _anchor_of(self.workspace_id, records, candidate_refs)
            selected_refs = set(request.candidate_refs)
            dependencies = tuple(self._entries[ref].candidate for ref in
                                 _dependency_refs(candidate_log, considered, session.session_id)
                                 if ref not in selected_refs)
            staged = _stage_bundle(session, request, workspace_id=self.workspace_id,
                                   records=records, candidate_log=candidate_log,
                                   considered=considered, dependencies=dependencies,
                                   derived_input_resolver=derived_input_resolver,
                                   parent_resolver=parent_resolver)
            def check_workspace_unchanged() -> None:
                if ((len(self._reviews), len(self._entries)) !=
                        (len(records), len(candidate_log)) or self.anchor() != anchor):
                    raise PublicationError("the workspace changed during staging; a resolver "
                                           "must not modify it")

            check_workspace_unchanged()
            verify_publication(dict(staged.files), staged.revision, session, anchor=anchor,
                               derived_input_resolver=derived_input_resolver,
                               parent_resolver=parent_resolver)
            check_workspace_unchanged()
            return staged


def _decide(session: CompileSession, candidate: Candidate, records: Sequence[ReviewRecord],
            position: int, known_ids: frozenset[str]) -> _Approved | Exclusion:
    """The per-candidate publication decision (shared by staging and verification)."""
    ref = candidate.ref
    if candidate.session_id != session.session_id:
        return Exclusion(ref, ExclusionReason.STALE_SESSION,
                         "candidate was compiled under another source/plant/policy tuple")
    effective = effective_review(records, ref, position)
    review = effective.record
    if review is None or effective.status in _STATUS_EXCLUSION:
        return Exclusion(ref, _STATUS_EXCLUSION[effective.status],
                         f"effective review status {effective.status.value}")
    if ((review.session_id, review.review_policy, review.validation_policy,
         review.plant_context) != (session.session_id, session.policies.review_policy,
                                   session.policies.validation_policy,
                                   session.plant.source_ref)):
        return Exclusion(ref, ExclusionReason.STALE_REVIEW,
                         "review was made against an incompatible tuple")
    remap = review.decision is ReviewDecision.REMAP
    if remap:
        original = (candidate.proposed_target if isinstance(candidate, CandidateBinding)
                    else None, candidate.scope_refs)
        if (review.selected_target, review.selected_scope_refs) == original:
            return Exclusion(ref, ExclusionReason.REMAP_INVALID, "REMAP_UNCHANGED")
    validation = validate_candidate(session, candidate, review, known_candidate_ids=known_ids)
    if not validation.passed:
        codes = sorted({item.code.value for item in validation.findings if item.blocking})
        return Exclusion(ref, ExclusionReason.REMAP_INVALID if remap
                         else ExclusionReason.VALIDATION_FAILED, ",".join(codes))
    return _Approved(candidate, review, validation, validation.selected_target,
                     validation.selected_scope_refs)


def _stage_bundle(session: CompileSession, request: PublicationRequest, *, workspace_id: str,
                  records: Sequence[ReviewRecord], candidate_log: Sequence[Candidate],
                  considered: Sequence[Candidate], dependencies: Sequence[Candidate],
                  derived_input_resolver: Callable[[DerivedInput], bytes] | None,
                  parent_resolver: Callable[[ContextSourceRef], bytes] | None
                  ) -> StagedPublication:
    """Staging as a pure function of its inputs; the verifier re-runs it byte-exactly."""
    _pattern(workspace_id, _ID, "workspace_id")
    records = tuple(records)
    position = len(records)
    considered = tuple(_by_ref(considered, lambda item: item.ref))
    dependencies = tuple(_by_ref(dependencies, lambda item: item.ref))
    candidate_log = tuple(candidate_log)
    if [candidate.ref for candidate in considered] != sorted(request.candidate_refs):
        raise PublicationError("considered candidates differ from the selection")
    logged = {candidate.ref: candidate for candidate in candidate_log}
    if len(logged) != len(candidate_log) or not set(request.candidate_refs) <= set(logged):
        raise PublicationError("selection names candidates outside the candidate log")
    partner_refs = _dependency_refs(candidate_log, considered, session.session_id)
    canonical = {ref.candidate_id: ref for ref in partner_refs}
    if any(candidate.candidate_id in canonical and
           candidate.ref != canonical[candidate.candidate_id] for candidate in considered):
        raise PublicationError("ambiguous partner selection")
    if [candidate.ref for candidate in dependencies] != sorted(
            set(partner_refs) - set(request.candidate_refs)):
        raise PublicationError("dependencies differ from the conflict partners in the "
                               "candidate log")
    partners = tuple(logged[ref] for ref in partner_refs)
    closure = _dependency_closure(
        session, considered=considered, dependencies=dependencies, partners=partners,
        reviews=records,
        lineage=request.lineage_id, parent=request.parent_revision,
        visibility=session.visibility, derived_input_resolver=derived_input_resolver,
        parent_resolver=parent_resolver)
    approved: list[_Approved] = []
    exclusions: list[Exclusion] = []
    for candidate in considered:
        outcome = _decide(session, candidate, records, position, closure.known_candidate_ids)
        if isinstance(outcome, Exclusion):
            exclusions.append(outcome)
        else:
            approved.append(outcome)
    for item in approved:  # excluded candidates need no resolvable derived inputs
        if item.candidate.ref not in closure.integrity_checked_refs:
            _resolve_derived_inputs(item.candidate, derived_input_resolver)
    return _build_bundle(session, request, workspace_id, records, candidate_log, approved,
                         tuple(exclusions), considered, dependencies)


def _build_bundle(session: CompileSession, request: PublicationRequest, workspace_id: str,
                  records: tuple[ReviewRecord, ...], candidate_log: tuple[Candidate, ...],
                  approved: list[_Approved],
                  exclusions: tuple[Exclusion, ...], considered: tuple[Candidate, ...],
                  dependencies: tuple[Candidate, ...]) -> StagedPublication:
    lineage = request.lineage_id
    root = f"{ENGINEERING_CONTEXT_ROOT}/{lineage}"
    position = len(records)
    anchor = _anchor_of(workspace_id, records, tuple(item.ref for item in candidate_log))
    approved = _by_ref(approved, lambda item: item.candidate.ref)
    exclusions = tuple(_by_ref(exclusions, lambda item: item.candidate_ref))
    bindings = [item for item in approved if item.candidate.route is Route.BINDING]
    knowledge = [item for item in approved if item.candidate.route is Route.KNOWLEDGE]
    rule_items: list[tuple[_Approved, Rule]] = []
    for item in approved:
        if item.candidate.route is Route.RULE:
            if not isinstance(item.candidate, CandidateKnowledge) or item.candidate.rule is None:
                raise PublicationError("RULE-routed item without a Rule carrier")
            rule_items.append((item, item.candidate.rule))
    files: list[tuple[str, bytes]] = []
    published_rule_set: ArtifactPathRef | None = None
    refs = [rule.ref for _, rule in rule_items]
    duplicates = sorted({f"{ref.rule_id}@{ref.version}" for ref in refs
                         if refs.count(ref) > 1})
    if duplicates:
        raise PublicationError("two approved candidates publish the same RuleRef: "
                               + ", ".join(duplicates))
    if rule_items:
        try:
            r1 = session.input_rule_set.derive(
                session.input_rule_set_ref, tuple(rule for _, rule in rule_items),
                visibility=session.visibility)
        except RuleSetError as exc:
            raise PublicationError(f"published rule set is invalid: {exc}") from exc
        _check_rule_set_visibility(r1, session.visibility)
        published_rule_set = ArtifactPathRef(f"{RULE_SET_ROOT}/{r1.checksum}.json",
                                             r1.checksum)
        files.append((published_rule_set.path, r1.serialize()))
    pinned = sorted(refs, key=lambda ref: (ref.rule_id, ref.version))
    # The complete log prefix (the workspace holds one visibility), hash-chained.
    log_bytes = canonical_json({
        "schema_version": REVIEW_LOG_SCHEMA_VERSION, "workspace_id": workspace_id,
        "review_log_position": position, "tail_hash": anchor.review_tail_hash,
        "records": [item.record() for item in records],
        "candidate_count": anchor.candidate_count,
        "candidate_tail_hash": anchor.candidate_tail_hash,
        "candidate_log": [candidate.record() for candidate in candidate_log]})
    log_ref = ArtifactPathRef.of(f"{root}/review-logs/{_sha256(log_bytes)}.json", log_bytes)
    files.append((log_ref.path, log_bytes))
    candidates_bytes = canonical_json({
        "schema_version": CANDIDATES_SCHEMA_VERSION,
        "candidates": [candidate.record() for candidate in considered],
        "dependencies": [candidate.record() for candidate in dependencies]})
    candidates_ref = ArtifactPathRef.of(
        f"{root}/candidates/{_sha256(candidates_bytes)}.json", candidates_bytes)
    files.append((candidates_ref.path, candidates_bytes))
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
        "acceptance_policies": sorted(session.policies.acceptance_policies),
        "extraction_methods": sorted({item.candidate.provenance.extraction_method
                                      for item in approved}),
        "grounding_methods": sorted({item.candidate.provenance.grounding_method
                                     for item in approved}),
        "considered_candidate_refs": [candidate.ref.record() for candidate in considered],
        "dependency_candidate_refs": [candidate.ref.record() for candidate in dependencies],
        "candidates": candidates_ref.record(),
        "approved_bindings": [_item_record(item) for item in bindings],
        "approved_knowledge": [_item_record(item) for item in knowledge],
        "input_rule_set": session.input_rule_set_ref.record(),
        "published_rule_set": {"revision": "INPUT"} if published_rule_set is None
        else {"revision": "SAME_COMMIT", **published_rule_set.record()},
        "pinned_rule_refs": [{"rule_id": ref.rule_id, "version": ref.version}
                             for ref in pinned],
        "rule_scope_bindings": [_item_record(item) for item, _ in rule_items],
        "review_log": {"review_log_position": position,
                       "tail_hash": anchor.review_tail_hash,
                       "candidate_count": anchor.candidate_count,
                       "candidate_tail_hash": anchor.candidate_tail_hash,
                       **log_ref.record()},
        "review_ids": sorted(item.review.review_id for item in approved),
        "conflicts": _conflict_summary(item.candidate for item in approved),
        "exclusions": [item.record() for item in exclusions],
        "provenance": {"published_by": request.published_by,
                       "published_at": request.published_at,
                       "workspace_id": workspace_id},
    }
    document = {**body, "revision_id": "ecr-" + json_digest(body)}
    data = canonical_json(document)
    revision = ArtifactPathRef.of(f"{root}/revisions/{document['revision_id']}.json", data)
    files.append((revision.path, data))
    return StagedPublication(lineage, request.expected_head, revision, log_ref,
                             candidates_ref, published_rule_set, position, exclusions,
                             tuple(files))


def _resolve_derived_inputs(candidate: Candidate,
                            resolver: Callable[[DerivedInput], bytes] | None) -> None:
    for item in candidate.provenance.derived_inputs:
        if resolver is None:
            raise PublicationError(f"derived input {item.ref} has no resolver")
        try:
            data = resolver(item)
        except Exception as exc:  # any resolver failure leaves the dependency unresolved
            raise PublicationError(f"derived input {item.ref} does not resolve") from exc
        if not isinstance(data, bytes) or _sha256(data) != item.content_checksum:
            raise PublicationError(f"derived input {item.ref} differs from its exact ref")


# -- verification from repository content alone ------------------------------------------
_REVISION_KEYS = frozenset({
    "schema_version", "kind", "lineage_id", "parent_revision", "visibility",
    "compile_session_id", "plant_context", "source_inventory", "compiler_version",
    "routing_policy", "validation_policy", "review_policy", "acceptance_policies",
    "extraction_methods", "grounding_methods", "considered_candidate_refs",
    "dependency_candidate_refs", "candidates",
    "approved_bindings", "approved_knowledge", "input_rule_set", "published_rule_set",
    "pinned_rule_refs", "rule_scope_bindings", "review_log", "review_ids", "conflicts",
    "exclusions", "provenance", "revision_id"})
@dataclass(frozen=True)
class VerifiedRevision:
    document: Mapping[str, Any]
    review_log_position: int
    effective_reviews: tuple[EffectiveReview, ...]
    published_rule_set: RuleSetRevision | None


def _fail(message: str) -> NoReturn:
    raise PublicationError(message)


def _keys(value: Any, expected: Iterable[str], what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        _fail(f"{what} does not have exactly the v0 fields")
    return value


def _artifact(files: Mapping[str, bytes], ref: ArtifactPathRef) -> Any:
    data = files.get(ref.path)
    if not isinstance(data, bytes):
        _fail(f"{ref.path} is missing from the publication")
    if _sha256(data) != ref.content_checksum:
        _fail(f"{ref.path} content checksum mismatch")
    try:
        value = strict_json_loads(data)
        canonical = canonical_json(value)
    except _JSON_ERRORS as exc:
        raise PublicationError(f"{ref.path} is not strict JSON") from exc
    if canonical != data:
        _fail(f"{ref.path} is not canonical JSON")
    return value


_LOG_KEYS = ("schema_version", "workspace_id", "review_log_position", "tail_hash", "records",
             "candidate_count", "candidate_tail_hash", "candidate_log")
_CANDIDATES_KEYS = ("schema_version", "candidates", "dependencies")
_PROVENANCE_KEYS = ("published_by", "published_at", "workspace_id")


def verify_publication(files: Mapping[str, bytes], revision: ArtifactPathRef,
                       session: CompileSession, *,
                       anchor: WorkspaceAnchor | None = None,
                       derived_input_resolver: Callable[[DerivedInput], bytes] | None = None,
                       parent_resolver: Callable[[ContextSourceRef], bytes] | None = None
                       ) -> VerifiedRevision:
    """Verify a bundle by re-staging it from its inputs and comparing every byte.

    Trusted inputs: ``session`` (the compile session over the revision's exact
    dependencies; its id must equal the revision's) and ``anchor`` (the trusted
    ``WorkspaceAnchor``: the bundle's review log and candidate log must be exactly
    those hash-chained prefixes). Bundle inputs that are checked rather than trusted:
    the candidate and dependency records (rebuilt exactly, members of the anchored
    candidate log, then validated by the shared closure and ``validate_candidate``)
    and the request metadata (re-run through ``PublicationRequest``). Everything else is recomputed by ``_stage_bundle``, the
    same function staging runs, and must equal the bundle byte for byte. A missing
    anchor or resolver fails closed.
    """
    if not isinstance(session, CompileSession):
        raise PublicationError("verification needs the revision's compile session")
    if not isinstance(anchor, WorkspaceAnchor):
        raise PublicationError("verification needs a trusted workspace anchor")
    try:
        return _verify(files, revision, session, anchor, derived_input_resolver,
                       parent_resolver)
    except PublicationError:
        raise
    except (CompilerError, RuleSetError, KeyError, TypeError, ValueError, AttributeError,
            RecursionError) as exc:
        raise PublicationError(f"publication does not verify: {exc!r}") from exc


def _verify(files: Mapping[str, bytes], revision: ArtifactPathRef, session: CompileSession,
            anchor: WorkspaceAnchor,
            derived_input_resolver: Callable[[DerivedInput], bytes] | None,
            parent_resolver: Callable[[ContextSourceRef], bytes] | None
            ) -> VerifiedRevision:
    document = _keys(_artifact(files, revision), _REVISION_KEYS, "revision")
    if document["compile_session_id"] != session.session_id:
        _fail("verification session differs from the revision's compile session")
    # (B) the review log: exactly the trusted, hash-chained prefix.
    log_meta = dict(_keys(document["review_log"], (
        "review_log_position", "tail_hash", "candidate_count", "candidate_tail_hash", "path",
        "content_checksum"), "review_log ref"))
    log = _keys(_artifact(files, ArtifactPathRef(log_meta["path"],
                                                 log_meta["content_checksum"])),
                _LOG_KEYS, "review log")
    records = tuple(ReviewRecord.from_record(item) for item in log["records"])
    previous = None
    for index, record in enumerate(records, start=1):
        if record.review_log_position != index or record.previous_hash != previous:
            _fail("review log is not a contiguous hash chain")
        previous = record.record_hash
    # Full immutable records expose each version's session; rebuilding their versions
    # binds those sessions to the same trusted candidate-ref hash chain.
    candidate_log = tuple(candidate_from_record(item) for item in log["candidate_log"])
    if _anchor_of(log["workspace_id"], records,
                  tuple(candidate.ref for candidate in candidate_log)) != anchor:
        _fail("review or candidate log differs from the trusted anchor")
    # Bundle inputs: request metadata re-run through its constructor, candidate records
    # rebuilt exactly (unique rows), then the same staging function as the workspace.
    provenance = _keys(document["provenance"], _PROVENANCE_KEYS, "provenance")
    parent = document["parent_revision"]
    request = PublicationRequest(
        lineage_id=document["lineage_id"],
        candidate_refs=tuple(CandidateRef.from_record(item)
                             for item in document["considered_candidate_refs"]),
        published_by=provenance["published_by"], published_at=provenance["published_at"],
        parent_revision=None if parent is None else _context_source(parent))
    artifact = _keys(_artifact(files, ArtifactPathRef.from_record(document["candidates"])),
                     _CANDIDATES_KEYS, "candidates artifact")
    considered = tuple(candidate_from_record(item) for item in artifact["candidates"])
    dependencies = tuple(candidate_from_record(item) for item in artifact["dependencies"])
    rows = [candidate.ref for candidate in (*considered, *dependencies)]
    if len(set(rows)) != len(rows):
        _fail("candidates artifact holds duplicate candidate rows")
    restaged = _stage_bundle(session, request, workspace_id=provenance["workspace_id"],
                             records=records, candidate_log=candidate_log,
                             considered=considered, dependencies=dependencies,
                             derived_input_resolver=derived_input_resolver,
                             parent_resolver=parent_resolver)
    # (A) every artifact is recomputed and compared byte-exactly.
    if restaged.revision != revision:
        _fail("revision differs from its re-staged bytes")
    if set(files) != {path for path, _ in restaged.files}:
        _fail("bundle files differ from the re-staged file set")
    for path, data in restaged.files:
        if files.get(path) != data:
            _fail(f"{path} differs from its re-staged bytes")
    effective = tuple(effective_review(records, CandidateRef.from_record(item["candidate_ref"]),
                                       anchor.review_log_position)
                      for section in ("approved_bindings", "approved_knowledge",
                                      "rule_scope_bindings")
                      for item in document[section])
    rule_set = None
    if restaged.published_rule_set is not None:
        rule_set = load_rule_set(dict(restaged.files)[restaged.published_rule_set.path],
                                 restaged.published_rule_set.content_checksum)
    return VerifiedRevision(freeze_json(document), anchor.review_log_position, effective,
                            rule_set)
