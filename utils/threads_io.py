"""Shared helpers for loading threads and rendering them for the LLM.

Thread messages carry ``{id, datetime, sender, content}`` and, once the C1 threads land,
optionally ``quote`` / ``reactions``. ``load_export_index`` + ``with_export`` fill in the
missing quote, reactions and tags from the chat export, joined by WhatsApp message id, so every
consumer sees one normalized message shape:
``quote = {sender, text, mentions?} | None``, ``reactions = [{emoji, senders}]`` (senders are
the private identity strings of ``person_key``) and ``mentions = [{tag, member}]``: the group
members the message tags. A tag is ``@<tag>`` in the text (the digits WhatsApp writes) and
``member`` is the tagged member's id, the same string ``person_key`` gives for their messages.
A quote's ``mentions`` are the tags of the export found in the quoted text (only when there is
one).

``render_thread_for_llm`` shows a thread to a model without identities: messages are numbered
``[1]``, ``[2]``, ... and speakers are per-thread aliases. A tag is shown as ``@<public name>``
when the member is a provider (``providers``: member id -> public name, the owner's table of
``step_3_extract.tagged``), else as ``@<the member's alias>``; no raw number reaches the model.
The labels come from ``config.json`` ``extract.render``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from utils.config import section
from utils.paths import resolve_threads_path
from utils.reactions import summarize_reactions

DEFAULT_THREADS_PATH = resolve_threads_path()
RENDER_CONFIG = section("extract")["render"]
# A tag as WhatsApp writes it in a message: "@" and the digits of the tagged member.
TAG_PATTERN = re.compile(r"@(\d+)")


def person_key(person: Any) -> str:
    """One stable identity string for a sender, from a plain id or an export
    ``{user_name, phone}`` dict (``user_name`` first: it is what thread messages carry).

    Identities are private: they go to the audit file, never to the LLM or the public claims.
    """

    if isinstance(person, dict):
        return str(person.get("user_name") or person.get("phone") or "")
    return str(person or "")


def load_threads(path: Path | str = DEFAULT_THREADS_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def messages_period(threads: dict[str, Any]) -> tuple[str, str]:
    """The ``datetime`` of the earliest and of the latest message of a C1 threads payload."""
    moments = [message["datetime"] for thread in threads["threads"] for message in thread.get("messages", [])]
    if not moments:
        raise ValueError("the threads payload holds no message, so it has no period")
    return min(moments), max(moments)


def _normalize_mentions(mentions: Any) -> list[dict[str, str]]:
    out = []
    for mention in mentions or []:
        if isinstance(mention, dict) and mention.get("tag") and mention.get("member"):
            out.append({"tag": str(mention["tag"]), "member": str(mention["member"])})
    return out


def _normalize_quote(
    quote: Any, tags: Mapping[str, str] | None = None
) -> dict[str, Any] | None:
    """``{sender, text, mentions?}``; ``mentions`` are kept when the quote has them, else found
    in its text through ``tags`` (tag -> member id of the export)."""
    if not isinstance(quote, dict):
        return None
    text = str(quote.get("text") or "").strip()
    if not text:
        return None
    normalized: dict[str, Any] = {"sender": person_key(quote.get("sender")), "text": text}
    mentions = _normalize_mentions(quote.get("mentions")) or [
        {"tag": tag, "member": tags[tag]}
        for tag in dict.fromkeys(TAG_PATTERN.findall(text))
        if tags and tag in tags
    ]
    if mentions:
        normalized["mentions"] = mentions
    return normalized


def _normalize_reactions(reactions: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for reaction in reactions or []:
        emoji = reaction.get("emoji") or ""
        senders = sorted({person_key(s) for s in reaction.get("senders") or []} - {""})
        if emoji and senders:
            out.append({"emoji": emoji, "senders": senders})
    return sorted(out, key=lambda r: r["emoji"])


def load_export_index(path: Path | str) -> dict[str, dict[str, Any]]:
    """WhatsApp id -> ``{quote, reactions, mentions}`` (normalized) from a chat export list."""

    export_path = Path(path)
    if not export_path.is_file():
        raise FileNotFoundError(
            f"Chat export not found: {export_path}. Quotes and reactions are joined from it "
            "by message id; pass the export path (or None when threads carry them inline)."
        )
    with export_path.open(encoding="utf-8") as f:
        return export_index(json.load(f))


def export_index(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """WhatsApp id -> ``{quote, reactions, mentions}`` (normalized) of a chat export's
    messages; a quote's tags are read through every tag the export lists."""
    tags = {
        mention["tag"]: mention["member"]
        for m in messages
        for mention in _normalize_mentions(m.get("mentions"))
    }
    return {
        str(m["id"]): {
            "quote": _normalize_quote(m.get("quote"), tags),
            "reactions": _normalize_reactions(m.get("reactions")),
            "mentions": _normalize_mentions(m.get("mentions")),
        }
        for m in messages
        if m.get("id")
    }


def with_export(
    thread: dict[str, Any], export_index: dict[str, dict[str, Any]] | None
) -> dict[str, Any]:
    """A copy of ``thread`` whose messages all carry normalized ``quote``, ``reactions`` and
    ``mentions``.

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
        merged["mentions"] = _normalize_mentions(
            message["mentions"] if "mentions" in message else joined.get("mentions")
        )
        messages.append(merged)
    return {**thread, "messages": messages}


def message_tags(message: dict[str, Any]) -> list[dict[str, str]]:
    """Every tag of a (``with_export``) message: its own, then those of its quote."""
    quote = message.get("quote") or {}
    return [*(message.get("mentions") or []), *(quote.get("mentions") or [])]


def _squash(text: str) -> str:
    return " ".join(text.split())


def format_reactions_for_llm(reactions: list[dict[str, Any]] | None) -> str:
    """Anonymized reaction counts appended to a rendered message line."""

    detail = " ".join(
        f"{item['emoji']}×{item['count']}" for item in summarize_reactions(reactions)
    )
    return f" [{RENDER_CONFIG['reactions_label']}: {detail}]" if detail else ""


def render_thread_for_llm(
    thread: dict[str, Any],
    *,
    new_ids: frozenset[str] = frozenset(),
    providers: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Render a thread as anonymized numbered text; return it with the number -> id map.

    One line per message with content:
    ``[n] YYYY-MM-DD <alias>[ (new)][ ↩…]: text[ reactions]``.
    ``refs[n - 1]`` is the WhatsApp id of message ``[n]``. A reply shows ``↩[k]`` when the
    quoted message is message ``k`` of this thread, else ``↩«<alias>: quoted text»``
    (shortened to ``quote_max_chars``). Messages in ``new_ids`` get the new-message marker.
    A tag listed in the message's (or its quote's) ``mentions`` is shown as ``@<public name>``
    when ``providers`` names the member, else as ``@<alias>``: the alias the member has in
    this thread, or the next one, which they keep if they speak later. Any other ``@<digits>``
    stays as it is.
    """

    aliases: dict[str, str] = {}
    names = providers or {}

    def alias(person: Any) -> str:
        key = person_key(person)
        if key not in aliases:
            aliases[key] = RENDER_CONFIG["alias"].format(n=len(aliases) + 1)
        return aliases[key]

    def shown(text: str, mentions: list[dict[str, str]]) -> str:
        members = {mention["tag"]: mention["member"] for mention in mentions}

        def label(match: re.Match[str]) -> str:
            member = members.get(match.group(1))
            if member is None:
                return match.group(0)
            return f"@{names.get(member) or alias(member)}"

        return TAG_PATTERN.sub(label, text) if members else text

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
                quoted = alias(quote["sender"])
                text = shown(wanted, quote.get("mentions") or [])
                text = text if len(text) <= limit else text[:limit].rstrip() + "…"
                head += f" {reply}«{quoted}: {text}»"
        body = shown(content, message.get("mentions") or [])
        lines.append(f"{head}: {body}{format_reactions_for_llm(message.get('reactions'))}")
        refs.append(str(message.get("id")))
        earlier.append((number, sender, _squash(content)))

    return "\n".join(lines), refs
