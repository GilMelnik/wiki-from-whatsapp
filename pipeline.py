"""Orchestrate the full threads-to-wiki pipeline (import-only, no CLI).

Runs steps in order:

    3. classify  -> data/threads_classified.json
    4. extract   -> data/claims.json + data/audit/ (private)
    4b. entities -> data/entities.json
    5. aggregate -> data/claims_aggregated.json (claim groups, contract C6)
    6. plan      -> data/wiki_plan.json (contract C7; planned once, then curated by hand)
    6b. assign   -> data/assignments.json (contract C8; groups on pages + labels, proposals)
    7. write pages -> data/wiki_pages.json (agentic, mini-batch over claims)
    9. site      -> mkdocs.yml

Human gates (web UIs, no CLI):
    step 1 — thread review: ``python -m step_1_threads_split.review``
    step 3 — PII review: ``python -m review_tool --tool pii``
    step 4 — entity review: ``python -m review_tool --tool entities``
    step 5 — aggregate review: ``python -m step_5_aggregate.reviewer``
    step 6 — plan review: rebuilt by task 35a on ``step_6_plan.operations``
"""

from __future__ import annotations

import logging

from step_2_classify.run import run as classify
from step_3_extract.run import run as extract
from step_4_entities.run import run as resolve_entities
from step_5_aggregate.run import run as aggregate
from step_6_plan.assign import run as assign
from step_6_plan.run import run as plan
from step_7_write_pages.run import run as write_pages
from step_9_site.run import run as site
from utils.llm_client import LLMClient
from utils.logging_setup import setup_step_logging
from utils.paths import (
    SHARED,
    STEP_2,
    STEP_3,
    STEP_4,
    STEP_5,
    STEP_6,
    STEP_7,
    STEP_9,
)


def _stage_clients(logger: logging.Logger) -> dict[str, LLMClient]:
    return {
        "extract": LLMClient.for_stage("extract", logger=logger),
        "plan": LLMClient.for_stage("plan", logger=logger),
        "generate": LLMClient.for_stage("generate", logger=logger),
    }


def run(
    *,
    use_batch: bool = False,
) -> None:
    logger = setup_step_logging(SHARED)
    clients = _stage_clients(logger)
    logger.info("LLM configuration:")
    for stage, client in clients.items():
        batch_note = " (batch)" if use_batch and client.supports_batch() else ""
        logger.info(f"  {stage}: {client.provider} / {client.model}{batch_note}")

    logger = setup_step_logging(STEP_2)
    logger.info("[3] Classifying threads...")
    c_meta = classify()
    logger.info(
        f"    {c_meta['knowledge_bearing_count']} knowledge-bearing of "
        f"{c_meta['thread_count']} threads"
    )

    logger = setup_step_logging(STEP_3)
    logger.info("[4] Extracting claims...")
    e_meta = extract(llm=clients["extract"], use_batch=use_batch)
    logger.info(
        f"    {e_meta['claims_count']} claims ({e_meta['new_claims']} new, "
        f"{e_meta['model_calls']} model calls, {len(e_meta['failed_threads'])} failed); "
        f"new redactions: {e_meta['scrub']['total_redactions']}, "
        f"PII review: {e_meta['scrub']['pii_review_claims']}"
    )
    logger.info(
        "    -> Review claims via "
        "`python -m review_tool --tool pii` before aggregate."
    )

    logger = setup_step_logging(STEP_4)
    logger.info("[4b] Resolving entities...")
    en_meta = resolve_entities()
    logger.info(
        f"    {en_meta['entity_count']} entities "
        f"({en_meta['multi_member_count']} multi-member) "
        f"from {en_meta['distinct_entity_count']} distinct strings"
    )
    logger.info(
        "    -> Review entity merges via "
        "`python -m review_tool --tool entities` before aggregate."
    )

    logger = setup_step_logging(STEP_5)
    logger.info("[5] Aggregating claims...")
    a_meta = aggregate()
    logger.info(
        f"    {a_meta['group_count']} groups ({a_meta['multi_member_group_count']} with several "
        f"claims) from {a_meta['claim_count']} claims; {a_meta['new_claims']} new claims"
    )

    logger = setup_step_logging(STEP_6)
    logger.info("[6] Planning wiki structure (no model call once a plan exists)...")
    p_meta = plan()
    logger.info(
        f"    {p_meta['page_count']} pages, {p_meta['label_count']} labels "
        f"-> {p_meta['plan_path']} ({p_meta['model_calls']} model calls)"
    )
    logger.info("    -> Review the plan in the plan review tool before writing pages.")

    logger.info("[6b] Assigning claim groups to pages and labels...")
    as_meta = assign()
    counts = as_meta["summary"]
    logger.info(
        f"    {counts['group_count']} groups: {counts['unplaced']} unplaced, "
        f"{counts['two_pages']} on two pages, {counts['manual']} manual "
        f"({as_meta['classifier_requests']} classifier requests, "
        f"{as_meta['proposal_calls']} proposal calls)"
    )

    logger = setup_step_logging(STEP_7)
    logger.info("[7] Writing wiki pages (agentic, mini-batch)...")
    cm_meta = write_pages(llm=clients["generate"])
    logger.info(
        f"    {cm_meta['statements']} statements across {cm_meta['pages']} pages "
        f"from {cm_meta['claims']} claims ({cm_meta['batches']} batches)"
    )
    logger.info(
        "    -> Review the pages before the site step."
    )

    logger = setup_step_logging(STEP_9)
    logger.info("[9] Writing site config...")
    s_meta = site()
    nav_src = "plan" if s_meta.get("plan_nav") else "taxonomy"
    logger.info(
        f"    {s_meta['config_path']} ({s_meta['page_count']} pages in docs/, nav: {nav_src})"
    )
