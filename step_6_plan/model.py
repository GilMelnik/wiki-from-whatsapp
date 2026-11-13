"""The wiki plan (contract C7): shape, validation and the tree helpers every other module uses.

A plan is a JSON object::

    {"tree": [{id, title_he, description, children: [...]}],
     "pages": {page_id: {title_he, description, scope, path: [node ids], aliases: {he, en},
                         example_group_ids: [group_id], merged_from?: [page_id]}},
     "labels": {label_id: {title_he, description, kind, merged_from?: [label_id]}},
     "metadata": {..., retired_ids?: [id]}}

The tree is the wiki's navigation: the top level is an abstraction layer (for example the
phases of the process), inner nodes are categories, and the leaves are the pages. A tree node
whose id is a key of ``pages`` IS that page: it has no children, and its ``title_he`` and
``description`` mirror the page's (``pages`` is the source of truth; every operation keeps
the two in sync). Any other node is a category; a category may be empty while the owner is
restructuring. ``path`` lists the ids of a page's ancestors, top level first. Siblings are
shown in list order.

Ids are short Latin slugs (``plan.slug_pattern``), unique across nodes, pages and labels, and
never change once assigned. An id merged away (``merged_from``) or deleted
(``metadata.retired_ids``) is retired: no live item may take it again, so an old link or
assignment can never silently point at something new.

Depth counts levels from the top (a top-level node has depth 1); every page sits at a depth
between ``plan.min_depth`` and ``plan.max_depth``. ``plan.max_pages`` is only a safety ceiling on
the page count: the planning prompt sizes pages by ``plan.page_groups``, not by a page count.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from typing import Any

from utils.config import section

PLAN_CONFIG = section("plan")
_SLUG = re.compile(PLAN_CONFIG["slug_pattern"])
ALIAS_LANGUAGES = ("he", "en")


class PlanError(ValueError):
    """A plan (or a proposed edit) breaks the C7 rules; ``errors`` lists every problem."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def iter_nodes(
    tree: list[dict[str, Any]], ancestors: tuple[str, ...] = ()
) -> Iterator[tuple[dict[str, Any], tuple[str, ...]]]:
    """Yield every tree node with the ids of its ancestors (top level first), depth first."""

    for node in tree:
        yield node, ancestors
        yield from iter_nodes(node["children"], (*ancestors, node["id"]))


def find_node(plan: dict[str, Any], node_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the node with ``node_id`` and the sibling list that holds it."""

    stack = [plan["tree"]]
    while stack:
        siblings = stack.pop()
        for node in siblings:
            if node["id"] == node_id:
                return node, siblings
            stack.append(node["children"])
    raise PlanError([f"no tree node {node_id!r}"])


def sync_pages(plan: dict[str, Any]) -> dict[str, Any]:
    """Copy each page's title and description onto its tree node and recompute its ``path``
    from the tree (in place; returns ``plan``)."""

    pages = plan["pages"]
    for node, ancestors in iter_nodes(plan["tree"]):
        page = pages.get(node["id"])
        if page is not None:
            node["title_he"] = page["title_he"]
            node["description"] = page["description"]
            page["path"] = list(ancestors)
    return plan


def retired_ids(plan: dict[str, Any]) -> set[str]:
    """Every id that may not be used again: merged away or deleted."""

    retired = set(plan.get("metadata", {}).get("retired_ids") or [])
    for item in (*plan["pages"].values(), *plan["labels"].values()):
        retired.update(item.get("merged_from") or [])
    return retired


def live_ids(plan: dict[str, Any]) -> set[str]:
    """Every id in use: tree nodes, pages and labels."""

    nodes = {node["id"] for node, _ in iter_nodes(plan["tree"])}
    return nodes | set(plan["pages"]) | set(plan["labels"])


def slug_error(item_id: Any) -> str | None:
    """Why ``item_id`` is not a valid slug, or ``None``."""

    if not isinstance(item_id, str) or not _SLUG.fullmatch(item_id):
        return f"id {item_id!r} is not a slug matching {PLAN_CONFIG['slug_pattern']}"
    if len(item_id) > PLAN_CONFIG["slug_max_length"]:
        return f"id {item_id!r} is longer than {PLAN_CONFIG['slug_max_length']} characters"
    return None


def _text_errors(where: str, item: Any, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(item, dict):
        return [f"{where} is not an object"]
    return [
        f"{where} needs a non-empty text {key!r}"
        for key in keys
        if not isinstance(item.get(key), str) or not item[key].strip()
    ]


def _alias_errors(where: str, aliases: Any) -> list[str]:
    if not isinstance(aliases, dict) or set(aliases) != set(ALIAS_LANGUAGES):
        return [f"{where} aliases must be an object with exactly the keys {list(ALIAS_LANGUAGES)}"]
    return [
        f"{where} aliases.{lang} must be a list of non-empty texts"
        for lang in ALIAS_LANGUAGES
        if not isinstance(aliases[lang], list)
        or not all(isinstance(a, str) and a.strip() for a in aliases[lang])
    ]


def _shape_errors(tree: Any, where: str = "tree") -> list[str]:
    """Structural problems that would stop a walk of the tree (checked before anything else)."""

    if not isinstance(tree, list):
        return [f"{where} must be a list of nodes"]
    errors: list[str] = []
    for index, node in enumerate(tree):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            errors.append(f"{where}[{index}] must be an object with a text 'id'")
        else:
            errors += _shape_errors(node.get("children"), f"children of {node['id']!r}")
    return errors


def _tree_errors(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pages = plan["pages"]
    placed: Counter[str] = Counter()
    for node, ancestors in iter_nodes(plan["tree"]):
        node_id = node["id"]
        where = f"tree node {node_id!r}"
        errors += _text_errors(where, node, ("title_he",))
        if not isinstance(node.get("description"), str):
            errors.append(f"{where} needs a text 'description'")
        depth = len(ancestors) + 1
        page = pages.get(node_id)
        if page is None:
            if depth >= PLAN_CONFIG["max_depth"]:
                errors.append(
                    f"category {node_id!r} sits at depth {depth}; a category must leave room "
                    f"for pages below it (max depth {PLAN_CONFIG['max_depth']})"
                )
            continue
        placed[node_id] += 1
        if node["children"]:
            errors.append(f"page {node_id!r} has children; pages are leaves")
        if not PLAN_CONFIG["min_depth"] <= depth <= PLAN_CONFIG["max_depth"]:
            errors.append(
                f"page {node_id!r} sits at depth {depth}; pages must sit between depth "
                f"{PLAN_CONFIG['min_depth']} and {PLAN_CONFIG['max_depth']}"
            )
        if (node.get("title_he"), node.get("description")) != (page["title_he"], page["description"]):
            errors.append(f"tree node {node_id!r} does not match its page's title/description")
        if page.get("path") != list(ancestors):
            errors.append(f"page {node_id!r} path {page.get('path')} != tree {list(ancestors)}")
    errors += [f"page {pid!r} appears {n} times in the tree" for pid, n in placed.items() if n > 1]
    errors += [f"page {pid!r} is not in the tree" for pid in pages if pid not in placed]
    return errors


def _id_errors(plan: dict[str, Any]) -> list[str]:
    ids = [node.get("id") for node, _ in iter_nodes(plan["tree"])]
    ids += list(plan["labels"])
    errors = [e for e in map(slug_error, ids) if e]
    errors += [f"id {i!r} is used {n} times" for i, n in Counter(ids).items() if n > 1]
    clash = set(ids) & retired_ids(plan)
    errors += [f"id {i!r} is retired (merged away or deleted) and cannot be reused"
               for i in sorted(clash)]
    return errors


def validation_errors(plan: Any) -> list[str]:
    """Every rule ``plan`` breaks (empty list = a valid C7 plan)."""

    if not isinstance(plan, dict):
        return ["the plan is not a JSON object"]
    missing = [k for k, t in (("tree", list), ("pages", dict), ("labels", dict), ("metadata", dict))
               if not isinstance(plan.get(k), t)]
    if missing:
        return [f"the plan needs {', '.join(missing)} (tree list, pages/labels/metadata objects)"]
    errors = _shape_errors(plan["tree"])
    for page_id, page in plan["pages"].items():
        where = f"page {page_id!r}"
        errors += _text_errors(where, page, ("title_he", "description", "scope"))
        if isinstance(page, dict):
            errors += _alias_errors(where, page.get("aliases"))
            if not isinstance(page.get("example_group_ids"), list):
                errors.append(f"{where} needs an 'example_group_ids' list")
    for label_id, label in plan["labels"].items():
        where = f"label {label_id!r}"
        errors += _text_errors(where, label, ("title_he", "description", "kind"))
        if isinstance(label, dict) and isinstance(label.get("kind"), str):
            kind_error = slug_error(label["kind"])
            errors += [f"{where} kind: {kind_error}"] if kind_error else []
    if errors:
        return errors
    if len(plan["pages"]) > PLAN_CONFIG["max_pages"]:
        errors.append(f"{len(plan['pages'])} pages; at most {PLAN_CONFIG['max_pages']} (plan.max_pages, the safety ceiling)")
    return errors + _id_errors(plan) + _tree_errors(plan)


def check_plan(plan: Any) -> dict[str, Any]:
    """Return ``plan`` if it is a valid C7 plan, else raise ``PlanError`` listing every problem."""

    errors = validation_errors(plan)
    if errors:
        raise PlanError(errors)
    return plan
