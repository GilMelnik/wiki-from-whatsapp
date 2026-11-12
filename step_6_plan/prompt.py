"""What the planning model reads and returns: the prompt, the response schema, and the
conversion of its flat answer into a C7 plan (``step_6_plan.model``).

The model sees every claim group as one compact line (id, stance, distinct supporters and
opposers, first and last month, thread count, entity names, representative claim text) and
nothing about the people behind it; when the list does not fit ``plan.max_input_tokens`` it sees
a sample (``step_6_plan.fit``), with a note and the size guide scaled to the sample.
Structured output cannot express a recursive tree, so the model returns flat lists of category
nodes and pages that point at their parent; the tree is built here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from step_3_extract.scrub import FORBIDDEN_TERM_INSTRUCTION
from step_6_plan.model import PLAN_CONFIG, iter_nodes, sync_pages, validation_errors

_EXAMPLES = PLAN_CONFIG["example_group_ids"]
_GUIDE = PLAN_CONFIG["page_groups"]


def scaled_guide(shown: int, total: int) -> dict[str, int]:
    """The size guide ``plan.page_groups`` in groups of a list that shows ``shown`` of ``total``
    groups: each number times shown / total, rounded, never below 1 (unchanged for a full list)."""

    if shown == total:
        return dict(_GUIDE)
    return {key: max(1, round(value * shown / total)) for key, value in _GUIDE.items()}


def plan_system(shown: int, total: int) -> str:
    """The planning instructions for a list of ``shown`` of ``total`` claim groups.

    For a full list this is the prompt planning has always had. For a sample it says that the
    list is a sample and gives the size guide in groups of the list (``scaled_guide``), so the
    pages come out the size they would from the full list."""

    guide = scaled_guide(shown, total)
    listed = (
        "every claim group, one per line, before the request." if shown == total else
        "a sample of the claim groups, one per line, before the request; a note before the list "
        "says how many groups there are in all and how the sample was chosen."
    )
    scaled = "" if shown == total else (
        f" These numbers count groups of the list, which shows {shown} of all {total} groups: "
        f"they are the guide for the whole corpus (roughly {_GUIDE['typical_min']} to "
        f"{_GUIDE['typical_max']} groups, split above {_GUIDE['split_above']}, fold below "
        f"{_GUIDE['fold_below']}) scaled by that share, so that a page sized by the list comes "
        "out the right size for all the groups."
    )
    return f"""\
You are designing the structure of a Hebrew wiki for an Israeli WhatsApp community of gay \
couples and gay men who are going through surrogacy abroad or considering it. Over several \
years members asked questions and shared experience, recommendations and warnings. Their \
statements were extracted as claims, and claims that say the same thing were grouped. The \
user message lists {listed}

Your job is to propose the wiki's structure: a tree of content whose leaves are the wiki \
pages, plus labels that cut across pages. You do not write the pages. The owner of the wiki \
will review and edit your proposal by hand; then a fast, fairly literal classifier will read \
only the page titles and scopes you wrote and assign every claim group to a page and to the \
labels that match it. So the structure must come from what the members actually talk about, \
weighted by how much they talk about it, not from a generic table of contents.

## The tree
- Levels: {PLAN_CONFIG["min_depth"]} to {PLAN_CONFIG["max_depth"]} levels, counting the \
pages: every page sits on level {PLAN_CONFIG["min_depth"]} or deeper, never below level \
{PLAN_CONFIG["max_depth"]}. The top level is an abstraction layer above the categories, a "world of content" a \
reader can orient by. The owner's example: the steps of the process: before starting, during \
the process, the birth, and afterwards. Use that principle, or another one if the claims \
clearly call for it, and explain the choice in top_level_rationale (a few sentences). Below \
the top level come categories; below a category come pages, or, where a category is large, \
one more level of sub-categories and then pages. Pages are always leaves.
- Pages: size each page by its material and let the number of pages follow from that; there \
is no target number of pages. A page is one coherent subject a member would look up and expect \
to read in one place, and it rests on roughly {guide["typical_min"]} to \
{guide["typical_max"]} claim groups (count the groups of the list that belong to it; the \
support counts show how much the community cares). A subject with more than about \
{guide["split_above"]} groups is too much for one page: split it by sub-subject into pages \
of their own. A subject with fewer than about {guide["fold_below"]} groups is not a page: \
make it a section of the neighbouring page it belongs with. So no few long pages that each \
cover many subjects, and no thin page for a subject that only a handful of groups touch.{scaled}
- Well defined and self-contained: define each page so that every claim group fits one page \
easily and obviously. Pages must not overlap; where a subject could sit in two places, decide \
where it goes and say so in both scopes. Together the pages must leave no common kind of \
claim without a home.
- A facet of many pages (a country, a stage, a type of provider) is not a page: make it a label.

## Page fields
- id: a short, stable, lowercase Latin slug (letters, digits and single hyphens, at most \
{PLAN_CONFIG["slug_max_length"]} characters), for example "egg-donation". Ids are permanent, \
so name the subject, not its position.
- parent_id: the id of the category node the page sits in.
- title_he: a short Hebrew page heading.
- description: one Hebrew sentence saying what the page is about (shown under the title and \
in search results).
- scope: two or three Hebrew sentences that say exactly what belongs on the page AND what does \
not, naming the page that takes each neighbouring subject. The classifier and the owner decide \
where a claim goes from this text alone, so make it concrete (the kinds of questions and \
statements it covers), not abstract.
- aliases_he, aliases_en: other names a reader might search for (a few each; may be empty).
- example_group_ids: {_EXAMPLES["min"]} to {_EXAMPLES["max"]} ids of groups from the list that \
clearly belong on the page.

## Category nodes (every node that is not a page, the top level included)
- id (a slug as above, unique across nodes, pages and labels), parent_id (null for the top \
level), title_he, and description (one Hebrew sentence). List siblings in the order a reader \
should meet them (for phases of the process: chronologically).

## Labels
Labels are facets that cut across pages and are not pages, so that a reader can find, for \
example, everything about one country on whichever pages it appears. The owner's example: by \
country. Propose the label kinds the claims support (for example country, stage of the \
process, type of provider; your call) and, for each kind, the labels that enough groups would \
carry. A label describes a facet of the subject (a country, a stage, a kind of provider, a \
family situation) and never exists to find private or contact details: no label for phone \
numbers, contact details or who to call. A label is one value of a kind: kind "country", \
label "usa" titled "ארצות הברית". Each \
label has an id (slug), a kind (slug), a title_he, and a description: one Hebrew sentence \
precise enough to decide yes or no for a single claim. Skip labels that only a few groups \
would carry. A label must not duplicate a page or the tree's top level. Labels are not part \
of the tree.

## The input
Each line is one claim group: id | stance | support | month or first..last month | threads \
| [entity names] | the claim as worded by its representative member claim. Stance: \
positive (recommends), negative (warns against), neutral or factual. Support: +N \
distinct members backed it, -M disputed it (left out when none). Threads appears only when \
the claim came up in more than one conversation, entity names only when it names any. Support \
shows how much the community cares; dates show what is current. Lines are in order of first \
appearance.

## Rules
- Write titles, descriptions and scopes in Hebrew. {FORBIDDEN_TERM_INSTRUCTION}
- Titles name topics. Never put a private person's name into a title, description or scope.
- Return only the JSON object asked for."""

_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": _TEXT}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


PROPOSAL_SCHEMA: dict[str, Any] = _object({
    "top_level_rationale": _TEXT,
    "nodes": {"type": "array", "items": _object({
        "id": _TEXT,
        "parent_id": {"anyOf": [_TEXT, {"type": "null"}]},
        "title_he": _TEXT,
        "description": _TEXT,
    })},
    "pages": {"type": "array", "items": _object({
        "id": _TEXT,
        "parent_id": _TEXT,
        "title_he": _TEXT,
        "description": _TEXT,
        "scope": _TEXT,
        "aliases_he": _TEXTS,
        "aliases_en": _TEXTS,
        "example_group_ids": _TEXTS,
    })},
    "labels": {"type": "array", "items": _object({
        "id": _TEXT,
        "kind": _TEXT,
        "title_he": _TEXT,
        "description": _TEXT,
    })},
})


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def group_line(group: dict[str, Any]) -> str:
    """One claim group as the model reads it. Only group-level fields: no people, no messages.

    Compact on purpose (the whole corpus goes into one prompt): optional parts are left out
    when they carry nothing (no opposers, one month, one thread, no entities)."""

    first, last = group["first_date"][:7], group["last_date"][:7]
    support = f"+{group['supporter_count']}"
    if group["opposer_count"]:
        support += f" -{group['opposer_count']}"
    parts = [group["group_id"], group["stance"], support, first if first == last else f"{first}..{last}"]
    if group["thread_count"] > 1:
        parts.append(f"{group['thread_count']} threads")
    if group.get("entity_names"):
        parts.append(f"[{'; '.join(_one_line(name) for name in group['entity_names'])}]")
    parts.append(_one_line(group["claim_text"]))
    return " | ".join(parts)


def groups_block(groups: Sequence[dict[str, Any]]) -> str:
    """Every group, one line each, for the start of the user message.

    Not in the (cached) system prompt on purpose: a planning call takes longer than the
    prompt cache lives (5 minutes), so a cache write would cost more and never be read."""

    lines = "\n".join(group_line(group) for group in groups)
    return f'<claim_groups count="{len(groups)}">\n{lines}\n</claim_groups>'


def _part(shown: int, total: int) -> str:
    return f"all {total}" if shown == total else f"{shown} of the {total}"


def sample_note(shown_many: int, many: int, shown_rest: int, rest: int) -> str:
    """The note before a sampled list: how many groups there are, how many are shown, and how
    they were chosen (``many`` groups that more than one member backs, ``rest`` the others)."""

    total = many + rest
    return (
        "Not every claim group fits in this request, so the list below is a sample: it shows "
        f"{shown_many + shown_rest} of all {total} claim groups, namely "
        f"{_part(shown_many, many)} groups that more than one member backs and "
        f"{_part(shown_rest, rest)} groups that fewer than two members back. Where a part is not "
        "shown whole, its groups were chosen at random but evenly over time, so that no period "
        "of the chat is missing. The groups not shown resemble the shown ones in subject and in "
        f"proportion: plan the wiki for all {total} groups as if the list held every one of them."
    )


def initial_request(shown: int, total: int) -> str:
    if shown == total:
        return f"Propose the wiki structure for the {total} claim groups above."
    return f"Propose the wiki structure for all {total} claim groups, of which the {shown} above are a sample."


def retry_request(shown: int, total: int, answer: Any, errors: Sequence[str]) -> str:
    problems = "\n".join(f"- {error}" for error in errors)
    return (
        f"{initial_request(shown, total)}\n\nYour previous answer did not pass validation:\n"
        f"{problems}\n\nThat answer was:\n{json.dumps(answer, ensure_ascii=False)}\n\n"
        "Return the complete corrected answer, keeping everything that was not wrong."
    )


def revision_request(
    plan: dict[str, Any], sample_size: int, findings: Sequence[str],
    unlisted: Sequence[dict[str, Any]],
) -> str:
    """The revision call's request; ``unlisted`` are the groups the findings name that a sampled
    list does not show (their lines are added so the model can read them)."""

    found = "\n".join(f"- {finding}" for finding in findings)
    unlisted_lines = "".join(f"{group_line(group)}\n" for group in unlisted)
    if unlisted_lines:
        unlisted_lines = f"\nThe groups named here that the list does not show:\n{unlisted_lines}"
    return (
        f"This is the structure you proposed:\n"
        f"{json.dumps(proposal_from_plan(plan), ensure_ascii=False)}\n\n"
        f"A classifier then placed a random sample of {sample_size} claim groups, reading only "
        f"the page titles and scopes. What it found:\n{found}\n{unlisted_lines}\n"
        "Revise the structure where these problems are real: sharpen scopes that overlap or "
        "are vague, split overloaded pages, fold pages that attract almost nothing into their "
        "neighbours, and give a home to the kinds of groups that fit nowhere. Keep the ids of "
        "the pages you keep; when you merge pages, keep the id that best names the merged "
        "subject. Return the complete revised answer."
    )


def proposal_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """A C7 plan in the model's flat answer format (the inverse of ``plan_from_proposal``)."""

    pages = plan["pages"]
    nodes: list[dict[str, Any]] = []
    flat_pages: list[dict[str, Any]] = []
    for node, ancestors in iter_nodes(plan["tree"]):
        parent = ancestors[-1] if ancestors else None
        if node["id"] not in pages:
            nodes.append({"id": node["id"], "parent_id": parent,
                          "title_he": node["title_he"], "description": node["description"]})
            continue
        page = pages[node["id"]]
        flat_pages.append({
            "id": node["id"], "parent_id": parent, "title_he": page["title_he"],
            "description": page["description"], "scope": page["scope"],
            "aliases_he": page["aliases"]["he"], "aliases_en": page["aliases"]["en"],
            "example_group_ids": page["example_group_ids"],
        })
    return {
        "top_level_rationale": plan["metadata"].get("top_level_rationale", ""),
        "nodes": nodes,
        "pages": flat_pages,
        "labels": [{"id": lid, **{k: label[k] for k in ("kind", "title_he", "description")}}
                   for lid, label in plan["labels"].items()],
    }


def _attach(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Build the tree from the flat node and page lists; returns (tree, errors)."""

    errors: list[str] = []
    built: dict[str, dict[str, Any]] = {}
    parents: dict[str, Any] = {}
    for kind, items in (("node", raw["nodes"]), ("page", raw["pages"])):
        for item in items:
            if item["id"] in built:
                errors.append(f"{kind} id {item['id']!r} is used more than once")
                continue
            built[item["id"]] = {"id": item["id"], "title_he": item["title_he"],
                                 "description": item["description"], "children": []}
            parents[item["id"]] = item["parent_id"]
    page_ids = {page["id"] for page in raw["pages"]}
    tree: list[dict[str, Any]] = []
    for item_id, parent in parents.items():
        if parent is None:
            tree.append(built[item_id])
        elif parent not in built or parent in page_ids:
            errors.append(f"{item_id!r} has parent_id {parent!r}, which is not a category node")
        else:
            built[parent]["children"].append(built[item_id])
    reachable = {node["id"] for node, _ in iter_nodes(tree)}
    errors += [f"{item_id!r} cannot be reached from the top level (its parent_id chain loops)"
               for item_id in built if item_id not in reachable
               and parents[item_id] in built and parents[item_id] not in page_ids]
    return tree, errors


def plan_from_proposal(
    raw: dict[str, Any], group_ids: set[str]
) -> tuple[dict[str, Any], list[str]]:
    """Turn the model's answer into a C7 plan; returns ``(plan, errors)``.

    ``errors`` holds every structural and validation problem (empty = usable). Example group
    ids that are not real groups are dropped silently: they are hints, not structure.
    """

    tree, errors = _attach(raw)
    pages = {
        page["id"]: {
            "title_he": page["title_he"],
            "description": page["description"],
            "scope": page["scope"],
            "path": [],
            "aliases": {"he": page["aliases_he"], "en": page["aliases_en"]},
            "example_group_ids": [g for g in page["example_group_ids"] if g in group_ids],
        }
        for page in raw["pages"]
    }
    labels = {
        label["id"]: {"title_he": label["title_he"], "description": label["description"],
                      "kind": label["kind"]}
        for label in raw["labels"]
    }
    errors += [f"label id {lid!r} is used more than once"
               for lid in labels if sum(1 for lab in raw["labels"] if lab["id"] == lid) > 1]
    plan = sync_pages({
        "tree": tree,
        "pages": pages,
        "labels": labels,
        "metadata": {"top_level_rationale": raw["top_level_rationale"]},
    })
    return plan, errors + validation_errors(plan)
