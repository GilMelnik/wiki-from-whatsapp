"""Synthetic step-9 inputs: a small C7 plan and C9 pages built through ``utils.okf``.

The plan is ``plan_fixtures.make_plan`` (phases ``before`` / ``during``, categories
``choosing`` / ``medical``, pages agencies, costs, clinics, hospital, country labels usa and
georgia) plus an empty category and a category whose only page is never written. The claim
records (C6, for the references) are ``claim_fixtures.claim_records(4)`` unless a test passes its
own; ``IDS[n]`` (``pages_fixtures.IDS``) is the short id of record ``n``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from step_6_plan import operations
from step_6_plan.model import find_node
from step_5_same_complaint.claim_records import ClaimSources, Record
from step_11_site.entity_tags import write_decisions
from step_11_site.run import run
from tests.claim_fixtures import claim_records, write_claim_inputs
from tests.pages_fixtures import IDS
from tests.plan_fixtures import make_plan
from utils.config import PROJECT_ROOT, section
from utils.okf import build_front_matter

GENERATED_AT = "2026-11-01T10:00:00Z"
FIRST_MESSAGE, LAST_MESSAGE = "2023-01-15T09:30:00", "2024-06-20T21:05:00"
SOURCE_RESOURCE_PREFIX = section("pages")["source_resource_prefix"]


def make_site_plan() -> dict[str, Any]:
    plan = make_plan()
    plan = operations.add_node(plan, "empty", title_he="ריק", parent_id="before").plan
    plan = operations.add_node(plan, "later", title_he="מאוחר", parent_id="during").plan
    return operations.add_page(
        plan, "later-page", parent_id="later", title_he="עמוד מאוחר", description="ת", scope="ה"
    ).plan


def make_page(
    plan: dict[str, Any],
    page_id: str,
    body: str,
    *,
    labels: tuple[str, ...] = (),
    related: tuple[str, ...] = (),
    **optional: Any,
) -> dict[str, Any]:
    """A C9 page for ``page_id``; ``optional`` passes summary / questions / status through."""
    page = plan["pages"][page_id]
    front_matter = build_front_matter(
        title=page["title_he"],
        description=page["description"],
        tags=[],
        generated_by="wiki-from-whatsapp/test-model",
        generated_at=GENERATED_AT,
        sources=[{"id": IDS[1], "resource": f"{SOURCE_RESOURCE_PREFIX}{IDS[1]}", "usage_count": 2}],
        aliases=page["aliases"],
        entities=["Entity1"],
        claims={
            "total": 1,
            "supporting": 2,
            "opposing": 0,
            "first_claim_at": "2024-01-01",
            "last_claim_at": "2025-01-01",
        },
        related=[{"slug": slug, "relation": "see_also"} for slug in related],
        path=[{"id": node_id, "title": find_node(plan, node_id)[0]["title_he"]} for node_id in page["path"]],
        labels=[{"id": label_id, "kind": "country"} for label_id in labels],
        **optional,
    )
    return {"front_matter": front_matter, "body_md": body, "input_fingerprint": f"fp-{page_id}"}


def make_threads(*moments: str) -> dict[str, Any]:
    """A C1 threads payload with one message at each of ``moments`` (the first in a thread of its
    own, the others in a second thread, out of order on purpose)."""
    messages = [{"id": f"m{n}", "datetime": moment, "sender": "s", "content": "x"}
                for n, moment in enumerate(moments)]
    return {"threads": [{"thread_id": "t1", "messages": messages[:1]},
                        {"thread_id": "t2", "messages": messages[:0:-1]}], "metadata": {}}


def run_site(
    tmp_path: Path,
    plan: dict[str, Any],
    pages: dict[str, dict[str, Any]],
    claims: list[Record] | None = None,
    threads: dict[str, Any] | None = None,
    sources: ClaimSources | None = None,
    decisions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write the inputs under ``tmp_path`` and build the site into ``tmp_path/docs``; the
    messages run from ``FIRST_MESSAGE`` to ``LAST_MESSAGE`` unless ``threads`` says otherwise.
    ``sources``: claim inputs already written (then ``claims`` is not used); ``decisions``:
    the owner's entity tag decisions (none by default)."""
    plan_path, pages_path = tmp_path / "plan.json", tmp_path / "wiki_pages.json"
    threads_path = tmp_path / "threads.json"
    chat = make_threads(FIRST_MESSAGE, "2023-08-01T12:00:00", LAST_MESSAGE) if threads is None else threads
    threads_path.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    pages_path.write_text(json.dumps({"pages": pages, "metadata": {}}, ensure_ascii=False), encoding="utf-8")
    if sources is None:
        sources = write_claim_inputs(claim_records(4) if claims is None else claims, tmp_path)
    decisions_path = tmp_path / "entity_tag_decisions.json"
    if decisions is not None:
        write_decisions(decisions, decisions_path)
    return run(
        plan_path=plan_path,
        pages_path=pages_path,
        sources=sources,
        threads_path=threads_path,
        docs_dir=tmp_path / "docs",
        mkdocs_path=tmp_path / "mkdocs.yml",
        decisions_path=decisions_path,
    )


def issues_of(result: dict[str, Any], kind: str) -> list[tuple[str, str]]:
    return [(issue["page"], issue["target"]) for issue in result["issues"] if issue["issue"] == kind]


def build_html(
    tmp_path: Path, plan: dict[str, Any], pages: dict[str, dict[str, Any]], claims: list[Record] | None = None
) -> None:
    """Step 9 into ``tmp_path/docs``, then ``mkdocs build --strict`` into ``tmp_path/out``."""
    shutil.copytree(PROJECT_ROOT / "docs" / "stylesheets", tmp_path / "docs" / "stylesheets")
    run_site(tmp_path, plan, pages, claims)
    command = [sys.executable, "-m", "mkdocs", "build", "--strict", "-f", str(tmp_path / "mkdocs.yml")]
    built = subprocess.run([*command, "-d", str(tmp_path / "out")], capture_output=True, text=True, check=False)
    assert built.returncode == 0, built.stderr
