"""Step 8's batch route: every page's call of a run in one batch per client and round.

The requests are the sync route's (``PageWriter.write_call`` / ``update_call``: same prompts,
schema, output limit and stage); only the way they are sent differs. In plain words:

- Round 1: every due page's first request goes out, one batch for the ``write`` stage and one
  for the ``update`` stage (two clients). An answer the disk cache holds is not sent again
  (the cache key is the sync call's, so either route reuses the other's answers).
- Each answer is checked as the sync route checks it (``PageWriter.checked``,
  ``retry_problems``). An answer with problems gets ONE retry naming them: those retries go
  out together as round 2, again one batch per stage. An answer of either round that is
  unusable (no body, no points / left_out lists, no parseable answer, a refusal) fails its
  page, as a sync call that raises does. Then ``PageWriter.finish`` repairs and records what
  is still wrong.
- A truncated answer is the client's business, as on the sync route: it is sent again with
  a larger window (``LLMClient.complete_batch_json``) up to the same ceiling.
- A failed page never loses another page's text: ``BatchRounds.run`` returns the pages
  written and, apart, the pages that failed with the reason.

Every batch is resumable (``LLMClient`` keeps its id under ``llm.batch_state_dir``). A batch
that cannot be sent, polled or fetched (or ended with requests that never ran) is not one
page's failure: the run stops with ``RuntimeError`` and writes nothing, so a ``--rebuild``
never replaces the pages with what one dropped connection left. Nothing is lost: the next run
with the same requests picks the batch up instead of paying for it again (or sends only the
requests that never ran), and every answer already fetched comes from the disk cache.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from step_4_entities.match_judge import CALL_ERRORS
from step_8_write_pages.prompts import retry_prompt
from step_8_write_pages.writer import PageCall, PageText, PageWriter
from utils.llm_client import BatchRequest, CacheSegment
from utils.llm_client.ask import ask_json

NO_ANSWER = "no usable answer from the batch (unparseable, refused or cut off; see the LLM log)"
Prompts = Mapping[str, tuple[PageCall, list[CacheSegment]]]


class BatchRounds:
    """The batch rounds of one run (see the module docstring)."""

    def __init__(self, writer: PageWriter, logger: logging.Logger) -> None:
        self.writer = writer
        self.logger = logger
        self.failed: dict[str, str] = {}

    def run(self, calls: Mapping[str, PageCall]) -> tuple[dict[str, PageText], dict[str, str]]:
        """``({page_id: PageText}, {page_id: why it failed})`` for every page of ``calls``."""
        texts: dict[str, PageText] = {}
        firsts = self._round(1, {page_id: (call, call.user) for page_id, call in calls.items()})
        retries: dict[str, tuple[PageCall, list[CacheSegment]]] = {}
        problems: dict[str, list[str]] = {}
        for page_id, answer in firsts.items():
            call = calls[page_id]
            problems[page_id] = self.writer.retry_problems(call, answer)
            if problems[page_id]:
                retries[page_id] = (call, retry_prompt(call.user, answer, problems[page_id]))
            else:
                self._finish(texts, page_id, call, answer, [])
        for page_id, answer in self._round(2, retries).items():
            self._finish(texts, page_id, calls[page_id], answer, problems[page_id])
        self.logger.info(f"Pages via the batch API: {len(calls)} pages, {len(retries)} retried "
                         f"in round 2, {len(texts)} written, {len(self.failed)} failed")
        return texts, self.failed

    def _finish(self, texts: dict[str, PageText], page_id: str, call: PageCall,
                answer: Mapping[str, Any], first: list[str]) -> None:
        try:
            texts[page_id] = self.writer.finish(call, answer, first)
        except CALL_ERRORS as exc:
            self.failed[page_id] = repr(exc)

    def _round(self, number: int, prompts: Prompts) -> dict[str, dict[str, Any]]:
        """The checked answers of one round, by page; the pages without one go to ``failed``."""
        by_client: dict[int, list[str]] = {}
        for page_id, (call, _) in prompts.items():
            by_client.setdefault(id(call.llm), []).append(page_id)
        answers: dict[str, dict[str, Any]] = {}
        kind = "first answers" if number == 1 else "retries"
        for page_ids in by_client.values():
            llm = prompts[page_ids[0]][0].llm
            requests = []
            for page_id in page_ids:
                call, user = prompts[page_id]
                requests.append(BatchRequest(page_id, call.system, user, task=call.task,
                                             response_schema=call.schema))
            cached = sum(1 for request in requests if llm.cached_json(request) is not None)
            self.logger.info(f"Pages, round {number} ({kind}), {llm.model}: {len(requests)} "
                             f"requests, {cached} from the cache, {len(requests) - cached} "
                             "through the batch API")
            try:
                raw = ask_json(llm, requests, batch=True, logger=self.logger, resumable=True)
            except CALL_ERRORS as exc:
                raise RuntimeError(
                    f"Pages, round {number}: the batch of {len(requests)} requests ({llm.model}) "
                    f"did not finish ({exc!r}); nothing was written. Run again: a batch already "
                    "submitted is picked up, answered requests are cached.") from exc
            for page_id in page_ids:
                if page_id not in raw:
                    self.failed[page_id] = NO_ANSWER
                    continue
                try:
                    answers[page_id] = self.writer.checked(prompts[page_id][0], raw[page_id])
                except ValueError as exc:
                    self.failed[page_id] = repr(exc)
        return answers
