"""Find and replace the wiki's internal links in a page body.

Two forms are recognised, both with an optional display text after ``|``:

* ``[[page_id|text]]``         -> ``WikiLink(kind="page", target="page_id", ...)``
* ``[[label:label_id|text]]``  -> ``WikiLink(kind="label", target="label_id", ...)``

The display text may contain balanced square brackets (``[[usa|ארה"ב [חדש]]]``)
but not a newline. Anything inside fenced code blocks (``` or ~~~) and inline
code spans is ignored; indented code blocks are not detected. Step 9 resolves
the links to real markdown links; the MCP index reuses ``find_wiki_links``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

_OPEN = re.compile(r"\[\[(label:)?([\w\-./]+)(\|)?")
_FENCE_OPEN = re.compile(r" {0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)", re.DOTALL)


@dataclass(frozen=True)
class WikiLink:
    """One ``[[...]]`` link; ``text`` is "" when absent; ``start``/``end`` span the whole link."""

    kind: Literal["page", "label"]
    target: str
    text: str
    start: int
    end: int


def _fenced_ranges(body: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    fence = ""
    start = pos = 0
    for line in body.splitlines(keepends=True):
        if not fence:
            match = _FENCE_OPEN.match(line)
            if match:
                fence, start = match.group(1), pos
        else:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                ranges.append((start, pos + len(line)))
                fence = ""
        pos += len(line)
    if fence:
        ranges.append((start, len(body)))
    return ranges


def _code_ranges(body: str) -> list[tuple[int, int]]:
    fenced = _fenced_ranges(body)
    masked = body
    for start, end in fenced:
        masked = masked[:start] + re.sub(r"[^\n]", " ", masked[start:end]) + masked[end:]
    inline = [match.span() for match in _INLINE_CODE.finditer(masked)]
    return sorted(fenced + inline)


def _read_text(body: str, pos: int) -> tuple[str, int] | None:
    """Read display text from ``pos`` up to the closing ``]]``; return (text, end) or None."""
    depth = 0
    for i in range(pos, len(body)):
        char = body[i]
        if char == "\n":
            return None
        if char == "[":
            depth += 1
        elif char == "]":
            if depth:
                depth -= 1
            elif body.startswith("]]", i):
                return body[pos:i], i + 2
            else:
                return None
    return None


def find_wiki_links(body_md: str) -> list[WikiLink]:
    """Return every wiki link in ``body_md`` outside code, in document order."""
    code = _code_ranges(body_md)
    links: list[WikiLink] = []
    pos = 0
    while match := _OPEN.search(body_md, pos):
        start = match.start()
        pos = start + 2
        if any(a <= start < b for a, b in code):
            continue
        if match.group(3):
            parsed = _read_text(body_md, match.end())
        else:
            parsed = ("", match.end() + 2) if body_md.startswith("]]", match.end()) else None
        if parsed is None:
            continue
        text, end = parsed
        links.append(
            WikiLink("label" if match.group(1) else "page", match.group(2), text, start, end)
        )
        pos = end
    return links


def replace_wiki_links(body_md: str, resolver: Callable[[WikiLink], str | None]) -> str:
    """Substitute each link with ``resolver(link)``; ``None`` keeps the display text (or the id)."""
    out: list[str] = []
    last = 0
    for link in find_wiki_links(body_md):
        replacement = resolver(link)
        out.append(body_md[last : link.start])
        out.append(replacement if replacement is not None else (link.text or link.target))
        last = link.end
    out.append(body_md[last:])
    return "".join(out)
