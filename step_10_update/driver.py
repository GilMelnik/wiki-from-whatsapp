"""The update driver: takes a newer chat export through the pipeline, stopping for people and
money. The command line is ``step_10_update.run``; this module holds the logic.

One update, start to end:

1. ``begin``: compare the export with the current pipeline input (``exports``); refuse one
   that does not continue it; with nothing new or changed, do nothing at all. Otherwise copy
   the export to ``updates/<stamp>_chat_android.json`` (unless it is already there), write
   ``<stamp>_new_messages.json`` and the report, and record the update in the state.
2. ``walk``: the registry's steps in order (``review_tool.steps``; the curated plan step is
   never run). Before a step runs, every step it depends on that already ran in this update
   and whose output is stale again (``review_tool.artifacts``: e.g. the plan edited by an
   applied suggestion) runs again first. Before every step: the review server must be idle
   (``server``) and a paid step needs ``--pay <step>`` unless its estimate makes no paid
   call. The step runs through its own ``run()`` under the lease (``utils.step_lease``), so
   the server refuses jobs and tool saves to the file it rewrites.
3. The pipeline input is replaced right before step 1 runs, after a copy of the old input is
   kept as ``updates/<stamp>_previous_chat_android.json``; if step 1 fails the old input is
   put back. Messages the export lacks stay in the input unless ``--drop-missing``.
4. After a step with a review tool whose output changed, the update stops (unless
   ``--no-stops`` or ``--through``); running the command again means "reviewed, go on". A
   finished step is never redone, except as in 2.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from review_tool.artifacts import ArtifactGraph
from review_tool.steps import Step
from step_10_update.estimate import Estimate, embedding_usd
from step_10_update.exports import (
    ExportDiff,
    NotAContinuation,
    check_continuation,
    compare_exports,
    file_fingerprint,
    load_export,
    merged_input,
    message_time,
)
from step_10_update.record import UpdateReport, scalar_summary, update_log
from step_10_update.state import BUSY, PAID, REVIEW, THROUGH, UpdateState
from utils.config import section
from utils.json_io import write_json_file
from utils.paths import (
    CHAT_ANDROID_PATH,
    INPUT_CONFIG,
    NEW_MESSAGES_SUFFIX,
    UPDATE_LEASE_PATH,
    UPDATE_LOG_SUFFIX,
    UPDATE_REPORT_SUFFIX,
    UPDATE_STATE_PATH,
    UPDATES_DIR,
)
from utils.reconcile import fingerprint
from utils.split_chat_export import newest_export
from utils.step_lease import LeaseHeld, StepLease

UPDATE_CONFIG = section("update")
COMMAND = "python -m step_10_update.run"
PRIVATE_NOTE = "PRIVATE - chat messages. Do not publish."
NOT_STARTED = "(not started)"
COMPLETED, STOPPED, FAILED = "completed", "stopped", "failed"
NOTHING, REFUSED = "nothing", "refused"
EXIT_CODES = {COMPLETED: 0, STOPPED: 0, NOTHING: 0, FAILED: 1, REFUSED: 2}


@dataclass(frozen=True)
class UpdateFiles:
    export: Path
    new_messages: Path
    report: Path
    log: Path
    previous_input: Path


@dataclass(frozen=True)
class UpdatePaths:
    input: Path = CHAT_ANDROID_PATH
    updates_dir: Path = UPDATES_DIR
    state: Path = UPDATE_STATE_PATH
    lease: Path = UPDATE_LEASE_PATH

    def files(self, stamp: str) -> UpdateFiles:
        name = INPUT_CONFIG["export_filename"]
        return UpdateFiles(
            export=self.updates_dir / f"{stamp}_{name}",
            new_messages=self.updates_dir / f"{stamp}_{NEW_MESSAGES_SUFFIX}",
            report=self.updates_dir / f"{stamp}_{UPDATE_REPORT_SUFFIX}",
            log=self.updates_dir / f"{stamp}_{UPDATE_LOG_SUFFIX}",
            previous_input=self.updates_dir / f"{stamp}_previous_{name}",
        )

    def stamp_of(self, export: Path) -> str | None:
        """The stamp of an export already in the updates folder, else ``None``."""
        suffix = f"_{INPUT_CONFIG['export_filename']}"
        if export.resolve().parent == self.updates_dir.resolve() and export.name.endswith(suffix):
            return export.name[: -len(suffix)]
        return None


@dataclass(frozen=True)
class Options:
    pay: frozenset[str] = frozenset()
    through: str | None = None
    no_stops: bool = False
    drop_missing: bool = False


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]


class StepFailed(RuntimeError):
    """A step raised; the state records it and the next run retries the step."""


class UpdateDriver:
    def __init__(
        self,
        steps: Sequence[Step],
        graph: ArtifactGraph,
        *,
        estimator: Callable[[Step], Estimate | None],
        server_check: Callable[[], str | None],
        paths: UpdatePaths = UpdatePaths(),
        runners: Mapping[str, Callable[[], dict[str, Any]]] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.graph = graph
        self.steps = [step for step in steps if not graph.node(step.spec.produces).curated]
        self.paths = paths
        self.estimator = estimator
        self.server_check = server_check
        self.runners = runners or {}
        self.logger = logger or logging.getLogger(__name__)
        self.lease = StepLease(paths.lease)
        self.state = UpdateState.load(paths.state)
        self.input_key = next(
            (node.key for node in graph.nodes if node.original == paths.input), None
        )
        if self.input_key is None:
            raise ValueError(f"no artifact of the graph is the pipeline input {paths.input}")

    # ------------------------------------------------------------------ entry points

    def run(self, export: Path | None, options: Options) -> Outcome:
        """Start an update with ``export`` (default: the newest in the updates folder) or go on
        with the one in progress."""
        if options.through is not None and options.through not in {s.key for s in self.steps}:
            return self._said(Outcome(REFUSED, f"--through {options.through}: not a step an "
                                               "update runs (the plan is never re-planned)."))
        try:
            with self.lease.hold():
                outcome = self._open(export, options, dry=False)
                if outcome is not None:
                    return self._said(outcome)
                files = self.paths.files(self.state.current()["stamp"])
                with update_log(files.log):
                    return self._said(self._walk(options, UpdateReport(files.report)))
        except LeaseHeld as exc:
            return self._said(Outcome(REFUSED, f"Not started: {exc}."))

    def plan(self, export: Path | None, options: Options) -> Outcome:
        """Say what the update would do and estimate the next step; change nothing."""
        outcome = self._open(export, options, dry=True)
        if outcome is not None:
            return self._said(outcome)
        update = self.state.current()
        pending = [step for step in self.steps if not self.state.done(step.key)]
        stopped = (update["stopped"] or {}).get("message")
        self.logger.info(f"Update {update['stamp']}: {update['counts']}; done: "
                         f"{list(update['steps'])}; stopped: {stopped}")
        for index, step in enumerate(pending):
            flags = ["paid"] if step.spec.paid else []
            flags += [f"review: {step.spec.review_tool}"] if step.spec.review_tool else []
            self.logger.info(f"  to run: {step.spec.title} ({step.key}) {flags}")
            if index == 0 and update["stamp"] != NOT_STARTED:
                self._plan_first(step)
        return self._said(Outcome(NOTHING, f"Plan only: nothing was changed "
                                           f"({len(pending)} steps to go)."))

    # ------------------------------------------------------------------ opening an update

    def _open(self, export: Path | None, options: Options, *, dry: bool) -> Outcome | None:
        """Make sure an update is in progress (``None``), or say why there is nothing to do."""
        if not self.state.initialised:
            if not self.paths.input.is_file():
                return Outcome(REFUSED, f"No pipeline input yet ({self.paths.input}): build the "
                                        "first wiki before updating it.")
            messages = load_export(self.paths.input)
            # Saved with the first update that starts (a refusal leaves no state behind).
            self.state.record_input(self.paths.input, max(map(message_time, messages)),
                                    len(messages))
            self.logger.info(f"First update: the state starts from the current input "
                             f"({len(messages)} messages)")
        if self.state.in_progress:
            update = self.state.current()
            if export is not None and file_fingerprint(export) != update["export_sha1"]:
                return Outcome(REFUSED, f"Update {update['stamp']} is still in progress; finish it "
                                        f"(`{COMMAND}`) before starting one with another export.")
            return None
        if export is None:
            try:
                export = newest_export(self.paths.updates_dir, INPUT_CONFIG["export_filename"])
            except FileNotFoundError:
                return Outcome(NOTHING, f"No update in progress and no export given or found in "
                                        f"{self.paths.updates_dir}.")
        return self._begin(export, options, dry=dry)

    def _begin(self, export: Path, options: Options, *, dry: bool) -> Outcome | None:
        old, new = load_export(self.paths.input), load_export(export)
        diff = compare_exports(old, new)
        try:
            check_continuation(diff)
        except NotAContinuation as exc:
            return Outcome(REFUSED, f"Refused {export}: {exc}")
        self._describe(diff)
        if diff.nothing_new and not (options.drop_missing and diff.missing):
            return Outcome(NOTHING, "Nothing new: the pipeline input already holds every message "
                                    "of this export. Nothing was changed.")
        if dry:
            by_id = {str(m["id"]): m for m in new}
            texts = _texts([by_id[i] for i in (*diff.new, *diff.changed)])
            self.logger.info(f"Step 1 would embed up to {len(texts)} messages: about "
                             f"${embedding_usd(texts):.3f} (rough; cached vectors are free)")
            self.state.data["update"] = {"stamp": NOT_STARTED, "counts": diff.counts(),
                                         "steps": {}, "stopped": None}
            return None
        kept_stamp = self.paths.stamp_of(export)
        stamp = kept_stamp or datetime.now().strftime(UPDATE_CONFIG["stamp_format"])
        files = self.paths.files(stamp)
        if kept_stamp is None:
            files.export.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(export, files.export)
        by_id = {str(m["id"]): m for m in new}
        write_json_file({
            "metadata": {"warning": PRIVATE_NOTE, "export": str(files.export),
                         "previous_input": str(self.paths.input), **diff.counts()},
            "new": [by_id[i] for i in diff.new],
            "changed": [by_id[i] for i in diff.changed],
            "missing_ids": list(diff.missing),
        }, files.new_messages)
        record = {"stamp": stamp, "export": str(files.export),
                  "export_sha1": file_fingerprint(files.export),
                  "new_messages": str(files.new_messages), "report": str(files.report),
                  "log": str(files.log), "counts": diff.counts(),
                  "drop_missing": options.drop_missing}
        self.state.begin(record)
        self.state.save()
        UpdateReport(files.report).create({**record, "export_last": diff.new_last.isoformat()})
        self.logger.info(f"Update {stamp} started: {files.export.name}")
        return None

    def _describe(self, diff: ExportDiff) -> None:
        self.logger.info(
            f"New export: {len(diff.new)} new messages, {len(diff.changed)} changed (text or "
            f"reactions); it runs to {diff.new_last:%Y-%m-%d %H:%M}, the input to "
            f"{diff.old_last:%Y-%m-%d %H:%M}"
        )
        if diff.missing:
            self.logger.warning(
                f"{len(diff.missing)} messages of the current input are not in this export. "
                "They are kept (exports can be partial); --drop-missing removes them."
            )

    def _plan_first(self, step: Step) -> None:
        """The estimate of the next step of an update in progress (its inputs are current)."""
        if step.spec.paid:
            estimate = self.estimator(step)
            self.logger.info(f"    estimate: {estimate.line() if estimate else 'none available'}")

    # ------------------------------------------------------------------ the walk

    def _walk(self, options: Options, report: UpdateReport) -> Outcome:
        stopped = self.state.take_stop()
        if stopped is not None and stopped["kind"] == REVIEW:
            self.state.pass_gate(stopped["step"])
            self.logger.info(f"Going on after the review of {stopped['step']}")
        self.state.clear_failure()
        self.state.save()
        order = [step.key for step in self.steps]
        rerun: set[str] = set()
        for step in self.steps:
            if self.state.done(step.key):
                continue
            if options.through is not None and order.index(step.key) > order.index(options.through):
                return self._stop(report, THROUGH, step, f"Stopped: --through {options.through} "
                                                         f"is done. Run `{COMMAND}` to go on.")
            for dependency in self._dependencies(step):
                if dependency.key in rerun or not self._stale(dependency):
                    continue
                rerun.add(dependency.key)
                self.logger.info(f"{dependency.spec.title} is stale again "
                                 f"({self._stale(dependency)}); running it again")
                outcome = self._execute(dependency, options, report)
                if outcome is not None:
                    return outcome
            outcome = self._execute(step, options, report)
            if outcome is not None:
                return outcome
        self.state.finish()
        self.state.save()
        report.add("completed", None)
        return Outcome(COMPLETED, f"Update {self.state.current()['stamp']} is complete.")

    def _dependencies(self, step: Step) -> list[Step]:
        """The update's steps, done in this update, that produce what ``step`` reads (directly
        or further up), in pipeline order."""
        wanted: set[str] = set()
        todo = list(step.upstream)
        while todo:
            key = todo.pop()
            if key not in wanted:
                wanted.add(key)
                todo.extend(self.graph.node(key).upstream)
        return [s for s in self.steps if s.spec.produces in wanted and self.state.done(s.key)]

    def _stale(self, step: Step) -> tuple[str, ...]:
        status = next(s for s in self.graph.status() if s.key == step.spec.produces)
        return status.stale_because

    def _execute(self, step: Step, options: Options, report: UpdateReport) -> Outcome | None:
        replaces_input = (self.input_key in step.upstream
                          and not self.state.current()["input"]["replaced"])
        before = self._fingerprint(step)
        try:
            if step.spec.paid:
                estimate = self.estimator(step)
                report.add("estimate", step.key, estimate=estimate.to_json() if estimate else None)
                what = estimate.line() if estimate else "no estimate is available"
                if (estimate is None or estimate.paid_calls > 0) and step.key not in options.pay:
                    return self._stop(report, PAID, step,
                                      f"Stopped before {step.spec.title}: {what}. To allow it: "
                                      f"`{COMMAND} --pay {step.key}`.")
                self.logger.info(f"{step.spec.title}: {what}")
            # Announce first, then ask the server: a tool save or a job that starts after the
            # check is refused by the server itself (write guard, job runner).
            with self.lease.running(step.key, step.spec.produces, step.spec.title):
                refusal = self.server_check()
                if refusal is not None:
                    return self._stop(report, BUSY, step,
                                      f"Not starting {step.spec.title}: {refusal}. Run "
                                      f"`{COMMAND}` again when it is done.")
                self.logger.info(f"Running {step.spec.title}")
                if replaces_input:
                    self._replace_input(report)
                result = (self.runners.get(step.key) or step.run_function())()
        # Whatever a step raises, the update must be left resumable and say what happened.
        except Exception as exc:
            if replaces_input:
                self._restore_input(report)
            self.state.fail(step.key, repr(exc))
            self.state.save()
            report.add("failed", step.key, error=repr(exc))
            raise StepFailed(f"{step.spec.title} failed: {exc!r}. The update is kept; run "
                             f"`{COMMAND}` to retry this step.") from exc
        if replaces_input:
            self._input_processed()
        changed = before != self._fingerprint(step)
        self.state.mark_run(step.key, changed=changed)
        self.state.save()
        report.add("run", step.key, changed=changed, result=result)
        self.logger.info(f"{step.spec.title} done ({'changed' if changed else 'unchanged'}): "
                         f"{scalar_summary(result)}")
        if step.key == options.through:
            return self._stop(report, THROUGH, step, f"Stopped after {step.spec.title} "
                                                     f"(--through). Run `{COMMAND}` to go on.")
        tool = step.spec.review_tool
        if tool and changed and not options.no_stops and options.through is None:
            hint = UPDATE_CONFIG["review_hints"][tool]
            return self._stop(report, REVIEW, step,
                              f"Stopped after {step.spec.title}: review {hint} with "
                              f"`python -m review_tool --tool {tool}`, then run `{COMMAND}` "
                              "to go on.")
        return None

    def _stop(self, report: UpdateReport, kind: str, step: Step, message: str) -> Outcome:
        self.state.stop(kind, step.key, message)
        self.state.save()
        report.add("stop", step.key, stop=kind, message=message)
        return Outcome(STOPPED, message)

    def _said(self, outcome: Outcome) -> Outcome:
        level = logging.ERROR if outcome.status in (FAILED, REFUSED) else logging.INFO
        self.logger.log(level, outcome.message)
        return outcome

    def _fingerprint(self, step: Step) -> str:
        """The step's output without its ``metadata`` (run counters and times change on every
        write; a review is due only when the content changed)."""
        files = self.graph.node(step.spec.produces).files.values()
        return fingerprint([_content(path) for path in files])

    # ------------------------------------------------------------------ the pipeline input

    def _replace_input(self, report: UpdateReport) -> None:
        update = self.state.current()
        files = self.paths.files(update["stamp"])
        if update["input"]["backup"] is None:
            files.previous_input.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.paths.input, files.previous_input)
            update["input"]["backup"] = str(files.previous_input)
            self.state.save()
        old = load_export(Path(update["input"]["backup"]))
        merged = merged_input(old, load_export(Path(update["export"])),
                              drop_missing=update["drop_missing"])
        write_json_file(merged, self.paths.input)  # type: ignore[arg-type]  # a list is fine
        update["input"]["replaced"] = True
        self.state.save()
        report.add("input", None, messages=len(merged), backup=update["input"]["backup"])
        self.logger.info(f"Pipeline input replaced: {len(merged)} messages (the old one is kept "
                         f"as {files.previous_input.name})")

    def _restore_input(self, report: UpdateReport) -> None:
        update = self.state.current()
        if update["input"]["backup"] is None:  # failed before the input was touched
            return
        shutil.copy2(Path(update["input"]["backup"]), self.paths.input)
        update["input"]["replaced"] = False
        self.state.save()
        report.add("input_restored", None)
        self.logger.warning("The old pipeline input is back in place")

    def _input_processed(self) -> None:
        messages = load_export(self.paths.input)
        self.state.record_input(Path(self.state.current()["export"]),
                                max(map(message_time, messages)), len(messages))
        self.state.save()


def _content(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError):
        return file_fingerprint(path)
    return {k: v for k, v in data.items() if k != "metadata"} if isinstance(data, dict) else data


def _texts(messages: Sequence[dict[str, Any]]) -> list[str]:
    return [message.get("text") or "" for message in messages]
