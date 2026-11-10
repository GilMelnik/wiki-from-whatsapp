"""``utils.migrate_claims_v1`` on a synthetic v1 corpus; the result feeds a no-call re-run.

Synthetic data only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from step_3_extract.claims import claim_id_for
from step_3_extract.run import run
from utils.config import DATA_DIR
from utils.migrate_claims_v1 import main, migrate
from utils.paths import AUDIT_PATH, CHAT_ANDROID_PATH, CLAIMS, THREADS, Artifact

C3_KEYS = {
    "claim_id", "thread_id", "claim_text", "entities", "stance", "supporting_message_ids",
    "opposing_message_ids", "first_date", "last_date", "supporter_count", "opposer_count",
    "reaction_summary",
}


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


def _at(root: Path, path: Path) -> Path:
    return root / path.relative_to(DATA_DIR)


@pytest.fixture
def backup(tmp_path: Path) -> Path:
    root = tmp_path / "backup"
    messages = [
        {"id": "WA", "datetime": "2023-05-01T09:00:00", "sender": "u1", "content": "a"},
        {"id": "WB", "datetime": "2023-05-02T09:00:00", "sender": "u2", "content": "b"},
        {"id": "WC", "datetime": "2023-06-10T09:00:00", "sender": "u3", "content": "c"},
    ]
    _write(_at(root, THREADS.edited), {"threads": [
        {"thread_id": "thread-0001", "message_ids": [0, 1, 2], "messages": messages}]})
    _write(_at(root, CHAT_ANDROID_PATH), [
        {"id": "WA", "quote": None, "reactions": [
            {"emoji": "👍", "count": 1, "senders": [{"user_name": "u4", "phone": ""}]}]},
        {"id": "WB", "quote": None, "reactions": []},
        {"id": "WC", "quote": None, "reactions": []},
    ])
    old_claims = [
        {"claim_id": "thread-0001-c0", "thread_id": "thread-0001", "claim_text": "reviewed text",
         "topic_tags": ["legacy"], "entities": ["Alpha"], "stance": "positive", "date": "2023-05"},
        {"claim_id": "thread-0001-c1", "thread_id": "thread-0001", "claim_text": "same",
         "entities": [], "stance": "factual"},
        {"claim_id": "thread-0001-c2", "thread_id": "thread-0001", "claim_text": "same",
         "entities": [], "stance": "factual"},
        {"claim_id": "thread-0001-c3", "thread_id": "thread-0001", "claim_text": "no audit",
         "entities": [], "stance": "factual"},
        {"claim_id": "thread-0009-c0", "thread_id": "thread-0009", "claim_text": "no thread",
         "entities": [], "stance": "factual"},
        {"claim_id": "thread-0001-c4", "thread_id": "thread-0001", "claim_text": "uncited",
         "entities": [], "stance": "neutral"},
    ]
    _write(_at(root, CLAIMS.edited), {"claims": old_claims, "metadata": {}})
    _write(_at(root, AUDIT_PATH), {"audit": [
        {"claim_id": "thread-0001-c0", "raw_claim_text": "raw text", "local_message_ids": [0],
         "opposing_local_message_ids": [1]},
        {"claim_id": "thread-0001-c1", "raw_claim_text": "same", "local_message_ids": [1]},
        {"claim_id": "thread-0001-c2", "raw_claim_text": "same", "local_message_ids": [2, 7]},
        {"claim_id": "thread-0009-c0", "local_message_ids": [0]},
        {"claim_id": "thread-0001-c4", "raw_claim_text": "uncited", "local_message_ids": []},
    ]})
    return root


def test_migrate_emits_c3_claims_and_audit(backup: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = migrate(backup, out)
    assert summary["v1_claims"] == 6 and summary["c3_claims"] == 3
    assert summary["claims_without_evidence"] == 1
    assert summary["merged_duplicates"] == 1 and summary["skipped_no_audit"] == 1
    assert summary["skipped_no_thread"] == 1 and summary["evidence_ids_dropped"] == 1
    payload = json.loads(_at(out, CLAIMS.original).read_text("utf-8"))
    claims = {c["claim_id"]: c for c in payload["claims"]}
    first = claims[claim_id_for("thread-0001", "raw text")]  # id from the extracted text
    assert set(first) == C3_KEYS
    assert first["claim_text"] == "reviewed text"
    assert first["supporting_message_ids"] == ["WA"] and first["opposing_message_ids"] == ["WB"]
    assert (first["first_date"], first["last_date"]) == ("2023-05-01", "2023-05-02")
    assert (first["supporter_count"], first["opposer_count"]) == (2, 1)
    same = claims[claim_id_for("thread-0001", "same")]
    assert same["supporting_message_ids"] == ["WB", "WC"]
    assert payload["metadata"]["legacy_claim_ids"][same["claim_id"]] == [
        "thread-0001-c1", "thread-0001-c2"]
    uncited = claims[claim_id_for("thread-0001", "uncited")]
    assert uncited["supporting_message_ids"] == [] and uncited["supporter_count"] == 0
    assert (uncited["first_date"], uncited["last_date"]) == ("2023-05-01", "2023-06-10")
    audit = json.loads(_at(out, AUDIT_PATH).read_text("utf-8"))["audit"]
    assert {r["claim_id"] for r in audit} == set(claims)
    assert {e["person"] for e in audit[0]["supporters"]} == {"u1", "u4"}


def test_migrated_corpus_reruns_without_model_calls(backup: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    migrate(backup, out)
    classified = tmp_path / "classified.json"
    _write(classified, {"threads": [{"thread_id": "thread-0001", "is_knowledge_bearing": True}]})

    class NoCalls:
        provider, model = "none", "none"

        def supports_batch(self) -> bool:
            return False

        def set_logger(self, logger: object) -> None:
            del logger

        def complete_json(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("no model call expected")

    before = json.loads(_at(out, CLAIMS.original).read_text("utf-8"))["claims"]
    run(threads_path=_at(backup, THREADS.edited), classified_path=classified,
        export_path=_at(backup, CHAT_ANDROID_PATH),
        artifact=Artifact(_at(out, CLAIMS.original), _at(out, CLAIMS.edited)),
        audit_path=_at(out, AUDIT_PATH), llm=NoCalls(), use_batch=False)
    assert json.loads(_at(out, CLAIMS.original).read_text("utf-8"))["claims"] == before


def test_cli_requires_the_output_dir(backup: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--backup-dir", str(backup)])
