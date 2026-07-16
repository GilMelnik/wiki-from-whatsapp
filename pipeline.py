"""Orchestrate the full threads-to-wiki pipeline (import-only, no CLI).

Runs steps in order:

    3. classify  -> data/threads_classified.json
    4. extract   -> data/claims.json + data/audit/ (private)
    4b. entities -> data/entities.json
    5. aggregate -> data/claims_aggregated.json
    6. plan      -> data/wiki_plan.json (seed page catalog)
    7. community -> data/wiki_pages.json (agentic, mini-batch over claims)
    8. background-> drafts/*.md (public overview + assembled drafts)
    9. site      -> mkdocs.yml

Human gates (web UIs, no CLI):
    step 1 — thread review: ``python -m step_1_threads_split.review``
    step 3 — PII review: ``python -m step_3_extract.reviewer``
    step 4 — entity review: ``python -m step_4_entities.reviewer``
    step 5 — aggregate review: ``python -m step_5_aggregate.reviewer``
    step 6 — plan review: ``python -m step_6_plan.reviewer``
"""

from __future__ import annotations

import logging

from step_2_classify.run import run as classify
from step_3_extract.run import run as extract
from step_4_entities.run import run as resolve_entities
from step_5_aggregate.run import run as aggregate
from step_6_plan.run import run as plan
from step_7_community.run import run as community
from step_8_background.run import run as background
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
    STEP_8,
    STEP_9,
)


def _stage_clients(logger: logging.Logger) -> dict[str, LLMClient]:
    return {
        "classify": LLMClient.for_stage("classify", logger=logger),
        "extract": LLMClient.for_stage("extract", logger=logger),
        "plan": LLMClient.for_stage("plan", logger=logger),
        "generate": LLMClient.for_stage("generate", logger=logger),
        "research": LLMClient.for_stage("research", logger=logger),
    }


def run(
    use_embeddings: bool = True,
    *,
    use_batch: bool = False,
    skip_plan: bool = False,
    enable_web_search: bool | None = None,
) -> None:
    logger = setup_step_logging(SHARED)
    clients = _stage_clients(logger)
    logger.info("LLM configuration:")
    for stage, client in clients.items():
        batch_note = " (batch)" if use_batch and client.supports_batch() else ""
        logger.info(f"  {stage}: {client.provider} / {client.model}{batch_note}")

    logger = setup_step_logging(STEP_2)
    logger.info("[3] Classifying threads...")
    c_meta = classify(llm=clients["classify"], use_batch=use_batch)
    logger.info(
        f"    {c_meta['knowledge_bearing_count']} knowledge-bearing of "
        f"{c_meta['thread_count']} threads"
    )

    logger = setup_step_logging(STEP_3)
    logger.info("[4] Extracting claims...")
    e_meta = extract(llm=clients["extract"], use_batch=use_batch)
    logger.info(
        f"    {e_meta['claims_count']} claims; "
        f"redactions: {e_meta['scrub']['total_redactions']}, "
        f"PII review: {e_meta['scrub']['pii_review_claims']}"
    )
    logger.info(
        "    -> Review claims via "
        "`python -m step_3_extract.reviewer` before aggregate."
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
        "`python -m step_4_entities.reviewer` before aggregate."
    )

    logger = setup_step_logging(STEP_5)
    logger.info("[5] Aggregating claims...")
    a_meta = aggregate(use_embeddings=use_embeddings)
    logger.info(f"    {a_meta['topic_count']} topics (merge: {a_meta['merge_method']})")

    logger = setup_step_logging(STEP_6)
    if skip_plan:
        logger.info("[6] Planning wiki structure... (identity mapping, skip_plan=True)")
        p_meta = plan(llm=clients["plan"], skip_agent=True)
    else:
        logger.info("[6] Planning wiki structure...")
        p_meta = plan(llm=clients["plan"], use_batch=use_batch)
    logger.info(
        f"    {p_meta['page_count']} pages, {p_meta['link_count']} links "
        f"-> {p_meta['output_path']} ({p_meta['mode']})"
    )
    logger.info(
        "    -> Edit plan via "
        "`python -m step_6_plan.reviewer` before generate."
    )

    logger = setup_step_logging(STEP_7)
    logger.info("[7] Building community pages (agentic, mini-batch)...")
    cm_meta = community(llm=clients["generate"])
    logger.info(
        f"    {cm_meta['statements']} statements across {cm_meta['pages']} pages "
        f"from {cm_meta['claims']} claims ({cm_meta['batches']} batches)"
    )
    logger.info(
        "    -> Review pages in the community page store before background/assembly."
    )

    logger = setup_step_logging(STEP_8)
    logger.info("[8] Researching background + assembling drafts...")
    bg_meta = background(
        research_llm=clients["research"],
        enable_web_search=enable_web_search,
    )
    search_note = "with web search" if bg_meta.get("web_search") else "no web search"
    logger.info(
        f"    {bg_meta['pages_written']} drafts in {bg_meta['drafts_dir']}/ ({search_note})"
    )
    logger.info("    Manual review: edit drafts/ and copy approved pages into docs/.")

    logger = setup_step_logging(STEP_9)
    logger.info("[9] Writing site config...")
    s_meta = site()
    nav_src = "plan" if s_meta.get("plan_nav") else "taxonomy"
    logger.info(
        f"    {s_meta['config_path']} ({s_meta['page_count']} pages in docs/, nav: {nav_src})"
    )
