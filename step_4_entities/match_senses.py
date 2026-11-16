"""Stage 4 of the model matching: which meaning each claim of a two-meaning name is about.

For a name the judge listed in more than one group (or, on an update, a known name held by
members of several entities that gets new claims), one call of the judge's chat stage lists the
name's claims grouped by conversation (``thread_id``), each conversation with up to
``entities.match.senses.context_claims`` other claims of it as context lines, and the candidate
groups (label and names). It answers, per claim, which group is meant, several groups when the
claim is about more than one of them (the two things can be related), or ``unclear``. A name
with more than ``senses.claims_per_call`` claims is asked in several calls (a conversation is
never cut). A claim the answer leaves out counts as unclear; the caller sends unclear claims to
the group that got the most claims of that name.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from step_4_entities.match_claims import ClaimSampler
from step_4_entities.match_judge import CALL_ERRORS
from utils.config import ENTITIES_CONFIG
from utils.llm_client import BatchRequest, LLMClient
from utils.llm_client.ask import ask_json

SENSES_CONFIG = ENTITIES_CONFIG["match"]["senses"]
CLAIM_ID = "C"
GROUP_ID = "G"
UNCLEAR = "unclear"
REQUEST_PREFIX = "senses-"
REQUEST_HASH_CHARS = 16

SENSES_SYSTEM = """\
You help build a Hebrew wiki from the conversations of an Israeli WhatsApp group of gay men \
going through surrogacy abroad. One name string is used in the chat for more than one \
real-world thing (for example a short name that is both a lawyer's first name and the name of \
an insurance company).

The user message gives the name, the things it may stand for (groups G1, G2, ..., each with a \
short label and the names it is known by), and claims from the chat that mention the name, \
grouped by conversation. Lines starting with "context:" are other claims from the same \
conversation, shown only to help; they get no answer.

Your job: for every claim with an id (C1, C2, ...), say which group it is about: the group's \
id; several group ids when the claim is about more than one of the things (they can be \
related, for example a claim about a dispute between them); or "unclear" when the claim and \
its context do not tell.

Rules:
- Answer every claim id exactly once. "groups" lists the group ids, or holds only "unclear".
- Decide from the claim, its conversation's context and the groups' labels and names.
- Return only the JSON object asked for."""

SENSES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": {
        "type": "object",
        "properties": {"claim": {"type": "string"},
                       "groups": {"type": "array", "items": {"type": "string"}}},
        "required": ["claim", "groups"],
        "additionalProperties": False,
    }}},
    "required": ["claims"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class SenseJob:
    """One name to divide: its claims to decide and its candidate groups (label, names)."""

    name: str
    claim_ids: tuple[str, ...]
    groups: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass
class SenseResult:
    """Per name: ``claim_id -> group indexes`` (empty: unclear); names whose calls failed."""

    answers: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    uncached: list[BatchRequest] = field(default_factory=list)  # dry run: would be asked
    requests: int = 0


class SenseSplitter:
    """Builds the per-claim requests and reads the answers."""

    def __init__(self, llm: LLMClient, sampler: ClaimSampler,
                 claims_by_id: Mapping[str, Mapping[str, Any]], logger: logging.Logger) -> None:
        self.llm = llm
        self.sampler = sampler
        self.claims_by_id = claims_by_id
        self.logger = logger
        self.threads: dict[str, list[str]] = {}
        for claim_id in sampler.ordered(list(claims_by_id)):
            thread = str(claims_by_id[claim_id].get("thread_id") or "")
            self.threads.setdefault(thread, []).append(claim_id)

    def _thread_of(self, claim_id: str) -> str:
        return str(self.claims_by_id[claim_id].get("thread_id") or "")

    def chunks(self, job: SenseJob) -> list[list[list[str]]]:
        """The job's claims by conversation (oldest first), packed into calls."""

        by_thread: dict[str, list[str]] = {}
        for claim_id in self.sampler.ordered(job.claim_ids):
            by_thread.setdefault(self._thread_of(claim_id), []).append(claim_id)
        calls: list[list[list[str]]] = [[]]
        size = 0
        for claims in by_thread.values():
            if size and size + len(claims) > SENSES_CONFIG["claims_per_call"]:
                calls.append([])
                size = 0
            calls[-1].append(claims)
            size += len(claims)
        return [call for call in calls if call]

    def user_prompt(self, job: SenseJob, threads: Sequence[Sequence[str]]) -> str:
        lines = [f'<name>"{job.name}"</name>', "", "<groups>"]
        for i, (label, names) in enumerate(job.groups, 1):
            known = ", ".join(f'"{n}"' for n in names)
            lines.append(f"{GROUP_ID}{i}: {label or 'no label'} (names: {known})")
        lines += ["</groups>", "", "<claims>"]
        number = 0
        for claims in threads:
            asked = set(claims)
            context = [c for c in self.threads.get(self._thread_of(claims[0]), [])
                       if c not in asked]
            lines.append("<conversation>")
            for claim_id in claims:
                number += 1
                lines.append(f"{CLAIM_ID}{number}: {self.sampler.line(claim_id)}")
            for claim_id in self.sampler.spread(context, SENSES_CONFIG["context_claims"]):
                lines.append(f"context: {self.sampler.line(claim_id)}")
            lines.append("</conversation>")
        lines += ["</claims>", "", "For every claim id, say which group it is about."]
        return "\n".join(lines)

    def requests(self, job: SenseJob) -> list[tuple[BatchRequest, list[str]]]:
        """``(request, the claim ids C1, C2, ... stand for)`` per call of the job."""

        out = []
        for threads in self.chunks(job):
            claims = [c for thread in threads for c in thread]
            seed = "\n".join([job.name, *claims])
            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:REQUEST_HASH_CHARS]
            request_id = f"{REQUEST_PREFIX}{digest}"
            out.append((BatchRequest(request_id, SENSES_SYSTEM, self.user_prompt(job, threads),
                                     task=f"entity-senses:{request_id}",
                                     response_schema=SENSES_SCHEMA), claims))
        return out

    @staticmethod
    def parse(answer: Any, claims: Sequence[str], group_count: int) -> dict[str, list[int]]:
        """``claim_id -> group indexes`` for one answer (unclear or left out: ``[]``)."""

        picked: dict[str, list[int]] = {claim: [] for claim in claims}
        rows = answer.get("claims") if isinstance(answer, dict) else None
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            claim = str(row.get("claim") or "")
            groups = row.get("groups") if isinstance(row.get("groups"), list) else []
            if not (claim.startswith(CLAIM_ID) and claim[1:].isdigit()):
                continue
            position = int(claim[1:]) - 1
            if not 0 <= position < len(claims):
                continue
            indexes = {int(g[1:]) - 1 for g in map(str, groups)
                       if g.startswith(GROUP_ID) and g[1:].isdigit()}
            picked[claims[position]] = sorted(i for i in indexes if 0 <= i < group_count)
        return picked

    def split(self, jobs: Sequence[SenseJob], *, batch: bool, dry: bool = False) -> SenseResult:
        result = SenseResult()
        planned = [(job, request, claims) for job in jobs for request, claims in self.requests(job)]
        if dry:
            answers = {}
            for _, request, _ in planned:
                cached = self.llm.cached_json(request)
                if cached is None:
                    result.uncached.append(request)
                else:
                    answers[request.request_id] = cached
        else:
            result.requests = len(planned)
            try:
                answers = ask_json(self.llm, [request for _, request, _ in planned],
                                   batch=batch and self.llm.supports_batch(), logger=self.logger)
            except CALL_ERRORS as exc:
                self.logger.error(f"Entity senses: the calls failed ({exc!r})")
                answers = {}
        for job, request, claims in planned:
            if request.request_id not in answers:
                if not dry and job.name not in result.failed:
                    result.failed.append(job.name)
                continue
            result.answers.setdefault(job.name, {}).update(
                self.parse(answers[request.request_id], claims, len(job.groups)))
        for name in result.failed:
            result.answers.pop(name, None)
        if result.failed:
            self.logger.warning(f"Entity senses: {len(result.failed)} names have no usable "
                                "answer; they are flagged for review")
        return result
