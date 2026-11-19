"""The same-complaint check: which negative claims about one named thing make the same complaint.
``python -m step_5_same_complaint.run [--estimate] [--only ENTITY_ID ...] [--rebuild]``.

The wiki publishes a negative statement about a named provider, place or public body only when
at least two different members reported it (``withhold`` in ``config.json``). A lone complaint
is released when a second person made the SAME complaint, and a model judges "same", entity by
entity:

- For every entity whose list (its negative claims, ``step_5_same_complaint.release.entity_lists``)
  could release something (``needs_call``), one call to the stage ``same_complaint.stage``
  (Opus 5.5, effort high, structured JSON). The user message names the entity (its canonical
  name) and lists the claims as ``C1: <text>``, ``C2: <text>`` ... in the list's order: never a
  short id, a claim id, a date, a count or a person. The answer: groups of two or more claim
  ids that make the same complaint, each with a short English label.
- An entity is asked again only when its list's fingerprint changed (C14); ``--rebuild`` asks
  about every list again (the LLM disk cache answers repeats); ``--only`` asks about the given
  entities only and leaves every other stored answer as it is. A list longer than
  ``max_claims_per_call`` stops the run before any call (a list is never cut).
- The answers are stored in ``ClaimSources.same_complaint`` (``SAME_COMPLAINT_PATH``) as short ids (shape: see
  ``step_5_same_complaint.release``), with the run's metadata (model, prompt version, config, git
  commit). An answer that does not fit its list is stored with its ``problem`` and releases
  nothing; a call without an answer stores nothing (the next run asks again).
- ``--estimate``: the calls a run would make that the LLM disk cache cannot answer, their
  input tokens (characters / ``estimate.chars_per_token``) and a price range from
  ``update.estimate.usd_per_million_tokens``: output at a low and a high figure per call
  (``estimate.output_tokens_per_call``), at ``update.estimate.batch_discount`` when the calls go
  as a batch. No model call, nothing written.

What the answers release, and so what is withheld now, is computed on every read
(``step_5_same_complaint.release.withheld_ids``). In the pipeline the check is step 5: it runs
right after the entities and before the plan (``review_tool.steps``); the update run stops after
it for the owner's look at the Withheld page when its answers changed.
"""

from __future__ import annotations

import argparse
import logging
import math
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from step_4_entities.match_judge import CALL_ERRORS
from step_5_same_complaint.claim_records import ClaimSources, Record, claim_backers, load_claim_records
from step_5_same_complaint.release import (
    Check,
    entity_name,
    groups_problem,
    list_fingerprint,
    load_answers,
    load_vetoes,
)
from utils.config import LLM_CONFIG, section
from utils.json_io import write_json_file
from utils.llm_client import BatchRequest, LLMClient
from utils.llm_client.ask import ask_json
from utils.logging_setup import git_commit, setup_step_logging
from utils.model_prices import ESTIMATE_CONFIG, stage_model, usd
from utils.paths import STEP_5
from utils.timestamps import utc_now

CONFIG = section("same_complaint")
ESTIMATE = CONFIG["estimate"]
CLAIM_ID = "C"

SYSTEM = """\
You help build a Hebrew wiki from the conversations of an Israeli WhatsApp group of gay men \
going through surrogacy abroad. The wiki publishes a complaint about a named provider, place \
or public body only when at least two different members made that same complaint. A \
complaint that only one member made stays out of the wiki.

The user message names one thing (an agency, a clinic, a doctor, a lawyer, another provider, \
a place or a public body) and lists the negative claims from the chat that mention it, each \
with its id (C1, C2, ...). Each claim was taken from one conversation.

Your job: find the claims that make the same complaint about this thing, and return them as \
groups of claim ids. A group holds two or more claims and gets a short English label of the \
complaint they share (for example "did not return the deposit").

When two claims make the same complaint:
- Same: both criticise the named thing for the same problem, also when the wording, the \
details or the strength differ (for example both say that the agency did not return a \
deposit, or that the clinic answers messages very slowly).
- Same: both warn against using the named thing in general, without giving a reason.
- Not the same: two different problems with the same thing (slow answers and a dispute \
about a bill).
- Not the same: a general warning and a specific problem.
- Not the same: a claim whose complaint is really about another thing that it also \
mentions, or a claim that mentions the named thing without criticising it.
- Not sure: do not group. A claim left out of every group stays unpublished, and that is \
the safe outcome.

Rules:
- A claim appears in at most one group. Many claims belong to no group.
- Decide from the claim texts only.
- Return only the JSON object asked for."""

_TEXT = {"type": "string"}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": {"type": "array", "items": {
            "type": "object",
            "properties": {"label": _TEXT, "claims": {"type": "array", "items": _TEXT}},
            "required": ["label", "claims"],
            "additionalProperties": False,
        }},
    },
    "required": ["groups"],
    "additionalProperties": False,
}


def local_ids(claims: Sequence[Record]) -> dict[str, str]:
    """``C1 -> short id`` ... in the list's order: the only ids the model sees."""

    return {f"{CLAIM_ID}{k}": claim["short_id"] for k, claim in enumerate(claims, 1)}


def user_prompt(name: str, claims: Sequence[Record]) -> str:
    lines = [f"<thing>{name}</thing>", "", f'<claims count="{len(claims)}">']
    lines += [f"{local}: {' '.join(claim['claim_text'].split())}"
              for local, claim in zip(local_ids(claims), claims)]
    lines += ["</claims>", "", "Find the claims that make the same complaint about this thing."]
    return "\n".join(lines)


def build_request(entity_id: str, claims: Sequence[Record]) -> BatchRequest:
    return BatchRequest(entity_id, SYSTEM, user_prompt(entity_name(entity_id, claims), claims),
                        task=f"same-complaint:{entity_id}", response_schema=SCHEMA)


def parse_answer(answer: Any, local: Mapping[str, str]) -> tuple[list[dict[str, Any]], str | None]:
    """The answer's groups as short ids, or ``([], problem)`` when it is unusable (an unknown
    id, a claim in two groups, a group of fewer than two claims, a wrong shape)."""

    raw = answer.get("groups") if isinstance(answer, dict) else None
    problem = groups_problem(raw, list(local))
    if problem:
        return [], problem
    groups = [{"label": " ".join(group["label"].split()),
               "claims": [local[c] for c in group["claims"]]} for group in raw]
    return groups, None


def due_lists(asked: Mapping[str, list[Record]], stored: Mapping[str, Any], *,
              only: Collection[str] | None, logger: logging.Logger) -> dict[str, list[Record]]:
    """The lists to ask about: the ones with no answer for today's fingerprint, restricted to
    ``only``. A list longer than ``max_claims_per_call`` raises before any call."""

    if only is not None:
        for entity_id in sorted(set(only) - set(asked)):
            logger.warning(f"--only {entity_id}: no list of this entity can release a claim; "
                           "it is not asked about")
    due = {
        entity_id: claims for entity_id, claims in asked.items()
        if (only is None or entity_id in only)
        and (stored.get(entity_id) or {}).get("fingerprint") != list_fingerprint(claims)
    }
    longest = max(due.items(), key=lambda item: len(item[1]), default=None)
    if longest and len(longest[1]) > CONFIG["max_claims_per_call"]:
        entity_id, claims = longest
        raise ValueError(
            f"Entity {entity_id} ({entity_name(entity_id, claims)}) has {len(claims)} negative "
            f"claims, more than same_complaint.max_claims_per_call "
            f"({CONFIG['max_claims_per_call']}). One call must read the whole list, so it is "
            "never cut: raise the limit in config.json, or run with --only for other entities."
        )
    return due


def estimate_calls(llm: LLMClient, requests: Sequence[BatchRequest], *, batch: bool
                   ) -> dict[str, Any]:
    """Calls, tokens and a price range of ``requests`` (see the module docstring)."""

    uncached = [r for r in requests if llm.cached_json(r) is None]
    tokens = math.ceil(sum(len(str(r.system)) + len(str(r.user)) for r in uncached)
                       / ESTIMATE["chars_per_token"])
    model = stage_model(CONFIG["stage"])
    discount = ESTIMATE_CONFIG["batch_discount"] if batch else 1.0
    outputs = [len(uncached) * ESTIMATE["output_tokens_per_call"][end] for end in ("low", "high")]
    return {"calls": len(uncached), "cached": len(requests) - len(uncached),
            "input_tokens": tokens, "output_tokens": outputs,
            "usd": [round(usd(model, tokens, out) * discount, 4) for out in outputs],
            "model": model, "batch": batch}


class SameComplaintRun:
    """One run of the check over the current claim records (see the module docstring)."""

    def __init__(self, sources: ClaimSources, llm: LLMClient | None,
                 logger: logging.Logger) -> None:
        self.answers_path = sources.same_complaint
        self.vetoes_path = sources.same_complaint_vetoes
        self.logger = logger
        self.llm = llm or LLMClient.for_stage(CONFIG["stage"], logger=logger,
                                              max_tokens=CONFIG["max_output_tokens"])
        self.records = load_claim_records(sources)
        self.backers = claim_backers(self.records, sources.audit)

    def run(self, *, rebuild: bool, only: Collection[str] | None, estimate: bool
            ) -> dict[str, Any]:
        stored = load_answers(self.answers_path)
        check = Check(self.records, self.backers, stored, frozenset())
        asked = check.asked()
        due = due_lists(asked, {} if rebuild else stored, only=only, logger=self.logger)
        requests = {entity_id: build_request(entity_id, claims) for entity_id, claims in due.items()}
        batch = bool(CONFIG["batch"]) and self.llm.supports_batch()
        self.logger.info(f"Same-complaint check: {len(asked)} of {len(check.lists)} entity lists "
                         f"can release a claim; {len(due)} to ask about")
        if estimate:
            result = estimate_calls(self.llm, list(requests.values()), batch=batch)
            self.logger.info(f"Estimate: {result}")
            return {"estimate": result, "lists": len(asked), "due": len(due)}
        answers = self._ask(list(requests.values()), batch=batch)
        if only is not None:  # every other answer stays as it is
            entries = dict(stored)
        else:
            entries = {} if rebuild else {e: entry for e, entry in stored.items() if e in asked}
        counts = {"lists": len(asked), "due": len(due), "answered": 0, "unusable": 0, "failed": 0}
        for entity_id, claims in due.items():
            if entity_id not in answers:
                entries.pop(entity_id, None)
                counts["failed"] += 1
                continue
            groups, problem = parse_answer(answers[entity_id], local_ids(claims))
            if problem:
                self.logger.warning(f"Entity {entity_id}: the answer is unusable ({problem}); "
                                    "none of its claims is released")
            counts["unusable" if problem else "answered"] += 1
            entries[entity_id] = {"fingerprint": list_fingerprint(claims), "groups": groups,
                                  "problem": problem, "answered_at": utc_now()}
        written = self._write(entries, stored, counts)
        check = Check(self.records, self.backers, entries, load_vetoes(self.vetoes_path))
        counts["released"] = len(check.released)
        self.logger.info(f"Same-complaint check done: {counts}; written: {written}")
        return {**counts, "written": written}

    def _ask(self, requests: Sequence[BatchRequest], *, batch: bool) -> dict[str, Any]:
        if not requests:
            return {}
        try:
            return ask_json(self.llm, requests, batch=batch, logger=self.logger, resumable=True)
        except CALL_ERRORS as exc:
            raise RuntimeError(
                f"The same-complaint calls about {len(requests)} entities did not finish "
                f"({exc!r}); nothing was written. Run again: a batch already submitted is "
                "picked up, answered calls are cached."
            ) from exc

    def _write(self, entries: Mapping[str, Any], stored: Mapping[str, Any],
               counts: Mapping[str, int]) -> bool:
        """Write the answers when they changed (or the file is missing)."""

        if dict(entries) == dict(stored) and self.answers_path.is_file():
            return False
        stage = LLM_CONFIG["stages"][CONFIG["stage"]]
        metadata = {"generated_at": utc_now(), "git_commit": git_commit(),
                    "model": stage["model"], "effort": stage.get("thinking_param"),
                    "prompt_version": CONFIG["prompt_version"], "config": CONFIG,
                    "counts": dict(counts), "usage": list(self.llm.usage)}
        ordered = {entity_id: entries[entity_id] for entity_id in sorted(entries)}
        write_json_file({"entities": ordered, "metadata": metadata}, self.answers_path)
        return True


def run(*, rebuild: bool = False, only: Collection[str] | None = None, estimate: bool = False,
        sources: ClaimSources = ClaimSources(), llm: LLMClient | None = None
        ) -> dict[str, Any]:
    logger = setup_step_logging(STEP_5)
    logger.info(f"Same-complaint check (commit {git_commit()}; config {CONFIG}; "
                f"rebuild={rebuild}, only={only}, estimate={estimate})")
    return SameComplaintRun(sources, llm, logger).run(
        rebuild=rebuild, only=only, estimate=estimate)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="The same-complaint check: which lone negative claims about a named thing "
                    "a second person's same complaint releases (one model call per entity).")
    parser.add_argument("--estimate", action="store_true",
                        help="count the calls, tokens and price a run would make; no model "
                             "call, nothing written")
    parser.add_argument("--only", nargs="+", metavar="ENTITY_ID",
                        help="ask about these entities only; every other answer stays")
    parser.add_argument("--rebuild", action="store_true",
                        help="ask about every list again (the LLM disk cache answers repeats)")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild, only=args.only, estimate=args.estimate)


if __name__ == "__main__":
    main()
