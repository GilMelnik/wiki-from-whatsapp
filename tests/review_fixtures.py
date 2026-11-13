"""Synthetic step-8 workspace: step 7's synthetic wiki (``pages_fixtures``) written with longer
pages, a plan pair, a suggestions pair, a fake review model and a fake page corrector.

Pages (plan ``agencies`` / ``costs`` under ``before`` > ``choosing``, ``clinics`` / ``hospital``
under ``during`` > ``medical``): every group of a page gets two sentences of its own, so a
one-sentence correction keeps most of the page word for word. The review cuts this wiki into
the parts ``before`` and ``during`` plus the ``cross`` pass (every page has stored points).
Nothing here comes from real data.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Any

from step_8_review.apply import Applier, GroupLinker
from step_8_review.proposals import ANSWER_FIELDS
from step_8_review.run import run
from step_8_review.store import SuggestionsStore
from tests.pages_fixtures import (
    FakeProofreader,
    FakeWriter,
    PagesWorkspace,
    group_ids_in,
    main_ids_in,
    page_id_in,
    points_of,
)
from utils.llm_client import BatchRequest, _flatten
from utils.paths import Artifact, resolve

_BODY = re.compile(r"<body>\n(.*?)\n</body>", re.DOTALL)


def sentence_a(group_id: str) -> str:
    return f"הקבוצה {group_id} מספרת על הניסיון הראשון.[^{group_id}]"


def long_article(system: Any, user: Any, task: str) -> dict[str, Any]:
    """A write answer: a lead, then two sentences per group and a sentence with links."""
    page_id = page_id_in(user)
    ids = group_ids_in(user)
    other = "costs" if page_id != "costs" else "agencies"
    lines = [f"פתיח קצר על הדף {page_id}.[^{ids[0]}]", "", "## חלק ראשון", ""]
    for gid in ids:
        lines.append(f"{sentence_a(gid)} עוד פרט על {gid} מאותה שיחה.[^{gid}]")
    lines.append(f"ראו גם [[{other}|דף אחר]] ו[[label:usa|ארה\"ב]].[^{ids[0]}]")
    answer = {**points_of(main_ids_in(user)), "body": "\n".join(lines),
              "summary": f"תקציר {page_id}", "questions": ["שאלה?"],
              "related": [{"page_id": other, "relation": "see_also"}]}
    if task.startswith("update:"):
        answer["no_change"] = False
    return answer


def current_body(user: Any) -> str:
    """The <current_article> body of an update prompt."""
    return _BODY.search(_flatten(user)).group(1)


def corrector(old: str, new: str) -> Callable[[Any, Any, str], dict[str, Any]]:
    """A fake ``apply`` model that replaces ``old`` with ``new`` in the page it is given."""

    def respond(system: Any, user: Any, task: str) -> dict[str, Any]:
        body = current_body(user)
        return {**points_of(main_ids_in(user)), "no_change": False, "body": body.replace(old, new),
                "summary": "תקציר מתוקן", "questions": ["שאלה?"], "related": []}

    return respond


def no_change(system: Any, user: Any, task: str) -> dict[str, Any]:
    return {**points_of(main_ids_in(user)), "no_change": True, "body": "", "summary": "",
            "questions": [], "related": []}


def flat(kind: str, **fields: Any) -> dict[str, Any]:
    """One suggestion in the model's flat answer format ("" / [] for unused fields)."""
    answer = {key: [] if spec.get("type") == "array" else "" for key, spec in ANSWER_FIELDS.items()}
    answer.update(kind=kind, title=f"כותרת {kind}", reason=f"נימוק {kind}", **fields)
    return answer


class FakeReviewer:
    """A fake ``LLMClient`` for the review stage. Every pass answers ``suggestions`` unless
    ``by_pass`` has an answer of its own; the passes in ``fail`` get no answer (a sync call
    raises). Records every call (``pass_id``: the pass it was for) and every batch."""

    def __init__(self, suggestions: list[dict[str, Any]], note: str = "הערה", *,
                 by_pass: dict[str, list[dict[str, Any]]] | None = None,
                 fail: Collection[str] = (), batch: bool = True) -> None:
        self.model = "fake-opus"
        self.answer = {"note": note, "suggestions": suggestions}
        self.by_pass = by_pass or {}
        self.fail = set(fail)
        self.batch = batch
        self.calls: list[dict[str, Any]] = []
        self.batches: list[list[str]] = []

    def supports_batch(self) -> bool:
        return self.batch

    def _answer(self, system: Any, user: Any, task: str, schema: dict | None) -> dict[str, Any]:
        pass_id = task.removeprefix("review:")
        self.calls.append({"system": system, "user": _flatten(user), "task": task,
                           "pass_id": pass_id, "schema": schema})
        if pass_id in self.by_pass:
            return {"note": f"הערה {pass_id}", "suggestions": copy.deepcopy(self.by_pass[pass_id])}
        return copy.deepcopy(self.answer)

    def complete_json(self, system: Any, user: Any, task: str = "",
                      response_schema: dict | None = None) -> dict[str, Any]:
        answer = self._answer(system, user, task, response_schema)
        if task.removeprefix("review:") in self.fail:
            raise RuntimeError("fake refusal")
        return answer

    def complete_batch_json(self, requests: Sequence[BatchRequest]) -> dict[str, Any]:
        self.batches.append([request.request_id for request in requests])
        answers = {request.request_id: self._answer(request.system, request.user, request.task,
                                                    request.response_schema)
                   for request in requests}
        return {pass_id: answer for pass_id, answer in answers.items() if pass_id not in self.fail}

    def passes(self) -> list[str]:
        return [call["pass_id"] for call in self.calls]


def count_tokens(system: str, user: str) -> int:
    return (len(system) + len(user)) // 4


class FakeLinker:
    """Stands in for the groups tool's store: records the links and writes a file."""

    def __init__(self, edited: Path) -> None:
        self.edited = edited
        self.links: list[tuple[str, str, bool]] = []

    def set_contradiction(self, group_id: str, other_id: str, linked: bool) -> dict[str, Any]:
        self.links.append((group_id, other_id, linked))
        self.edited.write_text(json.dumps({"links": self.links}), encoding="utf-8")
        return {}


class ReviewWorkspace(PagesWorkspace):
    """The step-7 workspace with its pages written, plus the step-8 files."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.plan_artifact = Artifact(self.plan_path, root / "plan_edited.json")
        self.suggestions = Artifact(root / "suggestions.json", root / "suggestions_edited.json")
        self.linker = FakeLinker(root / "groups_links.json")
        self.run(llm=FakeWriter(respond=long_article))

    def review(self, llm: FakeReviewer, **kwargs: Any) -> dict[str, Any]:
        return run(artifact=self.suggestions, pages_artifact=self.artifact,
                   aggregated_path=self.groups_path, plan_path=resolve(self.plan_artifact),
                   assignments_path=self.assignments_path, llm=llm,
                   count_tokens=kwargs.pop("count_tokens", count_tokens), **kwargs)

    def applier(self, llm: FakeWriter | None = None,
                linker: Callable[[], GroupLinker] | None = None,
                proofread_llm: FakeProofreader | None = None) -> Applier:
        return Applier(pages_artifact=self.artifact, plan_artifact=self.plan_artifact,
                       aggregated_path=self.groups_path, assignments_path=self.assignments_path,
                       llm=llm or FakeWriter("fake-apply"),
                       proofread_llm=proofread_llm or FakeProofreader(),
                       linker=linker or (lambda: self.linker))

    def store(self, llm: FakeWriter | None = None,
              linker: Callable[[], GroupLinker] | None = None) -> SuggestionsStore:
        return SuggestionsStore(self.suggestions, pages_artifact=self.artifact,
                                plan_artifact=self.plan_artifact,
                                applier=self.applier(llm, linker))

    def suggestions_payload(self, edited: bool = False) -> dict[str, Any]:
        path = self.suggestions.edited if edited else self.suggestions.original
        return json.loads(path.read_text(encoding="utf-8"))

    def effective_page(self, page_id: str) -> dict[str, Any]:
        path = self.artifact.edited if self.artifact.edited.is_file() else self.artifact.original
        return json.loads(path.read_text(encoding="utf-8"))["pages"][page_id]

    def snapshot(self) -> dict[str, bytes | None]:
        """The bytes of every file an apply could write (``None``: no file)."""
        paths = [self.artifact.original, self.artifact.edited, self.plan_artifact.original,
                 self.plan_artifact.edited, self.suggestions.original, self.suggestions.edited,
                 self.groups_path, self.assignments_path, self.linker.edited]
        return {str(p): p.read_bytes() if p.is_file() else None for p in paths}


def valid_answers() -> list[dict[str, Any]]:
    """One valid suggestion of every kind for the synthetic wiki."""
    return [
        flat("mistake", page_id="agencies", sentence=sentence_a("g-00000004"),
             group_ids=["g-00000004"], correction="לתקן: הניסיון היה השני"),
        flat("contradiction", groups_a=["g-00000000"], groups_b=["g-00000002"],
             pages=["agencies", "clinics"], clash="סותרות", correction="להציג את שני הצדדים"),
        flat("split", page_id="clinics", new_page_id="clinic-prices", title_he="מחירי מרפאות",
             description="תיאור", scope="תחום", original_scope="תחום מצומצם"),
        flat("merge", survivor_id="agencies", merged_ids=["costs"]),
        flat("add_page", page_id="travel", parent_id="choosing", title_he="נסיעות",
             description="תיאור", scope="תחום", pages=["agencies", "hospital"]),
        flat("add_label", label_id="texas", title_he="טקסס", description="תיאור",
             label_kind="country", pages=["clinics"]),
    ]
