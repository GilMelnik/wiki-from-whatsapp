"""Synthetic step-7 inputs: groups, a plan, assignments on disk and a fake page-writing model.

The plan is ``plan_fixtures.make_plan`` (pages agencies / costs / clinics / hospital, labels
usa / georgia). Group ``i`` goes to page ``PAGE_IDS[i % 4]``; group 4 also has ``costs`` as
its second page; group 11 is unplaced. Groups carry private-looking fields (claim ids,
identities, a reaction summary) that no prompt may show. ``FakeWriter`` answers like the
model: one point per ``main`` group of the prompt's ``<groups>`` block, then an article with
one sentence per group (each citing it) and links to one other page and a label. Nothing
here comes from real data.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from step_7_write_pages.run import run
from tests.plan_fixtures import PAGE_IDS, make_groups, make_plan
from utils.json_io import write_json_file
from utils.llm_client import _flatten
from utils.paths import Artifact

SECRET = "PRIVATE-IDENTITY"
_GROUP_LINE = re.compile(r"^(?:new: |changed: )?(g-[0-9a-f]{8}) \|", re.MULTILINE)
_MAIN_LINE = re.compile(r"^(g-[0-9a-f]{8}) \| main \|", re.MULTILINE)
_PAGE_ID = re.compile(r"^id: (\S+)$", re.MULTILINE)


def page_groups() -> list[dict[str, Any]]:
    """Twelve groups; group 0 has two months of backing, group 3 (hospital) contradicts group 6 (clinics); odd groups
    are disputed (one opposer)."""
    groups = make_groups(12)
    for group in groups:
        group["all_supporters"] = [SECRET]
        group["reaction_summary"] = [{"emoji": "❤️", "count": 9, "who": SECRET}]
    groups[0]["supporter_count"] = 4
    groups[0]["last_date"] = "2024-06-01"
    groups[0]["by_month"] = {"2023-01": {"supporters": 1, "opposers": 0},
                             "2024-06": {"supporters": 3, "opposers": 0}}
    groups[3]["contradicts"] = ["g-00000006"]
    groups[6]["contradicts"] = ["g-00000003"]
    return groups


def page_assignments(groups: list[dict[str, Any]]) -> dict[str, Any]:
    records = {}
    for index, group in enumerate(groups):
        pages = [PAGE_IDS[index % 4]]
        if index == 4:
            pages.append("costs")
        if index == 11:
            pages = []
        records[group["group_id"]] = {"pages": pages, "labels": ["usa"] if index % 2 == 0 else [],
                                      "p": {}, "label_p": {}}
    return {"assignments": records, "proposals": [], "metadata": {}}


def group_ids_in(user: Any) -> list[str]:
    """Group ids of a prompt's ``<groups>`` block (the page's groups now)."""
    text = _flatten(user)
    block = text[text.rindex("<groups"):]
    return list(dict.fromkeys(_GROUP_LINE.findall(block)))


def main_ids_in(user: Any) -> list[str]:
    """The ``main`` group ids of a prompt's ``<groups>`` block."""
    text = _flatten(user)
    return _MAIN_LINE.findall(text[text.rindex("<groups"):])


def page_id_in(user: Any) -> str:
    return _PAGE_ID.search(_flatten(user)).group(1)


def points_of(ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """``points`` (one ``fact`` per group) and an empty ``left_out``."""
    return {"points": [{"kind": "fact", "statement": f"נקודה {gid}", "groups": [gid]} for gid in ids],
            "left_out": []}


def article(ids: list[str], page_id: str, extra: str = "") -> str:
    other = next(pid for pid in PAGE_IDS if pid != page_id)
    told = " ".join(f"משפט על הקבוצה {gid}.[^{gid}]" for gid in ids)
    return (f"פתיח קצר על הדף.\n\n## חלק ראשון\n\n{told}\nטקסט עם קישור ל[[{other}|דף אחר]] "
            f"ול[[label:usa|ארה\"ב]].\n{extra}")


Responder = Callable[[str, Any, str], dict[str, Any]]


class FakeWriter:
    """A fake ``LLMClient`` for the write / update stages; records every call."""

    def __init__(self, model: str = "fake-sonnet", respond: Responder | None = None) -> None:
        self.model = model
        self.respond = respond
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, system: Any, user: Any, task: str = "",
                      response_schema: dict | None = None) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "task": task, "schema": response_schema})
        if self.respond is not None:
            return copy.deepcopy(self.respond(system, user, task))
        page_id = page_id_in(user)
        answer = {**points_of(main_ids_in(user)), "body": article(group_ids_in(user), page_id),
                  "summary": f"תקציר {page_id}", "questions": ["שאלה?"],
                  "related": [{"page_id": "clinics", "relation": "see_also"}]}
        if task.startswith("update:"):
            answer["no_change"] = False
        return answer

    def tasks(self) -> list[str]:
        return [call["task"] for call in self.calls]


class PagesWorkspace:
    """Groups, plan, assignments and the pages artifact in a temp folder."""

    def __init__(self, root: Path) -> None:
        self.groups_path = root / "groups.json"
        self.plan_path = root / "plan.json"
        self.assignments_path = root / "assignments.json"
        self.artifact = Artifact(root / "wiki_pages.json", root / "wiki_pages_edited.json")
        groups = page_groups()
        self.write_groups(groups)
        write_json_file(make_plan(), self.plan_path)
        write_json_file(page_assignments(groups), self.assignments_path)

    def write_groups(self, groups: list[dict[str, Any]]) -> None:
        write_json_file({"groups": groups, "metadata": {}}, self.groups_path)

    def groups(self) -> list[dict[str, Any]]:
        return json.loads(self.groups_path.read_text(encoding="utf-8"))["groups"]

    def plan(self) -> dict[str, Any]:
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def write_plan(self, plan: dict[str, Any]) -> None:
        write_json_file(plan, self.plan_path)

    def assignments(self) -> dict[str, Any]:
        return json.loads(self.assignments_path.read_text(encoding="utf-8"))

    def write_assignments(self, payload: dict[str, Any]) -> None:
        write_json_file(payload, self.assignments_path)

    def pages(self, edited: bool = False) -> dict[str, Any]:
        path = self.artifact.edited if edited else self.artifact.original
        return json.loads(path.read_text(encoding="utf-8"))

    def write_edited(self, payload: dict[str, Any]) -> None:
        write_json_file(payload, self.artifact.edited)

    def run(self, llm: FakeWriter | None = None, update_llm: FakeWriter | None = None,
            **kwargs: Any) -> dict[str, Any]:
        return run(aggregated_path=self.groups_path, plan_path=self.plan_path,
                   assignments_path=self.assignments_path, artifact=self.artifact,
                   llm=llm or FakeWriter(), update_llm=update_llm or FakeWriter("fake-update"),
                   **kwargs)
