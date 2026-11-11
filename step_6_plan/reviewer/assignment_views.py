"""Read-only views of the assignments (C8) against today's plan and claim groups.

Every record is read as the next assignment run will see it (``assignments.followed``):
groups on a page that was merged away show under the survivor, marked as waiting for the
run; groups whose every page was deleted are in the "waiting" list, with the groups that
have no record yet (newer than the last run). Nothing here writes.

Lists (``scope``): ``page`` (the groups the page owns, then those that only mention it),
``label``, ``unplaced`` (``pages == []``) and ``waiting``. Default order: least certain
first (the classifier's probability for that page / label; the top page for an unplaced
group), records pinned by hand last; also by last date or by supporters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from step_6_plan.assign_rules import ASSIGN_CONFIG, apply_plan_edit
from step_6_plan.model import PLAN_CONFIG, iter_nodes
from step_6_plan.operations import PlanEdit
from step_6_plan.reviewer.assignments import MANUAL, followed
from step_6_plan.reviewer.history import Record
from step_6_plan.reviewer.proposals import ACCEPTED, PENDING, REJECTED, status_of
from step_6_plan.reviewer.views import group_summary, redirect_map

REVIEW_CONFIG = PLAN_CONFIG["review"]
FINDINGS = REVIEW_CONFIG["label_findings"]
SCOPES = ("page", "label", "unplaced", "waiting")
SORTS = ("uncertain", "date", "support")
ROLES = ("all", "primary", "mention")


def _matches(row: Mapping[str, Any], needle: str) -> bool:
    texts = [row.get("claim_text") or "", row["group_id"], *(row.get("entity_names") or [])]
    return any(needle in text.casefold() for text in texts)


class AssignmentView:
    """The assignments of one store load, read against ``plan`` and ``groups`` (``None``:
    no groups file, so rows have no text)."""

    def __init__(self, plan: dict[str, Any], data: Mapping[str, Any],
                 groups: Mapping[str, dict[str, Any]] | None) -> None:
        self.plan = plan
        self.stored: dict[str, Record] = data["assignments"]
        self.proposals: list[dict[str, Any]] = data["proposals"]
        self.metadata: Mapping[str, Any] = data.get("metadata") or {}
        self.groups = groups
        order = list(groups) if groups is not None else list(self.stored)
        known = set(order)
        self.order = [gid for gid in order if gid in self.stored]
        self.new_groups = [gid for gid in order if gid not in self.stored]
        self.gone = [gid for gid in self.stored if gid not in known]
        self.records = {gid: followed(self.stored[gid], plan) for gid in self.order}
        self._redirects = redirect_map(plan)

    # --- one group ----------------------------------------------------------------

    def _changed_ids(self, gid: str) -> tuple[dict[str, str], list[str]]:
        """Stored ids the plan no longer has: ``{merged-away: survivor}`` and deleted ones."""

        stored = self.stored[gid]
        live = set(self.plan["pages"]) | set(self.plan["labels"])
        stale = [i for i in [*stored["pages"], *stored["labels"]] if i not in live]
        moved = {i: self._redirects[i] for i in stale if i in self._redirects}
        return moved, [i for i in stale if i not in moved]

    def row(self, gid: str, certainty: float | None = None, role: str | None = None
            ) -> dict[str, Any]:
        """One group as the lists show it (its text and where it is)."""

        group = (self.groups or {}).get(gid)
        base = group_summary(group) if group else {"group_id": gid, "missing": self.groups is not None}
        record = self.records.get(gid)
        if record is None:
            return {**base, "record": False}
        moved, dropped = self._changed_ids(gid)
        return {
            **base,
            "record": True,
            "pages": record["pages"],
            "labels": record["labels"],
            "manual": bool(record.get(MANUAL)),
            "p": sorted(record["p"].items(), key=lambda kv: -kv[1]),
            "redirected": moved,
            "dropped": dropped,
            "certainty": certainty,
            "role": role,
        }

    # --- lists --------------------------------------------------------------------

    def _members(self, scope: str, item_id: str | None) -> list[tuple[str, float | None, str | None]]:
        """``(group_id, certainty, role)`` of every group in the list, in group order."""

        out: list[tuple[str, float | None, str | None]] = []
        if scope == "waiting":
            lost = [gid for gid in self.order
                    if self.stored[gid]["pages"] and not self.records[gid]["pages"]]
            return [(gid, None, None) for gid in [*lost, *self.new_groups]]
        for gid in self.order:
            record = self.records[gid]
            if scope == "page" and item_id in record["pages"]:
                role = "primary" if record["pages"][0] == item_id else "mention"
                out.append((gid, record["p"].get(item_id, 0.0), role))
            elif scope == "label" and item_id in record["labels"]:
                out.append((gid, record["label_p"].get(item_id, 0.0), None))
            elif scope == "unplaced" and not record["pages"] and not self.stored[gid]["pages"]:
                out.append((gid, max(record["p"].values(), default=0.0), None))
        return out

    def groups_list(self, *, scope: str, item_id: str | None, role: str, sort: str, q: str,
                    offset: int, limit: int) -> dict[str, Any]:
        """One page of a list, filtered by ``role`` (page lists) and the text ``q``."""

        members = self._members(scope, item_id)
        counts = {"all": len(members),
                  "primary": sum(1 for _, _, r in members if r == "primary"),
                  "mention": sum(1 for _, _, r in members if r == "mention")}
        if role != "all":
            members = [m for m in members if m[2] == role]
        rows = [self.row(gid, certainty, member_role) for gid, certainty, member_role in members]
        needle = q.strip().casefold()
        if needle:
            rows = [row for row in rows if _matches(row, needle)]
        rows = self._sorted(rows, sort)
        return {"scope": scope, "id": item_id, "items": rows[offset:offset + limit],
                "total": len(rows), "offset": offset, "limit": limit, "counts": counts,
                "manual": sum(1 for row in rows if row.get("manual"))}

    @staticmethod
    def _sorted(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
        if sort == "date":
            rows = sorted(rows, key=lambda row: row.get("last_date") or "", reverse=True)
        elif sort == "support":
            rows = sorted(rows, key=lambda row: -(row.get("supporter_count") or 0))
        else:
            rows = sorted(rows, key=lambda row: (bool(row.get("manual")), row.get("certainty") or 0.0))
        # A page's own groups first, then those that only mention it (stable: order kept).
        return sorted(rows, key=lambda row: row.get("role") == "mention")

    # --- numbers ------------------------------------------------------------------

    def label_findings(self, counts: Mapping[str, int]) -> list[dict[str, Any]]:
        """Labels that attach to very few groups or to a very large share of them."""

        total = len(self.order)
        found = []
        for label_id, count in counts.items():
            share = count / total if total else 0.0
            finding = ("large" if share >= FINDINGS["large_share"]
                       else "few" if count <= FINDINGS["few_groups"] else None)
            if finding:
                found.append({"label_id": label_id, "count": count, "share": share,
                              "finding": finding})
        return found

    def summary(self) -> dict[str, Any]:
        """The numbers the overview and the tree show."""

        records = self.records
        pages = {pid: {"all": 0, "primary": 0, "manual": 0} for pid in self.plan["pages"]}
        labels = dict.fromkeys(self.plan["labels"], 0)
        for record in records.values():
            for index, pid in enumerate(record["pages"]):
                pages[pid]["all"] += 1
                pages[pid]["primary"] += index == 0
                pages[pid]["manual"] += bool(record.get(MANUAL))
            for lid in record["labels"]:
                labels[lid] += 1
        statuses = dict.fromkeys((PENDING, ACCEPTED, REJECTED), 0)
        for proposal in self.proposals:
            statuses[status_of(proposal)] = statuses.get(status_of(proposal), 0) + 1
        return {
            "generated_at": self.metadata.get("generated_at"),
            "classifier": self.metadata.get("classifier"),
            "counts": {
                "groups": len(records),
                "unplaced": len(self._members("unplaced", None)),
                "two_pages": sum(1 for r in records.values() if len(r["pages"]) > 1),
                "manual": sum(1 for r in records.values() if r.get(MANUAL)),
                "waiting": len(self._members("waiting", None)),
                "new_groups": len(self.new_groups),
                "followed": sum(1 for gid in self.order if any(self._changed_ids(gid))),
                "gone": len(self.gone),
            },
            "pages": pages,
            "labels": labels,
            "label_findings": self.label_findings(labels),
            "proposals": statuses,
            # Below these the classifier was unsure (the step's own decision rules).
            "thresholds": {"page": ASSIGN_CONFIG["confident_probability"],
                           "label": ASSIGN_CONFIG["label_probability"]},
        }

    # --- proposals ----------------------------------------------------------------

    def _target(self, proposal: Mapping[str, Any], check_id: Callable[[str], dict[str, Any]]
                ) -> dict[str, Any]:
        kind, target = proposal.get("kind"), proposal.get("target_id") or ""
        if kind == "scope":
            page_id = self._redirects.get(target, target)
            page = self.plan["pages"].get(page_id)
            return {"page_id": page_id if page else None,
                    "title_he": page["title_he"] if page else None,
                    "scope": page["scope"] if page else None}
        answer: dict[str, Any] = {"id_check": check_id(target) if target else None}
        if kind == "page":
            answer["parent_ok"] = (proposal.get("parent_id") or "") in _category_ids(self.plan)
        else:
            answer["kind_known"] = proposal.get("label_kind") in {
                label["kind"] for label in self.plan["labels"].values()}
        return answer

    def proposals_view(self, check_id: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
        """Every proposal, pending first, with its groups and what its target looks like
        in today's plan."""

        ordered = sorted(self.proposals, key=lambda p: status_of(p) != PENDING)
        items = [{**proposal,
                  "status": status_of(proposal),
                  "groups": [self.row(gid) for gid in proposal.get("group_ids") or []],
                  "target": self._target(proposal, check_id)}
                 for proposal in ordered]
        return {"items": items, "counts": self.summary()["proposals"]}

    # --- what a plan operation means for the assignments ----------------------------

    def plan_edit_effect(self, edit: PlanEdit) -> dict[str, Any]:
        """For the operation dialogs: how many groups the next assignment run redirects,
        asks again or takes off a deleted page / label, and how many hand-pinned records
        lose a page (``step_6_plan.assign_rules.apply_plan_edit``)."""

        result = apply_plan_edit(self.stored, edit)
        moved = set(edit.redirects)
        manual = {gid for gid, record in self.stored.items() if record.get(MANUAL)}
        removed_pages, removed_labels = set(edit.removed_pages), set(edit.removed_labels)
        new_labels = [i for i in edit.created
                      if i in edit.plan["labels"] and i not in edit.redirects.values()]
        return {
            "redirected": sum(1 for r in self.stored.values()
                              if moved & {*r["pages"], *r["labels"]}),
            "reask": len(result.reask),
            "screen_pages": list(result.screen_pages),
            "asked_labels": new_labels,
            "pages_cleared": sum(1 for ids in result.cleared.values() if removed_pages & set(ids)),
            "labels_cleared": sum(1 for ids in result.cleared.values() if removed_labels & set(ids)),
            "manual_lost": sum(1 for gid, ids in result.cleared.items()
                               if gid in manual and removed_pages & set(ids)),
            "manual_reask": sum(1 for gid in result.reask if gid in manual),
        }


def _category_ids(plan: Mapping[str, Any]) -> set[str]:
    return {node["id"] for node, _ in iter_nodes(plan["tree"]) if node["id"] not in plan["pages"]}
