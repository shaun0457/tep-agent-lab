"""Deterministic, byte-anchored Markdown sections and paragraphs (parser policy v1).

This deliberately bounded grammar does not interpret tables, lists, code or HTML.
Only LF UTF-8 input is accepted; no source text is normalized before slicing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import re
import unicodedata

from .canonical_context import BYTES_SHA256, ContextSourceRef
from .context_compiler import CompilerError, FragmentLocation, LocationKind, SourceFragmentRef
from .persistence import content_checksum as json_digest

PARSER_POLICY = "markdown.sections-paragraphs@1"

_HEADING = re.compile(rb" {0,3}(#{1,6})(?:[ \t]+(.*)|$)")
_FENCE = re.compile(rb" {0,3}(`{3,}|~{3,})(.*)$")
_LIST = re.compile(rb" {0,3}(?:[-+*]|[0-9]+[.)])(?:[ \t]+|$)")
_THEMATIC = re.compile(rb" {0,3}([*_-])(?:[ \t]*\1){2,}[ \t]*$")
_SETEXT = re.compile(rb" {0,3}(?:=+|-+)[ \t]*$")
_HTML_TAG = re.compile(rb"<([A-Za-z][A-Za-z0-9-]*)(?:[ \t/>])")


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    text: bytes
    blocked: bool
    heading: tuple[int, str] | None


def _slug(title: str) -> str:
    text = unicodedata.normalize("NFC", title).casefold()
    return re.sub(r"-+", "-", "".join(c if c.isalnum() else "-" for c in text)).strip("-") or "section"


def _lines(content: bytes) -> tuple[_Line, ...]:
    if not isinstance(content, bytes):
        raise CompilerError("Markdown source must be exact bytes")
    if b"\r" in content:
        raise CompilerError("Markdown parser policy v1 requires LF (CRLF/bare CR rejected)")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompilerError("Markdown source must be UTF-8") from exc
    lines: list[_Line] = []
    offset = 0
    fence: bytes | None = None
    html_end: bytes | None = None
    list_continuation = False
    for raw in content.splitlines(keepends=True):
        text = raw.removesuffix(b"\n")
        stripped = text.strip(b" \t")
        blocked = False
        heading = None
        if fence is not None:
            blocked = True
            match = _FENCE.fullmatch(text)
            if (match and match[1][:1] == fence[:1] and len(match[1]) >= len(fence)
                    and not match[2].strip(b" \t")):
                fence = None
        elif html_end is not None:
            blocked = True
            if html_end in stripped.lower():
                html_end = None
        elif match := _FENCE.fullmatch(text):
            blocked = True
            fence = match[1]
        elif stripped.startswith(b"<"):
            blocked = True
            if stripped.startswith(b"<!--"):
                ending = b"-->"
            elif stripped.startswith(b"<?"):
                ending = b"?>"
            elif stripped.startswith(b"<![CDATA["):
                ending = b"]]>"
            elif match := _HTML_TAG.match(stripped):
                ending = b"</" + match[1].lower() + b">"
            else:
                ending = b">"
            if ending not in stripped.lower() and not stripped.endswith(b"/>"):
                html_end = ending
        else:
            if stripped:
                if list_continuation and text.startswith((b" ", b"\t")):
                    blocked = True
                else:
                    list_continuation = bool(_LIST.match(text))
                    blocked = list_continuation
            if not blocked and (match := _HEADING.fullmatch(text)):
                title = (match[2] or b"").strip(b" \t")
                # Optional closing hashes require preceding whitespace.
                title = re.sub(rb"(?:^|[ \t]+)#+[ \t]*$", b"", title)
                heading = (len(match[1]), title.decode("utf-8"))
        lines.append(_Line(offset, offset + len(raw), text, blocked, heading))
        offset += len(raw)
    return tuple(lines)


def _unsupported(block: list[_Line]) -> bool:
    # Conservative exclusions: a mixed block remains wholly unsupported.
    return any(line.blocked or b"|" in line.text or _LIST.match(line.text)
               or line.text.startswith((b"    ", b"\t"))
               or line.text.lstrip(b" ").startswith((b">", b"<"))
               or _THEMATIC.fullmatch(line.text) or _SETEXT.fullmatch(line.text)
               for line in block)


def _locations(content: bytes) -> tuple[FragmentLocation, ...]:
    lines = _lines(content)
    # Each entry is (heading line index, level, semantic path, byte end).
    sections: list[tuple[int, int, tuple[str, ...], int]] = []
    stack: list[int] = []
    occurrences: dict[tuple[tuple[str, ...], str], int] = {}
    for index, line in enumerate(lines):
        if line.heading is None:
            continue
        level, title = line.heading
        while stack and sections[stack[-1]][1] >= level:
            previous = stack.pop()
            old_index, old_level, path, _ = sections[previous]
            sections[previous] = (old_index, old_level, path, line.start)
        parent = sections[stack[-1]][2] if stack else ()
        slug = _slug(title)
        key = (parent, slug)
        count = occurrences.get(key, 0) + 1
        occurrences[key] = count
        # '~' never occurs in the base slug, so literal numbered titles cannot collide.
        part = slug if count == 1 else f"{slug}~{count}"
        sections.append((index, level, (*parent, part), len(content)))
        stack.append(len(sections) - 1)

    result: list[FragmentLocation] = []
    for position, (index, _, path, end) in enumerate(sections):
        result.append(FragmentLocation.markdown(lines[index].start, end, path))
        # Paragraphs belong to their innermost heading, never duplicate into ancestors.
        stop = sections[position + 1][0] if position + 1 < len(sections) else len(lines)
        block: list[_Line] = []
        paragraph_index = 0

        def flush() -> None:
            nonlocal paragraph_index
            if block and not _unsupported(block):
                paragraph_index += 1
                result.append(FragmentLocation.markdown(block[0].start, block[-1].end,
                                                        path, paragraph_index))
            block.clear()

        for line in lines[index + 1:stop]:
            if not line.text.strip(b" \t"):
                flush()
            else:
                block.append(line)
        flush()
    return tuple(result)


def extract_location(content: bytes, location: FragmentLocation) -> bytes:
    """Recompute policy-v1 semantics and require the exact recorded byte anchor.

    No locator search against a newer source, normalized output or offset fallback.
    SourceFragmentRef.verify additionally checks the entire exact source checksum.
    """
    if location.kind is not LocationKind.MARKDOWN or location not in _locations(content):
        raise CompilerError("Markdown semantic locator does not match its exact byte range")
    return content[location.start:location.end]


def fragment_markdown(source_ref: ContextSourceRef, content: bytes) -> tuple[SourceFragmentRef, ...]:
    """Fragment one trusted registered source's exact bytes; never accept a local path.

    The application supplies the ref/bytes resolved from its frozen registry. Revision
    membership remains the registry's responsibility; this function re-attests bytes.
    """
    if (type(source_ref) is not ContextSourceRef or source_ref.checksum_method != BYTES_SHA256
            or source_ref.schema_version != "text/markdown"):
        raise CompilerError("Markdown fragmenter needs an exact BYTES_SHA256 Markdown source ref")
    if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != source_ref.content_checksum:
        raise CompilerError("Markdown bytes do not match the exact source checksum")
    return tuple(SourceFragmentRef(
        source_ref=source_ref, location=location,
        content_checksum=hashlib.sha256(content[location.start:location.end]).hexdigest(),
        fragment_kind="SECTION" if location.paragraph_index is None else "PARAGRAPH",
        fragmentation_policy=PARSER_POLICY) for location in _locations(content))


def diff_key(fragment: SourceFragmentRef) -> str:
    """Non-identity comparison key for Markdown edits within one source namespace.

    Revision/checksum/provenance of the source and byte offsets are excluded. Compare
    only corresponding documents; this key never substitutes for an exact fragment
    ref in candidate hashing, review binding or publication. Ancestor sections include
    descendant bytes, so a paragraph edit changes their diff keys too. Not persisted.
    """
    if (not isinstance(fragment, SourceFragmentRef)
            or fragment.location.kind is not LocationKind.MARKDOWN):
        raise CompilerError("diff_key needs a MARKDOWN SourceFragmentRef")
    return "diff-" + json_digest({
        "location": {"section_path": list(fragment.location.section_path or ()),
                     "paragraph_index": fragment.location.paragraph_index},
        "fragment_kind": fragment.fragment_kind,
        "fragmentation_policy": fragment.fragmentation_policy,
        "content_checksum": fragment.content_checksum,
    })


def format_fragment_tree(fragments: Iterable[SourceFragmentRef]) -> tuple[str, ...]:
    """Read-only demo rows in deterministic parser order."""
    rows: list[str] = []
    for fragment in fragments:
        loc = fragment.location
        depth = len(loc.section_path or ()) - 1 + (loc.paragraph_index is not None)
        rows.append(f"{'  ' * depth}{fragment.fragment_kind} {loc.semantic_locator} "
                    f"[{loc.start}, {loc.end}) sha256:{fragment.content_checksum[:12]}")
    return tuple(rows)
