"""Canonical long-term context sources for one Playground run (playground-backend-v0.md).

``CanonicalContextRegistry`` is trusted application/harness state, never model-authored:
it registers immutable ``ContextSourceRef``s during run preparation, attests each one
against the exact attested repository revision and its content checksum, freezes the
inventory at READY, and resolves sources deterministically per projection scope.

It is not chat/model memory, a vector database, a RAG framework, or a replacement for
ProcessGraph, the Rule Registry, or the TaskStateStore. Local materialization never
implies Agent visibility: AGENT scope resolves AGENT-visible sources only, and an
unknown source and a hidden one are indistinguishable to it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
from importlib import resources
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Protocol

from industrial_agent_runtime import Visibility, to_jsonable
from industrial_agent_runtime.serialization import freeze_json

from .persistence import canonical_json

CONTEXT_REGISTRY_VERSION = "tep-agent-lab.canonical-context/v0"
CANONICAL_JSON_SHA256 = "sha256:canonical-json"
BYTES_SHA256 = "sha256:bytes"
CHECKSUM_METHODS = frozenset({CANONICAL_JSON_SHA256, BYTES_SHA256})

_GIT_REVISION = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KIND = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")
_RESERVED_SEGMENTS = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
                                *(f"lpt{i}" for i in range(10))})


class ProjectionScope(StrEnum):
    """Logical application projection scopes (not a second runtime visibility model)."""

    AGENT = "AGENT"
    EVALUATOR = "EVALUATOR"


# Source/ref visibility reuses the runtime ``Visibility`` semantics.
_SCOPE_VISIBILITY = {
    ProjectionScope.AGENT: frozenset({Visibility.AGENT}),
    ProjectionScope.EVALUATOR: frozenset({Visibility.AGENT, Visibility.EVALUATOR,
                                          Visibility.INTERNAL}),
}


def visible_in(visibility: Visibility, scope: ProjectionScope) -> bool:
    return Visibility(visibility) in _SCOPE_VISIBILITY[ProjectionScope(scope)]


class ContextSourceError(ValueError):
    """Trusted-side registration/attestation failure (revision, checksum, freeze)."""


class UnknownContextSource(LookupError):
    """Raised alike for an unknown source and one hidden from the caller's scope."""

    def __init__(self) -> None:
        super().__init__("unknown context source")


def checked_relative_path(path: Any) -> str:
    """A repository-relative POSIX path; never absolute, parent-relative, or a drive."""
    if not isinstance(path, str) or not path or "\\" in path or ":" in path:
        raise ValueError("path_or_ref must be a repository-relative POSIX path")
    parts = PurePosixPath(path).parts
    # Canonical spelling only ("a//b", "./a", "a/" would alias one source under two ids).
    if (path.startswith("/") or not parts or path != "/".join(parts)
            or any(part in (".", "..") or part.endswith(".")
                   or not _PATH_SEGMENT.fullmatch(part)
                   or part.split(".")[0].lower() in _RESERVED_SEGMENTS for part in parts)):
        raise ValueError("path_or_ref must be a repository-relative POSIX path")
    return path


def content_checksum(data: bytes, method: str) -> str:
    """Line-ending independent for JSON sources; exact bytes otherwise."""
    if method == CANONICAL_JSON_SHA256:
        return hashlib.sha256(canonical_json(json.loads(data))).hexdigest()
    if method == BYTES_SHA256:
        return hashlib.sha256(data).hexdigest()
    raise ValueError("unknown checksum method")


@dataclass(frozen=True, kw_only=True)
class ContextSourceRef:
    """Immutable identity of one canonical source at an exact revision and content."""

    source_id: str
    repository: str
    git_revision: str
    path_or_ref: str
    content_checksum: str
    kind: str
    schema_version: str
    visibility: Visibility
    checksum_method: str = CANONICAL_JSON_SHA256
    provenance: Mapping[str, Any] = field(default_factory=dict)
    governance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, pattern in (("source_id", _SOURCE_ID), ("repository", _REPOSITORY),
                              ("git_revision", _GIT_REVISION),
                              ("content_checksum", _SHA256), ("kind", _KIND)):
            value = getattr(self, name)
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise ValueError(f"invalid ContextSourceRef {name}")
        checked_relative_path(self.path_or_ref)
        if not isinstance(self.schema_version, str) or not self.schema_version:
            raise ValueError("schema_version must be a nonempty string")
        if self.checksum_method not in CHECKSUM_METHODS:
            raise ValueError("unknown checksum method")
        object.__setattr__(self, "visibility", Visibility(self.visibility))
        # Source-kind-specific metadata; never reinterpreted as Rule authority.
        for name in ("provenance", "governance"):
            if not isinstance(getattr(self, name), Mapping):
                raise ValueError(f"{name} must be a mapping")
            object.__setattr__(self, name, freeze_json(getattr(self, name)))

    def record(self) -> dict[str, Any]:
        return to_jsonable(self)


class SourceMaterializer(Protocol):
    def read_bytes(self, path: str) -> bytes:
        """Bytes of a repository-relative path in a local exact-revision materialization."""
        ...


class PackageSourceMaterializer:
    """A pinned repository's ``src/<package>/...`` files, read from the importable package."""

    def __init__(self, package: str) -> None:
        if not isinstance(package, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*", package):
            raise ValueError("package must be an importable top-level package name")
        self.package = package

    def read_bytes(self, path: str) -> bytes:
        prefix = f"src/{self.package}/"
        if not checked_relative_path(path).startswith(prefix):
            raise ContextSourceError("path is outside the materialized package")
        node = resources.files(self.package)
        for part in path[len(prefix):].split("/"):
            node = node.joinpath(part)
        return node.read_bytes()


class DirectorySourceMaterializer:
    """A local checkout directory of one repository at its attested revision."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def read_bytes(self, path: str) -> bytes:
        target = (self.root / checked_relative_path(path)).resolve()
        if not target.is_relative_to(self.root):
            raise ContextSourceError("path escapes the materialized checkout")
        return target.read_bytes()


@dataclass(frozen=True)
class ResolvedSource:
    ref: ContextSourceRef
    content: bytes

    def json(self) -> Any:
        return json.loads(self.content)


class CanonicalContextRegistry:
    """Run-scoped trusted source inventory; frozen at READY together with the manifest."""

    def __init__(self, revisions: Mapping[str, str],
                 materializers: Mapping[str, SourceMaterializer]) -> None:
        clean = {}
        for repository, revision in dict(revisions).items():
            if (not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository)
                    or not isinstance(revision, str) or not _GIT_REVISION.fullmatch(revision)):
                raise ValueError("attested revisions map repository -> 40-hex git revision")
            clean[repository] = revision
        self._revisions = freeze_json(clean)
        self._materializers = dict(materializers)
        self._sources: dict[str, ContextSourceRef] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def _attest(self, ref: ContextSourceRef) -> bytes:
        if self._revisions.get(ref.repository) != ref.git_revision:
            raise ContextSourceError(
                f"source {ref.source_id} revision is not the attested repository revision")
        materializer = self._materializers.get(ref.repository)
        if materializer is None:
            raise ContextSourceError(f"source {ref.source_id} has no local materialization")
        try:
            data = materializer.read_bytes(ref.path_or_ref)
            actual = content_checksum(data, ref.checksum_method)
        except ContextSourceError:
            raise
        except (OSError, ValueError) as exc:
            raise ContextSourceError(f"source {ref.source_id} cannot be materialized") from exc
        if actual != ref.content_checksum:
            raise ContextSourceError(f"source {ref.source_id} content checksum mismatch")
        return data

    def register(self, ref: ContextSourceRef) -> None:
        if self._frozen:
            raise ContextSourceError("context source inventory is frozen")
        if type(ref) is not ContextSourceRef:
            raise ContextSourceError("typed ContextSourceRef required")
        if ref.source_id in self._sources:
            raise ContextSourceError(f"duplicate context source {ref.source_id}")
        self._attest(ref)
        self._sources[ref.source_id] = ref

    def freeze(self) -> tuple[ContextSourceRef, ...]:
        self._frozen = True
        return self.inventory(ProjectionScope.EVALUATOR)

    def inventory(self, scope: ProjectionScope) -> tuple[ContextSourceRef, ...]:
        """Only sources visible to ``scope``; hidden ones leave no id, path, or count."""
        return tuple(self._sources[key] for key in sorted(self._sources)
                     if visible_in(self._sources[key].visibility, scope))

    def ref(self, source_id: Any, scope: ProjectionScope) -> ContextSourceRef:
        ref = self._sources.get(source_id) if isinstance(source_id, str) else None
        if ref is None or not visible_in(ref.visibility, scope):
            raise UnknownContextSource()
        return ref

    def resolve(self, source_id: Any, scope: ProjectionScope) -> ResolvedSource:
        """Visibility first, then re-attest: a source changed after READY fails closed."""
        ref = self.ref(source_id, scope)
        return ResolvedSource(ref, self._attest(ref))


def inventory_checksum(sources: Sequence[ContextSourceRef]) -> str:
    return hashlib.sha256(canonical_json([source.record() for source in sources])).hexdigest()
