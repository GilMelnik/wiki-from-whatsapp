"""What the split experiment sends for a unit, and the claims it takes from the answer.

Every unit goes to step 3's own model setup: ``EXTRACT_SYSTEM`` and ``FRESH_SCHEMA``, the
messages rendered by ``render_thread_for_llm`` and the answer read by ``ThreadClaims``. A
plain unit (A, B, C; a D unit with nothing before it; an E pack of one conversation) is sent
exactly as step 3 sends a thread (``ThreadClaims.prompt``). Two variants add an instruction at
the top of the user prompt, never in the system prompt:

- background (D): the messages before the conversation come first, numbered like the rest and
  under their own heading, with ``BACKGROUND_NOTE``;
- packed (E): each conversation under its own heading, numbered across the request, with
  ``PACKED_NOTE``.

The whole request is rendered as one thread (one numbering, one set of aliases, replies across
the parts shown by number) and then cut at the parts' borders; a part's text is exactly what
rendering that prefix gives, which ``_parts`` checks.

From the answer: claims as step 3 makes them (``absorb`` + ``finalize_all``), plus per claim
``unit_id``, ``window``, ``conversations`` (how many of the unit's conversations its supporting
messages are in) and ``cites_background``. A background message never becomes evidence: claims
are finalized against the unit's own messages, so background ids leave them and a claim that
rests only on background is dropped; every claim that cited one is counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import accumulate
from typing import Any

from step_3_extract.claims import ThreadClaims
from step_3_extract.split_experiment.units import MessageSet, Unit
from utils.threads_io import render_thread_for_llm

BACKGROUND_NOTE = """\
Before the conversation you get some background: messages [1] to [{last}] are the {count} \
chat messages sent right before it. They are shown only to help you understand the \
conversation; they may belong to other conversations. Extract claims only from the messages \
under "Conversation:". Never write a claim from the background, never put a background \
message's number in supporting or opposing, and never cite it in any other way."""

PACKED_NOTE = """\
This request holds {count} separate conversations of the group, one after another, each under \
its own heading. Treat each one as if it had been sent alone: a claim comes from one \
conversation, and its supporting and opposing messages are messages of that conversation. \
The messages are numbered across the whole request and an alias is the same person in all of \
them; cite messages by these numbers as usual."""


@dataclass(frozen=True)
class UnitClaims:
    """The claims one answer gave, and what was dropped or flagged on the way."""

    claims: list[dict[str, Any]]
    ungrounded: int  # claims of the answer without supporting messages
    dropped: int  # claims left without supporting messages of the unit (background only)
    background_citing: int  # claims that cited a background message (kept or dropped)


class UnitJob:
    """One unit's model call: the user prompt, and the claims from its answer."""

    def __init__(self, unit: Unit, messages: MessageSet) -> None:
        self.unit = unit
        self.messages = messages
        self.shown = [*unit.background, *unit.message_ids]
        self.reader = ThreadClaims(messages.thread(unit.unit_id, self.shown))
        self.own = (ThreadClaims(messages.thread(unit.unit_id, unit.message_ids))
                    if unit.background else self.reader)

    def prompt(self) -> str:
        if self.unit.background:
            background, conversation = self._parts([len(self.unit.background)])
            note = BACKGROUND_NOTE.format(last=len(self.unit.background),
                                          count=len(self.unit.background))
            return (f"{note}\n\nBackground (read only):\n{background}\n\n"
                    f"Conversation:\n{conversation}")
        count = len(self.unit.conversations)
        if count > 1:
            sizes = list(accumulate(len(c) for c in self.unit.conversations))
            parts = self._parts(sizes[:-1])
            listed = "\n\n".join(f"Conversation {number} of {count}:\n{part}"
                                 for number, part in enumerate(parts, start=1))
            return f"{PACKED_NOTE.format(count=count)}\n\n{listed}"
        return self.reader.prompt([])

    def _parts(self, borders: list[int]) -> list[str]:
        """The rendered request cut after each count of shown messages in ``borders``."""
        full = self.reader.rendered
        parts: list[str] = []
        start = 0
        for border in borders:
            prefix, _ = render_thread_for_llm(self.messages.thread("", self.shown[:border]))
            if not full.startswith(prefix):
                raise RuntimeError(f"{self.unit.unit_id}: the rendering of its first {border} "
                                   "messages is not a prefix of the whole rendering")
            parts.append(full[start:len(prefix)])
            start = len(prefix) + 1  # the line break between two messages
        return [*parts, full[start:]]

    def claims(self, answer: Any) -> UnitClaims:
        """The unit's claims from one answer; raises ``OutputError`` on a malformed one."""
        records = self.reader.absorb(answer, [])
        background = set(self.unit.background)
        citing = {r["claim_id"] for r in records
                  if background & {*r["supporting_message_ids"], *r["opposing_message_ids"]}}
        kept, dropped = self.own.finalize_all(records)
        part_of = {wa_id: number for number, conversation in enumerate(self.unit.conversations)
                   for wa_id in conversation}
        claims = [
            {**claim, "unit_id": self.unit.unit_id, "window": self.unit.window,
             "conversations": len({part_of[i] for i in claim["supporting_message_ids"]}),
             "cites_background": claim["claim_id"] in citing}
            for claim, _ in kept
        ]
        return UnitClaims(claims, self.reader.ungrounded, dropped, len(citing))
