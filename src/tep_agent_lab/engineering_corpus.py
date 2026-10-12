"""Trusted package corpus loading and immutable engineering-source registration.

This application API accepts an exact package manifest, never compiler candidates.
Repository/revision attestation is supplied by the caller, as in the existing registry.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any
from urllib.parse import urlsplit

from industrial_agent_runtime import Visibility
from industrial_agent_runtime.serialization import freeze_json

from .canonical_context import (
    BYTES_SHA256, CANONICAL_JSON_SHA256, CanonicalContextRegistry, ContextSourceRef,
    PackageSourceMaterializer, ProjectionScope, SourceMaterializer, checked_relative_path,
    content_checksum,
)
from .context_compiler import CompileSession, CompilerPolicies, CandidateBinding
from .deterministic_extraction import (
    ExtractionRuleSet, extract_candidates, load_rules, status_flags,
)
from .persistence import canonical_json, strict_json_loads
from .markdown_fragments import (
    PARSER_POLICY, TABLES_STEPS_POLICY, fragment_markdown, format_fragment_tree, table_row_cells,
)

DEFAULT_MANIFEST = (
    "src/tep_agent_lab/engineering_corpus/tep_reactor_cooling/manifest.json"
)
_CORPUS_ROOT = "src/tep_agent_lab/engineering_corpus/"
_MANIFEST_KEYS = frozenset({"corpus_id", "version", "subsystem", "author", "authored_at",
                            "inputs_consulted", "leakage_statement", "documents",
                            "intentional_conflicts"})
_DOCUMENT_KEYS = frozenset({"path", "sha256", "purpose"})
_CONFLICT_KEYS = frozenset({"conflict_id", "documents", "description"})
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FORBIDDEN_INPUTS = ("benchmark", "scorer", "evaluator", "fixtures")


class CorpusError(ValueError):
    """Manifest or source bytes failed validation; no registry is returned."""


@dataclass(frozen=True)
class EngineeringCorpus:
    manifest_path: str
    manifest_checksum: str
    manifest: Mapping[str, Any]


def _exact_keys(value: Any, keys: frozenset[str]) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise CorpusError("manifest object must contain exactly its schema keys")


def _text(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CorpusError("manifest text fields must be nonempty strings")


def _identifier(value: Any) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CorpusError("invalid manifest identifier")


def _list(value: Any, *, nonempty: bool = True) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        raise CorpusError("manifest field must be a list with the required entries")


def _validate_manifest(value: Any, manifest_path: str) -> None:
    _exact_keys(value, _MANIFEST_KEYS)
    _identifier(value["corpus_id"])
    for field in ("version", "subsystem", "author", "authored_at", "leakage_statement"):
        _text(value[field])
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["authored_at"]):
        raise CorpusError("authored_at must be an ISO calendar date")
    date.fromisoformat(value["authored_at"])
    _list(value["inputs_consulted"])
    for reference in value["inputs_consulted"]:
        _text(reference)
        parsed = urlsplit(reference)
        if (parsed.scheme != "https" or not parsed.netloc
                or any(word in reference.lower() for word in _FORBIDDEN_INPUTS)):
            raise CorpusError("inputs_consulted requires public references without restricted paths")

    _list(value["documents"])
    directory = str(PurePosixPath(manifest_path).parent) + "/"
    paths: set[str] = set()
    identities: set[str] = set()
    for document in value["documents"]:
        _exact_keys(document, _DOCUMENT_KEYS)
        path = checked_relative_path(document["path"])
        if not path.startswith(directory) or not path.endswith(".md"):
            raise CorpusError("document must be Markdown inside the manifest's corpus directory")
        stem = PurePosixPath(path).stem
        _identifier(stem)
        if path in paths or stem in identities:
            raise CorpusError("duplicate document path or source identity")
        paths.add(path)
        identities.add(stem)
        checksum = document["sha256"]
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            raise CorpusError("document sha256 must be lowercase 64-hex")
        _text(document["purpose"])

    _list(value["intentional_conflicts"], nonempty=False)
    conflict_ids: set[str] = set()
    for conflict in value["intentional_conflicts"]:
        _exact_keys(conflict, _CONFLICT_KEYS)
        _identifier(conflict["conflict_id"])
        if conflict["conflict_id"] in conflict_ids:
            raise CorpusError("duplicate conflict identity")
        conflict_ids.add(conflict["conflict_id"])
        _text(conflict["description"])
        _list(conflict["documents"])
        references = conflict["documents"]
        if (any(not isinstance(path, str) or path not in paths for path in references)
                or len(set(references)) != len(references) or len(references) < 2):
            raise CorpusError("conflict must cite at least two distinct corpus documents")


def load_corpus(manifest_path: str = DEFAULT_MANIFEST, *,
                materializer: SourceMaterializer | None = None) -> EngineeringCorpus:
    """Validate strict provenance JSON and every document's exact package bytes.

    A custom materializer is a trusted application/test dependency, never model input.
    Paths remain bounded to the package corpus directory.
    """
    materializer = materializer if materializer is not None else PackageSourceMaterializer(
        "tep_agent_lab")
    try:
        path = checked_relative_path(manifest_path)
        if not path.startswith(_CORPUS_ROOT) or not path.endswith("/manifest.json"):
            raise CorpusError("manifest must be inside the package corpus directory")
        raw = materializer.read_bytes(path)
        manifest = strict_json_loads(raw)
        _validate_manifest(manifest, path)
        for document in manifest["documents"]:
            data = materializer.read_bytes(document["path"])
            if hashlib.sha256(data).hexdigest() != document["sha256"]:
                raise CorpusError(f"document checksum mismatch: {document['path']}")
            data.decode("utf-8")
        return EngineeringCorpus(path, hashlib.sha256(raw).hexdigest(), freeze_json(manifest))
    except CorpusError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise CorpusError("cannot validate corpus manifest or package bytes") from exc


def register_corpus(*, repository: str, git_revision: str,
                    manifest_path: str = DEFAULT_MANIFEST,
                    materializer: SourceMaterializer | None = None) -> CanonicalContextRegistry:
    """Build a fresh registry from trusted repository attestation; freeze before return.

    This does not modify an existing READY registry or publish reviewed knowledge.
    Source ids are corpus id + document stem; the exact revision/checksum is in each ref.
    """
    materializer = materializer if materializer is not None else PackageSourceMaterializer(
        "tep_agent_lab")
    corpus = load_corpus(manifest_path, materializer=materializer)
    manifest = corpus.manifest
    registry = CanonicalContextRegistry({repository: git_revision}, {repository: materializer})
    for document in manifest["documents"]:
        registry.register(ContextSourceRef(
            source_id=f"{manifest['corpus_id']}.{PurePosixPath(document['path']).stem}",
            repository=repository, git_revision=git_revision, path_or_ref=document["path"],
            content_checksum=document["sha256"], kind="ENGINEERING_DOCUMENT",
            schema_version="text/markdown", visibility=Visibility.AGENT,
            checksum_method=BYTES_SHA256,
            provenance={"corpus_id": manifest["corpus_id"], "corpus_version": manifest["version"],
                        "manifest_path": corpus.manifest_path,
                        "manifest_checksum": corpus.manifest_checksum,
                        "author": manifest["author"], "authored_at": manifest["authored_at"],
                        "inputs_consulted": manifest["inputs_consulted"],
                        "purpose": document["purpose"]}))
    registry.freeze()
    return registry


def prepare_candidate_demo(*, repository: str, git_revision: str,
                           plant_git_revision: str | None = None
                           ) -> tuple[CompileSession, ExtractionRuleSet]:
    """Source-only demo over the pinned packaged plant and an in-memory empty R0.

    R0 is a trusted demo fixture, not a repository publication. The caller attests
    the lab/plant revisions as in S1; production supplies its own CompileSession.
    """
    from tep_sim import build_process_graph
    from tep_sim.process import GRAPH_SCHEMA_VERSION, PACKAGED_GRAPH_FIXTURE
    from .rule_sets import RULE_SET_KIND, RULE_SET_SCHEMA_VERSION, RuleSetRevision
    from .rules import RuleRegistry

    materializer = PackageSourceMaterializer("tep_agent_lab")
    registry = register_corpus(repository=repository, git_revision=git_revision,
                               materializer=materializer)
    rule_path = str(PurePosixPath(DEFAULT_MANIFEST).with_name("extraction-rules.json"))
    rules = load_rules(materializer.read_bytes(rule_path))
    if plant_git_revision is None:
        pins = strict_json_loads((Path(__file__).resolve().parents[2] /
                                 "dependency-pins.json").read_bytes())
        plant_git_revision = pins["tep-sim"]
    plant_repository = "shaun0457/tep-sim"
    graph_materializer = PackageSourceMaterializer("tep_sim")
    graph_path = "src/tep_sim/fixtures/" + PACKAGED_GRAPH_FIXTURE
    graph_bytes = graph_materializer.read_bytes(graph_path)
    graph_ref = ContextSourceRef(
        source_id="compiler-demo.plant", repository=plant_repository,
        git_revision=plant_git_revision, path_or_ref=graph_path,
        content_checksum=content_checksum(graph_bytes, CANONICAL_JSON_SHA256),
        kind="PROCESS_GRAPH", schema_version=GRAPH_SCHEMA_VERSION, visibility=Visibility.AGENT)
    empty = RuleSetRevision(RuleRegistry(()), Visibility.AGENT)
    r0_path = "context/compiler-demo/empty-input-rule-set.json"
    r0_ref = ContextSourceRef(
        source_id="compiler-demo.empty-r0", repository=repository, git_revision=git_revision,
        path_or_ref=r0_path, content_checksum=empty.checksum, kind=RULE_SET_KIND,
        schema_version=RULE_SET_SCHEMA_VERSION, visibility=Visibility.AGENT,
        provenance={"demo_only": True, "in_memory": True})

    class DemoMaterializer:
        def read_bytes(self, path: str) -> bytes:
            return empty.serialize() if path == r0_path else materializer.read_bytes(path)

    session = CompileSession.prepare(
        {repository: git_revision, plant_repository: plant_git_revision},
        {repository: DemoMaterializer(), plant_repository: graph_materializer},
        (*registry.inventory(ProjectionScope.AGENT), graph_ref, r0_ref),
        process_graph_source_id=graph_ref.source_id, input_rule_set_source_id=r0_ref.source_id,
        plant_graph_loader=build_process_graph,
        policies=CompilerPolicies(review_policy="compiler-demo.no-review@1"))
    return session, rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "fragments", "rows", "candidates"))
    parser.add_argument("source_id", nargs="?", help="registered source id for fragments/rows")
    parser.add_argument("--policy", choices=(PARSER_POLICY, TABLES_STEPS_POLICY),
                        default=TABLES_STEPS_POLICY)
    parser.add_argument("--repository", default="shaun0457/tep-agent-lab")
    parser.add_argument("--git-revision", help="trusted exact revision (required outside a checkout)")
    parser.add_argument("--plant-git-revision", help="trusted pinned graph repository revision")
    args = parser.parse_args(argv)
    if (args.command in ("fragments", "rows")) != (args.source_id is not None):
        parser.error("fragments/rows requires one source-id; list/candidates takes none")
    if args.command in ("rows", "candidates") and args.policy != TABLES_STEPS_POLICY:
        parser.error("rows/candidates requires the tables/steps parser policy")
    try:
        revision = args.git_revision
        if revision is None:
            root = Path(__file__).resolve().parents[2]
            revision = subprocess.check_output(
                ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root),
                 "rev-parse", "HEAD"], text=True, stderr=subprocess.PIPE).strip()
        registry = register_corpus(repository=args.repository, git_revision=revision)
        if args.command == "candidates":
            session, rules = prepare_candidate_demo(
                repository=args.repository, git_revision=revision,
                plant_git_revision=args.plant_git_revision)
            result = extract_candidates(session, rules)
            for candidate in result.candidates:
                if isinstance(candidate, CandidateBinding):
                    target = (candidate.proposed_target.target_id if candidate.proposed_target
                              else "unresolved")
                    if candidate.alternative_targets:
                        target += " alternatives=" + ",".join(
                            t.target_id for t in candidate.alternative_targets)
                    detail = f"{candidate.mention_text!r} -> {target}"
                else:
                    detail = repr(candidate.rule.extraction.claim)
                citations = "; ".join(
                    f"{f.source_ref.source_id}:{f.location.semantic_locator} "
                    f"fragment:{f.fragment_id[:15]}" for f in candidate.fragments())
                flags = ",".join(status_flags(session, candidate))
                print(f"{candidate.extraction_kind.value} {detail} [{flags}] {citations}")
            for item in result.unsupported:
                print(f"UNSUPPORTED {item.reason} {item.fragment.source_ref.source_id}:"
                      f"{item.fragment.location.semantic_locator} cell={item.cell!r} "
                      f"text={item.text!r} fragment:{item.fragment.fragment_id[:15]}")
        elif args.command in ("fragments", "rows"):
            resolved = registry.resolve(args.source_id, ProjectionScope.AGENT)
            fragments = fragment_markdown(resolved.ref, resolved.content, policy=args.policy)
            if args.command == "fragments":
                for row in format_fragment_tree(fragments):
                    print(row)
            else:
                for fragment in fragments:
                    if fragment.fragment_kind == "TABLE_ROW":
                        loc = fragment.location
                        cells = canonical_json(table_row_cells(fragment, resolved.content)).decode()
                        print(f"TABLE_ROW {loc.semantic_locator} [{loc.start}, {loc.end}) "
                              f"sha256:{fragment.content_checksum[:12]} cells:{cells}")
        else:
            for ref in registry.inventory(ProjectionScope.AGENT):
                resolved = registry.resolve(ref.source_id, ProjectionScope.AGENT)
                print(f"{ref.source_id}\t{ref.path_or_ref}\t{len(resolved.content)}\t"
                      f"{ref.content_checksum}")
    except (OSError, ValueError, LookupError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"corpus validation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
