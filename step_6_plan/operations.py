"""Plan-editing operations: pure functions the plan review tool's buttons call.

Every operation takes a C7 plan, never changes it, and returns a ``PlanEdit`` whose ``plan``
is a new, fully validated plan; a request that would break a C7 rule raises ``PlanError``
(``errors`` lists every problem) and nothing changes. Ids never change: renaming touches only
titles. Ids are given by the caller (a Latin slug, ``plan.slug_pattern``) and must be new: an
id in use, merged away or deleted is refused.

What disappears is reported so task 24 can react: ``removed_pages`` / ``removed_labels``
(their groups need a new home), ``redirects`` (merged-away id -> surviving id; groups and
links follow it) and ``reassign`` (pages whose current groups should be classified again,
e.g. after a split or a scope change).
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from step_6_plan.model import (
    ALIAS_LANGUAGES,
    PlanError,
    check_plan,
    find_node,
    iter_nodes,
    live_ids,
    sync_pages,
)

EDITABLE_FIELDS = {
    "page": ("description", "scope", "aliases", "example_group_ids"),
    "node": ("description",),
    "label": ("description", "kind"),
}


@dataclass(frozen=True)
class PlanEdit:
    plan: dict[str, Any]
    created: tuple[str, ...] = ()
    removed_pages: tuple[str, ...] = ()
    removed_labels: tuple[str, ...] = ()
    redirects: dict[str, str] = field(default_factory=dict)
    reassign: tuple[str, ...] = ()


def _done(plan: dict[str, Any], **report: Any) -> PlanEdit:
    return PlanEdit(check_plan(sync_pages(plan)), **report)


def kind_of(plan: dict[str, Any], item_id: str) -> str:
    """``"page"``, ``"node"`` (a category) or ``"label"``; ``PlanError`` for an unknown id."""

    if item_id in plan["pages"]:
        return "page"
    if item_id in plan["labels"]:
        return "label"
    if any(node["id"] == item_id for node, _ in iter_nodes(plan["tree"])):
        return "node"
    raise PlanError([f"no page, category or label {item_id!r}"])


def _siblings_under(plan: dict[str, Any], parent_id: str | None) -> list[dict[str, Any]]:
    """The child list of category ``parent_id`` (``None`` = the top level)."""

    if parent_id is None:
        return plan["tree"]
    if parent_id in plan["pages"]:
        raise PlanError([f"{parent_id!r} is a page; only a category can hold children"])
    return find_node(plan, parent_id)[0]["children"]


def _insert(siblings: list[dict[str, Any]], node: dict[str, Any], position: int | None) -> None:
    siblings.insert(len(siblings) if position is None else position, node)


def _detach(plan: dict[str, Any], item_id: str) -> dict[str, Any]:
    node, siblings = find_node(plan, item_id)
    siblings.remove(node)
    return node


def _retire(plan: dict[str, Any], item_id: str) -> None:
    retired = plan["metadata"].setdefault("retired_ids", [])
    if item_id not in retired:
        retired.append(item_id)


def _empty_aliases() -> dict[str, list[str]]:
    return {lang: [] for lang in ALIAS_LANGUAGES}


def add_node(
    plan: dict[str, Any],
    node_id: str,
    *,
    title_he: str,
    description: str = "",
    parent_id: str | None = None,
    position: int | None = None,
) -> PlanEdit:
    """Add an (empty) category under ``parent_id`` (``None`` = a new top-level node)."""

    new = copy.deepcopy(plan)
    node = {"id": node_id, "title_he": title_he, "description": description, "children": []}
    _insert(_siblings_under(new, parent_id), node, position)
    return _done(new, created=(node_id,))


def add_page(
    plan: dict[str, Any],
    page_id: str,
    *,
    parent_id: str,
    title_he: str,
    description: str,
    scope: str,
    aliases: Mapping[str, Sequence[str]] | None = None,
    position: int | None = None,
) -> PlanEdit:
    """Add a page under category ``parent_id``."""

    new = copy.deepcopy(plan)
    new["pages"][page_id] = {
        "title_he": title_he,
        "description": description,
        "scope": scope,
        "path": [],
        "aliases": {k: list(v) for k, v in aliases.items()} if aliases else _empty_aliases(),
        "example_group_ids": [],
    }
    node = {"id": page_id, "title_he": title_he, "description": description, "children": []}
    _insert(_siblings_under(new, parent_id), node, position)
    return _done(new, created=(page_id,))


def add_label(
    plan: dict[str, Any], label_id: str, *, title_he: str, description: str, kind: str
) -> PlanEdit:
    """Add a label (one value of the facet ``kind``, e.g. kind "country")."""

    new = copy.deepcopy(plan)
    if label_id in new["labels"]:
        raise PlanError([f"label {label_id!r} already exists"])
    new["labels"][label_id] = {"title_he": title_he, "description": description, "kind": kind}
    return _done(new, created=(label_id,))


def rename(plan: dict[str, Any], item_id: str, title_he: str) -> PlanEdit:
    """Change the Hebrew title of a page, category or label; its id stays."""

    new = copy.deepcopy(plan)
    kind = kind_of(new, item_id)
    if kind == "node":
        find_node(new, item_id)[0]["title_he"] = title_he
    else:
        new["pages" if kind == "page" else "labels"][item_id]["title_he"] = title_he
    return _done(new)


def edit(plan: dict[str, Any], item_id: str, changes: Mapping[str, Any]) -> PlanEdit:
    """Set fields other than the title: ``EDITABLE_FIELDS[kind]`` (e.g. a page's scope or
    aliases ``{"he": [...], "en": [...]}``, a label's kind). A scope change asks task 24 to
    classify the page's groups again (``reassign``)."""

    new = copy.deepcopy(plan)
    kind = kind_of(new, item_id)
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS[kind]))
    if unknown:
        raise PlanError([f"a {kind} has no editable field {name!r}; editable: "
                         f"{list(EDITABLE_FIELDS[kind])}" for name in unknown])
    if kind == "node":
        target = find_node(new, item_id)[0]
    else:
        target = new["pages" if kind == "page" else "labels"][item_id]
    rescoped = kind == "page" and "scope" in changes and changes["scope"] != target["scope"]
    target.update(copy.deepcopy(dict(changes)))
    return _done(new, reassign=(item_id,) if rescoped else ())


def delete(plan: dict[str, Any], item_id: str) -> PlanEdit:
    """Delete a page, an empty category or a label; its id is retired for good."""

    new = copy.deepcopy(plan)
    kind = kind_of(new, item_id)
    if kind == "label":
        del new["labels"][item_id]
    else:
        node = _detach(new, item_id)
        if node["children"]:
            raise PlanError([
                f"category {item_id!r} still holds {[c['id'] for c in node['children']]}; "
                "move or delete them first"
            ])
        new["pages"].pop(item_id, None)
    _retire(new, item_id)
    return _done(
        new,
        removed_pages=(item_id,) if kind == "page" else (),
        removed_labels=(item_id,) if kind == "label" else (),
    )


def _union(*lists: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(item for items in lists for item in items))


def merge(plan: dict[str, Any], survivor_id: str, merged_ids: Collection[str]) -> PlanEdit:
    """Merge pages (or labels) into ``survivor_id``, which keeps its id, title and place.

    Each merged item disappears; its id (and anything it had absorbed before) is recorded in
    the survivor's ``merged_from`` so old links and assignments can be redirected. A merged
    page's title and aliases become aliases of the survivor and its example groups join the
    survivor's.
    """

    new = copy.deepcopy(plan)
    kind = kind_of(new, survivor_id)
    others = list(dict.fromkeys(merged_ids))
    errors = [f"{mid!r} cannot be merged into itself" for mid in others if mid == survivor_id]
    errors += [f"{mid!r} is not a {kind} like {survivor_id!r}"
               for mid in others if mid != survivor_id and kind_of(new, mid) != kind]
    if kind == "node" or not others:
        errors.append(f"merge needs a page or label survivor and at least one other; got "
                      f"{survivor_id!r} ({kind}) and {others}")
    if errors:
        raise PlanError(errors)
    items = new["pages" if kind == "page" else "labels"]
    survivor = items[survivor_id]
    redirects: dict[str, str] = {}
    for mid in others:
        gone = items.pop(mid)
        absorbed = gone.get("merged_from") or []
        survivor["merged_from"] = _union(survivor.get("merged_from") or [], [mid], absorbed)
        redirects.update({old: survivor_id for old in (mid, *absorbed)})
        if kind == "page":
            _detach(new, mid)
            survivor["aliases"] = {
                lang: _union(survivor["aliases"][lang], [gone["title_he"]] if lang == "he" else [],
                             gone["aliases"][lang])
                for lang in ALIAS_LANGUAGES
            }
            survivor["example_group_ids"] = _union(survivor["example_group_ids"],
                                                   gone["example_group_ids"])
    return _done(
        new,
        removed_pages=tuple(others) if kind == "page" else (),
        removed_labels=tuple(others) if kind == "label" else (),
        redirects=redirects,
    )


def split_page(
    plan: dict[str, Any],
    page_id: str,
    new_page_id: str,
    *,
    title_he: str,
    description: str,
    scope: str,
    aliases: Mapping[str, Sequence[str]] | None = None,
    parent_id: str | None = None,
    original_scope: str | None = None,
) -> PlanEdit:
    """Carve a new page out of ``page_id``. The new page goes right after the original (or at
    the end of category ``parent_id``); the original keeps its id, and its groups must be
    classified again between the two (``reassign``). ``original_scope`` narrows the
    original's scope in the same edit (else narrow it with ``edit`` afterwards)."""

    if kind_of(plan, page_id) != "page":
        raise PlanError([f"{page_id!r} is not a page"])
    original_parent = plan["pages"][page_id]["path"][-1]
    position = None
    if parent_id is None:
        siblings = find_node(plan, original_parent)[0]["children"]
        position = next(i for i, node in enumerate(siblings) if node["id"] == page_id) + 1
    added = add_page(
        plan, new_page_id, parent_id=parent_id or original_parent, title_he=title_he,
        description=description, scope=scope, aliases=aliases, position=position,
    )
    new = added.plan
    if original_scope is not None:
        new = edit(new, page_id, {"scope": original_scope}).plan
    return PlanEdit(new, created=(new_page_id,), reassign=(page_id,))


def change_id(plan: dict[str, Any], item_id: str, new_id: str) -> PlanEdit:
    """Give a page, category or label a better id: the item keeps everything else (place,
    texts, examples) under ``new_id``, and the old id is retired for good. For a page or a
    label the old id joins its ``merged_from``, so groups and links follow the redirect
    (nothing needs classifying again); a category's old id goes to ``retired_ids``."""

    new = copy.deepcopy(plan)
    kind = kind_of(new, item_id)
    if new_id in live_ids(new):
        raise PlanError([f"id {new_id!r} is already in use"])
    if kind != "label":
        find_node(new, item_id)[0]["id"] = new_id
    if kind == "node":
        _retire(new, item_id)
        return _done(new, created=(new_id,))
    key = "pages" if kind == "page" else "labels"
    item = new[key][item_id]
    absorbed = item.get("merged_from") or []
    item["merged_from"] = _union(absorbed, [item_id])
    new[key] = {new_id if k == item_id else k: v for k, v in new[key].items()}
    return _done(new, created=(new_id,), redirects={old: new_id for old in (item_id, *absorbed)})


def move(
    plan: dict[str, Any], item_id: str, parent_id: str | None, position: int | None = None
) -> PlanEdit:
    """Move a page or category under category ``parent_id`` (``None`` = top level) at index
    ``position`` among its new siblings (``None`` = last). Reordering is a move within the
    same parent."""

    new = copy.deepcopy(plan)
    if kind_of(new, item_id) == "label":
        raise PlanError([f"{item_id!r} is a label; labels are not in the tree"])
    node = _detach(new, item_id)
    inside = {n["id"] for n, _ in iter_nodes([node])}
    if parent_id in inside:
        raise PlanError([f"cannot move {item_id!r} into itself or its own subtree ({parent_id!r})"])
    _insert(_siblings_under(new, parent_id), node, position)
    return _done(new)
