"""Step 6: let the planning model propose the wiki's structure from the claim groups (contract C7).

How it works, in plain words: the model configured for stage ``plan.stage`` (Opus 5.5) reads
every claim group of the effective step-5 file, one compact line each (or, when they do not fit
``plan.max_input_tokens``, a sample of them: ``step_6_plan.fit``), and proposes a tree of
content: an abstraction layer on top (for example the phases of the process), categories
below it, and pages as the leaves (sized by ``plan.page_groups``, with ``plan.max_pages``
only as a safety ceiling), each with a scope that says
what belongs on it and what does not, plus labels that cut across pages (for example by
country). The answer is validated (``step_6_plan.model``); on failure the model gets ONE retry
with the errors. A coverage check (``step_6_plan.coverage``) then asks the cheap classifier to
place a fixed sample of groups using only titles and scopes; if it finds real problems, the
model gets ONE revision call, kept only when it does not lower the confident share. At most
``plan.max_model_calls`` model calls per run. The prompt's token count is checked with the
counting endpoint before each call: a prompt that would not fit the context window fails with a
clear message instead of being truncated.

A plan is curated, not derived: the owner edits it (``step_6_plan.operations``, the plan
review tool). So a normal run on an existing plan makes NO model call and writes nothing: it
validates the plan and reports how many groups are newer than it (task 24 assigns them). Only
``run(rebuild=True)`` / ``--rebuild`` plans again, after backing up the edited plan.

    python -m step_6_plan.run [--rebuild]

Output: ``wiki_plan.json`` == ``wiki_plan_edited.json`` after a planning run (C7, see
``step_6_plan.model``) and ``coverage_report.json`` (the coverage numbers of that run).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from step_6_plan.coverage import COVERAGE_CONFIG, coverage_check, findings, named_group_ids
from step_6_plan.fit import fitted_prompt
from step_6_plan.model import PLAN_CONFIG, PlanError, validation_errors
from step_6_plan.prompt import (
    PROPOSAL_SCHEMA,
    initial_request,
    plan_from_proposal,
    retry_request,
    revision_request,
)
from utils.config import LLM_CONFIG
from utils.json_io import write_json_file
from utils.llm_client import Classifier, LLMClient
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import PLAN, PLAN_COVERAGE_PATH, STEP_6, Artifact, resolve, resolve_aggregated_path
from utils.reconcile import guarded_write

TokenCounter = Callable[[str, str], int]


class AnthropicTokenCounter:
    """Counts a request's input tokens with the Messages API ``count_tokens`` endpoint (free)."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.client = anthropic.Anthropic()

    def __call__(self, system: str, user: str) -> int:
        return self.client.messages.count_tokens(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
        ).input_tokens


class Planner:
    """The model calls of one planning run over a fixed list of groups (the prompt lists all of
    them or a sample that fits ``plan.max_input_tokens``)."""

    def __init__(
        self,
        groups: Sequence[dict[str, Any]],
        llm: LLMClient,
        count_tokens: TokenCounter,
        logger: logging.Logger,
    ) -> None:
        self.groups = groups
        self.group_ids = {group["group_id"] for group in groups}
        self.llm = llm
        self.count_tokens = count_tokens
        self.logger = logger
        self.prompt = fitted_prompt(groups)
        self.shown_count = len(self.prompt.shown)
        self.input_tokens: list[int] = []
        logger.info(
            f"Plan prompt: {self.shown_count} of {len(groups)} groups listed; about "
            f"{self.prompt.estimated_tokens} input tokens by characters (plan.max_input_tokens "
            f"{PLAN_CONFIG['max_input_tokens']})"
        )

    @property
    def calls_left(self) -> int:
        return PLAN_CONFIG["max_model_calls"] - len(self.input_tokens)

    def _ask(self, user: str, task: str) -> dict[str, Any]:
        if self.calls_left <= 0:
            raise RuntimeError(f"plan.max_model_calls ({PLAN_CONFIG['max_model_calls']}) used up")
        user = self.prompt.user(user)
        tokens = self.count_tokens(self.prompt.system, user)
        budget = PLAN_CONFIG["context_window_tokens"] - PLAN_CONFIG["max_output_tokens"]
        if tokens > budget:
            raise ValueError(
                f"The planning prompt has {tokens} input tokens ({self.shown_count} of "
                f"{len(self.groups)} groups listed); with plan.max_output_tokens="
                f"{PLAN_CONFIG['max_output_tokens']} it does not fit plan.context_window_tokens="
                f"{PLAN_CONFIG['context_window_tokens']}. Nothing was sent. The list was fitted "
                f"to plan.max_input_tokens={PLAN_CONFIG['max_input_tokens']} by characters "
                f"(plan.chars_per_token={PLAN_CONFIG['chars_per_token']}): lower one of them."
            )
        self.input_tokens.append(tokens)
        self.logger.info(f"Plan {task}: {tokens} input tokens; calling {self.llm.model}")
        return self.llm.complete_json(
            self.prompt.system, user, task=task, response_schema=PROPOSAL_SCHEMA
        )

    def propose(self) -> dict[str, Any]:
        """A fresh, valid plan (one retry with the validation errors), else ``PlanError``."""

        raw = self._ask(initial_request(self.shown_count, len(self.groups)), "plan")
        plan, errors = plan_from_proposal(raw, self.group_ids)
        if errors and self.calls_left > 0:
            self.logger.warning(f"Plan failed validation ({len(errors)} problems); retrying: {errors}")
            raw = self._ask(
                retry_request(self.shown_count, len(self.groups), raw, errors), "plan-retry"
            )
            plan, errors = plan_from_proposal(raw, self.group_ids)
        if errors:
            raise PlanError(["the planning model's answer failed validation twice", *errors])
        return plan

    def revise(self, plan: dict[str, Any], report: dict[str, Any]) -> dict[str, Any] | None:
        """One revision call fed with the coverage report's findings (and the lines of the
        groups they name that the list does not show); ``None`` if no call is left or the
        revised plan is invalid (the caller keeps the plan it has)."""

        if self.calls_left <= 0:
            self.logger.info("Plan revision skipped: no model call left")
            return None
        named = set(named_group_ids(report)) - {group["group_id"] for group in self.prompt.shown}
        unlisted = [group for group in self.groups if group["group_id"] in named]
        request = revision_request(plan, report["sample_size"], report["findings"], unlisted)
        raw = self._ask(request, "plan-revise")
        revised, errors = plan_from_proposal(raw, self.group_ids)
        if errors:
            self.logger.warning(f"Revised plan is invalid; keeping the first plan: {errors}")
            return None
        return revised


def _coverage_line(report: dict[str, Any]) -> str:
    return (
        f"confident {report['confident_share']:.0%}, none {report['none_share']:.0%}, "
        f"unsure {report['unsure_share']:.0%} of {report['sample_size']}; "
        f"small pages {report['small_pages']}, large pages {report['large_pages']}"
    )


def _plan_and_check(
    planner: Planner, groups: Sequence[dict[str, Any]], classifier: Classifier
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Propose, check coverage and revise at most once; returns ``(plan, coverage report)``."""

    plan = planner.propose()
    report = coverage_check(plan, groups, classifier)
    report["findings"] = findings(report, plan)
    planner.logger.info(f"Coverage: {_coverage_line(report)}")
    if not (report["findings"] and COVERAGE_CONFIG["revise"]):
        return plan, report
    revised = planner.revise(plan, report)
    if revised is None:
        return plan, report
    revised_report = coverage_check(revised, groups, classifier)
    revised_report["findings"] = findings(revised_report, revised)
    planner.logger.info(f"Coverage after revision: {_coverage_line(revised_report)}")
    if revised_report["confident_share"] < report["confident_share"]:
        planner.logger.warning("The revision lowered the confident share; keeping the first plan")
        return plan, {**report, "revision": {"kept": False, "revised_plan": revised,
                                             "revised_report": revised_report}}
    return revised, {**revised_report, "revision": {"kept": True, "first_plan": plan,
                                                    "first_report": report}}


def run(
    *,
    aggregated_path: Path | None = None,
    artifact: Artifact = PLAN,
    rebuild: bool = False,
    llm: LLMClient | None = None,
    classifier: Classifier | None = None,
    count_tokens: TokenCounter | None = None,
) -> dict[str, Any]:
    """Plan the wiki if there is no plan yet (or on ``rebuild``); otherwise only check it.

    ``aggregated_path`` defaults to the effective (edited) step-5 file. ``llm``,
    ``classifier`` and ``count_tokens`` default to the configured stages. Returns
    ``written``, ``model_calls``, ``page_count``, ``label_count``, ``plan_path`` and, after
    a planning run, ``input_tokens``, ``coverage`` (the report) and ``backup``; after a check
    run, ``unplanned_groups`` (groups newer than the plan).
    """

    logger = setup_step_logging(STEP_6)
    source = aggregated_path or resolve_aggregated_path()
    groups = json.loads(source.read_text(encoding="utf-8"))["groups"]
    current = resolve(artifact)
    if current.is_file() and not rebuild:
        plan = json.loads(current.read_text(encoding="utf-8"))
        errors = validation_errors(plan)
        if errors:
            raise PlanError([f"{current} is not a valid C7 plan; fix it in the plan review "
                             "tool, or plan again with --rebuild (backs it up first)", *errors])
        planned = set(plan["metadata"].get("planned_group_ids") or [])
        unplanned = sum(1 for group in groups if group["group_id"] not in planned)
        logger.info(
            f"Plan {current}: {len(plan['pages'])} pages, {len(plan['labels'])} labels; kept "
            f"as is (curated; --rebuild to plan again). {unplanned} of {len(groups)} groups "
            "are newer than the plan."
        )
        return {"written": False, "model_calls": 0, "page_count": len(plan["pages"]),
                "label_count": len(plan["labels"]), "unplanned_groups": unplanned,
                "plan_path": str(current)}

    llm = llm or LLMClient.for_stage(
        PLAN_CONFIG["stage"], max_tokens=PLAN_CONFIG["max_output_tokens"], logger=logger
    )
    classifier = classifier or Classifier.for_stage(COVERAGE_CONFIG["stage"], logger=logger)
    planner = Planner(groups, llm, count_tokens or AnthropicTokenCounter(llm.model), logger)
    plan, report = _plan_and_check(planner, groups, classifier)
    plan["metadata"] = {
        **plan["metadata"],
        "source": str(source),
        "stage": PLAN_CONFIG["stage"],
        "prompt_version": PLAN_CONFIG["prompt_version"],
        "model": llm.model,
        "effort": LLM_CONFIG["stages"][PLAN_CONFIG["stage"]].get("thinking_param"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": PLAN_CONFIG,
        "group_count": len(groups),
        "shown_group_count": planner.shown_count,
        "sample_seed": PLAN_CONFIG["sample_seed"],
        "planned_group_ids": sorted(planner.group_ids),
        "model_calls": len(planner.input_tokens),
        "input_tokens": planner.input_tokens,
        "retired_ids": [],
    }
    backup = guarded_write(artifact.edited, plan, backup=True)
    guarded_write(artifact.original, plan, backup=rebuild)
    run_meta = {key: plan["metadata"][key] for key in ("generated_at", "git_commit", "model")}
    write_json_file({**report, "metadata": run_meta}, PLAN_COVERAGE_PATH)
    logger.info(
        f"Plan written: {len(plan['pages'])} pages, {len(plan['labels'])} labels, "
        f"{len(planner.input_tokens)} model calls (input tokens {planner.input_tokens}); "
        f"coverage {_coverage_line(report)}; backup {backup}"
    )
    return {"written": True, "model_calls": len(planner.input_tokens),
            "page_count": len(plan["pages"]), "label_count": len(plan["labels"]),
            "plan_path": str(artifact.edited), "input_tokens": planner.input_tokens,
            "coverage": report, "backup": str(backup) if backup else None}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 6: plan the wiki structure (C7).")
    parser.add_argument("--rebuild", action="store_true",
                        help="plan again with the model (backs up the edited plan first)")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild)


if __name__ == "__main__":
    main()
