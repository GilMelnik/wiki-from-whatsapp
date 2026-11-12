"""An update whose new messages yield no claim, no group, no plan change and no page change still
rebuilds the site, and the home page's sentence moves to the month of the newest message.

The driver walks every step it has not run in this update (it never skips a step because the
one before it changed nothing); this runs that walk on the synthetic pipeline of
``tests.update_support`` with the real step 9 as the last step. No API call, no real data."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from review_tool.steps import Step
from step_9_site.run import run as build_site
from tests.site_fixtures import make_page, make_site_plan, make_threads
from tests.update_support import PAID_STEPS, World, message, old_chat
from utils.config import section
from utils.paths import STEP_1, STEP_2, STEP_3, STEP_4, STEP_5, STEP_6_ASSIGN, STEP_7, STEP_8, STEP_9

SITE = section("site")
UNCHANGED = (STEP_2, STEP_3, STEP_4, STEP_5, STEP_6_ASSIGN, STEP_7, STEP_8)


class SiteWorld(World):
    """The synthetic pipeline whose step 1 writes a C1 threads file from the pipeline input and
    whose step 9 is the real one, reading what the fake steps left under the temp folder."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.site_dir = root / "site"
        self.plan = make_site_plan()
        pages = {"clinics": make_page(self.plan, "clinics", "טקסט.\n")}
        self.write_json(self.site_dir / "plan.json", self.plan)
        self.write_json(self.site_dir / "pages.json", {"pages": pages, "metadata": {}})
        self.write_json(self.site_dir / "groups.json", {"groups": [], "metadata": {}})
        for key in UNCHANGED:  # outputs the fake steps will rewrite with the same content
            self.write_json(self.node_for(key).original, {"items": ["same"], "metadata": {}})
        self.unchanged = set(UNCHANGED)
        self.write_threads()
        self.build()

    def node_for(self, step_key: str) -> Any:
        return self.node(next(step.spec.produces for step in self.steps if step.key == step_key))

    def write_threads(self) -> None:
        """Step 1's output: one message per message of the pipeline input, at its date."""
        moments = [datetime.strptime(f"{m['date']} {m['time']}", "%d/%m/%Y %H:%M").isoformat()
                   for m in json.loads(self.paths.input.read_text(encoding="utf-8"))]
        self.write_json(self.node("threads").original, make_threads(*moments))

    def build(self) -> dict[str, Any]:
        return build_site(
            plan_path=self.site_dir / "plan.json", pages_path=self.site_dir / "pages.json",
            groups_path=self.site_dir / "groups.json", threads_path=self.node("threads").original,
            docs_dir=self.site_dir / "docs", mkdocs_path=self.site_dir / "mkdocs.yml")

    def home_sentence(self) -> str:
        return (self.site_dir / "docs" / "index.md").read_text(encoding="utf-8").split("\n\n")[1].split(" [")[0]

    def runner(self, step: Step) -> Any:
        if step.key == STEP_1:
            def run_step_1() -> dict[str, Any]:
                self.calls.append(step.key)
                self.write_threads()
                return {"written": True}
            return run_step_1
        if step.key == STEP_9:
            def run_step_9() -> dict[str, Any]:
                self.calls.append(step.key)
                return self.build()
            return run_step_9
        return super().runner(step)


@pytest.fixture
def world(tmp_path: Path) -> SiteWorld:
    return SiteWorld(tmp_path)


def test_new_messages_alone_move_the_end_month_of_the_home_page(world: SiteWorld) -> None:
    assert world.home_sentence() == SITE["home"]["intro"].format(first="מרץ 2026", last="מרץ 2026")
    assert len(old_chat()) == 30  # the existing input is all in March 2026
    april = {**message("n1", 0), "date": "15/04/2026"}
    outcome = world.run(world.export([*old_chat(), april]), pay=PAID_STEPS, no_stops=True)
    assert outcome.status == "completed", outcome.message
    assert world.calls == [STEP_1, *UNCHANGED, STEP_9]
    # no step of the claim side changed anything: the report says so, and the site step still ran
    report = json.loads(next(world.paths.updates_dir.glob("*_report.json")).read_text(encoding="utf-8"))
    changed = {event["step"]: event["changed"] for event in report["events"] if event["kind"] == "run"}
    assert [step for step in UNCHANGED if changed[step]] == []
    assert world.home_sentence() == SITE["home"]["intro"].format(first="מרץ 2026", last="אפריל 2026")
