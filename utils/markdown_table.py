"""A GitHub markdown table, for the evaluation reports of steps 1 and 2."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = [" | ".join(headers), " | ".join("---" for _ in headers)]
    lines.extend(" | ".join(str(cell) for cell in row) for row in rows)
    return "\n".join(f"| {line} |" for line in lines)
