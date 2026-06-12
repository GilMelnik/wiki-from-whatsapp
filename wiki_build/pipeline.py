"""Orchestrate the full threads-to-wiki pipeline.

Runs the stages in order:

    A. classify  -> data/threads_classified.json
    B. extract   -> data/claims.json + data/audit/ (private)
    C. aggregate -> data/claims_aggregated.json
    D. generate  -> drafts/*.md
    F. site      -> mkdocs.yml (Stage E, human review, sits between D and F)

Each LLM stage can use a different provider/model (hybrid defaults):

    classify -> gemini / gemini-2.0-flash  (cheap, high volume)
    extract  -> anthropic / claude-sonnet-4-20250514  (anonymization + Hebrew)
    generate -> anthropic / claude-sonnet-4-20250514  (wiki prose)

Override per stage::

    WIKI_LLM_CLASSIFY_PROVIDER / WIKI_LLM_CLASSIFY_MODEL
    WIKI_LLM_EXTRACT_PROVIDER  / WIKI_LLM_EXTRACT_MODEL
    WIKI_LLM_GENERATE_PROVIDER / WIKI_LLM_GENERATE_MODEL

Or set a single model for all stages::

    WIKI_LLM_PROVIDER / WIKI_LLM_MODEL

Without any env vars the pipeline uses hybrid defaults (requires API keys).
Standalone stage scripts (``python -m wiki_build.classify``) still default to
offline ``mock`` unless you set env vars.

Usage:
    python -m wiki_build.pipeline                 # full run, hybrid LLMs
    python -m wiki_build.pipeline --mock          # offline mock for all stages
    python -m wiki_build.pipeline --pilot tamuz   # one-topic pilot through generate
"""

from __future__ import annotations

import os
import sys

from wiki_build import aggregate, classify, extract, generate, site
from wiki_build.llm_client import LLMClient


def _stage_clients(*, use_hybrid_defaults: bool) -> dict[str, LLMClient]:
    return {
        "classify": LLMClient.for_stage("classify", use_hybrid_defaults=use_hybrid_defaults),
        "extract": LLMClient.for_stage("extract", use_hybrid_defaults=use_hybrid_defaults),
        "generate": LLMClient.for_stage("generate", use_hybrid_defaults=use_hybrid_defaults),
    }


def run(
    pilot_topic: str | None = None,
    use_embeddings: bool = True,
    *,
    use_mock: bool = False,
) -> None:
    use_hybrid = not use_mock and not os.environ.get("WIKI_LLM_PROVIDER")
    clients = _stage_clients(use_hybrid_defaults=use_hybrid)

    if use_mock:
        clients = {stage: LLMClient(provider="mock", model="mock") for stage in clients}

    print("LLM configuration:")
    for stage, client in clients.items():
        print(f"  {stage}: {client.provider} / {client.model}")

    print("\n[A] Classifying threads...")
    c_meta = classify.run(llm=clients["classify"])
    print(
        f"    {c_meta['knowledge_bearing_count']} knowledge-bearing of "
        f"{c_meta['thread_count']} threads"
    )

    print("\n[B] Extracting claims...")
    e_meta = extract.run(llm=clients["extract"], topic_filter=pilot_topic)
    print(
        f"    {e_meta['claims_count']} claims; "
        f"redactions: {e_meta['scrub']['total_redactions']}, "
        f"flagged: {e_meta['scrub']['flagged_claims']}"
    )

    print("\n[C] Aggregating claims...")
    a_meta = aggregate.run(use_embeddings=use_embeddings)
    print(f"    {a_meta['topic_count']} topics (merge: {a_meta['merge_method']})")

    print("\n[D] Generating page drafts...")
    g_meta = generate.run(llm=clients["generate"])
    print(f"    {g_meta['pages_written']} drafts in {g_meta['drafts_dir']}/")

    print("\n[E] Manual review: edit drafts/ and copy approved pages into docs/.")

    print("\n[F] Writing site config...")
    s_meta = site.run()
    print(f"    {s_meta['config_path']} ({s_meta['page_count']} pages in docs/)")


if __name__ == "__main__":
    pilot = None
    use_mock = "--mock" in sys.argv
    args = sys.argv[1:]
    if "--pilot" in args:
        pilot = args[args.index("--pilot") + 1]
    run(pilot_topic=pilot, use_mock=use_mock)
