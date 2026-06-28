"""Orchestrate the full threads-to-wiki pipeline (import-only, no CLI).

Runs steps in order:

    3. classify  -> data/threads_classified.json
    4. extract   -> data/claims.json + data/audit/ (private)
    4b. entities -> data/entities.json
    5. aggregate -> data/claims_aggregated.json
    6. plan      -> data/wiki_plan.json
    7. generate  -> drafts/*.md
    8. site      -> mkdocs.yml

Human gates (web UIs, no CLI):
    step 1 — thread review: ``python -m step_1_threads_split.review``
    step 3 — PII review: ``uvicorn step_3_extract.reviewer.server:app``
    step 4 — entity review: ``python -m step_4_entities.reviewer``
    step 5 — aggregate review: ``python -m step_5_aggregate.reviewer``
    step 6 — plan review: ``uvicorn step_6_plan.reviewer.server:app``
"""

from __future__ import annotations

import os

from step_2_classify.run import run as classify
from step_3_extract.run import run as extract
from step_4_entities.run import run as resolve_entities
from step_5_aggregate.run import run as aggregate
from step_6_plan.run import run as plan
from step_7_generate.run import run as generate
from step_8_site.run import run as site
from utils.llm_client import LLMClient


def _stage_clients(*, use_hybrid_defaults: bool) -> dict[str, LLMClient]:
    return {
        "classify": LLMClient.for_stage("classify", use_hybrid_defaults=use_hybrid_defaults),
        "extract": LLMClient.for_stage("extract", use_hybrid_defaults=use_hybrid_defaults),
        "plan": LLMClient.for_stage("plan", use_hybrid_defaults=use_hybrid_defaults),
        "generate": LLMClient.for_stage("generate", use_hybrid_defaults=use_hybrid_defaults),
        "research": LLMClient.for_stage("research", use_hybrid_defaults=use_hybrid_defaults),
    }


def run(
    pilot_topic: str | None = None,
    use_embeddings: bool = True,
    *,
    use_mock: bool = False,
    use_batch: bool = False,
    skip_plan: bool = False,
    enable_web_search: bool | None = None,
) -> None:
    use_hybrid = not use_mock and not os.environ.get("WIKI_LLM_PROVIDER")
    clients = _stage_clients(use_hybrid_defaults=use_hybrid)

    if use_mock:
        clients = {stage: LLMClient(provider="mock", model="mock") for stage in clients}

    print("LLM configuration:")
    for stage, client in clients.items():
        batch_note = " (batch)" if use_batch and client.supports_batch() else ""
        print(f"  {stage}: {client.provider} / {client.model}{batch_note}")

    print("\n[3] Classifying threads...")
    c_meta = classify(llm=clients["classify"], use_batch=use_batch)
    print(
        f"    {c_meta['knowledge_bearing_count']} knowledge-bearing of "
        f"{c_meta['thread_count']} threads"
    )

    print("\n[4] Extracting claims...")
    e_meta = extract(
        llm=clients["extract"], topic_filter=pilot_topic, use_batch=use_batch
    )
    print(
        f"    {e_meta['claims_count']} claims; "
        f"redactions: {e_meta['scrub']['total_redactions']}, "
        f"PII review: {e_meta['scrub']['pii_review_claims']}"
    )
    print(
        "    → Review claims via "
        "`uvicorn step_3_extract.reviewer.server:app` before aggregate."
    )

    print("\n[4b] Resolving entities...")
    en_meta = resolve_entities()
    print(
        f"    {en_meta['entity_count']} entities "
        f"({en_meta['multi_member_count']} multi-member) "
        f"from {en_meta['distinct_entity_count']} distinct strings"
    )
    print(
        "    → Review entity merges via "
        "`python -m step_4_entities.reviewer` before aggregate."
    )

    print("\n[5] Aggregating claims...")
    a_meta = aggregate(use_embeddings=use_embeddings)
    print(f"    {a_meta['topic_count']} topics (merge: {a_meta['merge_method']})")

    if skip_plan:
        print("\n[6] Planning wiki structure... (identity mapping, skip_plan=True)")
        p_meta = plan(llm=clients["plan"], skip_agent=True)
    else:
        print("\n[6] Planning wiki structure...")
        p_meta = plan(llm=clients["plan"], use_batch=use_batch)
    print(
        f"    {p_meta['page_count']} pages, {p_meta['link_count']} links "
        f"-> {p_meta['output_path']} ({p_meta['mode']})"
    )
    print(
        "    → Edit plan via "
        "`uvicorn step_6_plan.reviewer.server:app` before generate."
    )

    print("\n[7] Generating page drafts...")
    g_meta = generate(
        llm=clients["generate"],
        research_llm=clients["research"],
        use_batch=use_batch,
        skip_plan=skip_plan,
        enable_web_search=enable_web_search,
    )
    search_note = "with web search" if g_meta.get("web_search") else "no web search"
    print(f"    {g_meta['pages_written']} drafts in {g_meta['drafts_dir']}/ ({search_note})")

    print("\n    Manual review: edit drafts/ and copy approved pages into docs/.")

    print("\n[8] Writing site config...")
    s_meta = site()
    nav_src = "plan" if s_meta.get("plan_nav") else "taxonomy"
    print(
        f"    {s_meta['config_path']} ({s_meta['page_count']} pages in docs/, nav: {nav_src})"
    )
