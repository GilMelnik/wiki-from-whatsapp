"""The update in progress (``step_10_update``), as the status tool shows it. Read-only.

An update takes a newer chat export through the pipeline's steps and stops after a step a
person must review and before a step that would spend money. What the owner needs at such a
moment comes from three files the driver keeps (contract C13): its state
(``step_10_update/state.json``: the update, the steps run, the stop or the failure), the
update's report (``updates/<stamp>_report.json``: the estimate recorded before a paid step)
and its lease (``step_10_update/running.json``: the step running right now). ``view()`` turns
them into one answer::

    {"state": "none" | "in_progress" | "completed" | "unreadable", "problem"?,
     "stamp", "export", "started_at", "completed_at", "counts": {new, changed, missing},
     "input_backup", "steps": [{key, title, status, paid, review_tool, runs, changed, at}],
     "running": {step, title, since} | null, "stopped": {kind, step, title, message, at,
     review_tool} | null, "failed": {step, title, error, at} | null,
     "estimate": {paid_calls, usd, what, counted} | null, "command": str | null}

A step's ``status``: done, review (done; the update waits for its review), running, failed,
stopped (the update stopped before it: money, a busy review server, ``--through``), next, or
waiting. Nothing here runs the driver: ``command`` is what the owner types to go on.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from review_tool.artifacts import ArtifactGraph
from review_tool.steps import Step
from step_10_update.state import COMMAND, PAID, REVIEW, UpdateState
from utils.step_lease import StepLease


class UpdateStatus:
    def __init__(
        self, state_path: Path, lease_path: Path, steps: Sequence[Step], graph: ArtifactGraph
    ) -> None:
        self._state_path = state_path
        self._lease = StepLease(lease_path)
        # The steps an update runs, as the driver picks them: the curated plan never re-plans.
        self._steps = [step for step in steps if not graph.node(step.spec.produces).curated]
        self._titles = {step.key: step.spec.title for step in self._steps}

    def view(self) -> dict[str, Any]:
        try:
            update = UpdateState.load(self._state_path).update
        except ValueError as exc:
            return {"state": "unreadable", "problem": str(exc)}
        if update is None:
            return {"state": "none"}
        completed = update.get("completed_at") is not None
        running = None if completed else self._running()
        stopped = None if completed or running else update.get("stopped")
        failed = None if completed or running else update.get("failed")
        return {
            "state": "completed" if completed else "in_progress",
            "stamp": update.get("stamp"),
            "export": Path(update.get("export") or "").name,
            "started_at": update.get("started_at"),
            "completed_at": update.get("completed_at"),
            "counts": update.get("counts") or {},
            "input_backup": (update.get("input") or {}).get("backup"),
            "steps": self._timeline(update, running, stopped, failed, completed),
            "running": running,
            "stopped": self._stop(stopped),
            "failed": failed and {**failed, "title": self._titles.get(failed["step"], failed["step"])},
            "estimate": self._estimate(update, stopped),
            "command": None if completed or running else self._command(stopped),
        }

    def _running(self) -> dict[str, Any] | None:
        holder = self._lease.current()
        if not holder or not holder.get("step"):
            return None
        return {"step": holder["step"], "title": holder.get("title"), "since": holder.get("since")}

    def _timeline(
        self,
        update: dict[str, Any],
        running: dict[str, Any] | None,
        stopped: dict[str, Any] | None,
        failed: dict[str, Any] | None,
        completed: bool,
    ) -> list[dict[str, Any]]:
        rows = []
        ahead = completed  # once the update's current place is found, the rest waits
        for step in self._steps:
            record = (update.get("steps") or {}).get(step.key)
            status = self._status(step.key, record, running, stopped, failed)
            if status is None:
                status = "waiting" if ahead else "next"
            if status in ("running", "failed", "stopped", "next"):
                ahead = True
            rows.append(
                {
                    "key": step.key,
                    "title": step.spec.title,
                    "status": status,
                    "paid": step.spec.paid,
                    "review_tool": step.spec.review_tool,
                    "runs": (record or {}).get("runs", 0),
                    "changed": (record or {}).get("changed"),
                    "at": (record or {}).get("at"),
                }
            )
        return rows

    @staticmethod
    def _status(
        key: str,
        record: dict[str, Any] | None,
        running: dict[str, Any] | None,
        stopped: dict[str, Any] | None,
        failed: dict[str, Any] | None,
    ) -> str | None:
        """The step's status when the update's state says it; ``None`` for a step not run yet."""
        if running and running["step"] == key:
            return "running"
        if failed and failed["step"] == key:
            return "failed"
        if stopped and stopped["step"] == key:
            if stopped["kind"] == REVIEW:
                return "review"
            return "done" if record else "stopped"
        return "done" if record else None

    def _stop(self, stopped: dict[str, Any] | None) -> dict[str, Any] | None:
        if not stopped:
            return None
        step = next((s for s in self._steps if s.key == stopped["step"]), None)
        return {
            **stopped,
            "title": step.spec.title if step else stopped["step"],
            "review_tool": step.spec.review_tool if step and stopped["kind"] == REVIEW else None,
        }

    @staticmethod
    def _estimate(update: dict[str, Any], stopped: dict[str, Any] | None) -> dict[str, Any] | None:
        """The estimate the driver recorded before the paid step it stopped at (the newest)."""
        if not stopped or stopped["kind"] != PAID:
            return None
        report = Path(update.get("report") or "")
        if not report.is_file():
            return None
        try:
            events = json.loads(report.read_text(encoding="utf-8")).get("events") or []
        except json.JSONDecodeError:
            return None
        found = [e for e in events if e.get("kind") == "estimate" and e.get("step") == stopped["step"]]
        return found[-1].get("estimate") if found else None

    @staticmethod
    def _command(stopped: dict[str, Any] | None) -> str:
        if stopped and stopped["kind"] == PAID:
            return f"{COMMAND} --pay {stopped['step']}"
        return COMMAND
