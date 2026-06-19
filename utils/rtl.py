"""RTL helpers for Hebrew wiki Markdown."""

from __future__ import annotations

HEBREW_CSS = "stylesheets/hebrew.css"


def wrap_rtl_markdown(body: str) -> str:
    text = body.strip()
    return f'<div dir="rtl" markdown="1">\n\n{text}\n\n</div>\n'
