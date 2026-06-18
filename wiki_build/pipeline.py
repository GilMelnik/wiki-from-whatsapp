"""Orchestrate the full threads-to-wiki pipeline.

Runs the stages in order:

    A. classify  -> data/threads_classified.json
    B. extract   -> data/claims.json + data/audit/ (private)
    C. aggregate -> data/claims_aggregated.json
    C.5 plan     -> data/wiki_plan.json
    D. generate  -> drafts/*.md (community + optional web background)
    E. manual review (human)
    F. site      -> mkdocs.yml

Each LLM stage can use a different provider/model (hybrid defaults):

    classify -> gemini / gemini-3.1-flash-lite  (cheap, high volume)
    extract  -> gemini / gemini-3.5-flash
    plan     -> gemini / gemini-3.1-flash-lite
    generate -> anthropic / claude-sonnet-4-6  (community synthesis)
    research -> gemini / gemini-2.5-flash  (Google Search grounding)

Override per stage::

    WIKI_LLM_CLASSIFY_PROVIDER / WIKI_LLM_CLASSIFY_MODEL
    WIKI_LLM_EXTRACT_PROVIDER  / WIKI_LLM_EXTRACT_MODEL
    WIKI_LLM_PLAN_PROVIDER     / WIKI_LLM_PLAN_MODEL
    WIKI_LLM_GENERATE_PROVIDER / WIKI_LLM_GENERATE_MODEL
    WIKI_LLM_RESEARCH_PROVIDER / WIKI_LLM_RESEARCH_MODEL

Or set a single model for all stages::

    WIKI_LLM_PROVIDER / WIKI_LLM_MODEL

Web search grounding (Gemini) is enabled when a Gemini/Google API key is set.
Disable with ``--no-search`` or ``WIKI_ENABLE_WEB_SEARCH=0``.

Usage:
    python -m wiki_build.pipeline                 # full run, hybrid LLMs
    python -m wiki_build.pipeline --mock          # offline mock for all stages
    python -m wiki_build.pipeline --batch         # batch API (50% cheaper, async)
    python -m wiki_build.pipeline --no-plan       # skip planning agent
    python -m wiki_build.pipeline --no-search     # skip web background
    python -m wiki_build.pipeline --pilot tamuz   # one-topic pilot through generate
"""

from __future__ import annotations

import os
import sys

from wiki_build import aggregate, classify, extract, generate, plan, site
from wiki_build.llm_client import LLMClient


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

    print("\n[A] Classifying threads...")
    c_meta = classify.run(llm=clients["classify"], use_batch=use_batch)
    print(
        f"    {c_meta['knowledge_bearing_count']} knowledge-bearing of "
        f"{c_meta['thread_count']} threads"
    )

    print("\n[B] Extracting claims...")
    e_meta = extract.run(
        llm=clients["extract"], topic_filter=pilot_topic, use_batch=use_batch
    )
    print(
        f"    {e_meta['claims_count']} claims; "
        f"redactions: {e_meta['scrub']['total_redactions']}, "
        f"PII review: {e_meta['scrub']['pii_review_claims']}"
    )
    print("    → Run `python -m pii_reviewer` to review scrubbed claims before aggregate.")

    print("\n[C] Aggregating claims...")
    a_meta = aggregate.run(use_embeddings=use_embeddings)
    print(f"    {a_meta['topic_count']} topics (merge: {a_meta['merge_method']})")

    if skip_plan:
        print("\n[C.5] Planning wiki structure... (identity mapping, --no-plan)")
        p_meta = plan.run(llm=clients["plan"], skip_agent=True)
    else:
        print("\n[C.5] Planning wiki structure...")
        p_meta = plan.run(llm=clients["plan"], use_batch=use_batch)
    print(
        f"    {p_meta['page_count']} pages, {p_meta['link_count']} links "
        f"-> {p_meta['output_path']} ({p_meta['mode']})"
    )

    print("\n[D] Generating page drafts...")
    g_meta = generate.run(
        llm=clients["generate"],
        research_llm=clients["research"],
        use_batch=use_batch,
        skip_plan=skip_plan,
        enable_web_search=enable_web_search,
    )
    search_note = "with web search" if g_meta.get("web_search") else "no web search"
    print(f"    {g_meta['pages_written']} drafts in {g_meta['drafts_dir']}/ ({search_note})")

    print("\n[E] Manual review: edit drafts/ and copy approved pages into docs/.")

    print("\n[F] Writing site config...")
    s_meta = site.run()
    nav_src = "plan" if s_meta.get("plan_nav") else "taxonomy"
    print(
        f"    {s_meta['config_path']} ({s_meta['page_count']} pages in docs/, nav: {nav_src})"
    )


if __name__ == "__main__":
    pilot = None
    use_mock = "--mock" in sys.argv
    use_batch = "--batch" in sys.argv
    skip_plan = "--no-plan" in sys.argv
    no_search = "--no-search" in sys.argv
    args = sys.argv[1:]
    if "--pilot" in args:
        pilot = args[args.index("--pilot") + 1]
    run(
        pilot_topic=pilot,
        use_mock=use_mock,
        use_batch=use_batch,
        skip_plan=skip_plan,
        enable_web_search=False if no_search else None,
    )
