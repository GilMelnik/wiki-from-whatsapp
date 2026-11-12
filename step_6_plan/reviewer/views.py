"""Read-only shapes the plan tool shows: the tree as a flat list, branches, labels by kind,
what changed since the model's proposal, the coverage numbers and claim-group summaries.

Nothing here edits a plan; every change goes through ``step_6_plan.operations``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from step_6_plan.model import PLAN_CONFIG, iter_nodes, live_ids

PAGE = "page"
CATEGORY = "category"
# Fields compared with the model's proposal, per kind of item.
COMPARED_FIELDS = {
    PAGE: ("title_he", "description", "scope", "aliases", "path"),
    CATEGORY: ("title_he", "description", "parent_id"),
    "label": ("title_he", "description", "kind"),
}
GROUP_FIELDS = (
    "group_id", "claim_text", "stance", "supporter_count", "opposer_count", "thread_count",
    "entity_names", "first_date", "last_date",
)


def flat_tree(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every tree node in display order with its kind, depth, parent and place among its
    siblings (what the list column and the move buttons need)."""

    rows: list[dict[str, Any]] = []
    for node, ancestors in iter_nodes(plan["tree"]):
        parent_id = ancestors[-1] if ancestors else None
        siblings = _children_of(plan, parent_id)
        is_page = node["id"] in plan["pages"]
        rows.append({
            "id": node["id"],
            "kind": PAGE if is_page else CATEGORY,
            "title_he": node["title_he"],
            "description": node["description"],
            "depth": len(ancestors) + 1,
            "parent_id": parent_id,
            "index": next(i for i, sibling in enumerate(siblings) if sibling is node),
            "siblings": len(siblings),
            "children": [child["id"] for child in node["children"]],
            "page_count": sum(1 for n, _ in iter_nodes(node["children"]) if n["id"] in plan["pages"]),
        })
    return rows


def _children_of(plan: dict[str, Any], parent_id: str | None) -> list[dict[str, Any]]:
    if parent_id is None:
        return plan["tree"]
    return next(node for node, _ in iter_nodes(plan["tree"]) if node["id"] == parent_id)["children"]


def branches(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Per top-level node: how many pages it holds and the depth of its deepest page."""

    result = []
    for top in plan["tree"]:
        depths = [len(ancestors) + 2 for node, ancestors in iter_nodes(top["children"])
                  if node["id"] in plan["pages"]]
        result.append({"id": top["id"], "title_he": top["title_he"], "pages": len(depths),
                       "depth": max(depths, default=0)})
    return result


def budget(plan: dict[str, Any]) -> dict[str, Any]:
    """The limits every owner edit must respect, with the current page count."""

    return {
        "pages": len(plan["pages"]),
        "labels": len(plan["labels"]),
        "max_pages": PLAN_CONFIG["max_pages"],
        "min_depth": PLAN_CONFIG["min_depth"],
        "max_depth": PLAN_CONFIG["max_depth"],
        "slug_max_length": PLAN_CONFIG["slug_max_length"],
    }


def labels_by_kind(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The labels grouped by kind, kinds in order of first appearance."""

    kinds: dict[str, list[str]] = {}
    for label_id, label in plan["labels"].items():
        kinds.setdefault(label["kind"], []).append(label_id)
    return [{"kind": kind, "label_ids": ids} for kind, ids in kinds.items()]


def _comparable(plan: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """``{id: (kind, fields)}`` for every live item, fields as compared by ``changes``."""

    items: dict[str, tuple[str, dict[str, Any]]] = {}
    for row in flat_tree(plan):
        if row["kind"] == CATEGORY:
            items[row["id"]] = (CATEGORY, row)
    items.update({pid: (PAGE, page) for pid, page in plan["pages"].items()})
    items.update({lid: ("label", label) for lid, label in plan["labels"].items()})
    return items


def changes(plan: dict[str, Any], proposal: dict[str, Any] | None) -> dict[str, Any]:
    """What the owner changed relative to the model's proposal (the original file):
    ``items`` = ``{id: "new" | [changed fields]}`` and ``removed`` = proposal ids no longer
    live, each with the id it now redirects to (``None`` = deleted)."""

    if proposal is None:
        return {"items": {}, "removed": []}
    before = _comparable(proposal)
    items: dict[str, Any] = {}
    for item_id, (kind, fields) in _comparable(plan).items():
        if item_id not in before:
            items[item_id] = "new"
            continue
        old = before[item_id][1]
        changed = [name for name in COMPARED_FIELDS[kind] if fields.get(name) != old.get(name)]
        if changed:
            items[item_id] = changed
    redirects = redirect_map(plan)
    live = live_ids(plan)
    removed = [{"id": item_id, "kind": kind, "title_he": fields["title_he"],
                "redirect": redirects.get(item_id)}
               for item_id, (kind, fields) in before.items() if item_id not in live]
    return {"items": items, "removed": removed}


def redirect_map(plan: dict[str, Any]) -> dict[str, str]:
    """``{merged-away or re-named id: live id}`` from every ``merged_from``."""

    return {
        old: item_id
        for collection in (plan["pages"], plan["labels"])
        for item_id, item in collection.items()
        for old in item.get("merged_from") or []
    }


def coverage_view(
    report: Mapping[str, Any] | None, plan: dict[str, Any], proposal: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The coverage numbers of the planning run, made readable against today's plan.

    The report measured the proposal as the model made it, so each page says whether its
    numbers still describe it: ``measured`` (the page existed then), ``scope_changed`` (its
    scope differs from the measured one) and ``merged_count`` (sample groups of pages merged
    into it since). The report's size flags, projected from the sample, are not shown: the
    exact sizes from the assignments replace them (``sizes``)."""

    if report is None:
        return None
    counts = report.get("page_counts") or {}
    projected = report.get("projected_groups") or {}
    measured_scopes = {pid: page["scope"] for pid, page in (proposal or {}).get("pages", {}).items()}
    pages = {}
    for page_id, page in plan["pages"].items():
        absorbed = [old for old in page.get("merged_from") or [] if old in counts]
        pages[page_id] = {
            "measured": page_id in counts,
            "count": counts.get(page_id),
            "projected": projected.get(page_id),
            "merged_count": sum(counts[old] for old in absorbed),
            "merged_projected": sum(projected.get(old, 0) for old in absorbed),
            "scope_changed": page_id in measured_scopes and measured_scopes[page_id] != page["scope"],
        }
    keys = ("sample_size", "total_groups", "confident_share", "none_share", "unsure_share",
            "findings")
    return {
        **{key: report.get(key) for key in keys},
        "generated_at": (report.get("metadata") or {}).get("generated_at"),
        "none_count": len(report.get("none_group_ids") or []),
        "unsure_count": len(report.get("unsure_group_ids") or []),
        "pages": pages,
    }


def group_summary(group: Mapping[str, Any]) -> dict[str, Any]:
    """What the tool shows of one claim group (C6)."""

    return {
        **{key: group.get(key) for key in GROUP_FIELDS},
        "claim_count": len(group.get("member_claim_ids") or []),
    }
