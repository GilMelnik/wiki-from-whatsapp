"""Shared helpers for loading threads and rendering them for the LLM.

Thread messages carry ``{id, datetime, sender, content}`` and, once the C1 threads land,
optionally ``quote`` / ``reactions``. ``load_export_index`` + ``with_export`` fill in the
missing quote and reactions from the chat export, joined by WhatsApp message id, so every
consumer sees one normalized message shape:
``quote = {sender, text} | None`` and ``reactions = [{emoji, senders}]`` (senders are the
private identity strings of ``utils.support.person_key``).

``render_thread_for_llm`` shows a thread to a model without identities: messages are numbered
``[1]``, ``[2]``, ... and speakers are per-thread aliases. The labels come from
``config.json`` ``extract.render``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.config import section
from utils.paths import resolve_threads_path
from utils.support import person_key, summarize_reactions

DEFAULT_THREADS_PATH = resolve_threads_path()
RENDER_CONFIG = section("extract")["render"]


def load_threads(path: Path | str = DEFAULT_THREADS_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def thread_text(thread: dict[str, Any]) -> str:
    """All message contents concatenated (used for heuristic keyword matching)."""
    return "\n".join(m.get("content", "") or "" for m in thread.get("messages", []))


def _normalize_quote(quote: Any) -> dict[str, str] | None:
    if not isinstance(quote, dict):
        return None
    text = str(quote.get("text") or "").strip()
    if not text:
        return None
    return {"sender": person_key(quote.get("sender")), "text": text}


def _normalize_reactions(reactions: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for reaction in reactions or []:
        emoji = reaction.get("emoji") or ""
        senders = sorted({person_key(s) for s in reaction.get("senders") or []} - {""})
        if emoji and senders:
            out.append({"emoji": emoji, "senders": senders})
    return sorted(out, key=lambda r: r["emoji"])


def load_export_index(path: Path | str) -> dict[str, dict[str, Any]]:
    """WhatsApp id -> ``{quote, reactions}`` (normalized) from a chat export list."""

    export_path = Path(path)
    if not export_path.is_file():
        raise FileNotFoundError(
            f"Chat export not found: {export_path}. Quotes and reactions are joined from it "
            "by message id; pass the export path (or None when threads carry them inline)."
        )
    with export_path.open(encoding="utf-8") as f:
        messages = json.load(f)
    return {
        str(m["id"]): {
            "quote": _normalize_quote(m.get("quote")),
            "reactions": _normalize_reactions(m.get("reactions")),
        }
        for m in messages
        if m.get("id")
    }


def with_export(
    thread: dict[str, Any], export_index: dict[str, dict[str, Any]] | None
) -> dict[str, Any]:
    """A copy of ``thread`` whose messages all carry normalized ``quote`` and ``reactions``.

    Values already on a message win; missing ones come from ``export_index`` (``None`` means
    the threads carry them inline and nothing is joined).
    """

    messages: list[dict[str, Any]] = []
    for message in thread.get("messages", []):
        joined = (export_index or {}).get(str(message.get("id")), {})
        merged = dict(message)
        merged["quote"] = _normalize_quote(
            message["quote"] if "quote" in message else joined.get("quote")
        )
        merged["reactions"] = _normalize_reactions(
            message["reactions"] if "reactions" in message else joined.get("reactions")
        )
        messages.append(merged)
    return {**thread, "messages": messages}


def _squash(text: str) -> str:
    return " ".join(text.split())


def format_reactions_for_llm(reactions: list[dict[str, Any]] | None) -> str:
    """Anonymized reaction counts appended to a rendered message line."""

    detail = " ".join(
        f"{item['emoji']}×{item['count']}" for item in summarize_reactions(reactions)
    )
    return f" [{RENDER_CONFIG['reactions_label']}: {detail}]" if detail else ""


def render_thread_for_llm(
    thread: dict[str, Any], *, new_ids: frozenset[str] = frozenset()
) -> tuple[str, list[str]]:
    """Render a thread as anonymized numbered text; return it with the number -> id map.

    One line per message with content:
    ``[n] YYYY-MM-DD <alias>[ (new)][ ↩…]: text[ reactions]``.
    ``refs[n - 1]`` is the WhatsApp id of message ``[n]``. A reply shows ``↩[k]`` when the
    quoted message is message ``k`` of this thread, else ``↩«<alias>: quoted text»``
    (shortened to ``quote_max_chars``). Messages in ``new_ids`` get the new-message marker.
    """

    aliases: dict[str, str] = {}

    def alias(person: Any) -> str:
        key = person_key(person)
        if key not in aliases:
            aliases[key] = RENDER_CONFIG["alias"].format(n=len(aliases) + 1)
        return aliases[key]

    lines: list[str] = []
    refs: list[str] = []
    earlier: list[tuple[int, str, str]] = []  # (number, sender, squashed content)
    reply = RENDER_CONFIG["reply_mark"]
    limit = int(RENDER_CONFIG["quote_max_chars"])

    for message in thread.get("messages", []):
        content = (message.get("content") or "").strip()
        if not content:
            continue
        number = len(refs) + 1
        sender = person_key(message.get("sender"))
        head = f"[{number}] {str(message.get('datetime') or '')[:10]} {alias(sender)}"
        if str(message.get("id")) in new_ids:
            head += f" {RENDER_CONFIG['new_marker']}"
        quote = _normalize_quote(message.get("quote"))
        if quote is not None:
            wanted = _squash(quote["text"])
            target = next(
                (
                    n
                    for n, who, text in reversed(earlier)
                    if who == quote["sender"] and text.startswith(wanted)
                ),
                None,
            )
            if target is not None:
                head += f" {reply}[{target}]"
            else:
                shown = wanted if len(wanted) <= limit else wanted[:limit].rstrip() + "…"
                head += f" {reply}«{alias(quote['sender'])}: {shown}»"
        lines.append(f"{head}: {content}{format_reactions_for_llm(message.get('reactions'))}")
        refs.append(str(message.get("id")))
        earlier.append((number, sender, _squash(content)))

    return "\n".join(lines), refs
