"""The update driver's state (contract C13): ``data/step_10_update/state.json``::

    {"last_export": "<path of the export now in the pipeline input>",
     "last_datetime": "YYYY-MM-DDTHH:MM:SS",          # its newest message
     "processed_message_count": 20891,                 # messages in the pipeline input
     "update": {                                       # the current (or last) update
        "stamp", "export", "export_sha1", "new_messages", "report", "log",
        "counts": {"new", "changed", "missing"}, "drop_missing",
        "started_at", "completed_at",                 # completed_at null while it runs
        "input": {"backup", "replaced"},              # the old input's backup copy
        "steps": {step_id: {"runs", "changed", "gate_passed", "at"}},
        "stopped": {"kind", "step", "message", "at"} | null,
        "failed": {"step", "error", "at"} | null},
     "history": [{"stamp", "export_sha1", "counts", "completed_at"}]}

``stopped.kind`` is ``review`` (a person must look; running the command again goes on),
``paid`` (the next step would spend money and was not allowed), ``busy`` (the review server
was running a job or an apply) or ``through`` (``--through`` reached its step).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.json_io import write_json_file

REVIEW, PAID, BUSY, THROUGH = "review", "paid", "busy", "through"
HISTORY_KEYS = ("stamp", "export_sha1", "counts", "completed_at")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class UpdateState:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path: Path) -> UpdateState:
        if not path.is_file():
            return cls(path, {})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON ({exc}); restore it from a backup") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path} must hold a JSON object")
        return cls(path, data)

    def save(self) -> None:
        write_json_file(self.data, self.path)

    # ------------------------------------------------------------------ the pipeline input

    @property
    def initialised(self) -> bool:
        return "last_export" in self.data

    def record_input(self, export: Path, last_datetime: datetime, count: int) -> None:
        """What the pipeline input now holds (C13's three fields)."""
        self.data.update(
            last_export=str(export),
            last_datetime=last_datetime.isoformat(),
            processed_message_count=count,
        )

    # ------------------------------------------------------------------ the update

    @property
    def update(self) -> dict[str, Any] | None:
        return self.data.get("update")

    @property
    def in_progress(self) -> bool:
        return self.update is not None and self.update.get("completed_at") is None

    def begin(self, record: dict[str, Any]) -> None:
        """Start a new update (the previous one, if any, must be completed)."""
        if self.in_progress:
            raise RuntimeError("an update is already in progress")
        previous = self.update
        if previous is not None:
            self.data.setdefault("history", []).append(
                {key: previous.get(key) for key in HISTORY_KEYS}
            )
        self.data["update"] = {
            **record,
            "started_at": now(),
            "completed_at": None,
            "input": {"backup": None, "replaced": False},
            "steps": {},
            "stopped": None,
            "failed": None,
        }

    def current(self) -> dict[str, Any]:
        if self.update is None:
            raise RuntimeError("no update has been started")
        return self.update

    def step(self, key: str) -> dict[str, Any] | None:
        return self.current()["steps"].get(key)

    def done(self, key: str) -> bool:
        return self.step(key) is not None

    def mark_run(self, key: str, *, changed: bool) -> None:
        record = self.current()["steps"].setdefault(key, {"runs": 0, "gate_passed": False})
        record.update(runs=record["runs"] + 1, changed=changed, at=now())
        if changed:
            record["gate_passed"] = False

    def pass_gate(self, key: str) -> None:
        self.current()["steps"][key]["gate_passed"] = True

    def stop(self, kind: str, key: str, message: str) -> None:
        self.current()["stopped"] = {"kind": kind, "step": key, "message": message, "at": now()}

    def take_stop(self) -> dict[str, Any] | None:
        """The stop the last run ended with, cleared (this run goes on from it)."""
        stopped = self.current()["stopped"]
        self.current()["stopped"] = None
        return stopped

    def fail(self, key: str, error: str) -> None:
        self.current()["failed"] = {"step": key, "error": error, "at": now()}

    def clear_failure(self) -> None:
        self.current()["failed"] = None

    def finish(self) -> None:
        self.current()["completed_at"] = now()
