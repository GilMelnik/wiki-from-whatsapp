"""Step 4: per-thread knowledge-claim extraction (the core).

For every knowledge-bearing thread the LLM produces a list of anonymized,
neutral Hebrew claims, each tagged with topics, a stance, the supporting
message indices and the entities mentioned. We then:

- map the supporting local message indices back to senders and months,
- count distinct supporters (message authors plus reaction senders, each user
  once even if they both stated and reacted),
- attach per-message reaction details to the private audit trace,
- compute a distinct-supporter count and the claim date (earliest support),
- run a light PII scrub (phones and emails only) over the claim text,
- write the publishable ``data/claims.json`` (no sender ids, no raw text) and
  a PRIVATE ``data/audit/`` source map that links each claim back to messages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.json_io import write_json_file
from utils.llm_client import BatchRequest, LLMClient, extract_json
from utils.paths import resolve_classified_path
from step_3_extract.scrub import FORBIDDEN_TERM_INSTRUCTION, scrub_claims
from utils.support import compute_support
from utils.taxonomy import page_ids, taxonomy_seed_block
from utils.threads_io import (
    DEFAULT_THREADS_PATH,
    load_threads,
    render_thread_for_llm,
)

DEFAULT_CLASSIFIED_PATH = resolve_classified_path()
DEFAULT_OUTPUT_PATH = Path("data/claims.json")
DEFAULT_AUDIT_DIR = Path("data/audit")

EXTRACT_SYSTEM = (
    "אתה עוזר שמחלץ ידע מקבוצת וואטסאפ על פונדקאות לגייז, לטובת בניית ויקי בעברית. "
    "מכל שיחה חלץ טענות/תובנות ספציפיות ומועילות: המלצות, אזהרות, עובדות, עלויות, "
    "ניסיון אישי, מידע משפטי/רפואי/כספי. "
    "כללי אנונימיזציה: אל תכלול שמות פרטיים של חברי קבוצה שאינם ספקים מקצועיים, "
    "מספרי טלפון, אימיילים או פרטים מזהים אחרים. "
    "שמות עסקים, סוכנויות, עורכי דין, סוכני נסיעות, מרפאות וספקים מקצועיים — לשמור "
    "בטקסט הטענה ובשדה entities. שמות מדינות ומקומות מותרים. "
    "נסח כל טענה בעברית ניטרלית. "
    "התעלם מצ'יטצ'אט, מסירות ציוד וויכוחים לא רלוונטיים. "
    f"{FORBIDDEN_TERM_INSTRUCTION} "
    "החזר אך ורק JSON תקין."
)


def build_extract_prompt(rendered: str) -> str:
    return (
        "נושאים מוצעים (נקודת התחלה — ניתן להוסיף מזהים חדשים):\n"
        f"{taxonomy_seed_block()}\n\n"
        "חלץ טענות מהשיחה והחזר JSON במבנה:\n"
        "{\n"
        '  "claims": [\n'
        "    {\n"
        '      "claim_text": "<טענה ניטרלית בעברית; שמות ספקים/מקצוענים מותרים>",\n'
        '      "topic_tags": ["<מזהה נושא>", ...],\n'
        '      "entities": ["<ספק/עו״ד/סוכנות/מדינה/מקום>", ...],\n'
        '      "stance": "positive|negative|neutral|factual",\n'
        '      "supporting_message_ids": [<מספרי [m..] התומכים בטענה>],\n'
        '      "opposing_message_ids": [<מספרי [m..] הסותרים את הטענה, אם יש>]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "שורות עם [תגובות: ...] מציינות תגובות אימוג'י להודעה — קח אותן בחשבון "
        "כשאתה מעריך עד כמה הטענה נתמכת.\n"
        "אם אין ידע מועיל, החזר claims ריק.\n\n"
        "השיחה:\n"
        f"{rendered}"
    )


def _knowledge_bearing_ids(
    classified: dict[str, Any], topic_filter: str | None
) -> set[str]:
    ids: set[str] = set()
    for record in classified["threads"]:
        if not record.get("is_knowledge_bearing"):
            continue
        if topic_filter and topic_filter not in (record.get("topic_tags") or []):
            continue
        ids.add(record["thread_id"])
    return ids


def _claims_from_result(
    result: dict[str, Any], thread: dict[str, Any], line_meta: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    raw_claims = result.get("claims") or []
    known = set(page_ids())
    claims: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_claims):
        if not isinstance(raw, dict):
            continue
        claim_text = (raw.get("claim_text") or "").strip()
        if not claim_text:
            continue

        local_ids = [
            i
            for i in (raw.get("supporting_message_ids") or [])
            if isinstance(i, int) and 0 <= i < len(line_meta)
        ]
        opposing_ids = [
            i
            for i in (raw.get("opposing_message_ids") or [])
            if isinstance(i, int) and 0 <= i < len(line_meta)
        ]
        support = compute_support(
            thread, line_meta, local_ids, opposing_local_message_ids=opposing_ids
        )
        months = sorted({line_meta[i]["month"] for i in local_ids})
        if not months:
            months = sorted({m["month"] for m in line_meta})

        topic_tags = [t for t in (raw.get("topic_tags") or []) if isinstance(t, str)]
        global_ids = [
            thread["message_ids"][line_meta[i]["message_index"]] for i in local_ids
        ]

        claims.append(
            {
                "claim_id": f"{thread['thread_id']}-c{position}",
                "thread_id": thread["thread_id"],
                "claim_text": claim_text,
                "topic_tags": topic_tags,
                "emergent_tags": [t for t in topic_tags if t not in known],
                "entities": raw.get("entities") or [],
                "stance": raw.get("stance", "neutral"),
                "date": months[0],
                "support_count": support["support_count"],
                "opposer_count": support["opposer_count"],
                "statement_count": support["statement_count"],
                "reaction_endorser_count": support["reaction_endorser_count"],
                "reaction_only_count": support["reaction_only_count"],
                "reaction_summary": support["reaction_summary"],
                "_support": support,
                "_local_message_ids": local_ids,
                "_opposing_local_message_ids": opposing_ids,
                "_global_message_ids": global_ids,
            }
        )
    return claims


def extract_from_text(
    raw: str, thread: dict[str, Any], line_meta: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    try:
        if not raw:
            return []
        return _claims_from_result(extract_json(raw), thread, line_meta)
    except Exception:  # noqa: BLE001 - keep the batch going
        return []


def extract_thread(thread: dict[str, Any], llm: LLMClient) -> list[dict[str, Any]]:
    rendered, line_meta = render_thread_for_llm(thread)
    if not rendered:
        return []
    prompt = build_extract_prompt(rendered)
    try:
        result = llm.complete_json(EXTRACT_SYSTEM, prompt, task="extract")
        return _claims_from_result(result, thread, line_meta)
    except Exception:  # noqa: BLE001 - keep the batch going
        return []


def run(
    input_path: Path | str = DEFAULT_THREADS_PATH,
    classified_path: Path | str = DEFAULT_CLASSIFIED_PATH,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    audit_dir: Path | str = DEFAULT_AUDIT_DIR,
    llm: LLMClient | None = None,
    topic_filter: str | None = None,
    max_threads: int | None = None,
    use_batch: bool = False,
) -> dict[str, Any]:
    """Extract claims from knowledge-bearing threads.

    ``topic_filter`` restricts to threads tagged with a single topic id (pilot).
    ``max_threads`` caps how many threads are processed.
    """

    llm = llm or LLMClient()
    payload = load_threads(input_path)
    threads_by_id = {t["thread_id"]: t for t in payload["threads"]}

    import json

    with Path(classified_path).open(encoding="utf-8") as f:
        classified = json.load(f)
    keep_ids = _knowledge_bearing_ids(classified, topic_filter)

    published_claims: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []
    pending_llm: list[tuple[dict[str, Any], str, list[dict[str, Any]]]] = []
    processed = 0

    for record in classified["threads"]:
        thread_id = record["thread_id"]
        if thread_id not in keep_ids:
            continue
        if max_threads is not None and processed >= max_threads:
            break
        thread = threads_by_id[thread_id]
        rendered, line_meta = render_thread_for_llm(thread)
        if rendered:
            pending_llm.append((thread, build_extract_prompt(rendered), line_meta))
        processed += 1

    def _publish_claims(thread_claims: list[dict[str, Any]]) -> None:
        for claim in thread_claims:
            support = claim.pop("_support")
            audit_records.append(
                {
                    "claim_id": claim["claim_id"],
                    "thread_id": claim["thread_id"],
                    "raw_claim_text": claim["claim_text"],
                    "supporting_senders": support["statement_senders"],
                    "opposing_senders": support["opposing_senders"],
                    "reaction_senders": support["reaction_senders"],
                    "reaction_opposers": support["reaction_opposers"],
                    "all_supporters": support["all_supporters"],
                    "all_opposers": support["all_opposers"],
                    "local_message_ids": claim.pop("_local_message_ids"),
                    "opposing_local_message_ids": claim.pop(
                        "_opposing_local_message_ids", []
                    ),
                    "global_message_ids": claim.pop("_global_message_ids"),
                    "message_reactions": support["message_reactions"],
                }
            )
            published_claims.append(claim)

    if pending_llm:
        if use_batch and llm.supports_batch():
            print(f"  Extract: submitting {len(pending_llm)} requests via batch API...")
            batch_results = llm.complete_batch(
                [
                    BatchRequest(
                        request_id=thread["thread_id"],
                        system=EXTRACT_SYSTEM,
                        user=prompt,
                        task="extract",
                    )
                    for thread, prompt, _ in pending_llm
                ]
            )
            for thread, _, line_meta in pending_llm:
                raw = batch_results.get(thread["thread_id"], "")
                _publish_claims(extract_from_text(raw, thread, line_meta))
        else:
            if use_batch:
                print("  Extract: batch not supported for this provider; using sync API.")
            for thread, prompt, line_meta in pending_llm:
                try:
                    result = llm.complete_json(EXTRACT_SYSTEM, prompt, task="extract")
                    _publish_claims(_claims_from_result(result, thread, line_meta))
                except Exception:  # noqa: BLE001 - keep the batch going
                    continue

    scrub_summary = scrub_claims(published_claims)

    write_json_file(
        {
            "claims": published_claims,
            "metadata": {
                "source": str(Path(input_path)),
                "threads_processed": processed,
                "claims_count": len(published_claims),
                "scrub": scrub_summary,
                "topic_filter": topic_filter,
                "provider": llm.provider,
                "model": llm.model,
                "batch_mode": use_batch and llm.supports_batch(),
            },
        },
        Path(output_path),
    )

    audit_path = Path(audit_dir) / "claims_audit.json"
    write_json_file(
        {
            "audit": audit_records,
            "metadata": {
                "warning": "PRIVATE - links claims to source messages/senders. Do not publish.",
                "claims_count": len(audit_records),
            },
        },
        audit_path,
    )

    return {
        "threads_processed": processed,
        "claims_count": len(published_claims),
        "scrub": scrub_summary,
        "audit_path": str(audit_path),
    }
