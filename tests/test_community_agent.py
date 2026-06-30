"""Step 7/8 checks: supporter dedup is identity-based and pages stay traceable."""

from __future__ import annotations

import json

from step_7_community import run as community_run
from step_7_community.store import PageStore
from step_8_background import run as background_run
from utils.llm_client import LLMClient
from utils.support import supporter_count_for_claims


def _mock_llm() -> LLMClient:
    return LLMClient(provider="mock", model="mock", use_cache=False)


def test_supporter_dedup_unions_identities():
    audit = {
        "c1": {"all_supporters": ["a", "b"]},
        "c2": {"all_supporters": ["b", "c"]},
    }
    # a,b,c -> 3 distinct; re-citing the same claim cannot inflate.
    assert supporter_count_for_claims(["c1", "c2"], audit) == 3
    assert supporter_count_for_claims(["c1", "c1"], audit) == 2
    assert supporter_count_for_claims([], audit) == 0


def test_statement_recite_is_idempotent():
    claims = {
        "c1": {"claim_id": "c1", "stance": "positive"},
        "c2": {"claim_id": "c2", "stance": "negative"},
    }
    audit = {
        "c1": {"all_supporters": ["a", "b"]},
        "c2": {"all_supporters": ["b", "c"]},
    }
    store = PageStore(claims, audit)
    st = store.upsert_statement("p", "", None, "טקסט", ["c1"])
    assert st["supporter_count"] == 2

    sid = st["statement_id"]
    store.upsert_statement("p", "", sid, "טקסט מעודכן", ["c2"])
    assert st["supporter_count"] == 3  # union of {a,b} and {b,c}

    # Seeing more support for the same claim later must not double-count.
    store.upsert_statement("p", "", sid, "טקסט", ["c1"])
    assert st["supporter_count"] == 3
    assert st["claim_ids"] == ["c1", "c2"]
    assert st["stance_breakdown"] == {"positive": 1, "negative": 1}


def test_unknown_claim_ids_are_dropped():
    store = PageStore({"c1": {"claim_id": "c1", "stance": "neutral"}}, {})
    st = store.upsert_statement("p", "", None, "txt", ["c1", "ghost"])
    assert st["claim_ids"] == ["c1"]
    # A statement backed by no real claim is rejected (every sentence needs claims).
    assert store.upsert_statement("p", "", None, "txt", ["ghost"]) is None


def test_claim_backs_many_statements_but_counts_once_per_statement():
    # A claim may back several statements and is counted fully in each; the only
    # dedup is within a single statement (same claim/supporter not counted twice).
    claims = {"c1": {"claim_id": "c1", "stance": "positive"}}
    audit = {"c1": {"all_supporters": ["a", "b"]}}
    store = PageStore(claims, audit)

    first = store.upsert_statement("p", "", None, "ראשון", ["c1"])
    second = store.upsert_statement("p", "", None, "שני", ["c1"])

    # Same claim backs two statements, counted fully in each.
    assert first["supporter_count"] == 2
    assert second["supporter_count"] == 2

    # Within one statement, citing the same claim twice still counts once.
    dup = store.upsert_statement("p", "", None, "כפול", ["c1", "c1"])
    assert dup["claim_ids"] == ["c1"]
    assert dup["supporter_count"] == 2


def test_mock_community_run_is_traceable_and_anonymous(tmp_path):
    claims = [
        {
            "claim_id": "t1-c1", "thread_id": "t1",
            "claim_text": "מדינה זו מתאימה לתהליך.", "topic_tags": ["usa"],
            "entities": [], "stance": "positive", "date": "2024-01",
            "support_count": 3,
        },
        {
            "claim_id": "t1-c2", "thread_id": "t1",
            "claim_text": "העלויות שם גבוהות.", "topic_tags": ["usa"],
            "entities": [], "stance": "negative", "date": "2024-02",
            "support_count": 2,
        },
    ]
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps({"claims": claims}, ensure_ascii=False), encoding="utf-8"
    )
    out = tmp_path / "wiki_pages.json"

    meta = community_run.run(
        llm=_mock_llm(),
        claims_path=claims_file,
        plan_path=tmp_path / "noplan.json",  # absent -> taxonomy seed
        output_path=out,
        batch_size=10,
    )
    assert meta["statements"] >= 2

    data = json.loads(out.read_text(encoding="utf-8"))
    cited = {
        cid
        for page in data["pages"].values()
        for sec in page["sections"]
        for st in sec["statements"]
        for cid in st["claim_ids"]
    }
    assert cited == {"t1-c1", "t1-c2"}

    store = PageStore.from_payload(data, {c["claim_id"]: c for c in claims})
    body = store.render_community("usa")
    assert "תומכים" in body  # computed supporter line is shown
    assert "t1-c1" not in body and "t1-c2" not in body  # trace stays in the store

    drafts = tmp_path / "drafts"
    background_run.run(
        pages_path=out,
        claims_path=claims_file,
        drafts_dir=drafts,
        enable_web_search=False,
    )
    page_md = (drafts / "usa.md").read_text(encoding="utf-8")
    assert "מידע מהקהילה" in page_md
    assert "t1-c1" not in page_md
    assert (drafts / "index.md").exists()


def test_multi_topic_claim_reaches_every_page(tmp_path):
    # A claim tagged for two pages must influence both, not just the first.
    claims = [
        {
            "claim_id": "t1-c1", "thread_id": "t1",
            "claim_text": "מידע שרלוונטי לשתי המדינות.",
            "topic_tags": ["usa", "israel"],
            "entities": [], "stance": "neutral", "date": "2024-01",
            "support_count": 1,
        }
    ]
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps({"claims": claims}, ensure_ascii=False), encoding="utf-8"
    )
    out = tmp_path / "wiki_pages.json"

    community_run.run(
        llm=_mock_llm(),
        claims_path=claims_file,
        plan_path=tmp_path / "noplan.json",  # absent -> taxonomy seed
        output_path=out,
        batch_size=10,
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    cited_on = {
        page_id
        for page_id, page in data["pages"].items()
        for sec in page["sections"]
        for st in sec["statements"]
        if "t1-c1" in st["claim_ids"]
    }
    assert {"usa", "israel"} <= cited_on
