"""rerun_missing backfills only knowledge-bearing threads absent from claims.json,
merging the recovered claims/audit into the existing outputs.
"""

from __future__ import annotations

import json
from pathlib import Path

from step_3_extract import rerun_missing
from utils.llm_client import LLMClient


def _thread(thread_id: str) -> dict:
    return {
        "thread_id": thread_id,
        "message_ids": [f"{thread_id}-m0"],
        "messages": [
            {
                "sender": "alice",
                "datetime": "2024-01-01T10:00:00",
                "content": "מידע על פונדקאות וסוכנות",
                "reactions": [],
            }
        ],
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_backfills_only_missing_knowledge_threads(tmp_path):
    threads_path = tmp_path / "threads.json"
    classified_path = tmp_path / "classified.json"
    claims_path = tmp_path / "claims.json"
    audit_dir = tmp_path / "audit"

    _write(threads_path, {"threads": [_thread("t-have"), _thread("t-missing")]})
    _write(
        classified_path,
        {
            "threads": [
                {"thread_id": "t-have", "is_knowledge_bearing": True, "topic_tags": []},
                {"thread_id": "t-missing", "is_knowledge_bearing": True, "topic_tags": []},
                {"thread_id": "t-chat", "is_knowledge_bearing": False, "topic_tags": []},
            ]
        },
    )
    # t-have already has a saved claim; t-missing does not.
    _write(claims_path, {"claims": [{"claim_id": "t-have-c0", "thread_id": "t-have"}]})

    llm = LLMClient(provider="mock", cache_dir=tmp_path / "cache")
    result = rerun_missing.run(
        input_path=threads_path,
        classified_path=classified_path,
        output_path=claims_path,
        audit_dir=audit_dir,
        llm=llm,
        use_batch=False,
    )

    assert result["missing_threads"] == 1  # only t-missing (t-chat is not knowledge-bearing)
    assert result["recovered_threads"] == 1

    merged = json.loads(claims_path.read_text(encoding="utf-8"))
    thread_ids = {c["thread_id"] for c in merged["claims"]}
    assert thread_ids == {"t-have", "t-missing"}  # original preserved + new merged
    assert merged["metadata"]["claims_count"] == len(merged["claims"])

    audit = json.loads((audit_dir / "claims_audit.json").read_text(encoding="utf-8"))
    assert any(rec["thread_id"] == "t-missing" for rec in audit["audit"])
