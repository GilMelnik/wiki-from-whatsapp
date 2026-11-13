"""Task 52's split experiment: extract claims from the gold messages under six ways of cutting
or sending them, and compare the claims side by side (``units`` says how each variant
cuts, ``prompts`` what is sent, ``compare`` how claims are matched, ``page`` the owner's page).

PAID (the ``extract`` stage, step 3's model): about $2 at the batch price for all six
variants on the eight gold windows. The run refuses to start when its estimate is above
``extract.split_experiment.max_usd``. Answers go through the LLM disk cache, so a repeated run
costs nothing::

    .venv/bin/python -m step_3_extract.split_experiment.run --estimate   # no call: cost per variant
    .venv/bin/python -m step_3_extract.split_experiment.run              # the batch API
    .venv/bin/python -m step_3_extract.split_experiment.run --dry-run    # a stand-in model, no call

``--sync`` uses the sync API (waits less, costs double); ``--variants A,B`` and ``--windows 1,3``
limit a run (B is needed for the matching). Inputs, by default in the data dir: the gold file
with its context part (``--gold``), the chat export (``--chat``), the new split
(``--new-split``, step 1's ``threads.json``) and the old split (``--old-split``,
``threads.measure.baseline_file``); both splits must be splits of that chat.

Output in ``<data dir>/step_3_extract/<output_dir>/`` (a dry run writes into its
``<dry_run_dir>`` below it): ``units.json`` (message ids per unit), ``results.json`` (the claims:
PRIVATE), ``table.json`` (counts only), ``usage_ledger.json`` (the API's usage per request) and
the owner's page ``<page_file>`` (PRIVATE text: open it locally, never commit or share it).
"""

from __future__ import annotations

import argparse
import logging
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from step_1_threads_split.gold import load_gold
from step_1_threads_split.run import load_export_messages
from step_3_extract.claims import OutputError
from step_3_extract.prompt import EXTRACT_CONFIG, EXTRACT_SYSTEM, FRESH_SCHEMA, STANCES
from step_3_extract.split_experiment.compare import (
    VariantRun,
    against_reference,
    table,
    table_lines,
)
from step_3_extract.split_experiment.page import write_page
from step_3_extract.split_experiment.prompts import UnitJob
from step_3_extract.split_experiment.units import (
    EXPERIMENT_CONFIG,
    VARIANTS,
    MessageSet,
    Unit,
    build_units,
)
from step_3_extract.split_experiment.usage import UsageLedger, estimate, summed
from step_10_update.estimate import ESTIMATE_CONFIG
from utils.config import section
from utils.json_io import write_json_file
from utils.llm_client import BatchRequest, LLMClient
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import CHAT_ANDROID_PATH, GOLD_THREADS_PATH, STEP_1, STEP_3, THREADS, step_path
from utils.threads_io import load_threads

OUTPUT_DIR = step_path(STEP_3, EXPERIMENT_CONFIG["output_dir"])
OLD_SPLIT_PATH = step_path(STEP_1, section("threads")["measure"]["baseline_file"])
_NUMBERED = re.compile(r"^\[(\d+)\] ")


class CostCapError(ValueError):
    """The run's estimate is above ``max_usd``."""


class DryRunModel:
    """A stand-in for the model (``--dry-run``): no call, placeholder claims in English (one
    for every ``dry_run_claim_every``-th citable message), usage counted by characters."""

    provider = "dry-run"
    model = "dry-run"

    def __init__(self) -> None:
        self.usage: list[dict[str, Any]] = []

    def supports_batch(self) -> bool:
        return True

    def set_logger(self, logger: logging.Logger) -> None:
        del logger

    def complete_batch_json(self, requests: Sequence[BatchRequest]) -> dict[str, Any]:
        return {r.request_id: self._answer(r.request_id, str(r.system), str(r.user))
                for r in requests}

    def complete_json(self, system: str, user: str, task: str = "",
                      response_schema: dict[str, Any] | None = None) -> Any:
        del task, response_schema
        return self._answer(None, system, user)

    def _answer(self, request_id: str | None, system: str, user: str) -> dict[str, Any]:
        lines = user.split("\n")
        start = next(i for i, line in enumerate(lines) if line.startswith("Conversation"))
        numbers = [int(m.group(1)) for line in lines[start:] if (m := _NUMBERED.match(line))]
        claims = [{"n": k + 1, "text": f"Dry-run claim {k + 1} of this unit (message [{n}])",
                   "entities": [], "stance": STANCES[k % len(STANCES)], "supporting": [n],
                   "opposing": [], "counters": []}
                  for k, n in enumerate(numbers[::int(EXPERIMENT_CONFIG["dry_run_claim_every"])])]
        per_token = ESTIMATE_CONFIG["chars_per_token"]
        self.usage.append({"request_id": request_id,
                           "input_tokens": math.ceil(len(system + user) / per_token),
                           "output_tokens": math.ceil(len(str(claims)) / per_token),
                           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})
        return {"claims": claims}


class Experiment:
    """The variants' units, sent to ``llm`` (one request per unit), and what came back."""

    def __init__(self, units: Mapping[str, Sequence[Unit]], messages: MessageSet,
                 llm: LLMClient | DryRunModel, logger: logging.Logger, *, use_batch: bool,
                 out_dir: Path) -> None:
        self.messages = messages
        self.llm = llm
        self.logger = logger
        self.use_batch = use_batch and llm.supports_batch()
        self.out_dir = out_dir
        self.jobs = {variant: [UnitJob(unit, messages) for unit in variant_units]
                     for variant, variant_units in units.items()}
        self.ledger = UsageLedger(out_dir / "usage_ledger.json")

    def estimate(self) -> dict[str, dict[str, Any]]:
        """Per variant and ``total``: calls, tokens and dollars by characters; logged."""
        found = {variant: estimate(jobs, self.use_batch) for variant, jobs in self.jobs.items()}
        found["total"] = {key: round(sum(e[key] for e in found.values()), 4)
                          for key in ("calls", "input_tokens", "output_tokens", "usd")}
        for variant, row in found.items():
            self.logger.info(
                f"Estimate {variant}: {row['calls']} calls, {row['input_tokens']} input and "
                f"{row['output_tokens']} output tokens by characters, ${row['usd']:.2f}"
                f"{' (batch price)' if self.use_batch else ''}")
        return found

    def run(self, max_usd: float | None) -> dict[str, VariantRun]:
        """Ask the model for every unit (refused above ``max_usd``; ``None``: no cap)."""
        estimated = self.estimate()
        if max_usd is not None and estimated["total"]["usd"] > max_usd:
            raise CostCapError(
                f"the estimate ${estimated['total']['usd']:.2f} is above the cap ${max_usd:.2f} "
                "(extract.split_experiment.max_usd): limit the run with --variants / --windows")
        answers, usage = self._ask()
        runs = {variant: self._collect(variant, jobs, answers, usage)
                for variant, jobs in self.jobs.items()}
        for variant, variant_run in runs.items():
            variant_run.estimate = estimated[variant]
        self.ledger.save()
        return runs

    def _requests(self) -> list[tuple[BatchRequest, str]]:
        return [(BatchRequest(job.unit.unit_id, EXTRACT_SYSTEM, prompt, "extract", FRESH_SCHEMA),
                 self.ledger.key(self.llm.model, EXTRACT_SYSTEM, prompt))
                for jobs in self.jobs.values() for job in jobs for prompt in [job.prompt()]]

    def _ask(self) -> tuple[dict[str, Any], dict[str, dict[str, Any] | None]]:
        """unit id -> answer, and unit id -> its usage on record (``fresh`` when paid now)."""
        requests = self._requests()
        before = len(self.llm.usage)
        responses: dict[str, list[dict[str, Any]]] = {}
        if self.use_batch:
            self.logger.info(f"Split experiment: {len(requests)} requests via the batch API")
            answers = self.llm.complete_batch_json([request for request, _ in requests])
            for entry in self.llm.usage[before:]:
                responses.setdefault(entry["request_id"], []).append(entry)
        else:
            answers = {}
            for request, _ in requests:
                start = len(self.llm.usage)
                try:
                    answers[request.request_id] = self.llm.complete_json(
                        request.system, request.user, task=request.task,
                        response_schema=request.response_schema)
                # Provider errors have no common base class; one unit must not stop the run.
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(f"Split experiment: call failed for "
                                        f"{request.request_id}: {exc!r}")
                responses[request.request_id] = self.llm.usage[start:]
        usage: dict[str, dict[str, Any] | None] = {}
        for request, key in requests:
            paid = responses.get(request.request_id)
            if paid:
                self.ledger.add(key, paid, self.use_batch)
            recorded = self.ledger.get(key)
            usage[request.request_id] = None if recorded is None else {**recorded,
                                                                       "fresh": bool(paid)}
        return answers, usage

    def _collect(self, variant: str, jobs: Sequence[UnitJob], answers: Mapping[str, Any],
                 usage: Mapping[str, dict[str, Any] | None]) -> VariantRun:
        run = VariantRun(variant, [job.unit for job in jobs])
        paid = []
        for job in jobs:
            unit_id = job.unit.unit_id
            record = usage.get(unit_id)
            if record is None:
                run.unpriced_calls += unit_id in answers
            else:
                paid.append(record)
                run.usd += record["usd"]
                run.fresh_calls += record["fresh"]
                run.cached_calls += not record["fresh"]
            if unit_id not in answers:
                run.no_answer.append(unit_id)
                continue
            try:
                outcome = job.claims(answers[unit_id])
            except OutputError as exc:
                self.logger.warning(f"Split experiment: {unit_id}: {exc}")
                run.schema_failures.append(unit_id)
                continue
            run.claims += outcome.claims
            run.ungrounded += outcome.ungrounded
            run.dropped += outcome.dropped
            run.background_citing += outcome.background_citing
        run.usage = summed(paid)
        return run

    def save(self, runs: Mapping[str, VariantRun], metadata: dict[str, Any]) -> Path:
        """Write the result files and the owner's page; return the page's path."""
        set_ids = {wa_id for ids in self.messages.ids.values() for wa_id in ids}
        matches = against_reference(runs)
        columns = table(runs, set_ids, matches)
        for line in table_lines(columns):
            self.logger.info(line)
        write_json_file({"units": {v: [u.to_json() for u in r.units] for v, r in runs.items()},
                         "metadata": metadata}, self.out_dir / "units.json")
        write_json_file({"variants": {v: r.to_json() for v, r in runs.items()},
                         "matches": matches, "metadata": metadata},
                        self.out_dir / "results.json")
        write_json_file({"table": columns, "metadata": metadata}, self.out_dir / "table.json")
        page = self.out_dir / EXPERIMENT_CONFIG["page_file"]
        write_page(page, runs, self.messages, columns, matches, metadata)
        self.logger.info(f"Split experiment: the page is {page} (private: open it locally only)")
        return page


def _letters(value: str) -> list[str]:
    chosen = [v.strip().upper() for v in value.split(",") if v.strip()]
    unknown = [v for v in chosen if v not in VARIANTS]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown variants {unknown}; known: {list(VARIANTS)}")
    return [v for v in VARIANTS if v in chosen]


def _numbers(value: str) -> list[int]:
    try:
        return [int(v) - 1 for v in value.split(",") if v.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"windows are numbers from 1, e.g. 1,3: {exc}") from exc


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Task 52: extract claims from the gold "
                                     "messages cut five ways and compare them (PAID).")
    parser.add_argument("--estimate", action="store_true", help="estimate only: no call")
    parser.add_argument("--dry-run", action="store_true", help="a stand-in model, no call")
    parser.add_argument("--sync", action="store_true", help="the sync API instead of the batch API")
    parser.add_argument("--variants", type=_letters, default=list(VARIANTS),
                        help="e.g. A,B (default: all)")
    parser.add_argument("--windows", type=_numbers, default=None,
                        help="gold windows, numbered from 1, e.g. 1,3 (default: all)")
    parser.add_argument("--gold", type=Path, default=GOLD_THREADS_PATH)
    parser.add_argument("--chat", type=Path, default=CHAT_ANDROID_PATH)
    parser.add_argument("--new-split", type=Path, default=THREADS.original)
    parser.add_argument("--old-split", type=Path, default=OLD_SPLIT_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logger = setup_step_logging(STEP_3)
    messages = MessageSet(load_gold(args.gold), load_export_messages(args.chat), args.windows)
    units = build_units(messages, load_threads(args.new_split)["threads"],
                        load_threads(args.old_split)["threads"], args.variants)
    logger.info(f"Split experiment: {messages.size()} messages in {len(messages.windows)} "
                f"windows; units {', '.join(f'{v} {len(u)}' for v, u in units.items())}")
    llm = DryRunModel() if args.dry_run else LLMClient.for_stage(
        "extract", logger=logger, max_tokens=EXTRACT_CONFIG["max_output_tokens"])
    out_dir = OUTPUT_DIR / EXPERIMENT_CONFIG["dry_run_dir"] if args.dry_run else OUTPUT_DIR
    experiment = Experiment(units, messages, llm, logger, use_batch=not args.sync,
                            out_dir=out_dir)
    if args.estimate:
        experiment.estimate()
        return
    metadata = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                "git_commit": git_commit(), "provider": llm.provider, "model": llm.model,
                "batch": experiment.use_batch, "dry_run": args.dry_run,
                "windows": [w + 1 for w in messages.windows], "variants": list(units),
                "inputs": {name: str(getattr(args, name))
                           for name in ("gold", "chat", "new_split", "old_split")},
                "config": EXPERIMENT_CONFIG}
    try:
        runs = experiment.run(None if args.dry_run else float(EXPERIMENT_CONFIG["max_usd"]))
    except CostCapError as exc:
        raise SystemExit(f"Split experiment refused: {exc}") from exc
    experiment.save(runs, metadata)


if __name__ == "__main__":
    main()
