"""Move the pipeline's data to a new export of the same messages.

    python -m update_run.move EXPORT [--members PATH] [--dry-run]

``EXPORT`` is a full export made by the current export tool (times with seconds, the phone
database's order, one member id per member, ``mentions``); ``--members`` its members file
(default ``data/shared/members.json``). The command refuses unless every message of the
current pipeline input is in the export with the same text, date, minute and sender. Then:

- the input (``data/chat_android.json``) becomes the export cut to exactly its messages;
- both threads files: each message gets its new time and member ids, a conversation's
  messages go in chat order, the conversations in step 1's order; which messages are in which
  conversation and every id stay as they are;
- step 1's, step 2's and step 3's stored fingerprints are re-stamped (``move_data``): one that
  matched before the move matches after it, one that did not still does not;
- step 3's claims get their counts from the step's own code (a member under two ids is one
  person), its private audit carries member ids; ids, texts, entities and evidence stay;
- the gold file's id lists follow the new order and its conversations their new start time;
  the update state (when there is one) its new last time.

Every file is backed up (``utils.reconcile.backup_file``) before it is written, and a file
that would not change is not written: a second run changes nothing. Before writing, the
command checks the proof (``move_proof``) on the result in memory and refuses if anything a
step or a person decided would change; after writing it checks it again on the files. It
ends by printing the proof and the numbers. ``--dry-run`` prints them and writes nothing.

Exit code 0 when moved or nothing to move, 1 when the proof failed after writing (restore
the backups), 2 when refused (nothing written).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_3_extract.claims import DERIVED_KEYS
from step_3_extract.run import write_audit
from step_3_extract.tagged import load_table, provider_names
from update_run.exports import load_export, message_time
from update_run.move_data import ChatMove, NotTheSameMessages, Restamps
from update_run.move_proof import PipelineData, Proof, people
from update_run.server import ReviewServerCheck
from update_run.state import UpdateState
from utils.json_io import read_json_if_present, write_json_file
from utils.logging_setup import setup_step_logging
from utils.paths import (
    AUDIT_PATH,
    CHAT_ANDROID_PATH,
    CLAIMS,
    CLASSIFIED,
    GOLD_THREADS_PATH,
    MEMBERS_PATH,
    TAGGED_MEMBERS_PATH,
    THREADS,
    UPDATE_LEASE_PATH,
    UPDATE_RUN,
    UPDATE_STATE_PATH,
    Artifact,
)
from utils.reconcile import backup_file, guarded_write, write_reconciled
from utils.step_lease import LeaseHeld, StepLease
from utils.threads_io import export_index, person_key

MOVED, REFUSED, PROOF_FAILED = 0, 2, 1


class Refused(RuntimeError):
    """The move cannot be made as asked; nothing was written."""


@dataclass(frozen=True)
class MovePaths:
    input: Path = CHAT_ANDROID_PATH
    threads: Artifact = THREADS
    classified: Artifact = CLASSIFIED
    claims: Artifact = CLAIMS
    audit: Path = AUDIT_PATH
    gold: Path = GOLD_THREADS_PATH
    state: Path = UPDATE_STATE_PATH
    lease: Path = UPDATE_LEASE_PATH
    tagged: Path = TAGGED_MEMBERS_PATH

    def load(self) -> PipelineData:
        audit = read_json_if_present(self.audit)
        return PipelineData(
            input=load_export(self.input),
            threads=read_json_if_present(self.threads.original),
            threads_edited=read_json_if_present(self.threads.edited),
            classified=read_json_if_present(self.classified.original),
            classified_edited=read_json_if_present(self.classified.edited),
            claims=read_json_if_present(self.claims.original),
            claims_edited=read_json_if_present(self.claims.edited),
            audit=audit["audit"] if audit is not None else None,
            gold=read_json_if_present(self.gold),
            state=read_json_if_present(self.state),
            providers=provider_names(load_table(self.tagged)),
        )


def load_members(path: Path) -> dict[str, str]:
    """Raw WhatsApp id -> member id, from the export tool's members file."""
    data = read_json_if_present(path)
    if data is None:
        raise Refused(f"members file {path} not found: export with `--members {path}` first")
    return {alias: member["id"] for member in data["members"] for alias in member["aliases"]}


def _order_changed(before: dict[str, Any] | None, after: dict[str, Any] | None) -> int:
    old = {t["thread_id"]: [m["id"] for m in t["messages"]] for t in (before or {}).get("threads", [])}
    return sum(1 for t in (after or {}).get("threads", []) if old.get(t["thread_id"]) != [m["id"] for m in t["messages"]])


def moved_data(data: PipelineData, export: list[dict[str, Any]], members: dict[str, str]) -> tuple[PipelineData, dict[str, Any]]:
    """The data after the move, in memory, and its numbers (nothing is written)."""
    move = ChatMove(data.input, export, members, data.providers)
    restamps = {name: Restamps() for name in ("step 1", "step 2", "step 3")}
    threads = move.move_threads(data.threads, restamps["step 1"]) if data.threads else None
    edited = move.move_threads(data.threads_edited, restamps["step 1"]) if data.threads_edited else None
    after = PipelineData(input=move.new_input, threads=threads, threads_edited=edited,
                         providers=data.providers)
    old_read = {t["thread_id"]: t for t in (data.read_threads or {}).get("threads", [])}
    new_read = {t["thread_id"]: t for t in (after.read_threads or {}).get("threads", [])}
    if data.classified is not None:
        settings = ChatMove.stored_settings(data.classified)
        after.classified = move.move_classified(data.classified, settings, old_read, new_read, restamps["step 2"])
        if data.classified_edited is not None:
            after.classified_edited = move.move_classified(
                data.classified_edited, settings, old_read, new_read, restamps["step 2"]
            )
    numbers: dict[str, Any] = {}
    if data.claims is not None:
        claims = move.move_claims(data.claims, data.audit or [], (old_read, new_read),
                                  (data.export_index(), export_index(move.new_input)), restamps["step 3"])
        after.claims, after.audit = claims.payload, claims.audit
        after.claims_edited = data.claims_edited  # its derived fields are refreshed on writing
        numbers["claims"] = {"count": len(claims.payload["claims"]),
                             "threads refreshed": claims.refreshed_threads,
                             "people counts changed": len(claims.counts_changed),
                             "evidence order changed": claims.order_changed}
    if data.gold is not None:
        after.gold = move.move_gold(data.gold)
    if data.state is not None:
        if UpdateState(MovePaths.state, data.state).in_progress:
            raise Refused(f"an update is in progress ({data.state['update'].get('stamp')}): finish it first")
        last = max(map(message_time, move.new_input)).isoformat()
        after.state = {**data.state, "last_datetime": last} if "last_datetime" in data.state else data.state
    places = [move.place[str(m["id"])] for m in data.input]
    new_sender = {str(m["id"]): person_key(m.get("sender")) for m in move.new_input}
    numbers.update({
        "input messages": len(data.input),
        "neighbouring pairs put in the database's order": sum(1 for a, b in zip(places, places[1:]) if a > b),
        "messages whose sender id changed": sum(
            1 for m in data.input if person_key(m.get("sender")) != new_sender[str(m["id"])]
        ),
        "messages whose content differs beyond time and ids": len(move.content_differs),
        "conversations whose message order changed": _order_changed(data.read_threads, after.read_threads),
        "conversations of step 1's own file whose order changed": _order_changed(data.threads, after.threads),
        "people before": people(data.input, data.audit),
        "people after": people(move.new_input, after.audit),
        "fingerprints": {name: {"re-stamped": r.restamped, "left as they were": r.left}
                         for name, r in restamps.items()},
    })
    return after, numbers


@dataclass(frozen=True)
class Writer:
    paths: MovePaths
    logger: logging.Logger

    def write(self, before: PipelineData, after: PipelineData) -> list[Path]:
        """Write every file that changes, each backed up first; the input last."""
        targets: list[tuple[str, Path, Callable[[Any, Path], None]]] = [
            ("threads", self.paths.threads.original, self._guarded),
            ("threads_edited", self.paths.threads.edited, self._guarded),
            ("classified", self.paths.classified.original, self._guarded),
            ("classified_edited", self.paths.classified.edited, self._guarded),
            ("claims", self.paths.claims.original, self._claims),
            ("audit", self.paths.audit, self._audit),
            ("gold", self.paths.gold, self._guarded),
            ("state", self.paths.state, self._guarded),
            ("input", self.paths.input, self._plain),
        ]
        written = []
        for name, path, write in targets:
            new = getattr(after, name)
            if new is None or new == getattr(before, name):
                continue
            write(new, path)
            written.append(path)
            self.logger.info(f"Moved {path.name}")
        return written

    @staticmethod
    def _guarded(payload: Any, path: Path) -> None:
        guarded_write(path, payload, backup=True)

    @staticmethod
    def _plain(payload: Any, path: Path) -> None:
        backup_file(path)
        write_json_file(payload, path)

    def _audit(self, records: Any, path: Path) -> None:
        backup_file(path)
        write_audit(path, records)

    def _claims(self, payload: Any, path: Path) -> None:
        """Step 3's own writer: the edited copy (if any) keeps manual edits, its counts follow."""
        backup_file(path)
        write_reconciled(self.paths.claims, payload, items_key="claims", id_key="claim_id",
                         refresh_keys=DERIVED_KEYS)


class Mover:
    def __init__(
        self,
        paths: MovePaths = MovePaths(),
        *,
        server_check: Callable[[], str | None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.paths = paths
        self.server_check = server_check or ReviewServerCheck()
        self.logger = logger or logging.getLogger(__name__)

    def run(self, export_path: Path, members_path: Path, *, dry_run: bool) -> int:
        try:
            if dry_run:
                return self._move(export_path, members_path, dry_run=True)
            with StepLease(self.paths.lease).hold():
                refusal = self.server_check()
                if refusal is not None:
                    raise Refused(f"not now: {refusal}")
                return self._move(export_path, members_path, dry_run=False)
        except (Refused, NotTheSameMessages, LeaseHeld) as exc:
            self.logger.error(f"Refused: {exc}")
            return REFUSED

    def _move(self, export_path: Path, members_path: Path, *, dry_run: bool) -> int:
        before = self.paths.load()
        after, numbers = moved_data(before, load_export(export_path), load_members(members_path))
        proof_before = Proof.of(before)
        missing = proof_before.differences(Proof.of(after))
        self._report(numbers, proof_before, missing)
        if missing:
            raise Refused(f"the move would change {missing}")
        if dry_run:
            self.logger.info("Dry run: nothing was written.")
            return MOVED
        written = Writer(self.paths, self.logger).write(before, after)
        if not written:
            self.logger.info("Nothing to move: the data is already on this export.")
            return MOVED
        on_disk = proof_before.differences(Proof.of(self.paths.load()))
        self.logger.info(f"Wrote {len(written)} files (each backed up first)")
        if on_disk:
            self.logger.error(f"PROOF FAILED on the written files: {on_disk}; restore the backups")
            return PROOF_FAILED
        self.logger.info("Proof on the written files: the same as before the move")
        return MOVED

    def _report(self, numbers: dict[str, Any], proof: Proof, missing: Sequence[str]) -> None:
        for name, value in numbers.items():
            self.logger.info(f"{name}: {value}")
        for line in proof.lines():
            name = line.split(":", 1)[0]
            self.logger.info(f"proof | {line} | {'DIFFERENT' if name in missing else 'same after the move'}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m update_run.move",
        description="Move the pipeline's data to a new export of the same messages.",
    )
    parser.add_argument("export", type=Path, help="the new full export (current export tool)")
    parser.add_argument("--members", type=Path, default=MEMBERS_PATH, help="its members file")
    parser.add_argument("--dry-run", action="store_true", help="print the numbers; write nothing")
    args = parser.parse_args(argv)
    logger = setup_step_logging(UPDATE_RUN)
    logger.info(f"Move to {args.export} (members {args.members}){' - dry run' if args.dry_run else ''}")
    return Mover(logger=logger).run(args.export, args.members, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
