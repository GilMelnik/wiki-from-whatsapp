"""Step 7: build community-insight pages with an agentic, mini-batch loop.

Instead of consuming the aggregate step, this reads the published claims, the
entity registry, and the PRIVATE supporter audit directly. Each claim is routed
to *every* page its ``topic_tags`` map to (so a multi-topic claim can influence
all relevant pages), and those per-page claims are fed to the LLM one mini-batch
at a time. A claim may back several statements, counted fully in each; ``PageStore``
recomputes a statement's support by unioning supporter identities across its
deduped claim_ids, so a claim is never counted twice within a single statement.
For each batch the agent sees the current page content plus the new
claims (with canonical entities, supporter counts, stance, dates) and emits JSON
actions that write/update statements or evolve the page catalog.

The agent's tools (a provider-agnostic JSON-action protocol, no SDK function
calling):

- ``upsert_statement`` — write or update a statement; attaching more claim_ids to
  an existing statement is how repeated support is accrued without duplicates.
- ``new_page`` / ``split_page`` — evolve the page catalog.
- ``add_link`` / ``set_related`` — linkage between pages.
- ``read_page`` — request another page's content; the loop re-prompts the same
  batch with that content (bounded).

Every statement must cite ``claim_ids``; supporter counts are recomputed in code
(``PageStore``) by unioning supporter identities from the audit. The LLM never
sees identities. Output: ``data/wiki_pages.json``.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from step_5_aggregate.resolver import EntityResolver, load_entity_resolver
from step_7_community.prompts import (
    COMMUNITY_ACTIONS_SCHEMA,
    COMMUNITY_AGENT_SYSTEM,
    build_batch_prompt,
)
from step_7_community.store import PageStore
from utils.json_io import write_json_file
from utils.llm_client import LLMClient
from utils.logging_setup import setup_step_logging
from utils.paths import (
    STEP_7,
    WIKI_PAGES,
    resolve_claims_path,
    resolve_plan_path,
)
from utils.support import load_audit_records
from utils.taxonomy import all_pages, get_page

DEFAULT_BATCH_SIZE = 15
DEFAULT_MAX_READS = 3
OVERVIEW_PAGE_ID = "overview"

def _apply_actions(
    store: PageStore,
    current_page_id: str,
    actions: list[dict[str, Any]],
) -> None:
    for action in actions:
        if not isinstance(action, dict):
            continue
        kind = action.get("type")
        if kind == "upsert_statement":
            store.upsert_statement(
                action.get("page_id") or current_page_id,
                action.get("section"),
                action.get("statement_id"),
                action.get("text"),
                action.get("claim_ids"),
            )
        elif kind == "new_page":
            store.ensure_page(
                action.get("id"),
                title=action.get("title"),
                category=action.get("category") or "emergent",
                parent=action.get("parent"),
            )
        elif kind == "split_page":
            origin = str(action.get("from") or "").strip()
            for target in action.get("into") or []:
                if not isinstance(target, dict):
                    continue
                new_page = store.ensure_page(
                    target.get("id"),
                    title=target.get("title"),
                    category=target.get("category") or "emergent",
                )
                if new_page and origin in store.pages:
                    store.add_link(origin, new_page["id"], action.get("reason", ""))
        elif kind == "add_link":
            store.add_link(
                action.get("from"), action.get("to"), action.get("reason", "")
            )
        elif kind == "set_related":
            store.set_related(action.get("page_id"), action.get("related_pages"))


def _run_batch(
    llm: LLMClient,
    store: PageStore,
    page_id: str,
    claims: list[dict[str, Any]],
    resolver: EntityResolver | None,
    max_reads: int,
    logger: logging.Logger,
) -> None:
    extra_pages = ""
    for _ in range(max_reads + 1):
        prompt = build_batch_prompt(store, page_id, claims, resolver, extra_pages)
        try:
            data = llm.complete_json(
                COMMUNITY_AGENT_SYSTEM,
                prompt,
                task="community_agent",
                response_schema=COMMUNITY_ACTIONS_SCHEMA,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Community: batch for {page_id} failed: {exc}")
            return
        actions = data.get("actions") if isinstance(data, dict) else None
        actions = [a for a in (actions or []) if isinstance(a, dict)]
        reads = [
            str(a.get("id") or "").strip()
            for a in actions
            if a.get("type") == "read_page"
        ]
        reads = [r for r in reads if r and r in store.pages]
        if reads:
            logger.info(f"{page_id}: agent requested read_page {reads}; re-prompting")
            # Don't write yet — show requested pages and let the agent re-decide
            # with that context. Avoids double-applying writes across re-prompts.
            extra_pages = "\n\n".join(
                json.dumps(store.page_view(rid), ensure_ascii=False, indent=2)
                for rid in reads
            )
            continue
        _apply_actions(store, page_id, actions)
        return


def _load_seed(
    plan_path: Path | str | None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Seed page catalog from the (edited) plan, falling back to the taxonomy."""

    path = Path(plan_path) if plan_path is not None else resolve_plan_path()
    pages: list[dict[str, Any]] = []
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for page in json.load(f).get("pages") or []:
                if isinstance(page, dict) and page.get("id"):
                    pages.append(
                        {
                            "id": page["id"],
                            "title": page.get("title", page["id"]),
                            "category": page.get("category", "emergent"),
                            "source_tags": page.get("source_tags") or [page["id"]],
                            "parent": page.get("parent"),
                        }
                    )
    if not pages:
        pages = [
            {
                "id": tp.id,
                "title": tp.title_he,
                "category": tp.category,
                "source_tags": [tp.id],
                "parent": tp.parent,
            }
            for tp in all_pages()
        ]

    tag_to_page: dict[str, str] = {}
    for page in pages:
        for tag in page["source_tags"] or [page["id"]]:
            tag_to_page.setdefault(tag, page["id"])
        tag_to_page.setdefault(page["id"], page["id"])
    return pages, tag_to_page


def _related_pages(claim: dict[str, Any], tag_to_page: dict[str, str]) -> list[str]:
    """Every page this claim's topic_tags map to (mapped page or emergent tag)."""

    pages: list[str] = []
    for tag in claim.get("topic_tags") or []:
        page_id = tag_to_page.get(tag, tag)
        if page_id and page_id not in pages:
            pages.append(page_id)
    return pages or [OVERVIEW_PAGE_ID]


def run(
    *,
    llm: LLMClient | None = None,
    claims_path: Path | str | None = None,
    plan_path: Path | str | None = None,
    output_path: Path | str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_reads: int = DEFAULT_MAX_READS,
) -> dict[str, Any]:
    logger = setup_step_logging(STEP_7)
    llm = LLMClient.for_stage("generate", logger=logger) if llm is None else llm
    llm.set_logger(logger)

    claims_file = Path(claims_path) if claims_path is not None else resolve_claims_path()
    with claims_file.open(encoding="utf-8") as f:
        claims = json.load(f)["claims"]
    claims_by_id = {c["claim_id"]: c for c in claims}

    audit_by_id = load_audit_records()
    resolver = load_entity_resolver()
    seed_pages, tag_to_page = _load_seed(plan_path)

    store = PageStore(claims_by_id, audit_by_id)
    for page in seed_pages:
        store.ensure_page(
            page["id"],
            title=page["title"],
            category=page["category"],
            parent=page.get("parent"),
            source_tags=page.get("source_tags"),
        )
    store.ensure_page(OVERVIEW_PAGE_ID, title="סקירה כללית", category="start")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    for claim in claims:
        for page_id in _related_pages(claim, tag_to_page):
            seed = get_page(page_id)
            store.ensure_page(
                page_id,
                title=seed.title_he if seed else None,
                category=seed.category if seed else "emergent",
            )
            if claim["claim_id"] not in seen[page_id]:
                seen[page_id].add(claim["claim_id"])
                grouped[page_id].append(claim)

    out_path = Path(output_path) if output_path is not None else WIKI_PAGES.original

    total_batches = sum(
        -(-len(page_claims) // batch_size) for page_claims in grouped.values()
    )
    logger.info(
        f"Community: {len(claims)} claims across {len(grouped)} pages "
        f"-> {total_batches} batches ({llm.provider}/{llm.model})"
    )

    batch_count = 0
    for page_id, page_claims in grouped.items():
        page_batches = -(-len(page_claims) // batch_size)
        logger.info(f"{page_id}: {len(page_claims)} claims in {page_batches} batch(es)")
        for start in range(0, len(page_claims), batch_size):
            batch = page_claims[start : start + batch_size]
            batch_count += 1
            before = store.statement_count()
            _run_batch(llm, store, page_id, batch, resolver, max_reads, logger)
            after = store.statement_count()
            logger.info(
                f"[{batch_count}/{total_batches}] {page_id}: "
                f"{len(batch)} claims -> +{after - before} statements ({after} total)"
            )
            # Persist after each batch so progress survives interruptions.
            # ponytail: re-runs reprocess all batches (LLM cache makes this cheap);
            # no skip-completed-batch resume logic.
            write_json_file(
                store.to_payload(
                    metadata={
                        "source_claims": str(claims_file),
                        "provider": llm.provider,
                        "model": llm.model,
                        "batches": batch_count,
                    }
                ),
                out_path,
            )

    logger.info(
        f"Community: done - {len(store.pages)} pages, {store.statement_count()} "
        f"statements, {len(store.links)} links, {batch_count} batches"
    )

    return {
        "pages": len(store.pages),
        "statements": store.statement_count(),
        "links": len(store.links),
        "batches": batch_count,
        "claims": len(claims),
        "output_path": str(out_path),
        "provider": llm.provider,
        "model": llm.model,
    }


if __name__ == "__main__":
    run()
