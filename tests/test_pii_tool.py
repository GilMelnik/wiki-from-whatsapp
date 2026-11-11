"""The PII review tool: endpoints, persistence to the edited file, reloads, locks (synthetic C3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from review_tool.artifacts import GRAPH
from review_tool.tools import TOOL_SPECS
from step_3_extract.reviewer.tool import PiiTool
from step_3_extract.scrub import REDACTION_MARK, scrub_claims
from tests.review_tool_support import BASE_URL, build_app, build_graph, locked_elsewhere
from utils.json_io import write_json_file

SHORT_LOCK_TIMEOUT = 0.2
PHONE_EMAIL = "t-B1-00000002"  # phone first, then email: two marks of different kinds
PHONE = "t-A1-00000001"
EMAIL = "t-C1-00000003"
CLEAN = "t-A1-00000004"  # no PII: never a review item


def c3_claim(claim_id: str, text: str, *, first: str, supporters: int, **extra: Any) -> dict:
    return {
        "claim_id": claim_id,
        "thread_id": claim_id.rsplit("-", 1)[0],
        "claim_text": text,
        "entities": ["Alpha"],
        "stance": "factual",
        "supporting_message_ids": ["A1", "A2"],
        "opposing_message_ids": ["A3"],
        "first_date": first,
        "last_date": "2024-06-01",
        "supporter_count": supporters,
        "opposer_count": 1,
        "reaction_summary": [],
        **extra,
    }


def claims_payload() -> dict[str, Any]:
    claims = [
        c3_claim(PHONE, "סוכנות Alpha זמינה ב-050-1234567.", first="2024-03-01", supporters=2),
        c3_claim(
            PHONE_EMAIL,
            "חייגו 03-1234567 או כתבו אל info@alpha.example.",
            first="2024-01-01",
            supporters=5,
        ),
        c3_claim(EMAIL, "פרטים בכתובת desk@beta.example.", first="2024-02-01", supporters=1),
        c3_claim(CLEAN, "עורך דין מומלץ.", first="2024-01-15", supporters=9),
    ]
    scrub_claims(claims)
    return {"claims": claims, "metadata": {"claims_count": len(claims)}}


class PiiSetup:
    def __init__(self, root: Path, *, with_claims: bool = True) -> None:
        self.graph = build_graph(root)
        node = self.graph.node("claims")
        self.original, self.edited = node.original, node.edited
        if with_claims:
            write_json_file(claims_payload(), self.original)
        self.client = self.new_client()

    def new_client(self) -> TestClient:
        """A fresh tool (new store handle) on the same files, as after a server restart."""
        tool = PiiTool(self.graph, lock_timeout=SHORT_LOCK_TIMEOUT)
        app = build_app(self.original.parent, specs=(tool.spec,), graph=self.graph)
        return TestClient(app, base_url=BASE_URL)

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(f"/api/pii{path}", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def post(self, path: str, body: dict[str, Any] | None = None, status: int = 200) -> Any:
        response = self.client.post(f"/api/pii{path}", json=body)
        assert response.status_code == status, response.text
        return response.json()

    def edited_claims(self) -> dict[str, dict[str, Any]]:
        payload = json.loads(self.edited.read_text(encoding="utf-8"))
        return {claim["claim_id"]: claim for claim in payload["claims"]}


@pytest.fixture
def pii(tmp_path: Path) -> PiiSetup:
    return PiiSetup(tmp_path)


def ids(page: dict[str, Any]) -> list[str]:
    return [item["claim_id"] for item in page["items"]]


# ------------------------------------------------------------- registration


def test_tool_is_hosted_by_the_server() -> None:
    spec = next(spec for spec in TOOL_SPECS if spec.name == "pii")
    assert spec.reads == ("claims",) and spec.writes == ("claims",)
    assert GRAPH.node("claims").step == "step_3_extract"


def test_missing_claims_file_still_registers_and_is_reported(tmp_path: Path) -> None:
    pii = PiiSetup(tmp_path, with_claims=False)
    tools = pii.client.get("/api/tools").json()
    assert tools == [
        {
            "name": "pii",
            "title": "Privacy",
            "reads": ["claims"],
            "writes": ["claims"],
            "missing": ["claims"],
        }
    ]
    for path in ("/api/pii/summary", "/api/pii/claims?limit=10", f"/api/pii/claims/{PHONE}"):
        response = pii.client.get(path)
        assert response.status_code == 409 and "step_3_extract.run" in response.json()["detail"]
    write_json_file(claims_payload(), pii.original)  # step 3 ran: no restart needed
    assert pii.get("/summary")["review"]["pending"] == 3


# ------------------------------------------------------------------ reading


def test_summary_counts_the_review(pii: PiiSetup) -> None:
    assert pii.get("/summary") == {
        "file": "claims.json",
        "edited_exists": False,
        "claims_count": 4,
        "review": {"pending": 3, "accepted": 0, "restored": 0, "partial": 0, "total": 3},
        "pending_by_type": {"email": 2, "phone": 2},
    }


def test_list_filters_sorts_and_pages(pii: PiiSetup) -> None:
    page = pii.get("/claims", limit=2)
    assert ids(page) == [PHONE, PHONE_EMAIL] and page["total"] == 3 and page["offset"] == 0
    assert ids(pii.get("/claims", limit=2, offset=2)) == [EMAIL]
    assert ids(pii.get("/claims", limit=10, sort="date")) == [PHONE_EMAIL, EMAIL, PHONE]
    assert ids(pii.get("/claims", limit=10, sort="supporters", order="desc"))[0] == PHONE_EMAIL
    assert ids(pii.get("/claims", limit=10, sort="redactions", order="desc"))[0] == PHONE_EMAIL
    assert pii.get("/claims", limit=10, filter="reviewed")["total"] == 0
    row = pii.get("/claims", limit=10, sort="date")["items"][0]
    assert row == {
        "claim_id": PHONE_EMAIL,
        "thread_id": "t-B1",
        "text": f"חייגו {REDACTION_MARK} או כתבו אל {REDACTION_MARK}.",
        "status": "pending",
        "redaction_counts": {"email": 1, "phone": 1},
        "first_date": "2024-01-01",
        "last_date": "2024-06-01",
        "supporter_count": 5,
        "opposer_count": 1,
    }
    assert pii.client.get("/api/pii/claims", params={"limit": 0}).status_code == 422
    assert pii.client.get("/api/pii/claims", params={"limit": 5, "sort": "x"}).status_code == 422


def test_detail_pairs_each_mark_with_its_value(pii: PiiSetup) -> None:
    detail = pii.get(f"/claims/{PHONE_EMAIL}")
    claim = detail["claim"]
    assert [item["type"] for item in claim["redactions"]] == ["phone", "email"]
    assert claim["segments"] == [
        {"text": "חייגו "},
        {"mark": 0},
        {"text": " או כתבו אל "},
        {"mark": 1},
        {"text": "."},
    ]
    assert claim["marks_match"] is True and claim["can_undo"] is False
    assert claim["supporting_message_count"] == 2 and claim["opposing_message_count"] == 1
    assert claim["entities"] == ["Alpha"] and claim["stance"] == "factual"
    assert detail["queue"] == {"position": 1, "total": 3, "prev_id": PHONE, "next_id": EMAIL}
    other_view = pii.get(f"/claims/{PHONE_EMAIL}", filter="reviewed")["queue"]
    assert other_view == {"position": None, "total": 0, "prev_id": None, "next_id": None}


@pytest.mark.parametrize("claim_id", ["t-Z9-ffffffff", CLEAN])
def test_unknown_or_clean_claim_is_404(pii: PiiSetup, claim_id: str) -> None:
    assert pii.client.get(f"/api/pii/claims/{claim_id}").status_code == 404
    pii.post(f"/claims/{claim_id}/decision", {"decision": "accept"}, status=404)
    pii.post(f"/claims/{claim_id}/undo", status=404)


# ---------------------------------------------------------------- decisions


def test_accept_is_saved_to_the_edited_file_only_and_survives_a_restart(pii: PiiSetup) -> None:
    original_bytes = pii.original.read_bytes()
    result = pii.post(f"/claims/{PHONE}/decision", {"decision": "accept"})
    assert result["claim"]["status"] == "accepted" and result["next_id"] == PHONE_EMAIL
    assert result["summary"]["review"]["accepted"] == 1 and result["summary"]["edited_exists"]
    assert pii.original.read_bytes() == original_bytes
    saved = pii.edited_claims()
    assert saved[PHONE]["_pii_review"] == "accepted" and "_redactions" not in saved[PHONE]
    assert REDACTION_MARK in saved[PHONE]["claim_text"] and saved[PHONE]["_pii_reviewed_at"]
    assert saved[CLEAN] == claims_payload()["claims"][3]  # untouched claims stay as they were
    metadata = json.loads(pii.edited.read_text(encoding="utf-8"))["metadata"]
    assert metadata["edited_by"] == "pii_reviewer" and metadata["pii_review"]["pending"] == 2

    pii.client = pii.new_client()
    assert pii.get("/summary")["file"] == "claims_edited.json"
    reviewed = pii.get(f"/claims/{PHONE}", filter="reviewed")["claim"]
    assert reviewed["status"] == "accepted" and reviewed["can_undo"] is True
    assert reviewed["redactions"] == [{"type": "phone", "value": "050-1234567", "in_text": False}]
    assert ids(pii.get("/claims", limit=10)) == [PHONE_EMAIL, EMAIL]


def test_restore_puts_every_value_back_in_its_own_place(pii: PiiSetup) -> None:
    result = pii.post(f"/claims/{PHONE_EMAIL}/decision", {"decision": "restore"})
    text = "חייגו 03-1234567 או כתבו אל info@alpha.example."
    assert result["claim"]["status"] == "restored" and result["claim"]["text"] == text
    assert pii.edited_claims()[PHONE_EMAIL]["claim_text"] == text
    assert all(item["in_text"] for item in result["claim"]["redactions"])


def test_restore_some_values_is_a_partial_decision(pii: PiiSetup) -> None:
    result = pii.post(f"/claims/{PHONE_EMAIL}/decision", {"decision": "restore", "restore": [1]})
    assert result["claim"]["status"] == "partial"
    assert pii.edited_claims()[PHONE_EMAIL]["claim_text"] == (
        f"חייגו {REDACTION_MARK} או כתבו אל info@alpha.example."
    )
    assert pii.get("/summary")["review"]["partial"] == 1


def test_decisions_that_do_not_fit_are_409(pii: PiiSetup) -> None:
    for bad in ([], [2], [-1]):
        pii.post(f"/claims/{PHONE_EMAIL}/decision", {"decision": "restore", "restore": bad}, 409)
    pii.post(f"/claims/{PHONE}/undo", status=409)  # nothing decided yet
    pii.post(f"/claims/{PHONE}/decision", {"decision": "accept"})
    detail = pii.post(f"/claims/{PHONE}/decision", {"decision": "restore"}, status=409)["detail"]
    assert "undo it first" in detail
    pii.post(f"/claims/{EMAIL}/decision", {"decision": "maybe"}, status=422)


def test_restore_is_refused_when_marks_and_redactions_disagree(pii: PiiSetup) -> None:
    payload = claims_payload()
    payload["claims"][0]["claim_text"] += f" {REDACTION_MARK}"  # a mark with no value
    write_json_file(payload, pii.original)
    assert pii.get(f"/claims/{PHONE}")["claim"]["marks_match"] is False
    pii.post(f"/claims/{PHONE}/decision", {"decision": "restore"}, status=409)
    pii.post(f"/claims/{PHONE}/decision", {"decision": "accept"})


def test_undo_makes_the_claim_pending_again(pii: PiiSetup) -> None:
    pii.post(f"/claims/{PHONE_EMAIL}/decision", {"decision": "restore"})
    result = pii.post(f"/claims/{PHONE_EMAIL}/undo")
    assert result["claim"]["status"] == "pending" and result["summary"]["review"]["pending"] == 3
    saved = pii.edited_claims()[PHONE_EMAIL]
    assert saved == claims_payload()["claims"][1]


def test_next_claim_follows_the_queue_and_wraps(pii: PiiSetup) -> None:
    by_date = {"filter": "pending", "sort": "date", "order": "asc"}  # PHONE_EMAIL, EMAIL, PHONE
    first = pii.post(f"/claims/{EMAIL}/decision", {"decision": "accept", **by_date})
    assert first["next_id"] == PHONE
    last = pii.post(f"/claims/{PHONE}/decision", {"decision": "accept", **by_date})
    assert last["next_id"] == PHONE_EMAIL  # end of the queue: back to the first one left
    done = pii.post(f"/claims/{PHONE_EMAIL}/decision", {"decision": "accept", **by_date})
    assert done["next_id"] is None
    pii.post(f"/claims/{PHONE}/undo")
    everything = {"filter": "all", "sort": "claim_id", "order": "desc"}  # EMAIL, PHONE_EMAIL, PHONE
    stays = pii.post(f"/claims/{PHONE}/decision", {"decision": "accept", **everything})
    assert stays["next_id"] == PHONE  # last in a view it stays in: keep showing it


# ------------------------------------------------- files changing underneath


def test_a_step_rerun_is_picked_up_before_the_next_decision(pii: PiiSetup) -> None:
    pii.post(f"/claims/{PHONE}/decision", {"decision": "accept"})
    rerun = json.loads(pii.edited.read_text(encoding="utf-8"))  # step 3 rewrites both files
    rerun["claims"] = [c for c in rerun["claims"] if c["claim_id"] != EMAIL]
    rerun["claims"].append(c3_claim("t-D1-00000005", "new", first="2024-07-01", supporters=1))
    rerun["claims"][-1]["_redactions"] = [{"type": "phone", "value": "*5400"}]
    rerun["claims"][-1]["claim_text"] = f"מוקד {REDACTION_MARK}"
    write_json_file(rerun, pii.original)
    write_json_file(rerun, pii.edited)

    pii.post(f"/claims/{EMAIL}/decision", {"decision": "accept"}, status=404)
    assert pii.get("/summary")["review"] == {
        "pending": 2, "accepted": 1, "restored": 0, "partial": 0, "total": 3
    }
    pii.post("/claims/t-D1-00000005/decision", {"decision": "restore"})
    saved = pii.edited_claims()
    assert EMAIL not in saved and saved["t-D1-00000005"]["claim_text"] == "מוקד *5400"
    assert saved[PHONE]["_pii_review"] == "accepted"


def test_a_locked_claims_file_is_423_and_reads_still_work(pii: PiiSetup) -> None:
    with locked_elsewhere(pii.original):
        response = pii.client.post(f"/api/pii/claims/{PHONE}/decision", json={"decision": "accept"})
        detail = response.json()["detail"]
        assert response.status_code == 423 and "being written by another process" in detail
        assert pii.get("/summary")["review"]["pending"] == 3
    assert not pii.edited.exists()
    pii.post(f"/claims/{PHONE}/decision", {"decision": "accept"})
