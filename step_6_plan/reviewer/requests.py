"""The plan tool's changes as request bodies: one model per kind of change, told apart by
``op``. Each calls exactly one function of ``step_6_plan.operations`` (which validates the
result) and names itself in Hebrew for the undo button and the log.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from step_6_plan import operations as ops
from step_6_plan.model import PlanError, find_node
from step_6_plan.operations import PlanEdit

Position = Annotated[int | None, Field(default=None, ge=0)]
FIELD_NAMES = {
    "description": "התיאור",
    "scope": "התחום",
    "aliases": "הכינויים",
    "kind": "הסוג",
    "example_group_ids": "הקבוצות לדוגמה",
}


def title_of(plan: dict[str, Any], item_id: str) -> str:
    """The Hebrew title of a page, category or label (the id itself if it is unknown)."""

    for collection in (plan["pages"], plan["labels"]):
        if item_id in collection:
            return collection[item_id]["title_he"]
    try:
        return find_node(plan, item_id)[0]["title_he"]
    except PlanError:
        return item_id


def _q(plan: dict[str, Any], item_id: str) -> str:
    return f"„{title_of(plan, item_id)}”"


class Aliases(BaseModel):
    he: list[str] = []
    en: list[str] = []


class AddCategory(BaseModel):
    op: Literal["add_category"]
    node_id: str
    title_he: str
    description: str = ""
    parent_id: str | None = None
    position: Position

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.add_node(plan, self.node_id, title_he=self.title_he,
                            description=self.description, parent_id=self.parent_id,
                            position=self.position)

    def describe(self, plan: dict[str, Any]) -> str:
        return f"הוספת הקטגוריה „{self.title_he}”"


class AddPage(BaseModel):
    op: Literal["add_page"]
    page_id: str
    parent_id: str
    title_he: str
    description: str
    scope: str
    aliases: Aliases | None = None
    position: Position

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.add_page(plan, self.page_id, parent_id=self.parent_id, title_he=self.title_he,
                            description=self.description, scope=self.scope,
                            aliases=self.aliases.model_dump() if self.aliases else None,
                            position=self.position)

    def describe(self, plan: dict[str, Any]) -> str:
        return f"הוספת הדף „{self.title_he}”"


class AddLabel(BaseModel):
    op: Literal["add_label"]
    label_id: str
    title_he: str
    description: str
    kind: str

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.add_label(plan, self.label_id, title_he=self.title_he,
                             description=self.description, kind=self.kind)

    def describe(self, plan: dict[str, Any]) -> str:
        return f"הוספת התווית „{self.title_he}”"


class Rename(BaseModel):
    op: Literal["rename"]
    item_id: str
    title_he: str

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.rename(plan, self.item_id, self.title_he)

    def describe(self, plan: dict[str, Any]) -> str:
        return f"שינוי השם {_q(plan, self.item_id)} ל„{self.title_he}”"


class Edit(BaseModel):
    op: Literal["edit"]
    item_id: str
    changes: dict[str, Any]

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.edit(plan, self.item_id, self.changes)

    def describe(self, plan: dict[str, Any]) -> str:
        fields = " ו".join(FIELD_NAMES.get(name, name) for name in self.changes)
        return f"עריכת {fields} של {_q(plan, self.item_id)}"


class Delete(BaseModel):
    op: Literal["delete"]
    item_id: str

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.delete(plan, self.item_id)

    def describe(self, plan: dict[str, Any]) -> str:
        return f"מחיקת {_q(plan, self.item_id)}"


class Merge(BaseModel):
    op: Literal["merge"]
    survivor_id: str
    merged_ids: list[str]

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.merge(plan, self.survivor_id, self.merged_ids)

    def describe(self, plan: dict[str, Any]) -> str:
        merged = ", ".join(_q(plan, item_id) for item_id in self.merged_ids)
        return f"מיזוג {merged} לתוך {_q(plan, self.survivor_id)}"


class Split(BaseModel):
    op: Literal["split"]
    page_id: str
    new_page_id: str
    title_he: str
    description: str
    scope: str
    aliases: Aliases | None = None
    parent_id: str | None = None
    original_scope: str | None = None

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.split_page(plan, self.page_id, self.new_page_id, title_he=self.title_he,
                              description=self.description, scope=self.scope,
                              aliases=self.aliases.model_dump() if self.aliases else None,
                              parent_id=self.parent_id, original_scope=self.original_scope)

    def describe(self, plan: dict[str, Any]) -> str:
        return f"פיצול „{self.title_he}” מתוך {_q(plan, self.page_id)}"


class Move(BaseModel):
    op: Literal["move"]
    item_id: str
    parent_id: str | None
    position: Position

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.move(plan, self.item_id, self.parent_id, self.position)

    def describe(self, plan: dict[str, Any]) -> str:
        target = "לרמה העליונה" if self.parent_id is None else f"אל {_q(plan, self.parent_id)}"
        return f"העברת {_q(plan, self.item_id)} {target}"


class ChangeId(BaseModel):
    op: Literal["change_id"]
    item_id: str
    new_id: str

    def apply(self, plan: dict[str, Any]) -> PlanEdit:
        return ops.change_id(plan, self.item_id, self.new_id)

    def describe(self, plan: dict[str, Any]) -> str:
        return f"החלפת המזהה של {_q(plan, self.item_id)} ל-{self.new_id}"


Operation = Annotated[
    Union[AddCategory, AddPage, AddLabel, Rename, Edit, Delete, Merge, Split, Move, ChangeId],
    Field(discriminator="op"),
]
