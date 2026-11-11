"""Guarantees enforced in code on every answer of the page writer, never trusted to the model.

``AnswerRules`` says what one answer is checked against: the group ids it may cite, the ids
it must cite (the "both sides" floor: disputed or contradicted groups), the ids whose cited
share must reach ``pages.min_cited_share``, and the link targets (the plan's pages and
labels). ``problems`` lists what is wrong in words the model can act on (the writer sends
them back in its one retry); ``repair`` fixes what code can fix: an unknown citation is
removed, an unknown or malformed link becomes its plain text (``utils.wiki_links``), a
front-matter block, a ``#`` heading and any footnote definition the model wrote are taken
out, ``related`` keeps only planned pages. A missing citation cannot be repaired; it is
recorded. A page body holds the marks only: step 9 renders the references from the groups.
``strip_definitions`` takes the definitions out of a body stored before that change.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from step_7_write_pages.inputs import PAGES_CONFIG, Record
from utils.config import section
from utils.wiki_links import WikiLink, find_wiki_links, replace_wiki_links

_PREFIX = re.escape(section("aggregate")["group_id_prefix"])
CITATION = re.compile(rf"\[\^({_PREFIX}[0-9a-f]+)\](?!:)")
_ANY_MARK = re.compile(r"\[\^([^\]\s]+)\](?!:)")
_DEFINITION = re.compile(r"^\[\^[^\]\s]+\]:.*(?:\n|$)", re.MULTILINE)
_H1 = re.compile(r"^#(?=\s)", re.MULTILINE)
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---[ \t]*\n", re.DOTALL)
_MALFORMED = re.compile(r"\[\[(?:[^\[\]\n|]*\|)?([^\[\]\n]+)\]\]")
_RELATION = re.compile(r"[^a-z0-9_]+")
_PLACEHOLDER = "\x00{}\x00"


def cited_ids(body: str) -> list[str]:
    """Group ids cited in ``body``, in order of first citation."""
    return list(dict.fromkeys(CITATION.findall(body)))


def strip_definitions(body: str) -> str:
    """The body without footnote definition lines (the form the model reads and writes)."""
    return _DEFINITION.sub("", body).rstrip() + "\n"


@dataclass(frozen=True)
class AnswerRules:
    """What one answer is checked against (see the module docstring)."""

    page_id: str
    allowed_ids: frozenset[str]
    required_ids: tuple[str, ...]
    coverage_ids: tuple[str, ...]
    page_targets: frozenset[str]
    label_targets: frozenset[str]
    min_share: float = field(default=PAGES_CONFIG["min_cited_share"])

    def valid_link(self, link: WikiLink) -> bool:
        if link.kind == "label":
            return link.target in self.label_targets
        return link.target in self.page_targets and link.target != self.page_id


def _malformed_spans(body: str) -> list[str]:
    """``[[...]]`` texts that are not wiki links ``utils.wiki_links`` recognises."""
    links = find_wiki_links(body)
    inside = [(link.start, link.end) for link in links]
    return [match.group(0) for match in _MALFORMED.finditer(body)
            if not any(start <= match.start() < end for start, end in inside)]


def coverage(body: str, rules: AnswerRules) -> dict[str, Any]:
    """Cited share of ``rules.coverage_ids`` and the required ids still uncited."""
    cited = set(cited_ids(body))
    covered = [gid for gid in rules.coverage_ids if gid in cited]
    share = len(covered) / len(rules.coverage_ids) if rules.coverage_ids else 1.0
    return {
        "cited_share": round(share, 3),
        "uncited": [gid for gid in rules.coverage_ids if gid not in cited],
        "uncited_required": [gid for gid in rules.required_ids if gid not in cited],
    }


def problems(answer: Mapping[str, Any], rules: AnswerRules) -> list[str]:
    """What is wrong with ``answer`` (empty list = it passes every check)."""
    body = answer["body"]
    if not body.strip():
        return ["the body is empty: return the whole article"]
    found: list[str] = []
    unknown = [mark for mark in dict.fromkeys(_ANY_MARK.findall(body))
               if mark not in rules.allowed_ids]
    if unknown:
        found.append(f"cites ids that are not groups of this page: {', '.join(unknown)}; "
                     "mark only ids from <groups>")
    bad_links = [f"[[{'label:' if link.kind == 'label' else ''}{link.target}|...]]"
                 for link in find_wiki_links(body) if not rules.valid_link(link)]
    if bad_links:
        found.append(f"links to targets that are not in <catalogue> (or to this page itself): "
                     f"{', '.join(dict.fromkeys(bad_links))}")
    malformed = _malformed_spans(body)
    if malformed:
        found.append(f"malformed links (anchors or other syntax): {', '.join(malformed[:10])}; "
                     "write [[page_id|text]] or [[label:label_id|text]]")
    stats = coverage(body, rules)
    if stats["uncited_required"]:
        found.append("these groups are disputed or contradicted but not cited; present both "
                     f"sides and cite them: {', '.join(stats['uncited_required'])}")
    if stats["cited_share"] < rules.min_share:
        found.append(
            f"only {stats['cited_share']:.0%} of the page's main groups are cited (at least "
            f"{rules.min_share:.0%} must be); work in the uncited groups that add something: "
            f"{', '.join(stats['uncited'])}")
    return found


def _clean_links(body: str, rules: AnswerRules, repairs: list[str]) -> str:
    def resolve(link: WikiLink) -> str | None:
        if rules.valid_link(link):
            return _markup(link)
        repairs.append(f"unlinked {link.kind} {link.target}")
        return None

    body = replace_wiki_links(body, resolve)
    links = find_wiki_links(body)
    kept = [body[link.start:link.end] for link in links]
    protected = body
    for index, link in reversed(list(enumerate(links))):
        protected = protected[:link.start] + _PLACEHOLDER.format(index) + protected[link.end:]

    def plain(match: re.Match[str]) -> str:
        repairs.append(f"unlinked malformed {match.group(0)}")
        return match.group(1)

    protected = _MALFORMED.sub(plain, protected)
    for index, text in enumerate(kept):
        protected = protected.replace(_PLACEHOLDER.format(index), text)
    return protected


def _markup(link: WikiLink) -> str:
    prefix = "label:" if link.kind == "label" else ""
    return f"[[{prefix}{link.target}|{link.text}]]" if link.text else f"[[{prefix}{link.target}]]"


def _plain(text: str) -> str:
    """A summary or question: no marks, no links."""
    text = _ANY_MARK.sub("", replace_wiki_links(text, lambda link: None))
    return " ".join(text.split())


def _related(raw: Sequence[Mapping[str, Any]], rules: AnswerRules) -> list[dict[str, str]]:
    related: dict[str, str] = {}
    for entry in raw:
        slug = str(entry.get("page_id") or entry.get("slug") or "").strip()
        if slug in rules.page_targets and slug != rules.page_id and slug not in related:
            relation = _RELATION.sub("_", str(entry.get("relation") or "").lower()).strip("_")
            if not relation or not relation[0].isalpha():
                relation = PAGES_CONFIG["default_relation"]
            related[slug] = relation
    return [{"slug": slug, "relation": relation} for slug, relation in related.items()]


def repair(answer: Mapping[str, Any], rules: AnswerRules) -> tuple[dict[str, Any], list[str]]:
    """``answer`` with everything code can fix fixed, and what was changed.

    The returned ``related`` uses the C9 form ``[{slug, relation}]``."""
    repairs: list[str] = []
    body = answer["body"]
    if _FRONT_MATTER.match(body):
        body = _FRONT_MATTER.sub("", body, count=1)
        repairs.append("removed a front-matter block")
    if _DEFINITION.search(body):
        body = _DEFINITION.sub("", body)
        repairs.append("removed footnote definitions written by the model")
    if _H1.search(body):
        body = _H1.sub("##", body)
        repairs.append("turned '#' headings into '##'")

    def keep_mark(match: re.Match[str]) -> str:
        if match.group(1) in rules.allowed_ids:
            return match.group(0)
        repairs.append(f"removed citation {match.group(1)}")
        return ""

    body = _ANY_MARK.sub(keep_mark, body)
    body = _clean_links(body, rules, repairs)
    fixed = {
        "body": body.strip() + "\n",
        "summary": _plain(answer.get("summary") or ""),
        "questions": [q for q in (_plain(q) for q in answer.get("questions") or []) if q],
        "related": _related(answer.get("related") or [], rules),
    }
    return fixed, repairs


def rules_for(page_id: str, records: Sequence[Record], plan: Mapping[str, Any], *,
              required: Collection[str], coverage_ids: Collection[str]) -> AnswerRules:
    """The rules of one answer about ``records`` (the page's groups now)."""
    return AnswerRules(
        page_id=page_id,
        allowed_ids=frozenset(record["id"] for record in records),
        required_ids=tuple(required),
        coverage_ids=tuple(coverage_ids),
        page_targets=frozenset(plan["pages"]),
        label_targets=frozenset(plan["labels"]),
    )
