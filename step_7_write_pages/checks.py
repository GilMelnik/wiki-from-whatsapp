"""Guarantees enforced in code on every answer of the page writer, never trusted to the model.

``AnswerRules`` says what one answer is checked against: the group ids it may cite, the page's
``main`` groups (which its points must account for), the ids it must cite (the "both sides"
floor: disputed or contradicted groups), the link targets (the plan's pages and labels) and,
for an update, the sentences of the current body (kept text is not held to the statement
rules it may predate). ``problems`` lists what is wrong in words the model can act on (the
writer sends them back in its one retry):
- the body: only the page's groups cited, valid links, every disputed / contradicted group
  cited, no sentence resting on a withheld group (``step_7_write_pages.withhold``; an update
  of a text written before the rule);
- the points (``step_7_write_pages.points``): every main group in exactly one point or left
  out with a reason; no mark on a group that was left out; every point carried by a statement
  that cites one of its groups; at most ``pages.max_marks_per_statement`` marks per statement;
  a point told in at most ``pages.max_statements_per_point`` statements after the lead (that
  cap is per point kind: a dispute and a change may take more than the default).
``cap_marks`` runs on every answer before the checks: a statement with more marks than the cap
keeps the marks of disputed or contradicted groups first, then the most backed, then the most
recent, and loses the rest from the body (never the last citation of a disputed or
contradicted group, never in text of an update that was kept); the retry is for what code
cannot fix. ``repair`` fixes what code can fix: a sentence that cites a withheld group is deleted whole
(its statement is never kept without its mark), an unknown citation is removed, an unknown or malformed
link becomes its plain text (``utils.wiki_links``), a front-matter block, a ``#`` heading and
any footnote definition the model wrote are taken out, ``related`` keeps only planned pages,
the points keep only the page's main groups, each once. A missing citation or an unaccounted
group cannot be repaired; it is recorded. A page body holds the marks only: step 9 renders the
references from the groups. ``strip_definitions`` takes the definitions out of a body stored
before that change.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from step_7_write_pages.inputs import MAIN, PAGES_CONFIG, Record
from step_7_write_pages.points import (
    MAX_MARKS,
    Statement,
    account,
    citing,
    over_cap,
    retold,
    tidy,
    told_cap,
)
from utils.config import section
from utils.wiki_links import WikiLink, find_wiki_links, replace_wiki_links

_PREFIX = re.escape(section("aggregate")["group_id_prefix"])
CITATION = re.compile(rf"\[\^({_PREFIX}[0-9a-f]+)\](?!:)")
_ANY_MARK = re.compile(r"\[\^([^\]\s]+)\](?!:)")
_DEFINITION = re.compile(r"^\[\^[^\]\s]+\]:.*(?:\n|$)", re.MULTILINE)
_H1 = re.compile(r"^#(?=\s)", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_PARAGRAPH_END = re.compile(r"\n[ \t]*\n")
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---[ \t]*\n", re.DOTALL)
_MALFORMED = re.compile(r"\[\[(?:[^\[\]\n|]*\|)?([^\[\]\n]+)\]\]")
_RELATION = re.compile(r"[^a-z0-9_]+")
_PLACEHOLDER = "\x00{}\x00"
# A sentence: up to its end mark (not a decimal point) and the citation marks right after it,
# or up to the end of its line.
_SENTENCE = re.compile(r"(?:[^.!?\n]|[.!?](?=\d))+(?:[.!?]+(?:\[\^[^\]\s]+\])*)?")
_QUOTED = 80  # characters of a statement quoted in a problem


def cited_ids(body: str) -> list[str]:
    """Group ids cited in ``body``, in order of first citation."""
    return list(dict.fromkeys(CITATION.findall(body)))


def strip_definitions(body: str) -> str:
    """The body without footnote definition lines (the form the model reads and writes)."""
    return _DEFINITION.sub("", body).rstrip() + "\n"


def sentences(body: str) -> list[str]:
    """The body's sentences (footnote definitions left out), stripped."""
    found = (part.strip() for part in _SENTENCE.findall(strip_definitions(body)))
    return [part for part in found if part]


def statements(body: str) -> list[Statement]:
    """The body's sentences that cite at least one group (see ``points.Statement``)."""
    body = strip_definitions(body).lstrip()
    ends = [match.start() for match in (_HEADING.search(body), _PARAGRAPH_END.search(body)) if match]
    lead_end = min(ends, default=len(body))
    found = []
    for match in _SENTENCE.finditer(body):
        marks = tuple(dict.fromkeys(CITATION.findall(match.group(0))))
        if marks:
            found.append(Statement(match.group(0).strip(), marks, match.start() < lead_end))
    return found


def prose_words(body: str) -> int:
    """Words a reader sees (marks and definitions left out; a link counts as its text)."""
    text = replace_wiki_links(strip_definitions(body), lambda link: link.text or link.target)
    return len(_ANY_MARK.sub("", text).split())


@dataclass(frozen=True)
class AnswerRules:
    """What one answer is checked against (see the module docstring)."""

    page_id: str
    allowed_ids: frozenset[str]
    main_ids: tuple[str, ...]
    required_ids: tuple[str, ...]
    page_targets: frozenset[str]
    label_targets: frozenset[str]
    frozen: frozenset[str] = frozenset()
    withheld: frozenset[str] = frozenset()
    backing: Mapping[str, tuple[int, str]] = field(default_factory=dict)  # id -> (supporters, last)

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


def uncited_required(body: str, rules: AnswerRules) -> list[str]:
    """Disputed or contradicted groups the body does not cite."""
    cited = set(cited_ids(body))
    return [gid for gid in rules.required_ids if gid not in cited]


def _quote(text: str) -> str:
    return f"«{text[:_QUOTED]}{'…' if len(text) > _QUOTED else ''}»"


def _body_problems(body: str, rules: AnswerRules) -> list[str]:
    found: list[str] = []
    marks = list(dict.fromkeys(_ANY_MARK.findall(body)))
    withheld = [mark for mark in marks if mark in rules.withheld]
    if withheld:
        found.append(f"cites groups that may appear on no page: {', '.join(withheld)}; delete "
                     "every sentence that rests on them, and do not say what they said in any "
                     "other words")
    unknown = [mark for mark in marks if mark not in rules.allowed_ids and mark not in rules.withheld]
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
    uncited = uncited_required(body, rules)
    if uncited:
        found.append("these groups are disputed or contradicted but not cited; present both "
                     f"sides and cite them: {', '.join(uncited)}")
    return found


def _point_problems(answer: Mapping[str, Any], rules: AnswerRules) -> list[str]:
    points, left_out, body = answer["points"], answer["left_out"], answer["body"]
    found = account(points, left_out, rules.main_ids).problems()
    stated = statements(body)
    cited = set(cited_ids(body))
    marked = [entry["group"] for entry in left_out if entry["group"] in cited]
    if marked:
        found.append("the body cites groups listed in left_out; put each in the point its "
                     f"sentence says, or take its mark out: {', '.join(marked)}")
    uncarried = [f"{_quote(p['statement'])} ({', '.join(p['groups'])})"
                 for p in points if p["groups"] and not citing(p, stated)]
    if uncarried:
        found.append("these points are carried by no statement; say each in the article, citing "
                     "one or more of its groups (or fold it into the point it belongs to): "
                     + "; ".join(uncarried))
    heavy = over_cap(stated, rules.frozen)
    if heavy:
        found.append(f"these statements carry more than {MAX_MARKS} marks; keep at most "
                     f"{MAX_MARKS} per sentence, the point's most backed and most recent groups "
                     "(the point keeps the full list): "
                     + "; ".join(f"{_quote(s.text)} ({len(s.marks)} marks)" for s in heavy))
    told = retold(points, stated, rules.frozen)
    for cap in dict.fromkeys(told_cap(p["kind"]) for p, _ in told):
        found.append(f"these points are told in more than {cap} statements after the lead; "
                     "say each point once, and after it only what adds something: "
                     + "; ".join(f"{_quote(p['statement'])} ({count} statements)"
                                 for p, count in told if told_cap(p["kind"]) == cap))
    return found


def problems(answer: Mapping[str, Any], rules: AnswerRules) -> list[str]:
    """What is wrong with ``answer`` (empty list = it passes every check)."""
    body = answer["body"]
    if not body.strip():
        return ["the body is empty: return the whole article"]
    return _body_problems(body, rules) + _point_problems(answer, rules)


def cap_marks(answer: Mapping[str, Any], rules: AnswerRules) -> tuple[dict[str, Any], int]:
    """``answer`` with each statement over ``MAX_MARKS`` marks cut to the cap, and the number of
    marks dropped (see the module docstring for which marks stay).

    A statement is left as it is, for the check to fail, when it cites an id that is not a group
    of the page (a repair of its own), when it is kept text of an update, or when dropping would
    leave a disputed or contradicted group cited by no other sentence."""
    body = answer["body"]
    cited = Counter(gid for match in _SENTENCE.finditer(body)
                    for gid in set(CITATION.findall(match.group(0))))
    required = set(rules.required_ids)
    dropped = 0

    def cut(match: re.Match[str]) -> str:
        nonlocal dropped
        text = match.group(0)
        marks = list(dict.fromkeys(CITATION.findall(text)))
        if (len(marks) <= MAX_MARKS or text.strip() in rules.frozen
                or not rules.allowed_ids.issuperset(marks)):
            return text
        ranked = sorted(marks, key=lambda gid: (gid in required, *rules.backing[gid]), reverse=True)
        out = ranked[MAX_MARKS:]
        if any(gid in required and cited[gid] < 2 for gid in out):
            return text
        cited.subtract(out)
        dropped += len(out)
        for gid in out:
            text = re.sub(rf"\[\^{re.escape(gid)}\](?!:)", "", text)
        return text

    capped = _SENTENCE.sub(cut, body)
    return {**answer, "body": capped}, dropped


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

    def drop_withheld(match: re.Match[str]) -> str:
        cited = rules.withheld.intersection(_ANY_MARK.findall(match.group(0)))
        if not cited:
            return match.group(0)
        repairs.append(f"deleted a sentence resting on withheld group(s) {', '.join(sorted(cited))}")
        return ""

    body = _SENTENCE.sub(drop_withheld, body)
    body = _ANY_MARK.sub(keep_mark, body)
    body = _clean_links(body, rules, repairs)
    tidied = tidy(answer["points"], answer["left_out"], rules.main_ids)
    if tidied.dropped:
        repairs.append(f"took out of the points ids that are not main groups or were listed "
                       f"again: {', '.join(dict.fromkeys(tidied.dropped))}")
    fixed = {
        "points": tidied.points,
        "left_out": tidied.left_out,
        "body": body.strip() + "\n",
        "summary": _plain(answer.get("summary") or ""),
        "questions": [q for q in (_plain(q) for q in answer.get("questions") or []) if q],
        "related": _related(answer.get("related") or [], rules),
    }
    return fixed, repairs


def rules_for(page_id: str, records: Sequence[Record], plan: Mapping[str, Any], *,
              required: Collection[str], frozen: Collection[str] = (),
              withheld: Collection[str] = ()) -> AnswerRules:
    """The rules of one answer about ``records`` (the page's groups now); ``frozen``: the
    sentences of the current body when updating; ``withheld``: the groups no page may rest
    on."""
    return AnswerRules(
        page_id=page_id,
        allowed_ids=frozenset(record["id"] for record in records),
        main_ids=tuple(record["id"] for record in records if record["role"] == MAIN),
        required_ids=tuple(required),
        page_targets=frozenset(plan["pages"]),
        label_targets=frozenset(plan["labels"]),
        frozen=frozenset(frozen),
        withheld=frozenset(withheld),
        backing={record["id"]: (record["supporters"], record["last"]) for record in records},
    )
