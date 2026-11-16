"""Stage 3 of the model matching: the judge splits each bundle into the things it holds.

The chat stage ``entities.match.judge.stage`` (Opus 5.5, effort high) sees ONE bundle per call:
every name with an id (``N1``...), its claim count and example claims spread over its claims
(``match_claims``), and the fixed units (a seed entry; on an update, an existing entity) whose
names are one thing. It answers with JSON: the bundle split into as many groups as it holds
real-world things (each a short label and its name ids; a name used for two things is listed in
both), and the pairs it kept apart but is unsure about, each with a short reason. The calls go
as one batch when the client has a batch API (``judge.batch``), and are cached like every stage.

``resolve_groups`` makes the answer obey the fixed units: a group that holds a name of a unit
joins every group holding another name of it, and the unit's names are all added to it. A name
listed in more than one group (a name with two meanings) does not pull groups together, so a
seed alias can keep a second meaning in a group of its own.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import anthropic

from step_4_entities.match_bundles import Bundle
from step_4_entities.match_claims import ClaimSampler
from step_4_entities.match_net import PairKey, pair_key
from utils.config import ENTITIES_CONFIG
from utils.llm_client import BatchRequest, LLMClient
from utils.llm_client.ask import ask_json

JUDGE_CONFIG = ENTITIES_CONFIG["match"]["judge"]
NAME_ID = "N"
UNIT_ID = "U"
# Errors the calls of a stage can end with as a whole (a batch that cannot be submitted,
# polled or fetched). One bundle's missing or unusable answer is not one of them.
CALL_ERRORS = (anthropic.APIError, RuntimeError, ValueError, OSError)


class StageFailed(RuntimeError):
    """The calls of a model stage failed as a whole: the run stops and writes nothing."""

    def __init__(self, stage: str, count: int, cause: BaseException) -> None:
        super().__init__(
            f"Entity {stage}: the calls of {count} requests failed as a whole ({cause!r}); "
            "nothing was written. Run the step again: a batch already submitted is picked up, "
            "not paid for again."
        )


def ask_stage(llm: LLMClient, requests: Sequence[BatchRequest], *, stage: str, batch: bool,
              logger: logging.Logger) -> dict[str, Any]:
    """The answers of one stage's requests (a resumable batch when ``batch``); a failure of
    the calls as a whole raises ``StageFailed``."""

    try:
        return ask_json(llm, requests, batch=batch and llm.supports_batch(), logger=logger,
                        resumable=True)
    except CALL_ERRORS as exc:
        raise StageFailed(stage, len(requests), exc) from exc

JUDGE_SYSTEM = """\
You help build a Hebrew wiki from the conversations of an Israeli WhatsApp group of gay men \
going through surrogacy abroad. Claims extracted from the chat name agencies, clinics, \
doctors, lawyers, other providers, places and public bodies in free text, so one real-world \
thing appears under many name strings (Hebrew and English spellings, abbreviations, titles, \
typos), and one name string can stand for two different things.

The user message lists one bundle of name strings that may refer to the same things: each \
name with its id (N1, N2, ...), how many claims mention it, and example claims spread over \
the time it was mentioned (month: claim). Some names form fixed units (U1, U2, ...): the \
owner's list, or an earlier grouping, says they are one thing.

Your job: split the bundle into as many groups as it holds real-world things. Each group gets \
a short English label of what the thing is (for example "the Israeli Ministry of Health", "a \
fertility clinic in Athens") and the ids of its names.

When names are the same thing:
- Together: another spelling, language, abbreviation or title of the same thing (for \
example "Ministry of Health", "משרד הבריאות" and "MoH").
- Together: a person and their own practice or business (a lawyer and their law office).
- Together: branches of one body only where a fixed unit in this bundle already joins such \
branches.
- Apart: a general word and one specific name (the word "bank" and one bank, "the embassy" \
and the embassy of one country).
- Apart: two different bodies of one kind (two ministries, two countries, two clinics).
- Apart, and reported as unsure: anything the names and the claims do not make clear.
- One name used for two different things: list it in both groups. Read all its claims: a \
name whose claims are about two different things has two meanings, also when it belongs to a \
fixed unit. A group may hold only that name when the second thing has no other name here.

Rules:
- Every name id appears in at least one group. A name that is the same as no other name is a \
group of its own.
- The names of a fixed unit always stay together in one group; you may add other names to \
that group. A name of a fixed unit may also appear in a second group when its claims show a \
second meaning.
- unsure: the pairs of names you put in different groups although they might be the same \
thing, each with a short reason in one English sentence. Leave out pairs you are sure about.
- Decide from the names and the claims only.
- Return only the JSON object asked for."""

_TEXT = {"type": "string"}
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": {"type": "array", "items": {
            "type": "object",
            "properties": {"label": _TEXT, "names": {"type": "array", "items": _TEXT}},
            "required": ["label", "names"],
            "additionalProperties": False,
        }},
        "unsure": {"type": "array", "items": {
            "type": "object",
            "properties": {"a": _TEXT, "b": _TEXT, "reason": _TEXT},
            "required": ["a", "b", "reason"],
            "additionalProperties": False,
        }},
    },
    "required": ["groups", "unsure"],
    "additionalProperties": False,
}


@dataclass
class Group:
    """One real-world thing of a bundle: its label and its names (sorted)."""

    label: str
    names: list[str]


@dataclass
class Verdict:
    """The judge's answer for one bundle, after ``resolve_groups``."""

    groups: list[Group]
    unsure: list[tuple[PairKey, str]] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)

    def shared(self) -> set[str]:
        """Names listed in more than one group."""

        seen: set[str] = set()
        twice: set[str] = set()
        for group in self.groups:
            twice |= seen & set(group.names)
            seen |= set(group.names)
        return twice


@dataclass
class JudgeResult:
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)  # bundle ids without a usable answer
    uncached: list[BatchRequest] = field(default_factory=list)  # dry run: would be asked
    requests: int = 0


def resolve_groups(groups: Sequence[Group], units: Sequence[Sequence[str]]) -> list[Group]:
    """``groups`` made to keep every fixed unit whole (see the module docstring)."""

    counts: dict[str, int] = {}
    for group in groups:
        for name in set(group.names):
            counts[name] = counts.get(name, 0) + 1
    sets = [set(group.names) for group in groups]
    root = list(range(len(groups)))

    def find(i: int) -> int:
        while root[i] != i:
            i = root[i]
        return i

    for unit in units:
        holders = sorted({i for i, names in enumerate(sets) for name in unit
                          if name in names and counts.get(name) == 1})
        if not holders:
            holders = sorted({i for i, names in enumerate(sets) if names & set(unit)})[:1]
        if not holders:
            continue
        target = find(holders[0])
        for other in holders[1:]:
            other = find(other)
            if other != target:
                root[max(other, target)] = min(other, target)
                target = min(other, target)
        sets[target] |= set(unit)
    merged: dict[int, set[str]] = {}
    for i, names in enumerate(sets):
        merged.setdefault(find(i), set()).update(names)
    return [Group(groups[i].label, sorted(names)) for i, names in sorted(merged.items())]


class Judge:
    """Builds the judge's requests and reads its answers."""

    def __init__(self, llm: LLMClient, sampler: ClaimSampler,
                 claims_of: Mapping[str, Sequence[str]], logger: logging.Logger) -> None:
        self.llm = llm
        self.sampler = sampler
        self.claims_of = claims_of
        self.logger = logger

    def ids(self, bundle: Bundle) -> dict[str, str]:
        """``id -> name``: most claims first, ties by name."""

        order = sorted(bundle.names, key=lambda n: (-len(self.claims_of.get(n) or []), n))
        return {f"{NAME_ID}{i}": name for i, name in enumerate(order, 1)}

    def user_prompt(self, bundle: Bundle, ids: Mapping[str, str]) -> str:
        lines = [f'<bundle names="{len(ids)}">']
        for name_id, name in ids.items():
            claims = self.claims_of.get(name) or []
            lines.append(f'{name_id} "{name}" | {len(claims)} claims')
            lines += [f"- {self.sampler.line(c)}" for c in self.sampler.spread(claims)]
            lines.append("")
        lines.append("</bundle>")
        id_of = {name: name_id for name_id, name in ids.items()}
        units = [f"{UNIT_ID}{i}: " + ", ".join(sorted((id_of[n] for n in unit),
                                                      key=lambda x: int(x[1:])))
                 for i, unit in enumerate(bundle.units, 1)]
        lines += ["", "<fixed_units>", *(units or ["none"]), "</fixed_units>", "",
                  "Split the bundle into the real-world things it holds."]
        return "\n".join(lines)

    def request(self, bundle: Bundle) -> BatchRequest:
        return BatchRequest(bundle.bundle_id, JUDGE_SYSTEM,
                            self.user_prompt(bundle, self.ids(bundle)),
                            task=f"entity-judge:{bundle.bundle_id}", response_schema=JUDGE_SCHEMA)

    def parse(self, answer: Any, bundle: Bundle) -> Verdict | None:
        """The verdict of one answer, or ``None`` when it has no usable groups."""

        ids = self.ids(bundle)
        groups_raw = answer.get("groups") if isinstance(answer, dict) else None
        if not isinstance(groups_raw, list):
            return None
        groups = []
        for raw in groups_raw:
            if not isinstance(raw, dict):
                continue
            names = [ids[i] for i in raw.get("names") or [] if i in ids]
            if names:
                groups.append(Group(" ".join(str(raw.get("label") or "").split()),
                                    sorted(set(names))))
        if not groups:
            return None
        groups = resolve_groups(groups, bundle.units)
        listed = {name for group in groups for name in group.names}
        unsure: dict[PairKey, str] = {}
        for raw in answer.get("unsure") or []:
            if isinstance(raw, dict) and raw.get("a") in ids and raw.get("b") in ids:
                a, b = ids[raw["a"]], ids[raw["b"]]
                if a != b:
                    unsure.setdefault(pair_key(a, b), " ".join(str(raw.get("reason") or "").split()))
        return Verdict(groups, sorted(unsure.items()),
                       sorted(set(bundle.names) - listed))

    def judge(self, bundles: Sequence[Bundle], *, batch: bool, dry: bool = False) -> JudgeResult:
        result = JudgeResult()
        requests = {b.bundle_id: self.request(b) for b in bundles}
        by_id = {b.bundle_id: b for b in bundles}
        if dry:
            answers = {}
            for bid, req in requests.items():
                cached = self.llm.cached_json(req)
                if cached is None:
                    result.uncached.append(req)
                else:
                    answers[bid] = cached
        else:
            result.requests = len(requests)
            answers = ask_stage(self.llm, list(requests.values()), stage="judge", batch=batch,
                                logger=self.logger)
        for bid in requests:
            if bid not in answers:
                if not dry:
                    result.failed.append(bid)
                continue
            verdict = self.parse(answers[bid], by_id[bid])
            if verdict is None:
                result.failed.append(bid)
            else:
                result.verdicts[bid] = verdict
        if result.failed:
            self.logger.warning(f"Entity judge: {len(result.failed)} of {len(requests)} bundles "
                                "have no usable answer; their names keep the spelling rules")
        return result
