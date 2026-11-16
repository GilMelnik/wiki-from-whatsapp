"""Fakes for step 4's model stages: an embedder, a classifier and a chat client, with a disk-like
cache each, so a second run is answered without a call. All names are invented."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from step_4_entities.match_judge import JUDGE_SYSTEM
from step_4_entities.mentions import SimpleAnalyzer
from step_4_entities.run import MatchClients, run
from utils.llm_client import Answer, BatchRequest, ClassifierError, Question
from utils.paths import Artifact

DIM = 16
NAME_LINE = re.compile(r'^(N\d+) "(.*)" \| \d+ claims$')
GROUP_LINE = re.compile(r"^(G\d+): (.*) \(names: (.*)\)$")
CLAIM_LINE = re.compile(r"^(C\d+): (.*)$")
STATE_NAME = re.compile(r"^Name [AB]: (.*)$")


def claim(claim_id: str, entities: list[str], text: str, date: str = "2024-01-01") -> dict:
    return {"claim_id": claim_id, "thread_id": claim_id.rsplit("-", 1)[0], "claim_text": text,
            "entities": entities, "first_date": date, "last_date": date}


class FakeEmbed:
    """Vectors by the name a text starts with: names of one ``topic`` point the same way."""

    def __init__(self, topics: Mapping[str, str] | None = None) -> None:
        self.topics = dict(topics or {})
        self.texts: list[str] = []
        self.seen: set[str] = set()

    def _vector(self, text: str) -> np.ndarray:
        name = text.split(": ", 1)[0]
        seed = self.topics.get(name, name)
        digest = hashlib.sha1(seed.encode("utf-8")).digest()
        vector = np.frombuffer(digest[:DIM], dtype=np.uint8).astype(np.float32) - 127.5
        return vector / np.linalg.norm(vector)

    def __call__(self, texts: Sequence[str], kind: str) -> np.ndarray:
        self.texts += [t for t in texts if t not in self.seen]
        self.seen |= set(texts)
        return np.stack([self._vector(t) for t in texts])


class FakeClassifier:
    """P(same) by a function of the two names; ``failing`` pairs raise."""

    provider, model = "fake", "jev-fake"

    def __init__(self, same: Callable[[str, str], float] = lambda a, b: 0.0,
                 failing: set[tuple[str, str]] | None = None) -> None:
        self.same = same
        self.failing = failing or set()
        self.cache: dict[str, float] = {}
        self.calls = 0
        self.states: list[str] = []

    @staticmethod
    def names(state: str) -> tuple[str, str]:
        found = [m.group(1) for line in state.splitlines() if (m := STATE_NAME.match(line))]
        return found[0], found[1]

    def _answer(self, state: str) -> list[Answer]:
        if state in self.cache:
            return [Answer("same", self.cache[state])]
        pair = self.names(state)
        if pair in self.failing:
            raise ClassifierError(f"fake failure for a pair of {len(pair)} names")
        self.calls += 1
        self.states.append(state)
        self.cache[state] = self.same(*pair)
        return [Answer("same", self.cache[state])]

    def classify(self, state: str, questions: Sequence[Question], task: str = "") -> list[Answer]:
        return self._answer(state)

    def classify_many(self, items: Sequence[tuple[str, Sequence[Question]]], *,
                      task: str = "") -> list[list[Answer]]:
        out = []
        for index, (state, _) in enumerate(items):
            try:
                out.append(self._answer(state))
            except ClassifierError as exc:
                exc.index = index
                raise
        return out

    def cached(self, state: str, questions: Sequence[Question]) -> list[Answer] | None:
        return [Answer("same", self.cache[state])] if state in self.cache else None


def judge_names(user: str) -> dict[str, str]:
    """``id -> name`` of a judge prompt."""
    return {m.group(1): m.group(2) for line in user.splitlines() if (m := NAME_LINE.match(line))}


class FakeLLM:
    """Answers judge and senses requests with ``judge`` / ``senses`` callbacks (``None``: no
    usable answer); keeps answers in a cache like the real client."""

    provider, model = "fake", "claude-opus-5-5"

    def __init__(self, judge: Callable[[dict[str, str]], dict | None],
                 senses: Callable[[str], dict | None] | None = None) -> None:
        self.judge = judge
        self.senses = senses or (lambda user: None)
        self.cache: dict[str, Any] = {}
        self.calls: list[str] = []
        self.usage: list[dict[str, int]] = []

    def supports_batch(self) -> bool:
        return True

    @staticmethod
    def _key(req: BatchRequest) -> str:
        return json.dumps([str(req.system), str(req.user)])

    def cached_json(self, req: BatchRequest) -> Any | None:
        return self.cache.get(self._key(req))

    def _answer(self, req: BatchRequest) -> Any | None:
        key = self._key(req)
        if key in self.cache:
            return self.cache[key]
        self.calls.append(req.task)
        self.usage.append({"input_tokens": 10, "output_tokens": 5})
        user = str(req.user)
        answer = (self.judge(judge_names(user)) if req.system == JUDGE_SYSTEM
                  else self.senses(user))
        if answer is not None:
            self.cache[key] = answer
        return answer

    def complete_batch_json(self, requests: Sequence[BatchRequest]) -> dict[str, Any]:
        answers = {req.request_id: self._answer(req) for req in requests}
        return {rid: answer for rid, answer in answers.items() if answer is not None}

    def complete_json(self, system: Any, user: Any, task: str = "",
                      response_schema: dict | None = None) -> Any:
        answer = self._answer(BatchRequest("sync", system, user, task=task))
        if answer is None:
            raise ValueError("no usable answer")
        return answer


def grouped(things: Mapping[str, Sequence[str]], unsure: Sequence[tuple[str, str]] = ()
            ) -> Callable[[dict[str, str]], dict]:
    """A judge that groups names by the things each stands for (``name -> [thing]``)."""

    def answer(ids: dict[str, str]) -> dict:
        by_thing: dict[str, list[str]] = {}
        for name_id, name in ids.items():
            for thing in things.get(name, [name]):
                by_thing.setdefault(thing, []).append(name_id)
        id_of = {name: name_id for name_id, name in ids.items()}
        return {"groups": [{"label": thing, "names": names} for thing, names in by_thing.items()],
                "unsure": [{"a": id_of[a], "b": id_of[b], "reason": "not clear"}
                           for a, b in unsure if a in id_of and b in id_of]}

    return answer


def by_keyword(keywords: Mapping[str, Sequence[str]]) -> Callable[[str], dict]:
    """A senses answer: a claim goes to every group whose label's keywords its text holds."""

    def answer(user: str) -> dict:
        groups = {m.group(1): m.group(2) for line in user.splitlines()
                  if (m := GROUP_LINE.match(line))}
        rows = []
        for line in user.splitlines():
            if (m := CLAIM_LINE.match(line)) and not line.startswith("context:"):
                text = m.group(2).casefold()
                hits = [gid for gid, label in groups.items()
                        if any(word in text for word in keywords.get(label, ()))]
                rows.append({"claim": m.group(1), "groups": hits or ["unclear"]})
        return {"claims": rows}

    return answer


class MatchStep:
    """Runs step 4 with the model stages on files inside ``tmp_path``."""

    def __init__(self, tmp_path: Path, clients: MatchClients, seed: list[dict] | None = None
                 ) -> None:
        self.claims_path = tmp_path / "claims.json"
        self.artifact = Artifact(tmp_path / "entities.json", tmp_path / "entities_edited.json")
        self.analysis_path = tmp_path / "analysis.json"
        self.seed_path = tmp_path / "seed.json"
        self.matches_path = tmp_path / "entity_matches.json"
        self.clients = clients
        if seed is not None:
            self.seed_path.write_text(json.dumps({"entities": seed}), encoding="utf-8")

    def run(self, claims: list[dict], **kwargs: Any) -> dict:
        self.claims_path.write_text(json.dumps({"claims": claims}), encoding="utf-8")
        return run(claims_path=self.claims_path, artifact=self.artifact,
                   analyzer=SimpleAnalyzer(), analysis_path=self.analysis_path,
                   seed_path=self.seed_path, matches_path=self.matches_path,
                   **{"match": True, "clients": self.clients, **kwargs})

    def edited(self) -> dict:
        return json.loads(self.artifact.edited.read_text(encoding="utf-8"))

    def matches(self) -> dict:
        return json.loads(self.matches_path.read_text(encoding="utf-8"))

    def entities_of(self, name: str) -> list[dict]:
        return [e for e in self.edited()["entities"]
                if any(m["name"] == name for m in e["members"])]

    def entity_of(self, name: str) -> dict:
        found = self.entities_of(name)
        assert len(found) == 1, f"{len(found)} entities hold the name"
        return found[0]

    def member(self, entity: dict, name: str) -> dict:
        return next(m for m in entity["members"] if m["name"] == name)
