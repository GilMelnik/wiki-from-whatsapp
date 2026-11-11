"""The page writer's prompts: one call writes a page in full, one call updates it.

Transport: JSON through the provider's structured output (``complete_json`` with a schema).
Long Hebrew markdown inside JSON is safe there: decoding is constrained to the schema, so
quotes such as the one in an abbreviation are always escaped, a truncated answer is never
cached and never parsed as a page, and the fields (points, left_out, body, summary,
questions, related, the update's "no change") need no hand-written parser. The order of the
schema is the order of the work: first the page's points (each one thing the page says, with
every group behind it, ``step_7_write_pages.points``) and the groups left out, then the body
written from them (one statement per point), then the summary and questions about it.

An update starts from the stored points: the prompt shows them with the groups that left the
page already taken out (a point that lost its last group is listed as gone), so a new group
that repeats a point can join it and leave the text alone.

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
from step_7_write_pages.points import KINDS, MAX_MARKS, POINTS_CONFIG, REASONS, Point, tidy
from utils.llm_client import CacheSegment

_RULES = """\
You write one article of a Hebrew community wiki about surrogacy for gay men (couples and \
single fathers, most of them Israeli). The wiki distils years of discussion in the \
community's WhatsApp group. An article is built ONLY from "claim groups": things members \
said in the group, each with how many distinct members backed or disputed it and when. \
Readers are members and newcomers planning or living the process; they want a clear, \
short, trustworthy picture of the community's experience, including where people disagree \
and how current the information is.

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
present it as something a member mentioned or reported, and do not remark on the missing \
backing (no "unsupported", "without backing", "not backed by others"); `by month:` appears \
when backing spread over several months;
  5. `disputed by M`: distinct members who disagreed (only when M > 0);
  6. when it was said: one month or a range;
  7. `entities:` names it is about (places, agencies, clinics, laws...);
  8. `contradicts:` groups that say the opposite (with their text and page when they live \
on another page);
  9. the claim text.

## How you work: first the points, then the article
The same advice is often given in many conversations, in other words, years apart, by \
different members, and each time it is a separate group. The article must say it ONCE. So \
you first list the page's points, and then write the article from them.
- A point is ONE thing the article says: its kind, a short statement, and the ids of ALL the \
`main` groups behind it. Groups that say the same thing (in any wording, in any year, with \
any number of backers) are ONE point. A group that adds something of its own (a condition, \
an exception, a different number, price or duration, a warning, a reason, the other side of \
a disagreement) is a point of its own, or joins the point that says exactly that.
- Kinds of point:
{kinds}
- Every `main` group is in exactly one point, or in `left_out` with one of these reasons:
{reasons}
  Leave out only what a reader cannot use. A group that says the same as a point belongs IN \
that point: that is how the point records how widely it is held. Never leave out a disputed \
or contradicting group. `mention` groups are neither in points nor left out.
- Then write the article: ONE statement per point, usually one sentence. A common view \
backed by nine groups is one sentence. What follows it is only what adds something: the \
conditions, the exceptions, the differing numbers, the warnings, the disagreement. Never \
say a point again in other words, never walk through the groups one by one, never write a \
chronicle of who said what when. The article is as long as its distinct points need and no \
longer: never padded.

Example: nine `main` groups on when to arrive before the birth (ids shortened):
  g-1 (2022): arrive about three weeks before the due date. g-2 (2024): the same, and with an \
early birth the flights may have to be moved urgently. g-7 (2024): weeks 36-37. g-8 (2025, \
backed by 7): week 37 at the latest, week 36 for those who can work remotely, to avoid the \
cost of changing flights. g-5: weeks 36-38 in a single pregnancy, which avoids last-minute \
changes. g-4: it depends on the type of pregnancy, the surrogate's birth history and \
complications such as preeclampsia. g-3: week 36 for twins, 38 for a single pregnancy. g-6: \
with twins, up to week 35. g-9: warned that complex trips or domestic flights in the last \
weeks risk a sudden early birth; rent a fixed base and make only short weekend trips.
  Points: common_view "arrive around weeks 36-37" [g-8, g-7, g-1, g-2, g-5]; condition "the \
timing depends on the pregnancy type, the surrogate's history and complications" [g-4]; \
number "twins earlier, up to week 35" [g-6, g-3]; warning "no complex trips before the due \
date; a fixed base" [g-9].
  Wrong (a chronicle, far too long): "ההמלצה הנפוצה בקבוצה היא להגיע ליעד סביב שבועות \
36–37 להריון. בשנת 2022 חברים המליצו להגיע כשלושה שבועות לפני התאריך המשוער, ובמאי 2024 \
נאמרה אותה המלצה... ביולי 2023 שלושה חברים ציינו טווח של שבוע 36 עד 38... באוקטובר 2024 חבר \
אחד המליץ על שבועות 36–37. המלצה עדכנית יותר, מאפריל 2025 ובגיבוי שבעה חברים: להגיע לכל \
המאוחר בשבוע 37..."
  Right: "ההמלצה הנפוצה בקבוצה היא להגיע ליעד סביב שבועות 36–37 להריון.[^g-8][^g-7][^g-1] \
חברים הדגישו שהמועד תלוי בסוג ההיריון, בהיסטוריית הלידות של הפונדקאית ובסיבוכים כמו רעלת \
היריון.[^g-4] בהריון תאומים הוצע להגיע מוקדם יותר, עד שבוע 35.[^g-6][^g-3] חברים הזהירו \
שתכנון טיולים מורכבים או טיסות פנים בארה״ב בשבועות שלפני התאריך המשוער מסכן בהקפצה פתאומית, \
והציעו לשכור דירה כבסיס קבוע ולצאת ממנה רק לגיחות קצרות בסופי שבוע.[^g-9]"

## How to write
1. A real article, not a list of claims. Natural, clear, neutral Hebrew. Open with a short \
lead paragraph (no heading) that gives the page's main takeaways. Then `##` sections by \
sub-topic, in an order that helps a reader (`###` for sub-sections when a section grows \
long), the statements joined into flowing paragraphs. Use a bullet list only where a list \
is natural (documents to bring, steps of a procedure, options side by side). It is \
community experience, not professional advice: write it that way ("לפי ניסיון חברי \
הקבוצה", "הורים סיפרו"), without repeating disclaimers in every paragraph.
2. Grounded. Every sentence that states a fact, an experience, a recommendation, a price, a \
duration, a date or a name rests on the groups of its point and ends with their marks: \
`[^g-xxxxxxxx]`, several in a row when several groups back it (`[^g-aaaaaaaa][^g-bbbbbbbb]`), \
at most {max_marks} per sentence: the point's most backed and most recent groups (the point \
keeps the full list, so nothing is lost). Use ONLY what the groups say. No outside \
knowledge, no general background, no filling of gaps, no invented names, numbers, prices, \
laws or dates, no advice the groups do not give. If the groups do not cover a point, do not \
write about it, and do not write about the material itself ("no answer was found in the \
discussions"). Add no advice of your own; the one exception is saying that information may \
be out of date. A framing or transition sentence may go without a mark only if it adds no \
information. Mark only ids from <groups>, and never a group you left out.
3. How widely: once per point, in words, and only where it helps the reader weigh it: \
"ההמלצה הנפוצה בקבוצה", "חברים רבים", "כמה הורים", "חבר אחד סיפר". A point several groups \
repeat is common: say so in words. NEVER add numbers up across groups (the same member can \
be in several groups) and never give each group's count. A number of members only where it \
helps the reader weigh a lone report or the two sides of a disagreement, and then exactly \
as one group gives it. No counters, no "+3/-1", no "תומכים: 3", no tables of counts, no \
per-claim score lines.
4. Both sides. When members disagree (a group is disputed, groups contradict each other, or \
groups take opposite stances on the same thing: one recommends an agency, clinic, lawyer or \
route and another warns against it; one says a rule applies and another says it does not), \
that is ONE point of kind `dispute`: both positions with their content, in the same place, \
both marked. When the two sides are a group and the group that answers it (they \
`contradicts` each other and were said around the same time), the members who disputed the \
first are mostly those who said the second: do NOT add or contrast their counts; write "חבר \
אחר השיב ש...", "אחרים חלקו וסיפרו ש...". `disputed by M` with no answering group means M \
members disagreed without a recorded reason: say so ("אחד החברים חלק על כך") only when it \
matters to the reader (a recommendation many would follow, a provider's quality, a rule); \
otherwise citing the group is enough. Never silently pick one side and never drop the \
minority view; if one side is newer or more widely held, say so and let the reader judge.
5. Dates only where they help the reader decide. Give a month or a year ONLY for prices and \
costs, rules, laws and procedures, the quality of a provider (agency, clinic, lawyer, \
doctor, hospital, insurer...) and things that changed over time. There, lead with the \
newest state and say when ("נכון למרץ 2025", from `last backed`, with Hebrew month names), \
present the older as earlier ("בעבר, ב-2023, ..."), and when the newest information on such \
a point is about two years older than <as_of> or more, say it may be out of date. \
Everything else carries no date: no "in July 2023 three members said". A view repeated over \
the years may say so once, in words ("חזרה שוב ושוב בין 2022 ל-2025"), when that makes it \
more trustworthy. Vary the sentences: lead with the subject.
6. Links, like Wikipedia. Where the running text mentions the subject of another page, link \
it: `[[page_id|טקסט הקישור]]`; where it mentions a country, US state, family situation or \
other label, link the label: `[[label:label_id|טקסט]]`. Only ids from <catalogue>, never \
an anchor (#), never this page itself. Link a target at its first natural mention in a \
section, not at every mention. Every `mention` group is mentioned with a link to its page. \
A good article has several links; a link must still read naturally in the sentence.
7. Format: the markdown body only. No front matter, no `#` heading (the title is added \
automatically), no footnote definitions and no references section (the references are added \
automatically from the marks), no HTML.
8. Privacy. Name agencies, clinics, lawyers, doctors, companies and other providers as the \
groups name them. Never name a private person (a member, a surrogate, a donor, a child), \
even if a group does: say "אחד החברים", "הפונדקאית". Never write a phone number, an email \
address or a street address."""

_POINTS_OUTPUT = """\
- `points`: the page's points, in the order the article presents them; each has `kind`, \
`statement` (the point in one short Hebrew sentence, no marks, no links) and `groups` (the \
ids of ALL its groups).
- `left_out`: one `{group, reason}` for each main group that is in no point (usually few or \
none)."""

_WRITE_OUTPUT = """

## What you return
A JSON object:
""" + _POINTS_OUTPUT + """
- `body`: the article (markdown, rules above), one statement per point.
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
- <current_points> lists the points the current article was written from (`kind | statement \
| groups`) and the groups it left out; groups that are no longer main groups of the page are \
already taken out of them.
- <changes> lists the groups that are `new` to the page, `changed` (counts, dates, role or \
wording; the line shows the current version) and `removed` (by id), and the points that \
lost their last group (delete what rests only on them).
- <groups> lists every group the page has now, so you can mark them and keep both sides \
balanced.
- Start from the current points and keep them. A new group that says what a point already \
says joins that point (add its id): the text then usually stays as it is. A changed group \
stays in its point unless what it says moved to another one. A new group that adds \
something is a new point: work it in where it fits (an existing section, or a new section \
when none fits), as one statement.
- Keep the wording, structure and order of every part the changes do not touch. Do not \
rephrase, shorten or reorder untouched sentences or sections.
- Revise a sentence that rests on a changed group only where its point's statement changed; \
delete what rests only on removed groups (or just their marks when other marks still back \
the sentence). Everything you add or change follows the writing rules.
- When the page's title, description or scope changed (<changes> says so), adjust only what \
no longer fits.
- If the article's text needs no change at all (for example every new group joined a point \
the text already says, or a count changed that the text does not quote), set `no_change` to \
true and return the current summary, questions and related unchanged and an empty body. \
`points` and `left_out` are returned in full even then: they are stored with the page.

## What you return
A JSON object:
""" + _POINTS_OUTPUT.replace("the page's points,", "the WHOLE updated list of the page's points,") + """
- `no_change`: true only when nothing in the article's text needs to change.
- `body`: the WHOLE updated article (not a diff), or "" when `no_change` is true.
- `summary`, `questions`, `related`: as in the current article, updated when the article's \
content changed (summary: one paragraph, no marks or links; questions: 5-10 in Hebrew; \
related: catalogue pages with a snake_case `relation`)."""


def _listing(described: Mapping[str, str]) -> str:
    return "\n".join(f"  - `{name}`: {text}" for name, text in described.items())


_SYSTEM = _RULES.format(kinds=_listing(POINTS_CONFIG["kinds"]),
                        reasons=_listing(POINTS_CONFIG["left_out_reasons"]),
                        max_marks=MAX_MARKS)

WRITE_SYSTEM = _SYSTEM + _WRITE_OUTPUT
UPDATE_SYSTEM = _SYSTEM + _UPDATE_RULES

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


_POINTS = {
    "points": {"type": "array", "items": _schema({
        "kind": {"type": "string", "enum": list(KINDS)},
        "statement": _TEXT,
        "groups": {"type": "array", "items": _TEXT},
    })},
    "left_out": {"type": "array", "items": _schema({
        "group": _TEXT,
        "reason": {"type": "string", "enum": list(REASONS)},
    })},
}
WRITE_SCHEMA = _schema({**_POINTS, **_ANSWER})
UPDATE_SCHEMA = _schema({**_POINTS, "no_change": {"type": "boolean"}, **_ANSWER})


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


_REQUEST = (
    "editor request (a correction the wiki's editor asks for after reviewing the whole wiki: "
    "carry it out exactly and only where it applies, using only what <groups> say; change "
    "nothing else, keep every other sentence word for word; if the groups do not support the "
    "requested correction, set `no_change` to true): "
)


def point_line(point: Point) -> str:
    """One stored point as an update reads it: ``kind | statement | groups``."""
    return f"{point['kind']} | {point['statement']} | {', '.join(point['groups'])}"


def _points_block(points: Sequence[Point], left_out: Sequence[Mapping[str, str]]) -> str:
    if not points and not left_out:
        return ("<current_points>\nnone stored: list the points of the current article from "
                "its groups\n</current_points>")
    lines = [f"- {point_line(point)}" for point in points]
    if left_out:
        lines.append("left out: " + ", ".join(f"{e['group']} ({e['reason']})" for e in left_out))
    return "<current_points>\n" + "\n".join(lines) + "\n</current_points>"


def _changes_block(context: PageContext, new: Sequence[str], changed: Sequence[str],
                   removed: Sequence[str], gone: Sequence[Point], brief_changed: bool,
                   request: str = "") -> str:
    by_id = {record["id"]: record for record in context.records}
    lines = [f"new: {group_line(by_id[gid])}" for gid in new]
    lines += [f"changed: {group_line(by_id[gid])}" for gid in changed]
    lines += [f"removed: {gid}" for gid in removed]
    lines += [f"point gone (its last group left the page): {point_line(point)}" for point in gone]
    if brief_changed:
        lines.append("the page's title, description or scope changed (see <page>)")
    if request:
        lines.append(_REQUEST + " ".join(request.split()))
    return "<changes>\n" + "\n".join(lines) + "\n</changes>"


def update_prompt(context: PageContext, current: Mapping[str, Any], *, new: Sequence[str],
                  changed: Sequence[str], removed: Sequence[str],
                  brief_changed: bool, request: str = "") -> tuple[str, list[CacheSegment]]:
    """``(system, user)`` of an update. ``current``: body (marks, no definitions), summary,
    questions, related, points and left_out of the current effective page (as
    ``writer.current_text`` gives them); the points are shown without the groups that are
    no longer main groups of the page. ``request``: a free-text correction the editor asks
    for (step 8 applies an accepted review suggestion this way); it goes into <changes> after
    the group changes, and an update without one is unchanged."""
    carried = tidy(current["points"], current["left_out"], context.main_ids)
    tail = "\n\n".join([
        _page_block(context),
        _current_block(current),
        _points_block(carried.points, carried.left_out),
        _changes_block(context, new, changed, removed, carried.emptied, brief_changed, request),
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
