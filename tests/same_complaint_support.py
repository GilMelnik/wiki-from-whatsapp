"""Synthetic claims, people and a fake model for the same-complaint tests (nothing real).

``World`` builds claim records whose backers are chosen per claim (``tests.claim_fixtures``
gives every claim the same people), writes the files the loader reads under a temp folder and
rewrites the private audit with those people. Person ids hold ``SECRET`` so a test can assert
that none of them reaches a prompt or a stored file. ``FakeModel`` answers the check's
requests from a callback and keeps its answers in a cache like the real client.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from step_6_plan.claim_records import ClaimSources, Record, load_claim_records
from tests.claim_fixtures import claim_record, write_claim_inputs
from utils.json_io import read_json_file, write_json_file
from utils.llm_client import BatchRequest

NAMES = {"e0001": "Agency One", "e0002": "Clinic Two", "e0003": "Lawyer Three"}
CLAIM_LINE = re.compile(r"^(C\d+): (.*)$")
THING = re.compile(r"<thing>(.*)</thing>")


def person(k: int) -> str:
    return f"p-SECRET-person-{k}"


def set_backers(sources: ClaimSources, people: Mapping[str, Sequence[str]]) -> None:
    """Rewrite step 3's private audit so that each claim of ``people`` (claim id -> person ids)
    is backed by exactly those people (on the claim's last date; its other events stay)."""

    audit = read_json_file(sources.audit)
    dates = {r["claim_id"]: r["last_date"] for r in load_claim_records(sources)}
    for entry in audit["audit"]:
        if entry["claim_id"] in people:
            entry["supporters"] = [
                {"person": p, "date": dates[entry["claim_id"]], "via": "message",
                 "message_id": "m-SECRET"}
                for p in people[entry["claim_id"]]
            ]
    write_json_file(audit, sources.audit)


class World:
    """Claims with chosen entities and backers, written as the loader's input files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.records: list[Record] = []
        self.people: dict[str, list[str]] = {}

    def add(self, entities: Sequence[str], people: Sequence[int], *, stance: str = "negative",
            text: str | None = None) -> str:
        """A claim naming ``entities``, backed by ``person(k)`` for each ``k``; its short id."""

        n = len(self.records)
        record = claim_record(
            n, stance=stance, entity_ids=sorted(entities),
            entity_names=[NAMES[e] for e in sorted(entities)],
            claim_text=text or f"Synthetic complaint {n}.", supporter_count=len(set(people)),
            opposer_count=0, first_date="2024-03-01", last_date="2024-03-01")
        self.records.append(record)
        self.people[record["short_id"]] = [person(k) for k in people]
        return record["short_id"]

    def write(self) -> ClaimSources:
        """Write claims, entities and an audit with the chosen people; check they load back."""

        sources = write_claim_inputs(self.records, self.root)
        set_backers(sources, {r["claim_id"]: self.people[r["short_id"]] for r in self.records})
        assert load_claim_records(sources) == self.records
        return sources


def prompt_claims(user: str) -> dict[str, str]:
    """``C1 -> text`` of a check prompt."""

    return {m.group(1): m.group(2) for line in user.splitlines() if (m := CLAIM_LINE.match(line))}


def same_text(words: Mapping[str, str]) -> Callable[[str, dict[str, str]], dict]:
    """An answer that groups the claims whose text contains the same keyword (one group per
    keyword with two claims or more), labelled with ``words[keyword]``."""

    def answer(thing: str, claims: dict[str, str]) -> dict:
        groups = []
        for keyword, label in words.items():
            ids = [c for c, text in claims.items() if keyword in text]
            if len(ids) >= 2:
                groups.append({"label": label, "claims": ids})
        return {"groups": groups}

    return answer


class FakeModel:
    """Answers each request with ``answer(thing, {C1: text, ...})`` (``None``: no answer)."""

    provider, model = "fake", "claude-opus-5-5"

    def __init__(self, answer: Callable[[str, dict[str, str]], Any | None],
                 batch: bool = True) -> None:
        self.answer = answer
        self.batch = batch
        self.cache: dict[str, Any] = {}
        self.calls: list[BatchRequest] = []
        self.usage: list[dict[str, int]] = []
        self.error: Exception | None = None  # a batch that fails as a whole

    def supports_batch(self) -> bool:
        return self.batch

    @staticmethod
    def _key(request: BatchRequest) -> str:
        return json.dumps([str(request.system), str(request.user), request.response_schema])

    def cached_json(self, request: BatchRequest) -> Any | None:
        return self.cache.get(self._key(request))

    def _answer(self, request: BatchRequest) -> Any | None:
        key = self._key(request)
        if key in self.cache:
            return self.cache[key]
        self.calls.append(request)
        self.usage.append({"input_tokens": 10, "output_tokens": 5})
        thing = THING.search(str(request.user))
        answer = self.answer(thing.group(1) if thing else "", prompt_claims(str(request.user)))
        if answer is not None:
            self.cache[key] = answer
        return answer

    def complete_batch_json(self, requests: Sequence[BatchRequest], *,
                            resumable: bool = False) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        answers = {request.request_id: self._answer(request) for request in requests}
        return {rid: answer for rid, answer in answers.items() if answer is not None}

    def complete_json(self, system: Any, user: Any, task: str = "",
                      response_schema: dict | None = None) -> Any:
        answer = self._answer(BatchRequest("sync", system, user, task, response_schema))
        if answer is None:
            raise ValueError("no usable answer")
        return answer
