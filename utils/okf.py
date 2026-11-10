"""Wiki pages as markdown with an Open Knowledge Format (OKF) front-matter block.

Built against OKF **v0.2** (Google Cloud, 2026-07-25), canonical spec:
https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md
(the older copy under GoogleCloudPlatform/knowledge-catalog/okf is frozen). In OKF only
``type`` is required; every other key is optional and unknown keys must be preserved.
Every timestamp is an ISO 8601 datetime with an explicit UTC offset; ``stale_after`` is
an absolute instant; ``status`` is draft | stable | deprecated and absent means stable;
``generated.by`` / ``verified[].by`` are actors (``producer/version``, ``human:<id>``,
``process:<id>``); each ``sources`` entry needs a ``resource``.

mkdocs reads the block as ``page.meta`` and never renders it. Material interprets only
``title`` (page title), ``description`` (``<meta>`` tag), ``status`` (a badge next to the
page in the nav, so ``stable`` is never written) and ``tags`` (chips, only if the tags
plugin is enabled); no other key here collides with Material's own (``hide``, ``search``,
``template``, ``icon``, ``subtitle``, ``author``, ``comments``, ``source``...).

Our keys beyond OKF are documented on ``build_front_matter``. Wiki links in the body
(``[[page_id|text]]``) are handled by ``utils.wiki_links``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import yaml

from utils.config import section

KEY_ORDER = (
    "okf_version", "type", "title", "description", "summary", "questions", "tags", "status",
    "lang", "dir", "aliases", "entities", "labels", "path", "related", "claims", "sources",
    "usage_window", "generated", "verified", "stale_after",
)  # fmt: skip
OPTIONAL_KEYS = frozenset({"summary", "questions", "labels", "status", "usage_window", "verified"})

_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
_ACTOR = re.compile(r"(?:human|process):\S+|[^\s/:]+/\S+")
_RELATION = re.compile(r"[a-z][a-z0-9_]*")
_FENCE_LINE = re.compile(r"^---[ \t]*$", re.MULTILINE)


class _FrontMatterLoader(yaml.SafeLoader):
    """SafeLoader that keeps ISO timestamps as strings so the round trip is exact."""


_FrontMatterLoader.yaml_implicit_resolvers = {
    char: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

Checker = Callable[[Any], "str | None"]


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _err_str(value: Any) -> str | None:
    return None if _is_str(value) else "expected a non-empty string"


def _err_line(value: Any) -> str | None:
    return None if _is_str(value) and "\n" not in value else "expected a non-empty single line"


def _err_str_list(value: Any) -> str | None:
    ok = isinstance(value, list) and all(_is_str(item) for item in value)
    return None if ok else "expected a list of non-empty strings"


def _err_ts(value: Any) -> str | None:
    try:
        if isinstance(value, str) and _TS.fullmatch(value) and _parse_ts(value).tzinfo:
            return None
    except ValueError:
        pass
    return f"expected an ISO 8601 datetime with UTC offset (2026-06-30T14:00:00Z), got {value!r}"


def _err_day(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, str) and len(value) == 10:
            date.fromisoformat(value)
            return None
    except ValueError:
        pass
    return None if _err_ts(value) is None else f"expected YYYY-MM-DD, an ISO datetime or null, got {value!r}"


def _err_count(value: Any) -> str | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return None if ok else f"expected a non-negative integer, got {value!r}"


def _err_actor(value: Any) -> str | None:
    ok = isinstance(value, str) and _ACTOR.fullmatch(value)
    return None if ok else f"expected an actor (producer/version, human:<id>, process:<id>), got {value!r}"


def _err_relation(value: Any) -> str | None:
    ok = isinstance(value, str) and _RELATION.fullmatch(value)
    return None if ok else f"expected a snake_case relation name, got {value!r}"


def _one_of(allowed: tuple[str, ...]) -> Checker:
    return lambda value: None if value in allowed else f"expected one of {list(allowed)}, got {value!r}"


def _mapping(fields: dict[str, tuple[Checker, bool]]) -> Checker:
    """Checker for a dict whose ``fields`` are ``name -> (checker, required)``; no other names."""

    def check(value: Any) -> str | None:
        if not isinstance(value, dict):
            return f"expected a mapping, got {type(value).__name__}"
        unknown = sorted(value.keys() - fields.keys())
        if unknown:
            return f"unknown field {unknown[0]!r}"
        for name, (checker, required) in fields.items():
            if name not in value:
                if required:
                    return f"missing field {name!r}"
            elif (error := checker(value[name])) is not None:
                return f"field {name!r}: {error}"
        return None

    return check


def _list_of(item: Checker) -> Checker:
    def check(value: Any) -> str | None:
        if not isinstance(value, list):
            return f"expected a list, got {type(value).__name__}"
        for index, entry in enumerate(value):
            if (error := item(entry)) is not None:
                return f"item {index}: {error}"
        return None

    return check


_BY_AT = {"by": (_err_actor, True), "at": (_err_ts, True)}
_CHECKS: dict[str, Checker] = {
    "okf_version": _err_str,
    "type": _err_str,
    "title": _err_str,
    "description": _err_line,
    "summary": _err_str,
    "questions": _err_str_list,
    "tags": _err_str_list,
    "status": _one_of(tuple(section("pages")["statuses"])),
    "lang": _err_str,
    "dir": _one_of(("ltr", "rtl")),
    "aliases": _mapping({"he": (_err_str_list, True), "en": (_err_str_list, True)}),
    "entities": _err_str_list,
    "labels": _list_of(_mapping({"id": (_err_str, True), "kind": (_err_str, True)})),
    "path": _list_of(_mapping({"id": (_err_str, True), "title": (_err_str, True)})),
    "related": _list_of(_mapping({"slug": (_err_str, True), "relation": (_err_relation, True)})),
    "claims": _mapping(
        {
            "total": (_err_count, True),
            "supporting": (_err_count, True),
            "opposing": (_err_count, True),
            "first_claim_at": (_err_day, True),
            "last_claim_at": (_err_day, True),
        }
    ),
    "sources": _list_of(
        _mapping(
            {
                "id": (_err_str, True),
                "resource": (_err_str, True),
                "title": (_err_str, False),
                "author": (_err_actor, False),
                "usage_count": (_err_count, False),
                "last_modified": (_err_ts, False),
            }
        )
    ),
    "usage_window": _mapping({"from": (_err_ts, True), "to": (_err_ts, True)}),
    "generated": _mapping(_BY_AT),
    "verified": _list_of(_mapping(_BY_AT)),
    "stale_after": _err_ts,
}


def validate_front_matter(front_matter: dict[str, Any]) -> None:
    """Raise ``ValueError`` naming the first missing or malformed key. Unknown keys are allowed."""
    missing = [k for k in KEY_ORDER if k not in OPTIONAL_KEYS and k not in front_matter]
    if missing:
        raise ValueError(f"front matter is missing required key(s): {missing}")
    for key, value in front_matter.items():
        if key in _CHECKS and (error := _CHECKS[key](value)) is not None:
            raise ValueError(f"front-matter key {key!r}: {error}")


def _to_utc_datetime(value: Any) -> Any:
    """Normalise a ``YYYY-MM-DD`` day to UTC midnight; other values pass through for validation."""
    return f"{value}T00:00:00Z" if isinstance(value, str) and len(value) == 10 else value


def build_front_matter(
    *,
    title: str,
    description: str,
    tags: list[str],
    generated_by: str,
    generated_at: str,
    sources: list[dict[str, Any]],
    aliases: dict[str, list[str]],
    entities: list[str],
    claims: dict[str, Any],
    related: list[dict[str, str]],
    path: list[dict[str, str]],
    summary: str | None = None,
    questions: list[str] | None = None,
    labels: list[dict[str, str]] | None = None,
    verified: list[dict[str, str]] | None = None,
    usage_window: dict[str, str] | None = None,
    status: str | None = None,
    stale_after: str | None = None,
) -> dict[str, Any]:
    """Return a validated front-matter dict in stable key order (``ValueError`` names a bad key).

    OKF keys (required unless marked optional). ``okf_version``, ``type`` (``wiki-page``),
    ``lang`` and ``dir`` come from ``config.json`` ``pages``.
      title, description (one line, from the page writer / C7 ``title_he``, ``description``);
      tags; status (optional, only ``draft``/``deprecated`` are written);
      generated {by: ``producer/model``, at} (``at`` = last content change, so keep it
      unchanged when the page is regenerated from identical input);
      verified [{by: ``human:<id>``|``process:<id>``, at}] (optional, review tool);
      sources [{id: C6 ``group_id``, resource: ``claim-group:<group_id>``, usage_count:
      distinct participants who voiced the claim = C6 supporter_count + opposer_count,
      last_modified: C6 ``last_date``, title?}]; usage_window {from, to} (optional; chat span);
      stale_after (default: ``generated.at`` + ``pages.stale_after_days``).
    Extension keys (our own, no personal data; day values are ``YYYY-MM-DD``):
      aliases {he, en}: other names for retrieval (C7 ``pages[id].aliases``);
      entities: canonical entity names (union of C6 ``entities``; never people);
      claims {total: number of claim groups, supporting / opposing: sums of C6
      supporter_count / opposer_count, first_claim_at / last_claim_at: min first_date /
      max last_date} - time coverage and disagreement level (opposing share);
      related [{slug: page_id, relation: snake_case}] and path [{id, title}]: place in the
      C7 tree (ancestors root -> parent) and neighbours; labels [{id, kind}] (optional,
      C7 ``labels`` x C8 assignments, e.g. kind ``country``): facets;
      summary (optional, one paragraph) and questions (optional, what the page answers):
      written by the page-writer LLM next to the body.
    """
    cfg = section("pages")
    generated = {"by": generated_by, "at": _to_utc_datetime(generated_at)}
    if stale_after is None and _err_ts(generated["at"]) is None:
        due = _parse_ts(generated["at"]) + timedelta(days=cfg["stale_after_days"])
        stale_after = due.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    values: dict[str, Any] = {
        "okf_version": cfg["okf_version"],
        "type": cfg["page_type"],
        "title": title,
        "description": description,
        "summary": summary,
        "questions": questions,
        "tags": tags,
        "status": None if status == cfg["default_status"] else status,
        "lang": cfg["lang"],
        "dir": cfg["dir"],
        "aliases": aliases,
        "entities": entities,
        "labels": labels,
        "path": path,
        "related": related,
        "claims": claims,
        "sources": [
            {**s, "last_modified": _to_utc_datetime(s["last_modified"])}
            if "last_modified" in s
            else s
            for s in sources
        ],
        "usage_window": usage_window
        and {k: _to_utc_datetime(v) for k, v in usage_window.items()},
        "generated": generated,
        "verified": verified and [{**v, "at": _to_utc_datetime(v.get("at"))} for v in verified],
        "stale_after": _to_utc_datetime(stale_after),
    }
    front_matter = {
        key: copy.deepcopy(values[key])
        for key in KEY_ORDER
        if key not in OPTIONAL_KEYS or values[key]
    }
    validate_front_matter(front_matter)
    return front_matter


def render_page(front_matter: dict[str, Any], body_md: str) -> str:
    """Return ``---`` YAML ``---`` blank line, then the body with exactly one trailing newline.

    Keys are written in ``KEY_ORDER`` (unknown keys after them); the same input always gives
    the same bytes. Leading/trailing newlines of the body are normalised.
    """
    validate_front_matter(front_matter)
    ordered = {k: front_matter[k] for k in KEY_ORDER if k in front_matter}
    ordered.update({k: v for k, v in front_matter.items() if k not in ordered})
    block = yaml.safe_dump(
        ordered, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10**9
    )
    body = body_md.strip("\n")
    return f"---\n{block}---\n" + (f"\n{body}\n" if body else "")


def parse_page(markdown: str) -> tuple[dict[str, Any], str]:
    """Split a rendered page into ``(front_matter, body_md)``; inverse of ``render_page``.

    Not validated (consumers must tolerate incomplete blocks); ``ValueError`` when the file has
    no front-matter block or it is not a YAML mapping. Timestamps stay strings.
    """
    if not markdown.startswith("---\n"):
        raise ValueError("page does not start with a '---' front-matter block")
    closing = _FENCE_LINE.search(markdown, 4)
    if closing is None:
        raise ValueError("front-matter block has no closing '---' line")
    try:
        data = yaml.load(markdown[4 : closing.start()], Loader=_FrontMatterLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"front-matter block is not valid YAML: {error}") from error
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"front matter must be a YAML mapping, got {type(data).__name__}")
    return data, markdown[closing.end() :].lstrip("\n")
