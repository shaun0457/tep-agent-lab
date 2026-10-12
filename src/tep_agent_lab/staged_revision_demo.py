"""Replay recorded human demo decisions through the existing compiler Application API.

This generic orchestration creates no acceptance policy or canonical publication.
Its caller supplies a frozen registered CompileSession and exact extraction DATA.
The named human test principal is a demo role, not an authenticated real reviewer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from industrial_agent_runtime import Visibility
from industrial_agent_runtime.serialization import freeze_json, to_jsonable

from .context_compiler import (
    CandidateBinding, CandidateRef, CompileSession, CompilerError, CompilerPolicies,
    CompilerWorkspace, DerivedInput, FragmentLocation, PlantTargetRef, PrincipalKind,
    PublicationRequest, ReviewDecision, ReviewPrincipal, StagedPublication, WorkspaceAnchor,
)
from .deterministic_extraction import ExtractionRuleSet, extract_candidates
from .persistence import strict_json_loads

REVIEW_SCHEMA = "recorded-demo-review/v1"
_FIELDS = {"schema_version", "visibility", "input_revisions", "session_id", "review_policy",
           "principal", "reviewed_at", "published_at", "workspace_id", "lineage_id",
           "decisions", "deferred"}
_DECISION_FIELDS = {"candidate_ref", "review_id", "decision", "reason", "selected_target",
                    "selected_scope_refs"}


class RecordedReviewError(CompilerError):
    """Recorded demo DATA is malformed, incomplete or incompatible; no bundle returned."""


def _keys(value: Any, fields: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise RecordedReviewError("recorded review object requires exactly its schema keys")


def _text(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RecordedReviewError("recorded review text must be nonempty")


def load_recorded_review(raw: bytes) -> Mapping[str, Any]:
    """Strict versioned DATA, containing append_review inputs, never executable policy.

    Every candidate needs either one decision or one explicit deferred entry. Coverage
    and exact session membership are checked by stage_revision before any append.
    """
    try:
        if not isinstance(raw, bytes):
            raise RecordedReviewError("recorded review requires exact bytes")
        data = strict_json_loads(raw)
        _keys(data, _FIELDS)
        if data["schema_version"] != REVIEW_SCHEMA:
            raise RecordedReviewError("unsupported recorded review schema")
        Visibility(data["visibility"])
        _keys(data["principal"], {"kind", "identifier"})
        principal = ReviewPrincipal(**data["principal"])
        if principal.kind is not PrincipalKind.HUMAN:
            raise RecordedReviewError("recorded demo decisions require a HUMAN test principal")
        for field in ("session_id", "reviewed_at", "published_at", "workspace_id", "lineage_id"):
            _text(data[field])
        CompilerPolicies(review_policy=data["review_policy"])
        revisions = data["input_revisions"]
        if not isinstance(revisions, dict) or not revisions:
            raise RecordedReviewError("input_revisions must pin repository revisions")
        for repository, revision in revisions.items():
            _text(repository)
            if (not isinstance(revision, str) or len(revision) != 40
                    or any(c not in "0123456789abcdef" for c in revision)):
                raise RecordedReviewError("input revisions require lowercase 40-hex")
        seen: set[CandidateRef] = set()
        review_ids: set[str] = set()
        for field in ("decisions", "deferred"):
            if not isinstance(data[field], list):
                raise RecordedReviewError("decisions and deferred must be arrays")
            for row in data[field]:
                _keys(row, _DECISION_FIELDS if field == "decisions"
                      else {"candidate_ref", "review_id", "reason"})
                ref = CandidateRef.from_record(row["candidate_ref"])
                if ref in seen:
                    raise RecordedReviewError("candidate must be decided or deferred exactly once")
                seen.add(ref)
                _text(row["reason"])
                _text(row["review_id"])
                if row["review_id"] in review_ids:
                    raise RecordedReviewError("duplicate review id")
                review_ids.add(row["review_id"])
                if field == "deferred":
                    continue
                decision = ReviewDecision(row["decision"])
                if decision is ReviewDecision.REMAP:
                    if not isinstance(row["selected_scope_refs"], list):
                        raise RecordedReviewError("REMAP requires the full selected scope")
                    for target in row["selected_scope_refs"]:
                        PlantTargetRef.from_record(target)
                    if row["selected_target"] is not None:
                        PlantTargetRef.from_record(row["selected_target"])
                elif row["selected_target"] is not None or row["selected_scope_refs"] is not None:
                    raise RecordedReviewError("only REMAP stores a selected binding")
        # Deferrals are an unordered set of explicit decisions, not log positions.
        data["deferred"].sort(key=lambda row: CandidateRef.from_record(row["candidate_ref"]))
        return freeze_json(data)
    except RecordedReviewError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise RecordedReviewError(f"invalid recorded demo review: {exc}") from exc


def extraction_input_resolver(rules: ExtractionRuleSet) -> Callable[[DerivedInput], bytes]:
    """Resolve only this exact rule-set dependency; compiler checks its byte checksum."""
    def resolve(item: DerivedInput) -> bytes:
        if item.ref != rules.method or item.role != "deterministic-extraction-rule-set":
            raise LookupError("unknown extraction input")
        return rules.raw
    return resolve


@dataclass(frozen=True)
class StageSummary:
    visibility: str
    approved: tuple[Mapping[str, Any], ...]
    rejected: tuple[Mapping[str, Any], ...]
    excluded: tuple[Mapping[str, Any], ...]
    deferred: tuple[Mapping[str, Any], ...]
    unsupported_count: int
    revision_id: str
    revision_checksum: str
    verification: str
    anchor: WorkspaceAnchor

    def record(self) -> dict[str, Any]:
        return to_jsonable({
            "visibility": self.visibility, "approved": list(self.approved),
            "rejected": list(self.rejected), "excluded": list(self.excluded),
            "deferred": list(self.deferred),
            "unsupported_count": self.unsupported_count, "revision_id": self.revision_id,
            "revision_checksum": self.revision_checksum, "verification": self.verification})


def stage_revision(session: CompileSession, rules: ExtractionRuleSet,
                   review_raw: bytes | Mapping[str, Any]
                   ) -> tuple[StagedPublication, StageSummary]:
    """Registered corpus -> S3 fragments -> S4 candidates -> recorded human review
    -> stage_publication (including its internal verification), entirely in memory.

    No implicit carry-forward: exact session, source revisions and candidate versions
    must match the recorded DATA. Excluded candidates remain in audit artifacts only.
    The summary carries session visibility, including counts and exclusion reasons.
    A mapping input must be the validated immutable output of load_recorded_review.
    PASS reports stage_publication's successful internal verify_publication call.
    """
    data = load_recorded_review(review_raw) if isinstance(review_raw, bytes) else review_raw
    if data["visibility"] != session.visibility.value:
        raise RecordedReviewError("recorded review visibility differs from the compile session")
    if (data["session_id"] != session.session_id
            or data["review_policy"] != session.policies.review_policy):
        raise RecordedReviewError("recorded review belongs to a stale input/session/policy tuple")
    result = extract_candidates(session, rules)
    candidates = {c.ref: c for c in result.candidates}
    rows = (*data["decisions"], *data["deferred"])
    recorded = {CandidateRef.from_record(row["candidate_ref"]) for row in rows}
    if recorded != set(candidates):
        raise RecordedReviewError("unknown/stale candidate version or missing candidate decision")
    workspace = CompilerWorkspace(data["workspace_id"], session.visibility)
    for candidate in result.candidates:
        workspace.add_candidate(session, candidate)
    principal = ReviewPrincipal(**data["principal"])
    try:
        for row in data["decisions"]:
            workspace.append_review(
                CandidateRef.from_record(row["candidate_ref"]), ReviewDecision(row["decision"]),
                review_id=row["review_id"], principal=principal, reason=row["reason"],
                reviewed_at=data["reviewed_at"], visibility=session.visibility,
                selected_target=None if row["selected_target"] is None
                else PlantTargetRef.from_record(row["selected_target"]),
                selected_scope_refs=None if row["selected_scope_refs"] is None else
                tuple(PlantTargetRef.from_record(t) for t in row["selected_scope_refs"]))
        # Explicit deferral is the contract's KEEP_CANDIDATE decision. Retain its
        # human rationale in the same append-only log, including revision identity.
        for row in data["deferred"]:
            workspace.append_review(
                CandidateRef.from_record(row["candidate_ref"]), ReviewDecision.KEEP_CANDIDATE,
                review_id=row["review_id"], principal=principal, reason=row["reason"],
                reviewed_at=data["reviewed_at"], visibility=session.visibility)
        request = PublicationRequest(
            lineage_id=data["lineage_id"], candidate_refs=tuple(candidates),
            published_by=principal.identifier, published_at=data["published_at"])
    except CompilerError as exc:
        raise RecordedReviewError(f"recorded review cannot be replayed: {exc}") from exc
    resolver = extraction_input_resolver(rules)
    staged = workspace.stage_publication(session, request, derived_input_resolver=resolver)
    anchor = workspace.anchor()
    approved_items = {CandidateRef.from_record(item["candidate_ref"]): item for field in
                      ("approved_bindings", "approved_knowledge", "rule_scope_bindings")
                      for item in staged.document[field]}
    recorded_rows = {CandidateRef.from_record(row["candidate_ref"]): row for row in rows}
    exclusions = {item.candidate_ref: item for item in staged.exclusions}
    review_ids = {item.candidate_ref: item.review_id for item in workspace.review_log()}
    groups: dict[str, list[Mapping[str, Any]]] = {
        "approved": [], "rejected": [], "excluded": [], "deferred": []}
    for candidate in result.candidates:
        row = recorded_rows[candidate.ref]
        decision = row.get("decision", "DEFERRED")
        item = approved_items.get(candidate.ref)
        group = ("approved" if item is not None else
                 "rejected" if decision == ReviewDecision.REJECT.value else
                 "deferred" if decision == "DEFERRED" else "excluded")
        detail = (candidate.mention_text if isinstance(candidate, CandidateBinding)
                  else candidate.rule.extraction.claim)
        target = None if item is None else item["validation"]["selected_target"]
        scope = () if item is None else item["validation"]["selected_scope_refs"]
        requested_target = (row["selected_target"] if item is None
                            and decision == ReviewDecision.REMAP.value else None)
        requested_scope = (row["selected_scope_refs"] if item is None
                           and decision == ReviewDecision.REMAP.value else ())
        groups[group].append(freeze_json({
            "candidate_ref": candidate.ref.record(), "kind": candidate.extraction_kind.value,
            "description": detail, "decision": decision, "reason": row["reason"],
            "review_id": review_ids[candidate.ref], "target": target,
            "scope_refs": scope, "requested_target": requested_target,
            "requested_scope_refs": requested_scope,
            "fragments": [f.record() for f in candidate.fragments()],
            "exclusion": None if item is not None
            else exclusions[candidate.ref].record()}))
    return staged, StageSummary(
        session.visibility.value, tuple(groups["approved"]), tuple(groups["rejected"]),
        tuple(groups["excluded"]), tuple(groups["deferred"]), len(result.unsupported),
        staged.document["revision_id"],
        staged.revision.content_checksum, "PASS", anchor)


def format_stage_summary(staged: StagedPublication, summary: StageSummary) -> tuple[str, ...]:
    lines = [f"Recorded HUMAN demo decisions; visibility={summary.visibility}; "
             f"canonical={staged.canonical}"]
    for label, rows in (("APPROVED", summary.approved), ("REJECTED", summary.rejected),
                        ("EXCLUDED", summary.excluded),
                        ("DEFERRED", summary.deferred)):
        lines.append(f"{label}: {len(rows)}")
        for row in rows:
            target = "" if row["target"] is None else " -> " + row["target"]["target_id"]
            if row["decision"] == "REMAP" and row["target"] is None and row["scope_refs"]:
                target = " scope: " + ",".join(t["target_id"] for t in row["scope_refs"])
            lines.append(f"  {row['decision']} {row['kind']} {row['description']}{target}")
            lines.append(f"    reason: {row['reason']} review={row['review_id']}")
            for record in row["fragments"]:
                location = FragmentLocation.from_record(record["location"])
                lines.append(f"    {record['source_ref']['source_id']}:"
                             f"{location.semantic_locator} "
                             f"fragment:{record['fragment_id'][:15]}")
            if row["exclusion"] is not None:
                requested = ("; requested target: " + row["requested_target"]["target_id"]
                             if row["requested_target"] is not None else "")
                if row["requested_scope_refs"]:
                    requested += "; requested scope: " + ",".join(
                        t["target_id"] for t in row["requested_scope_refs"])
                lines.append(f"    excluded: {row['exclusion']['reason']} "
                             f"{row['exclusion']['detail']}{requested}")
    lines.extend((f"UNSUPPORTED extraction entries: {summary.unsupported_count}",
                  f"revision_id={summary.revision_id}",
                  f"checksum={summary.revision_checksum}",
                  f"staged files={len(staged.files)}; canonical={staged.canonical}",
                  f"verify_publication: {summary.verification} "
                  "(stage_publication internal verification)"))
    return tuple(lines)
