"""Step 9's ``docs/wiki_index.json`` against contract C11 and the Worker's JSON Schema."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from step_9_site.agent_pages import disclaimer_line
from tests.schema_check import schema_errors
from tests.site_fixtures import GENERATED_AT, make_page, make_site_plan, run_site
from utils.config import PROJECT_ROOT, section

WORKER = PROJECT_ROOT / "worker"
SCHEMA = json.loads((WORKER / "wiki_index.schema.json").read_text(encoding="utf-8"))
FIXTURE = json.loads((WORKER / "test" / "fixture" / "wiki_index.json").read_text(encoding="utf-8"))
INDEX_FILE = section("mcp")["index_file"]
BODY = (
    "## רקע\n\n[[clinics]] ו-[[costs|עלויות]], שוב [[clinics|מרפאות]], [[agencies|עצמי]], "
    "[[hospital]] [[label:usa]].\n\n```\n[[later-page]]\n```\n"
)


def _build(tmp_path: Path) -> dict[str, Any]:
    plan = make_site_plan()
    pages = {
        "costs": make_page(plan, "costs", "## א\n\nטקסט.\n", status="draft"),
        "agencies": make_page(
            plan,
            "agencies",
            BODY,
            labels=("usa", "usa", "not-a-label"),
            related=("hospital", "clinics"),
            summary="סיכום קצר.",
            questions=["מה?"],
        ),
        "clinics": make_page(plan, "clinics", "טקסט.\n", status="stable"),
    }
    run_site(tmp_path, plan, pages)
    return json.loads((tmp_path / "docs" / INDEX_FILE).read_text(encoding="utf-8"))


def test_index_fields_follow_the_contract_table(tmp_path: Path) -> None:
    index = _build(tmp_path)
    plan = make_site_plan()
    assert index["index_version"] == section("mcp")["index_version"]
    assert list(index) == ["index_version", "generated_at", "disclaimer", "labels", "pages"]
    assert index["disclaimer"] == disclaimer_line()  # what get_page puts before every page body
    assert index["labels"] == {
        label_id: {"title": label["title_he"], "description": label["description"], "kind": label["kind"]}
        for label_id, label in plan["labels"].items()
    }
    assert [page["id"] for page in index["pages"]] == ["agencies", "costs", "clinics"]  # nav order
    agencies = index["pages"][0]
    assert list(agencies) == [
        "id", "url", "title", "description", "summary", "questions", "labels", "path", "aliases",
        "entities", "related", "out_links", "claims", "updated_at", "text",
    ]  # fmt: skip
    assert agencies["url"] == "agencies/"
    assert (agencies["title"], agencies["description"]) == ("כותרת agencies", "תיאור agencies")
    assert (agencies["summary"], agencies["questions"]) == ("סיכום קצר.", ["מה?"])
    assert agencies["labels"] == ["usa"]  # deduplicated, unknown label left out
    assert agencies["path"] == [{"id": "before", "title": "לפני"}, {"id": "choosing", "title": "בחירה"}]
    assert agencies["aliases"] == plan["pages"]["agencies"]["aliases"]
    assert agencies["entities"] == ["Entity1"]
    assert agencies["related"] == [{"id": "clinics", "relation": "see_also"}]  # published only
    assert agencies["out_links"] == ["clinics", "costs"]  # distinct, in order, no self, no code
    assert agencies["claims"]["total"] == 1
    assert agencies["updated_at"] == GENERATED_AT
    assert agencies["text"] == BODY  # before link resolution
    assert "status" not in agencies
    costs, clinics = index["pages"][1:]
    assert costs["status"] == "draft" and costs["questions"] == [] and "summary" not in costs
    assert "status" not in clinics  # "stable" is the default and never written


def test_index_matches_the_worker_schema(tmp_path: Path) -> None:
    assert schema_errors(_build(tmp_path), SCHEMA, SCHEMA) == []


def test_schema_check_accepts_the_worker_fixture_and_rejects_broken_pages() -> None:
    assert schema_errors(FIXTURE, SCHEMA, SCHEMA) == []
    page = FIXTURE["pages"][0]
    for patch in ({"url": "/colombia/"}, {"score": 1}, {"status": "stable"}, {"labels": ["a", "a"]}):
        broken = {**FIXTURE, "pages": [{**page, **patch}]}
        assert schema_errors(broken, SCHEMA, SCHEMA), patch
    without_text = {key: value for key, value in page.items() if key != "text"}
    assert schema_errors({**FIXTURE, "pages": [without_text]}, SCHEMA, SCHEMA)
    without_disclaimer = {key: value for key, value in FIXTURE.items() if key != "disclaimer"}
    assert schema_errors(without_disclaimer, SCHEMA, SCHEMA)


@pytest.mark.skipif(
    shutil.which("node") is None or not (WORKER / "node_modules").is_dir(),
    reason="needs node and `npm ci` in worker/",
)
def test_index_passes_the_workers_own_validator(tmp_path: Path) -> None:
    index_path = tmp_path / "docs" / INDEX_FILE
    _build(tmp_path)
    script = (
        'import { readFileSync } from "node:fs";'
        'import { CfWorkerJsonSchemaValidator } from "@modelcontextprotocol/server/validators/cf-worker";'
        'const schema = JSON.parse(readFileSync("wiki_index.schema.json", "utf8"));'
        "const index = JSON.parse(readFileSync(process.argv[1], 'utf8'));"
        "const result = new CfWorkerJsonSchemaValidator({ shortcircuit: false }).getValidator(schema)(index);"
        "console.log(JSON.stringify(result));"
    )
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(index_path)],
        cwd=WORKER,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(done.stdout)["valid"] is True, done.stdout


def test_generated_at_is_kept_while_nothing_else_changes(tmp_path: Path) -> None:
    index_path = tmp_path / "docs" / INDEX_FILE
    _build(tmp_path)
    stamped = json.loads(index_path.read_text(encoding="utf-8"))
    stamped["generated_at"] = "2020-01-01T00:00:00Z"
    index_path.write_text(json.dumps(stamped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert _build(tmp_path)["generated_at"] == "2020-01-01T00:00:00Z"
    stamped["pages"][0]["title"] = "ישן"
    index_path.write_text(json.dumps(stamped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert _build(tmp_path)["generated_at"] != "2020-01-01T00:00:00Z"
