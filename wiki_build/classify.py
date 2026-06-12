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
from wiki_build.llm_client import LLMClient
from wiki_build.taxonomy import page_ids, taxonomy_prompt_block
from wiki_build.threads_io import (
    DEFAULT_THREADS_PATH,
    load_threads,
    render_thread_for_llm,
)

DEFAULT_OUTPUT_PATH = Path("data/threads_classified.json")

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
        "להלן רשימת הנושאים האפשריים (מזהה: כותרת):\n"
        f"{taxonomy_prompt_block()}\n\n"
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


def classify_thread(thread: dict[str, Any], llm: LLMClient) -> dict[str, Any]:
    rendered, _ = render_thread_for_llm(thread)
    prompt = build_classify_prompt(rendered)
    try:
        result = llm.complete_json(CLASSIFY_SYSTEM, prompt, task="classify")
    except Exception as exc:  # noqa: BLE001 - keep the batch going
        return {
            "is_knowledge_bearing": False,
            "topic_tags": [],
            "entities": [],
            "reason": f"classification_error: {exc}",
        }

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


def run(
    input_path: Path | str = DEFAULT_THREADS_PATH,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    llm: LLMClient | None = None,
    min_messages: int = 3,
    min_senders: int = 2,
    max_threads: int | None = None,
    topic_filter: str | None = None,
) -> dict[str, Any]:
    """Classify all threads and write ``threads_classified.json``.

    ``max_threads`` limits how many candidate threads are sent to the LLM
    (handy for a pilot). ``topic_filter`` keeps only threads whose heuristic
    keyword text mentions the given substring (pilot on one topic).
    """

    llm = llm or LLMClient()
    payload = load_threads(input_path)
    threads = payload["threads"]

    classified: list[dict[str, Any]] = []
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
            record.update(
                {
                    "is_knowledge_bearing": False,
                    "topic_tags": [],
                    "emergent_tags": [],
                    "entities": [],
                    "reason": "filtered_by_heuristic",
                }
            )
            classified.append(record)
            continue

        if topic_filter and topic_filter.lower() not in "\n".join(
            (m.get("content") or "") for m in thread["messages"]
        ).lower():
            record.update(
                {
                    "is_knowledge_bearing": False,
                    "topic_tags": [],
                    "emergent_tags": [],
                    "entities": [],
                    "reason": "outside_topic_filter",
                }
            )
            classified.append(record)
            continue

        if max_threads is not None and sent_to_llm >= max_threads:
            record.update(
                {
                    "is_knowledge_bearing": False,
                    "topic_tags": [],
                    "emergent_tags": [],
                    "entities": [],
                    "reason": "skipped_max_threads",
                }
            )
            classified.append(record)
            continue

        record.update(classify_thread(thread, llm))
        sent_to_llm += 1
        classified.append(record)

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
        },
    }
    write_json_file(output, Path(output_path))
    return output["metadata"]


if __name__ == "__main__":
    meta = run()
    print(
        f"Classified {meta['thread_count']} threads; "
        f"{meta['knowledge_bearing_count']} knowledge-bearing "
        f"(LLM calls: {meta['classified_by_llm']})"
    )
