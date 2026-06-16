"""Stage A: filter noise threads and tag the rest by topic.

A cheap heuristic pass first discards threads that cannot carry shared
knowledge (too short, single participant). Each surviving thread is then sent
to the LLM for a single classification call that decides whether it is
knowledge-bearing and assigns multi-label topic tags from the taxonomy.

Output: ``data/threads_classified.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils import write_json_file
from wiki_build.llm_client import BatchRequest, LLMClient, extract_json
from thread_tagger.paths import (
    EDITED_THREADS_PATH,
    ORIGINAL_CLASSIFIED_PATH,
    ORIGINAL_THREADS_PATH,
    ensure_edited_workspace,
)
from wiki_build.taxonomy import page_ids, taxonomy_seed_block
from wiki_build.threads_io import (
    load_threads,
    render_thread_for_llm,
)

CLASSIFY_SYSTEM = (
    "אתה עוזר שממיין שיחות מקבוצת וואטסאפ על פונדקאות לגייז. "
    "המטרה היא לזהות שיחות הנושאות ידע מועיל (המלצות, חוויות, מידע משפטי/כספי/רפואי, "
    "השוואת ספקים, מדינות) ולתייג אותן לפי נושאים. "
    "שיחות שהן רכילות, לוגיסטיקה מקומית (\"מי גר ברמת גן\"), מכירת חפצים, ויכוחים לא רלוונטיים "
    "או צ'יטצ'אט חברתי אינן נושאות ידע. "
    "החזר אך ורק JSON תקין, ללא טקסט נוסף."
)


def build_classify_prompt(rendered: str) -> str:
    return (
        "נושאים מוצעים (נקודת התחלה — ניתן להוסיף מזהים חדשים):\n"
        f"{taxonomy_seed_block()}\n\n"
        "סווג את השיחה הבאה והחזר JSON במבנה:\n"
        "{\n"
        '  "is_knowledge_bearing": true/false,\n'
        '  "topic_tags": ["<מזהה נושא>", ...],\n'
        '  "entities": ["<ספק/מדינה/מקום שהוזכרו>", ...],\n'
        '  "reason": "<משפט קצר>"\n'
        "}\n"
        "אם השיחה אינה נושאת ידע, החזר topic_tags ריק.\n"
        "אפשר להוסיף מזהה נושא חדש אם אף קיים אינו מתאים.\n\n"
        "השיחה:\n"
        f"{rendered}"
    )


def heuristic_keep(thread: dict[str, Any], min_messages: int, min_senders: int) -> bool:
    return (
        thread.get("num_messages", 0) >= min_messages
        and thread.get("num_unique_senders", 0) >= min_senders
    )


def _parse_classify_result(result: dict[str, Any]) -> dict[str, Any]:
    known = set(page_ids())
    raw_tags = result.get("topic_tags") or []
    topic_tags = [t for t in raw_tags if isinstance(t, str)]
    emergent = [t for t in topic_tags if t not in known]
    return {
        "is_knowledge_bearing": bool(result.get("is_knowledge_bearing")),
        "topic_tags": topic_tags,
        "emergent_tags": emergent,
        "entities": result.get("entities") or [],
        "reason": result.get("reason", ""),
    }


def _classify_error(reason: str) -> dict[str, Any]:
    return {
        "is_knowledge_bearing": False,
        "topic_tags": [],
        "emergent_tags": [],
        "entities": [],
        "reason": reason,
    }


def classify_from_text(raw: str) -> dict[str, Any]:
    try:
        if not raw:
            raise ValueError("empty response")
        return _parse_classify_result(extract_json(raw))
    except Exception as exc:  # noqa: BLE001 - keep the batch going
        return _classify_error(f"classification_error: {exc}")


def classify_thread(thread: dict[str, Any], llm: LLMClient) -> dict[str, Any]:
    rendered, _ = render_thread_for_llm(thread)
    prompt = build_classify_prompt(rendered)
    try:
        result = llm.complete_json(CLASSIFY_SYSTEM, prompt, task="classify")
        return _parse_classify_result(result)
    except Exception as exc:  # noqa: BLE001 - keep the batch going
        return _classify_error(f"classification_error: {exc}")


def run(
    input_path: Path | str = ORIGINAL_THREADS_PATH,
    output_path: Path | str = ORIGINAL_CLASSIFIED_PATH,
    llm: LLMClient | None = None,
    min_messages: int = 3,
    min_senders: int = 2,
    max_threads: int | None = None,
    topic_filter: str | None = None,
    use_batch: bool = False,
) -> dict[str, Any]:
    """Classify all threads and write ``threads_classified.json``.

    ``max_threads`` limits how many candidate threads are sent to the LLM
    (handy for a pilot). ``topic_filter`` keeps only threads whose heuristic
    keyword text mentions the given substring (pilot on one topic).
    """

    llm = llm or LLMClient()
    ensure_edited_workspace()

    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path == ORIGINAL_THREADS_PATH and EDITED_THREADS_PATH.exists():
        input_path = EDITED_THREADS_PATH

    payload = load_threads(input_path)
    threads = payload["threads"]

    classified: list[dict[str, Any]] = []
    pending_llm: list[tuple[dict[str, Any], str]] = []
    pending_indices: list[int] = []
    sent_to_llm = 0

    for thread in threads:
        kept_heuristic = heuristic_keep(thread, min_messages, min_senders)
        record: dict[str, Any] = {
            "thread_id": thread["thread_id"],
            "start_time": thread["start_time"],
            "last_time": thread["last_time"],
            "num_messages": thread["num_messages"],
            "num_unique_senders": thread["num_unique_senders"],
            "passed_heuristic": kept_heuristic,
        }

        if not kept_heuristic:
            record.update(_classify_error("filtered_by_heuristic"))
            classified.append(record)
            continue

        if topic_filter and topic_filter.lower() not in "\n".join(
            (m.get("content") or "") for m in thread["messages"]
        ).lower():
            record.update(_classify_error("outside_topic_filter"))
            classified.append(record)
            continue

        if max_threads is not None and sent_to_llm >= max_threads:
            record.update(_classify_error("skipped_max_threads"))
            classified.append(record)
            continue

        rendered, _ = render_thread_for_llm(thread)
        prompt = build_classify_prompt(rendered)
        pending_llm.append((thread, prompt))
        pending_indices.append(len(classified))
        classified.append(record)
        sent_to_llm += 1

    if pending_llm:
        if use_batch and llm.supports_batch():
            print(f"  Classify: submitting {len(pending_llm)} requests via batch API...")
            batch_results = llm.complete_batch(
                [
                    BatchRequest(
                        request_id=thread["thread_id"],
                        system=CLASSIFY_SYSTEM,
                        user=prompt,
                        task="classify",
                    )
                    for thread, prompt in pending_llm
                ]
            )
            for (thread, _), record_idx in zip(pending_llm, pending_indices):
                classified[record_idx].update(
                    classify_from_text(batch_results.get(thread["thread_id"], ""))
                )
        else:
            if use_batch:
                print("  Classify: batch not supported for this provider; using sync API.")
            for (thread, _), record_idx in zip(pending_llm, pending_indices):
                classified[record_idx].update(classify_thread(thread, llm))

    kept = [r for r in classified if r["is_knowledge_bearing"]]
    output = {
        "threads": classified,
        "metadata": {
            "source": str(Path(input_path)),
            "thread_count": len(classified),
            "classified_by_llm": sent_to_llm,
            "knowledge_bearing_count": len(kept),
            "provider": llm.provider,
            "model": llm.model,
            "batch_mode": use_batch and llm.supports_batch(),
        },
    }
    write_json_file(output, output_path)
    actions = ensure_edited_workspace(classified_output=output_path)
    if actions:
        summary = ", ".join(f"{name} {action}" for name, action in actions.items())
        print(f"  Review workspace: {summary}")
    return output["metadata"]


if __name__ == "__main__":
    meta = run(
        llm=LLMClient.for_stage("classify", use_hybrid_defaults=True),
        use_batch=True,
    )
    print(
        f"Classified {meta['thread_count']} threads; "
        f"{meta['knowledge_bearing_count']} knowledge-bearing "
        f"(LLM calls: {meta['classified_by_llm']})"
    )
