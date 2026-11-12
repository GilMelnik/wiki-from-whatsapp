"""Orchestrate the full threads-to-wiki pipeline (import-only, no CLI).

The steps, their order, which of them are paid and where a person reviews come from the one
step registry, ``review_tool.steps.STEP_SPECS`` (the review tool and the update driver
``step_10_update`` read the same rows):

    1. threads     -> conversations (contract C1; only new export messages are placed)
    2. classify    -> which conversations carry knowledge (C2)
    3. extract     -> claims (C3) + the private audit             review: pii
    4. entities    -> canonical entities                         review: entities
    5. aggregate   -> claim groups (C6)                          review: groups
    6. plan        -> the wiki plan (C7; planned once, then curated)   review: plan
    6b. assign     -> groups on pages + labels, proposals (C8)   review: plan
    7. write pages -> wiki pages (C9; one call per new or changed page)
    8. review      -> suggestions (C12; one batched call per part of the wiki that changed, plus
                      one across the pages' points)
                                                                  review: suggestions
    9. site        -> docs/ (pages, label pages, wiki_index.json) + mkdocs.yml

A review is ``python -m review_tool --tool <name>``. This function runs every step once, in
order, without stopping; a later chat export goes through ``python -m step_10_update.run``,
which stops at the reviews and before paid steps.
"""

from __future__ import annotations

import logging

from review_tool.steps import SERVED_STEPS
from step_10_update.record import scalar_summary
from utils.llm_client import LLMClient
from utils.logging_setup import setup_step_logging
from utils.paths import SHARED, STEP_3

LLM_STAGES = ("extract", "plan", "write", "update", "review")


def _log_llm_configuration(logger: logging.Logger, use_batch: bool) -> None:
    logger.info("LLM configuration:")
    for stage in LLM_STAGES:
        client = LLMClient.for_stage(stage, logger=logger)
        batch_note = " (batch)" if use_batch and client.supports_batch() else ""
        logger.info(f"  {stage}: {client.provider} / {client.model}{batch_note}")


def run(*, use_batch: bool = False) -> None:
    logger = setup_step_logging(SHARED)
    _log_llm_configuration(logger, use_batch)
    for step in SERVED_STEPS.steps:
        logger.info(f"[{step.spec.title}] {step.spec.module}")
        options = {"use_batch": use_batch} if step.key == STEP_3 else {}
        result = step.run_function()(**options)
        logger = setup_step_logging(SHARED)
        logger.info(f"    {scalar_summary(result)}")
        if step.spec.review_tool:
            logger.info(f"    -> Review: python -m review_tool --tool {step.spec.review_tool}")
