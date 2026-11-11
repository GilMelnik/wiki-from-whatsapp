"""The page writer's prompts: one call writes a page in full, one call updates it.

Transport: JSON through the provider's structured output (``complete_json`` with a schema).
Long Hebrew markdown inside JSON is safe there: decoding is constrained to the schema, so
quotes such as the one in an abbreviation are always escaped, a truncated answer is never
cached and never parsed as a page, and the fields (body, summary, questions, related, the
update's "no change") need no hand-written parser. The body comes first in the schema so the
summary and questions are written after the article they describe.

Layout for prompt caching: the system prompt (rules, identical for every page) is cached
automatically; the link catalogue (identical for every page of a run) is the first user
segment with its own breakpoint; the page's own material follows. The "as of" date comes
from the data, never the clock, so a prompt is reproducible and a re-run after a crash is
served from the LLM disk cache.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from step_7_write_pages.inputs import MAIN, PageContext, Record, hebrew_month
from utils.llm_client import CacheSegment

_RULES = """\
You write one article of a Hebrew community wiki about surrogacy for gay men (couples and \
single fathers, most of them Israeli). The wiki distils years of discussion in the \
community's WhatsApp group. An article is built ONLY from "claim groups": things members \
said in the group, each with how many distinct members backed or disputed it and when. \
Readers are members and newcomers planning or living the process; they want a clear, \
trustworthy picture of the community's experience, including where people disagree and \
how current the information is.

## What you receive
- <page>: the article's id, title, description, scope (what belongs here and what is \
covered by other pages) and breadcrumb (where it sits in the wiki).
- <as_of>: the newest date in the whole corpus. Judge the age of a claim against it.
- <catalogue>: every page (`page_id | title | description`) and label (`label:label_id | \
title | kind`). These are the ONLY link targets.
- <groups>: one line per claim group, fields separated by " | ":
  1. the group id (`g-xxxxxxxx`);
  2. its role: `main` = the group belongs to this page, write it here in full; `mention -> \
page_id` = it lives on that other page: mention it briefly where it fits, with a link to \
that page, and leave the detail there;
  3. stance: `positive` recommends or reports a good experience, `negative` warns or reports \
a bad one, `neutral` reports or asks without judging, `factual` states a fact, price, rule \
or procedure;
  4. support: `backed by N` distinct members, and `last backed by K in <month year>` (the \
latest month in which members backed it, and how many did in that month); `no recorded \
backers` means a member said it but nobody is recorded endorsing it (older material): \
present it as something a member mentioned or reported, with when, and do not remark on \
the missing backing (no "unsupported", "without backing", "not backed by others"); `by month:` appears when backing spread over \
several months;
  5. `disputed by M`: distinct members who disagreed (only when M > 0);
  6. when it was said: one month or a range;
  7. `entities:` names it is about (places, agencies, clinics, laws...);
  8. `contradicts:` groups that say the opposite (with their text and page when they live \
on another page);
  9. the claim text.

## How to write
1. A real article, not a list of claims. Natural, clear, neutral Hebrew. Open with a short \
lead paragraph (no heading) that says what the page covers and the main takeaways. Then \
`##` sections by sub-topic, in an order that helps a reader (`###` for sub-sections when a \
section grows long). Merge groups that say related things into flowing paragraphs. Use a \
bullet list only where a list is natural (documents to bring, steps of a procedure, options \
side by side). It is community experience, not professional advice: write it that way \
("לפי ניסיון חברי הקבוצה", "הורים סיפרו"), without repeating disclaimers in every paragraph.
2. Grounded. Every sentence that states a fact, an experience, a recommendation, a price, a \
duration, a date or a name rests on one or more groups and ends with their marks: \
`[^g-xxxxxxxx]`, several in a row when several groups back it (`[^g-aaaaaaaa][^g-bbbbbbbb]`). \
Use ONLY what the groups say. No outside knowledge, no general background, no filling of \
gaps, no invented names, numbers, prices, laws or dates, no advice the groups do not give. \
If the groups do not cover a point, do not write about it, and do not write about the \
material itself ("no answer was found in the discussions"). Add no advice of your own; the \
one exception is saying that information may be out of date. A framing or transition \
sentence may go without a mark only if it adds no information. Mark only ids from <groups>.
3. Support in words, never as tallies. Say how widely something is held in prose: "אחד \
החברים סיפר", "כמה הורים המליצו", "חברים רבים מסכימים", or the group's own number when \
it matters ("ארבעה חברים המליצו על..."). Quote a group's numbers exactly as given and \
NEVER add numbers up across groups (the same member can be in several groups). No counters, \
no "+3/-1", no "תומכים: 3", no tables of counts, no per-claim score lines.
4. Both sides, always. When a group is disputed, when groups contradict each other, or when \
groups take opposite stances on the same thing (one recommends an agency, clinic, lawyer or \
route and another warns against it; one says a rule applies and another says it does not), \
present both views in the same place, each with its weight and date, and mark both. Never \
silently pick one and never drop the minority view. If one side is newer or backed by more \
members, say so and let the reader judge.
5. Recency. Newer information weighs more. Where information on the same point changed over \
time (a price, a rule, a procedure, a provider's quality), lead with the newest and present \
the older as earlier and possibly outdated ("בעבר, ב-2023, דווח ש..."). Say in the text \
how current the important claims are, in the spirit of "לאחרונה גובה על ידי שלושה חברים \
בנובמבר 2024" or "נכון למרץ 2025": use the `last backed` month and number as given, \
with Hebrew month names. For a group with no recorded backers say when it was mentioned, \
without a number. When the newest information on an important point is old relative to \
<as_of> (about two years or more), say it may be out of date. Give dates where they help the \
reader judge how current something is (the latest state of a point, a change over time, \
old information, the key claims of a section); do not open every sentence with a date. \
Vary the sentences: lead with the subject, and put when and how widely it is held where it \
reads naturally.
6. Links, like Wikipedia. Where the running text mentions the subject of another page, link \
it: `[[page_id|טקסט הקישור]]`; where it mentions a country, US state, family situation or \
other label, link the label: `[[label:label_id|טקסט]]`. Only ids from <catalogue>, never \
an anchor (#), never this page itself. Link a target at its first natural mention in a \
section, not at every mention. Every `mention` group is mentioned with a link to its page. \
A good article has several links; a link must still read naturally in the sentence.
7. Format: the markdown body only. No front matter, no `#` heading (the title is added \
automatically), no footnote definitions and no references section (definitions are added \
automatically from the marks), no HTML.
8. Privacy. Name agencies, clinics, lawyers, doctors, companies and other providers as the \
groups name them. Never name a private person (a member, a surrogate, a donor, a child), \
even if a group does: say "אחד החברים", "הפונדקאית". Never write a phone number, an email \
address or a street address.
9. Coverage. Use the material: every `main` group that adds something belongs in the \
article; groups saying nearly the same thing go into one sentence with all their marks. \
Every group that is disputed or contradicts another MUST be cited. The article is as long \
as its material needs."""

_WRITE_OUTPUT = """

## What you return
A JSON object:
- `body`: the article (markdown, rules above).
- `summary`: one paragraph (2-4 sentences) of plain Hebrew saying what the page tells the \
reader; no marks, no links.
- `questions`: 5-10 questions in Hebrew that a reader may ask and this page answers.
- `related`: pages of <catalogue> most closely related to this one (up to 6), each with a \
`relation` in snake_case English, e.g. `see_also`, `prerequisite`, `next_step`, `part_of`, \
`alternative`, `detail_of`."""

_UPDATE_RULES = """

## Updating an existing article
You do not write from scratch: you update <current_article> because some of its groups \
changed. The current article may contain edits by the wiki's editor: keep them.
- <changes> lists the groups that are `new` to the page, `changed` (counts, dates, role or \
wording; the line shows the current version) and `removed` (by id).
- <groups> lists every group the page has now, so you can mark them and keep both sides \
balanced.
- Keep the wording, structure and order of every part the changes do not touch. Do not \
rephrase, shorten or reorder untouched sentences or sections.
- Work each new group in where it fits (an existing section, or a new section when no \
section fits), revise the sentences that rest on changed groups (support, recency, \
numbers), and delete what rests only on removed groups (or just their marks when other \
marks still back the sentence). Everything you add or change follows the writing rules.
- When the page's title, description or scope changed (<changes> says so), adjust only what \
no longer fits.
- If the article needs no change at all (for example a count changed that the text does not \
quote), set `no_change` to true and return the current summary, questions and related \
unchanged and an empty body.

## What you return
A JSON object:
- `no_change`: true only when nothing in the article needs to change.
- `body`: the WHOLE updated article (not a diff), or "" when `no_change` is true.
- `summary`, `questions`, `related`: as in the current article, updated when the article's \
content changed (summary: one paragraph, no marks or links; questions: 5-10 in Hebrew; \
related: catalogue pages with a snake_case `relation`)."""

WRITE_SYSTEM = _RULES + _WRITE_OUTPUT
UPDATE_SYSTEM = _RULES + _UPDATE_RULES

_TEXT = {"type": "string"}
_RELATED = {"type": "array", "items": {
    "type": "object",
    "properties": {"page_id": _TEXT, "relation": _TEXT},
    "required": ["page_id", "relation"],
    "additionalProperties": False,
}}
_ANSWER = {"body": _TEXT, "summary": _TEXT, "questions": {"type": "array", "items": _TEXT},
           "related": _RELATED}


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


WRITE_SCHEMA = _schema(_ANSWER)
UPDATE_SCHEMA = _schema({"no_change": {"type": "boolean"}, **_ANSWER})


def _when(first: str, last: str) -> str:
    start, end = hebrew_month(first), hebrew_month(last)
    return f"said {start}" if start == end else f"said {start} – {end}"


def _support(record: Record) -> str:
    if record["supporters"] <= 0:
        return "no recorded backers"
    text = f"backed by {record['supporters']}"
    if record["last_backed"]:
        month, count = record["last_backed"]
        text += f", last backed by {count} in {hebrew_month(month)}"
    if "by_month" in record:
        months = []
        for month, (backers, opposers) in record["by_month"].items():
            part = f"{hebrew_month(month)}: {backers} backed"
            months.append(part + (f", {opposers} disputed" if opposers else ""))
        text += f" (by month: {'; '.join(months)})"
    return text


def group_line(record: Record) -> str:
    """One group as the model reads it (see the system prompt for the fields)."""
    role = record["role"] if record["role"] == MAIN else f"mention -> {record['primary']}"
    parts = [record["id"], role, record["stance"], _support(record)]
    if record["opposers"]:
        parts.append(f"disputed by {record['opposers']}")
    parts.append(_when(record["first"], record["last"]))
    if record["entities"]:
        parts.append(f"entities: {'; '.join(record['entities'])}")
    if record["contradicts"]:
        others = [c["id"] if "page" not in c else f'{c["id"]} (on {c["page"]}: "{c["text"]}")'
                  for c in record["contradicts"]]
        parts.append(f"contradicts: {', '.join(others)}")
    parts.append(record["text"])
    return " | ".join(parts)


def _page_block(context: PageContext) -> str:
    brief = context.brief
    return "\n".join([
        "<page>",
        f"id: {brief['id']}",
        f"title: {brief['title']}",
        f"description: {brief['description']}",
        f"scope: {brief['scope']}",
        f"breadcrumb: {' > '.join(context.breadcrumb)}",
        "</page>",
        f"<as_of>{context.as_of} ({hebrew_month(context.as_of)})</as_of>",
    ])


def _groups_block(records: Sequence[Record]) -> str:
    lines = "\n".join(group_line(record) for record in records)
    return f'<groups count="{len(records)}">\n{lines}\n</groups>'


def _catalogue(context: PageContext) -> CacheSegment:
    return CacheSegment(f"<catalogue>\n{context.catalogue}\n</catalogue>\n\n", cache=True)


def write_prompt(context: PageContext) -> tuple[str, list[CacheSegment]]:
    """``(system, user)`` of a full write."""
    tail = f"{_page_block(context)}\n\n{_groups_block(context.records)}\n\nWrite the article."
    return WRITE_SYSTEM, [_catalogue(context), CacheSegment(tail)]


def _current_block(current: Mapping[str, Any]) -> str:
    meta = {key: current[key] for key in ("summary", "questions", "related")}
    return "\n".join([
        "<current_article>",
        f"<meta>{json.dumps(meta, ensure_ascii=False)}</meta>",
        "<body>", current["body"], "</body>",
        "</current_article>",
    ])


def _changes_block(context: PageContext, new: Sequence[str], changed: Sequence[str],
                   removed: Sequence[str], brief_changed: bool) -> str:
    by_id = {record["id"]: record for record in context.records}
    lines = [f"new: {group_line(by_id[gid])}" for gid in new]
    lines += [f"changed: {group_line(by_id[gid])}" for gid in changed]
    lines += [f"removed: {gid}" for gid in removed]
    if brief_changed:
        lines.append("the page's title, description or scope changed (see <page>)")
    return "<changes>\n" + "\n".join(lines) + "\n</changes>"


def update_prompt(context: PageContext, current: Mapping[str, Any], *, new: Sequence[str],
                  changed: Sequence[str], removed: Sequence[str],
                  brief_changed: bool) -> tuple[str, list[CacheSegment]]:
    """``(system, user)`` of an update. ``current``: body (marks, no definitions), summary,
    questions, related of the current effective page."""
    tail = "\n\n".join([
        _page_block(context),
        _current_block(current),
        _changes_block(context, new, changed, removed, brief_changed),
        _groups_block(context.records),
        "Update the article.",
    ])
    return UPDATE_SYSTEM, [_catalogue(context), CacheSegment(tail)]


def retry_prompt(user: list[CacheSegment], answer: Mapping[str, Any],
                 problems: Sequence[str]) -> list[CacheSegment]:
    """The same request again, with the previous answer and what was wrong with it."""
    listed = "\n".join(f"- {problem}" for problem in problems)
    note = (
        "\n\n<previous_answer>\n" + json.dumps(dict(answer), ensure_ascii=False)
        + "\n</previous_answer>\n\nYour previous answer (above) broke these rules:\n" + listed
        + "\nReturn the whole answer again with these problems fixed. Keep everything else "
        "as it was."
    )
    return [*user[:-1], CacheSegment(user[-1].text + note)]
