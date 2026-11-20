"""Synthetic step-8 inputs: claim records, a plan, assignments on disk and a fake page-writing
model.

The plan is ``plan_fixtures.make_plan`` (pages agencies / costs / clinics / hospital, labels
usa / georgia). Claim ``i`` is ``claim_fixtures.claim_record(i)`` (short id ``IDS[i]``) and goes
to page ``PAGE_IDS[i % 4]``; claim 4 also has ``costs`` as its second page; claim 11 is
unplaced. The claim inputs on disk hold ids that would be private in real data (claim, thread
and person ids, each with ``SECRET``) that no prompt may show. ``FakeWriter`` answers like the
model: one point per ``main`` claim of the prompt's ``<claims>`` block, then an article with
one sentence per claim (each citing it) and links to one other page and a label.
``FakeProofreader`` stands in for the Hebrew language edit of ``step_10_proofread.run`` (no
edits unless told). Nothing here comes from real data.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Any

from step_5_same_complaint.claim_records import (
    SHORT_ID_HASH_CHARS,
    SHORT_ID_PREFIX,
    Record,
    load_claim_records,
)
from step_8_write_pages.run import run
from tests.claim_fixtures import claim_record, write_claim_inputs
from tests.plan_fixtures import PAGE_IDS, make_plan, short_id_of
from utils.json_io import write_json_file
from utils.llm_client import BatchRequest, _flatten
from utils.paths import Artifact

# The marker in every id the claim inputs hold that would be private in real data.
SECRET = "SECRET"
IDS = tuple(short_id_of(n) for n in range(12))
_ID = rf"{re.escape(SHORT_ID_PREFIX)}[0-9a-f]{{{SHORT_ID_HASH_CHARS}}}"
_CLAIM_LINE = re.compile(rf"^(?:new: |changed: )?({_ID}) \|", re.MULTILINE)
_MAIN_LINE = re.compile(rf"^({_ID}) \| main \|", re.MULTILINE)
_PAGE_ID = re.compile(r"^id: (\S+)$", re.MULTILINE)


def page_claims() -> list[Record]:
    """Twelve claim records; claim 0 has two months of backing, claim 3 (hospital) contradicts
    claim 6 (clinics); odd claims are disputed (one opposer)."""
    claims = [claim_record(n) for n in range(12)]
    claims[0] = claim_record(0, supporter_count=4, last_date="2024-06-01",
                             by_month={"2023-01": {"supporters": 1, "opposers": 0},
                                       "2024-06": {"supporters": 3, "opposers": 0}})
    claims[3]["contradicts"] = [IDS[6]]
    claims[6]["contradicts"] = [IDS[3]]
    return claims


def page_assignments(claims: list[Record]) -> dict[str, Any]:
    records = {}
    for index, claim in enumerate(claims):
        pages = [PAGE_IDS[index % 4]]
        if index == 4:
            pages.append("costs")
        if index == 11:
            pages = []
        records[claim["short_id"]] = {"pages": pages, "labels": ["usa"] if index % 2 == 0 else [],
                                      "p": {}, "label_p": {}}
    return {"assignments": records, "proposals": [], "metadata": {}}


def claim_ids_in(user: Any) -> list[str]:
    """Short ids of a prompt's ``<claims>`` block (the page's claims now)."""
    text = _flatten(user)
    block = text[text.rindex("<claims"):]
    return list(dict.fromkeys(_CLAIM_LINE.findall(block)))


def main_ids_in(user: Any) -> list[str]:
    """The short ids of the ``main`` claims of a prompt's ``<claims>`` block."""
    text = _flatten(user)
    return _MAIN_LINE.findall(text[text.rindex("<claims"):])


def page_id_in(user: Any) -> str:
    return _PAGE_ID.search(_flatten(user)).group(1)


def points_of(ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """``points`` (one ``fact`` per claim) and an empty ``left_out``."""
    return {"points": [{"kind": "fact", "statement": f"נקודה {sid}", "claims": [sid]} for sid in ids],
            "left_out": []}


def article(ids: list[str], page_id: str, extra: str = "") -> str:
    other = next(pid for pid in PAGE_IDS if pid != page_id)
    told = " ".join(f"משפט על הטענה {sid}.[^{sid}]" for sid in ids)
    return (f"פתיח קצר על הדף.\n\n## חלק ראשון\n\n{told}\nטקסט עם קישור ל[[{other}|דף אחר]] "
            f"ול[[label:usa|ארה\"ב]].\n{extra}")


Responder = Callable[[str, Any, str], dict[str, Any]]


class FakeWriter:
    """A fake ``LLMClient`` for the write / update stages; records every call. ``batch``: it
    has a batch API (``complete_batch_json`` answers each request as ``complete_json`` would;
    a request whose answer raises is left out, as a batch item without a usable answer) and
    records the tasks of every batch."""

    def __init__(self, model: str = "fake-sonnet", respond: Responder | None = None, *,
                 batch: bool = False) -> None:
        self.model = model
        self.respond = respond
        self.batch = batch
        self.calls: list[dict[str, Any]] = []
        self.batches: list[list[str]] = []

    def supports_batch(self) -> bool:
        return self.batch

    def cached_json(self, request: BatchRequest) -> None:
        return None

    def complete_json(self, system: Any, user: Any, task: str = "",
                      response_schema: dict | None = None) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "task": task, "schema": response_schema})
        if self.respond is not None:
            return copy.deepcopy(self.respond(system, user, task))
        page_id = page_id_in(user)
        answer = {**points_of(main_ids_in(user)), "body": article(claim_ids_in(user), page_id),
                  "summary": f"תקציר {page_id}", "questions": ["שאלה?"],
                  "related": [{"page_id": "clinics", "relation": "see_also"}]}
        if task.startswith("update:"):
            answer["no_change"] = False
        return answer

    def complete_batch_json(self, requests: Sequence[BatchRequest], *,
                            resumable: bool = False) -> dict[str, Any]:
        assert self.batch and resumable, "the pages' batches are resumable"
        self.batches.append([request.task for request in requests])
        answers = {}
        for request in requests:
            try:
                answers[request.request_id] = self.complete_json(
                    request.system, request.user, request.task, request.response_schema)
            except (RuntimeError, ValueError):
                continue
        return answers

    def tasks(self) -> list[str]:
        return [call["task"] for call in self.calls]


Edits = Callable[[str, str], list[dict[str, str]]]


def article_body(user: Any) -> str:
    """The page body of a proofreading prompt."""
    return _flatten(user).removeprefix("<article>\n").removesuffix("\n</article>")


class FakeProofreader:
    """A fake ``LLMClient`` for the proofread stage: ``edits(page_id, body)`` is a page's
    answer (none by default); the pages in ``fail`` get no answer. Records every batch (page
    ids) and every sync call."""

    def __init__(self, edits: Edits | None = None, *, fail: Collection[str] = (),
                 batch: bool = True) -> None:
        self.model = "fake-proofread"
        self.edits = edits or (lambda page_id, body: [])
        self.fail = set(fail)
        self.batch = batch
        self.batches: list[list[str]] = []
        self.calls: list[str] = []

    def supports_batch(self) -> bool:
        return self.batch

    def _answer(self, request: BatchRequest) -> dict[str, Any]:
        return {"edits": copy.deepcopy(self.edits(request.request_id, article_body(request.user)))}

    def complete_batch_json(self, requests: Sequence[BatchRequest]) -> dict[str, Any]:
        self.batches.append([request.request_id for request in requests])
        return {request.request_id: self._answer(request) for request in requests
                if request.request_id not in self.fail}

    def complete_json(self, system: Any, user: Any, task: str = "",
                      response_schema: dict | None = None) -> dict[str, Any]:
        page_id = task.removeprefix("proofread:")
        self.calls.append(page_id)
        if page_id in self.fail:
            raise RuntimeError("fake refusal")
        return self._answer(BatchRequest(page_id, system, user))

    def pages(self) -> list[str]:
        return [page_id for batch in self.batches for page_id in batch] + self.calls


class PagesWorkspace:
    """The claim inputs (``sources``), plan, assignments and the pages artifact in a temp
    folder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.plan_path = root / "plan.json"
        self.assignments_path = root / "assignments.json"
        self.artifact = Artifact(root / "wiki_pages.json", root / "wiki_pages_edited.json")
        claims = page_claims()
        self.sources = write_claim_inputs(claims, root)
        write_json_file(make_plan(), self.plan_path)
        write_json_file(page_assignments(claims), self.assignments_path)

    def write_claims(self, claims: list[Record]) -> None:
        """Rewrite the claim inputs so that they load to ``claims``."""
        write_claim_inputs(claims, self.root)

    def claims(self) -> list[Record]:
        return load_claim_records(self.sources)

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
        return run(sources=self.sources, plan_path=self.plan_path,
                   assignments_path=self.assignments_path, artifact=self.artifact,
                   llm=llm or FakeWriter(), update_llm=update_llm or FakeWriter("fake-update"),
                   **kwargs)
