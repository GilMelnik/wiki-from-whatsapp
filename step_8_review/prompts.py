"""The two review prompts (stage ``wiki_review.stage``); both answer with a short list of
suggestions (``proposals.answer_schema``).

- A PART pass reads the plan block of the whole wiki, then each page of its part (its body
  with citation marks and links, then the group lines step 7's writer read for it), then the
  suggestions already raised about those pages. It may raise every kind of suggestion, about
  the pages and groups of its part only.
- The CROSS pass reads the plan block, then per page with stored points its title, labels and
  points (``kind | statement | groups``; description and scope are in the plan block), then
  the suggestions already raised about those pages. It compares pages it cannot read in full,
  so it never raises a ``mistake``.

The rules are the system prompt (small; cached by the provider when it repeats). The user
message is not marked for caching: the passes of one run are sent together, so a cache write
(a quarter more than plain input) would rarely be read.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from step_3_extract.scrub import FORBIDDEN_TERM_INSTRUCTION
from step_6_plan.model import PLAN_CONFIG
from step_7_write_pages.inputs import MAIN, Record
from step_7_write_pages.points import KINDS as POINT_KINDS
from step_7_write_pages.prompts import group_line, point_line
from step_8_review.inputs import REVIEW_CONFIG, ReviewInput
from step_8_review.proposals import effective_proposal
from utils.llm_client import CacheSegment

PART_CAP = REVIEW_CONFIG["part"]["max_suggestions"]
CROSS_CAP = REVIEW_CONFIG["cross"]["max_suggestions"]

_WIKI = """\
You are the editor-in-chief of a Hebrew community wiki about surrogacy for gay men (couples \
and single fathers, most of them Israeli). Every page was written separately by a writer \
model that saw only that page's "claim groups" (things members said in the community's \
WhatsApp group, with how many distinct members backed or disputed each and when)."""


def _owner(cap: int) -> str:
    return f"""\
The wiki's owner reads every suggestion and accepts or rejects it; an accepted suggestion is \
carried out by code. The owner's time is the scarce resource: a short list of correct, \
checkable, important suggestions is worth far more than a long list of plausible ones, and \
one wrong suggestion costs the owner's trust in all of them. Return at most {cap} \
suggestions, the most valuable first. Return fewer, or none, when nothing is worth the \
owner's time: an empty list is a good answer."""


_PLAN = """\
- <plan>: the WHOLE wiki's tree. `category id | title | description`, and `page id | title | \
description | scope` (the scope says what belongs on the page and what belongs elsewhere). \
<labels>: `label_id | title | kind | description` (labels cut across pages, e.g. a country). \
<retired_ids>: ids that may never be used again (a link to a page that was merged away \
still works: the site sends it to the page it was merged into; never report it). \
<as_of>: the newest date in the corpus."""

_RAISED = """\
- <already_raised>: suggestions made in earlier reviews about the pages you see, with the \
owner's decision (pending, accepted, rejected, applied). Never raise any of them again, in \
the same or other words; a rejected one means the owner disagrees."""

_STRUCTURAL = f"""\
- `split`: one page mixes two subjects a reader would look up separately, and the second \
has enough material (many groups) for a page of its own.
- `merge`: two pages cover largely the same subject, so a reader cannot tell where to look.
- `add_page`: a subject told in pieces on several pages, with no page of its own, that \
members clearly look up. The plan holds at most {PLAN_CONFIG["max_pages"]} pages; count \
what is there.
- `add_label`: a facet value (a country, a US state, a family situation, ...) that recurs on \
several pages and that the labels lack."""

_NOT_CONTRADICTION = """\
NOT a contradiction: old versus new information that is dated (that is change over time); \
two views a page already presents as a disagreement; different cases (another country, \
state, agency, family situation)."""


def _fields(group_source: str) -> str:
    return f"""\
## How to propose (fields of one suggestion; every field is present, "" or [] when it does \
not apply to the kind)
- `kind`, `title` (a Hebrew headline of at most ten words), `pages` (the page ids the \
suggestion is about), `reason` (one to three Hebrew sentences for the owner: what is wrong \
and where, concretely: quote the words, name the group ids and the pages; for a structural \
change, why a reader is better off).
- `contradiction`: `groups_a` and `groups_b` (the groups on each side, ids from {group_source}; \
one or two per side), `pages` (every page that must show both sides), `clash` (one Hebrew \
sentence on what clashes), `correction` (a Hebrew instruction to the page writer: present \
both sides where the subject is discussed, with each side's weight and date).
- `split`: `page_id` (the page to split), `new_page_id` (a new id), `title_he`, \
`description` (one Hebrew sentence), `scope` (two or three Hebrew sentences: what belongs on \
the new page and what does not), `original_scope` (the narrowed scope of the original page).
- `merge`: `survivor_id` (the page that stays), `merged_ids` (the pages merged into it).
- `add_page`: `page_id` (a new id), `parent_id` (the category it belongs in), `title_he`, \
`description`, `scope`; `pages` = the pages where the subject is scattered now.
- `add_label`: `label_id` (a new id), `title_he`, `description` (one Hebrew sentence precise \
enough to decide yes or no for a single claim), `label_kind` (an existing kind when one \
fits); `pages` = the pages where the facet recurs.
- New ids are lowercase Latin slugs (letters, digits, single hyphens, at most \
{PLAN_CONFIG["slug_max_length"]} characters), not in use and not retired. Pages sit at depth \
{PLAN_CONFIG["min_depth"]}-{PLAN_CONFIG["max_depth"]} of the tree."""


_RULES = f"""\
- Every suggestion must be checkable by the owner from what you write: exact quotes, group \
ids, page ids.
- Only what the groups support. Never suggest adding information from outside the groups.
- Never a private person's name, a phone number or an email address in any field.
- {FORBIDDEN_TERM_INSTRUCTION}
- Return only the JSON object asked for."""

PART_SYSTEM = f"""\
{_WIKI} The wiki is reviewed in parts. You get ONE PART: a few related pages together with \
the groups behind them, and the plan of the whole wiki so you know what else exists. The \
other parts are reviewed in calls of their own, and a separate pass compares the points of \
every page.

Your job: find what is wrong or missing that a reader of these pages together can see, plus \
clear mistakes in a page's text, and propose concrete fixes. {_owner(PART_CAP)}

## What you receive
{_PLAN}
- <part>: the ids of this part's pages, then each page. <page id="..." title="...">: <body> \
is the page as readers see it. A mark `[^g-xxxxxxxx]` cites the group with that id; \
`[[page_id|text]]` and `[[label:id|text]]` are links. <groups> holds one line per group that \
belongs to the page, fields separated by " | ": id; `main` (or `mention -> page_id` for a \
group whose main page is another one); stance; support (`backed by N` distinct members, \
`last backed by K in <month year>`, `by month` when spread; `no recorded backers` = a member \
said it, nobody is recorded endorsing it); `disputed by M`; when it was said; `entities:` \
(names the group is about, which the claim text may not repeat); `contradicts:` (groups \
saying the opposite, with their page and text when elsewhere); the claim text. A group \
whose main page is another page of this part appears as `id | mention -> page_id`: its full \
line is in that page's <groups>. These lines are what the writer saw: the truth a sentence \
is checked against.
{_RAISED}

## What to look for
Across the pages of this part:
- `contradiction`: two pages (or two places) state opposite things about the same subject, \
and neither shows the other side: a duration, price, rule, requirement or a provider's \
quality told one way here and the opposite way there. Check the groups: it is a \
contradiction only when the groups behind the two statements really clash. \
{_NOT_CONTRADICTION}
- The same subject told at length on two pages: usually a `mistake` on the page where it \
does not belong (correction: shorten it to a brief mention with a link to the page that owns \
it), or a `merge` when two pages overlap so much that a reader cannot tell where to look.
{_STRUCTURAL}
Within a page (only clear mistakes that you checked against the group lines):
- `mistake`: a sentence that its cited groups do not support (it says more than they do, \
something else, or the opposite); a wrong date, month, year, price, duration or member count \
compared with the cited group (count words must match one group's `backed by` / `last \
backed by` number, never a sum; dates must match `said` or `last backed`); a disagreement \
told one-sidedly (the group is `disputed by` members or `contradicts` another, and the text \
presents one view as settled); stale information presented as current (the newest backing is \
about two years or more before <as_of> and the text gives no date or caveat). A sentence \
that cites several groups is supported when together they support it. Wording, tone, \
length, order and style are never a mistake.

{_fields("the pages' <groups>")}
- `mistake`: `page_id`, `sentence` (the sentence copied EXACTLY from <body>, marks \
included; code checks that it is there), `group_ids` (the groups that show the mistake), \
`correction` (a Hebrew instruction to the page writer: what the sentence must say instead, \
grounded in those groups).
- `note`: one or two Hebrew sentences on the state of this part (what you checked, whether \
you found real problems).

## Rules
- Name only the pages of this part and groups from their <groups>: a suggestion that names \
any other page or group is discarded. You see the other pages only as titles, descriptions \
and scopes in <plan>: never guess what their text says. A merge, a split or a \
contradiction between two pages of this part is fine.
{_RULES}"""

CROSS_SYSTEM = f"""\
{_WIKI} Before writing, the writer listed each page's POINTS: one line per thing the page \
tells, with the groups behind it. The pages' text is reviewed part by part elsewhere. You \
get the plan of the whole wiki and the points of every page, so you see what no part can: \
how the pages relate to each other.

Your job: find what is wrong or missing across pages, and propose concrete fixes. \
{_owner(CROSS_CAP)}

## What you receive
{_PLAN}
- <pages>: one <page id="..." title="..." labels="..."> per page (its description and scope \
are in <plan>; labels: the label ids its groups carry), then its points, one per line: \
`kind | statement | groups` (kind: one of {", ".join(POINT_KINDS)}; the statement in \
Hebrew; the ids of the groups behind it). A `dispute` or `change` point already shows both \
sides.
{_RAISED}

## What to look for
- `contradiction`: two pages whose points state opposite things about the same subject \
(a duration, price, rule, requirement or a provider's quality), and neither page shows the \
other side. {_NOT_CONTRADICTION} `groups_a` are the groups of the point on one side, \
`groups_b` those of the point on the other.
- The same thing told on two pages, or two pages that belong together: a `merge` when they \
overlap so much that a reader cannot tell where to look.
{_STRUCTURAL}
You never propose a `mistake`: you do not see the pages' text.

{_fields("the pages' points")}
- `note`: one or two Hebrew sentences on how the pages fit together (what you compared, \
whether you found real problems).

## Rules
- Name only pages listed in <pages> and groups from their points: a suggestion that names \
any other page or group is discarded.
{_RULES}"""


def evidence_line(record: Record, part: Collection[str]) -> str:
    """A group as the reviewer reads it: step 7's own line, or, on a page that only mentions
    it while its main page is in the same part, a pointer to that page (the prompt stays lean)."""
    if record["role"] != MAIN and record["primary"] in part:
        return f"{record['id']} | mention -> {record['primary']} (full line on that page)"
    return group_line(record)


def page_block(review: ReviewInput, page_id: str, part: Collection[str]) -> str:
    """One page of a part: its body, then its group lines."""
    page = review.pages[page_id]
    lines = "\n".join(evidence_line(record, part) for record in page.records)
    return (f'<page id="{page_id}" title="{page.title}">\n<body>\n{page.body.strip()}\n</body>\n'
            f'<groups count="{len(page.records)}">\n{lines}\n</groups>\n</page>')


def points_block(review: ReviewInput, page_id: str) -> str:
    """One page as the cross pass reads it: title, labels and stored points."""
    stored = review.all_pages[page_id]
    labels = ", ".join(label["id"] for label in stored["front_matter"].get("labels") or [])
    lines = "\n".join(f"- {point_line(point)}" for point in stored["points"])
    return (f'<page id="{page_id}" title="{review.pages[page_id].title}" labels="{labels}">\n'
            f"{lines}\n</page>")


def raised_block(raised: Sequence[Mapping[str, Any]]) -> str:
    """The suggestions already made, compactly: kind, status, pages, title, proposal."""
    rows = [json.dumps({"kind": s["kind"], "status": s["status"], "pages": s["pages"],
                        "title": s.get("title", ""), "proposal": effective_proposal(s)},
                       ensure_ascii=False) for s in raised]
    body = "\n".join(rows) if rows else "none"
    return f'<already_raised count="{len(rows)}">\n{body}\n</already_raised>'


def _tail(raised: Sequence[Mapping[str, Any]], task: str, cap: int) -> CacheSegment:
    return CacheSegment(f"{raised_block(raised)}\n\n{task} and return your suggestions (at most "
                        f"{cap}, the most valuable first; fewer, or none, when nothing is worth "
                        "the owner's time).")


def part_prompt(review: ReviewInput, part_id: str, pages: Sequence[str],
                raised: Sequence[Mapping[str, Any]]) -> tuple[str, list[CacheSegment]]:
    """``(system, user)`` of one part pass."""
    blocks = "\n\n".join(page_block(review, page_id, pages) for page_id in pages)
    names = ", ".join(pages)
    body = (f'{review.plan_block()}\n\n<part id="{part_id}" pages="{names}">\n{blocks}\n'
            "</part>\n\n")
    return PART_SYSTEM, [CacheSegment(body),
                         _tail(raised, f"Review this part (pages {names})", PART_CAP)]


def cross_prompt(review: ReviewInput, pages: Sequence[str],
                 raised: Sequence[Mapping[str, Any]]) -> tuple[str, list[CacheSegment]]:
    """``(system, user)`` of the cross pass over the pages with stored points."""
    blocks = "\n\n".join(points_block(review, page_id) for page_id in pages)
    body = f'{review.plan_block()}\n\n<pages count="{len(pages)}">\n{blocks}\n</pages>\n\n'
    return CROSS_SYSTEM, [CacheSegment(body),
                          _tail(raised, "Compare the pages through their points", CROSS_CAP)]
