"""Re-run a pipeline step without losing manual edits (contract C14).

A step writes ``X.json`` (the original); the user edits a copy, ``X_edited.json``, and the next
step reads that copy. With this module a step can run again and touch only what changed:
``fingerprint`` + ``diff_inputs`` tell which upstream items are new / changed / removed since
the previous run (judged against the ``metadata.inputs`` the step stored in its own output),
``load_previous`` returns that previous output so untouched items are carried over as they
are, and ``write_reconciled`` writes the fresh output to the original file and three-way merges
it into the edited file (``merge_edited``). ``guarded_write`` + ``backup_file`` lock, back up
and atomically write one file, for artifacts the step merges by its own logic.

Items may carry fields the step always owns and that change often (counts, dates, evidence
ids). Name them as ``refresh_keys`` and a manual edit survives their changes, while those
fields are still brought up to date; ``same_ignoring`` instead freezes volatile fields at the
user's old values.
"""

from __future__ import annotations

import hashlib
import json
import logging
import operator
import shutil
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.file_lock import file_lock
from utils.json_io import write_json_file
from utils.paths import BACKUPS_DIR, Artifact

logger = logging.getLogger(__name__)

IsSame = Callable[[Any, Any], bool]

# Same naming as the reviewer stores' ``_ensure_backup``: ``<stem>_<stamp><suffix>``.
BACKUP_STAMP = "%Y%m%d_%H%M%S"
_ABSENT = object()


def fingerprint(value: Any) -> str:
    """Return a sha1 of ``value`` as canonical JSON: stable across runs and dict key order."""
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InputDiff:
    """Upstream item ids, split by what happened to each since the step's previous run."""

    new: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]


def diff_inputs(previous: Mapping[str, str], current: Mapping[str, str]) -> InputDiff:
    """Compare ``{upstream_id: fingerprint}`` of the previous run (``{}`` if none) with now.

    ``new``, ``changed`` and ``unchanged`` keep the order of ``current``; ``removed`` keeps the
    order of ``previous``.
    """
    return InputDiff(
        new=tuple(i for i in current if i not in previous),
        changed=tuple(i for i, fp in current.items() if i in previous and previous[i] != fp),
        removed=tuple(i for i in previous if i not in current),
        unchanged=tuple(i for i, fp in current.items() if i in previous and previous[i] == fp),
    )


def same_ignoring(*keys: str) -> IsSame:
    """Build an ``is_same`` for dict items that ignores volatile ``keys`` (timestamps, scores).

    A manual edit that is kept because of it also keeps its own, older values of those keys; use
    ``refresh_keys`` instead when the kept edit should get the step's current values.
    """

    def is_same(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        stable_left = {k: v for k, v in left.items() if k not in keys}
        stable_right = {k: v for k, v in right.items() if k not in keys}
        return stable_left == stable_right

    return is_same


@dataclass(frozen=True)
class MergeChanges:
    """Ids affected by a merge.

    ``added`` / ``updated`` / ``removed`` describe the step's output (fresh vs base). The rest
    describe the user's manual changes: ``superseded`` = a manually modified item was replaced
    by a new or changed fresh item; ``resurrected`` = a manually deleted item came back for the
    same reason; ``dropped_edited`` = a manually modified item went away because the step no
    longer produces it; ``user_created`` = items only the user has, kept as they are.
    ``refreshed`` = kept manual edits whose ``refresh_keys`` were updated from the step's output;
    nothing the user wrote was lost there, so it is not part of ``lost_edits``.
    """

    added: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]
    superseded: tuple[str, ...]
    resurrected: tuple[str, ...]
    dropped_edited: tuple[str, ...]
    user_created: tuple[str, ...]
    refreshed: tuple[str, ...] = ()

    @property
    def lost_edits(self) -> tuple[str, ...]:
        """Every id whose manual change now exists only in the backup."""
        return self.superseded + self.resurrected + self.dropped_edited

    def summary(self) -> str:
        """Counts per list: ``added=1, updated=0, ...``."""
        return ", ".join(f"{f.name}={len(getattr(self, f.name))}" for f in fields(self))


@dataclass(frozen=True)
class MergeResult:
    """``items``: the merged ``{id: item}``, fresh order first, then user-created items."""

    items: dict[str, Any]
    changes: MergeChanges


@dataclass(frozen=True)
class ReconcileReport:
    """``backup``: copy of the edited file as it was before the call; ``None`` if none existed."""

    changes: MergeChanges
    backup: Path | None


def merge_edited(
    base: Mapping[str, Any],
    edited: Mapping[str, Any],
    fresh: Mapping[str, Any],
    *,
    is_same: IsSame = operator.eq,
    refresh_keys: Collection[str] = (),
) -> MergeResult:
    """Three-way merge of ``{id: item}`` collections; returns what the edited copy becomes.

    ``base`` is the step's previous output, ``edited`` the user's copy of it, ``fresh`` the
    step's new output, all as JSON would load them. Per id:

    * in fresh, in base, ``is_same(base, fresh)``: the user's version wins — the edited item,
      or nothing if the user deleted it (an item the user never touched follows fresh);
    * in fresh, and new or changed vs base: fresh wins; a manual change it replaces is
      recorded as ``superseded`` / ``resurrected``;
    * not in fresh, in base: dropped (``dropped_edited`` if the user had modified it);
    * in edited only: kept, after the fresh items (``user_created``).

    ``refresh_keys`` names top-level keys of dict items that the step always owns (counts,
    dates, evidence ids). They are left out when judging whether the step's output changed
    (``is_same`` then sees items without them) and whether the user modified an item, so a
    change in them alone neither supersedes a manual edit nor brings back a deleted item. Where
    the user's version is kept, its refresh keys are overwritten from the fresh item (a key
    missing there is removed), and the id is listed in ``refreshed``. Raises ``ValueError`` if
    ``refresh_keys`` is given and an item is not a dict. With ``refresh_keys=()`` nothing of
    this applies.
    """
    keys = _as_key_set(refresh_keys)
    if keys:
        _require_dict_items(keys, base=base, edited=edited, fresh=fresh)
    items: dict[str, Any] = {}
    found: dict[str, list[str]] = {field.name: [] for field in fields(MergeChanges)}

    for item_id, fresh_item in fresh.items():
        base_item = base.get(item_id, _ABSENT)
        user_item = edited.get(item_id, _ABSENT)
        step_changed = base_item is _ABSENT or not is_same(
            _without(base_item, keys), _without(fresh_item, keys)
        )
        user_changed = _without(user_item, keys) != _without(base_item, keys)
        if base_item is _ABSENT:
            found["added"].append(item_id)
        elif step_changed:
            found["updated"].append(item_id)
        if step_changed or not user_changed:
            items[item_id] = fresh_item
            if user_changed and _without(user_item, keys) != _without(fresh_item, keys):
                found["resurrected" if user_item is _ABSENT else "superseded"].append(item_id)
        elif user_item is not _ABSENT:
            items[item_id] = _refresh(user_item, fresh_item, keys)
            if items[item_id] != user_item:
                found["refreshed"].append(item_id)

    for item_id, base_item in base.items():
        if item_id not in fresh:
            found["removed"].append(item_id)
            if item_id in edited and edited[item_id] != base_item:
                found["dropped_edited"].append(item_id)

    for item_id, user_item in edited.items():
        if item_id not in fresh and item_id not in base:
            items[item_id] = user_item
            found["user_created"].append(item_id)

    changes = MergeChanges(**{name: tuple(ids) for name, ids in found.items()})
    return MergeResult(items=items, changes=changes)


def load_previous(artifact: Artifact) -> dict[str, Any]:
    """Return the step's own previous output (the original file); ``{}`` if it never ran.

    Carry untouched items over from here, never from the edited file: the merge keeps a manual
    edit only while the step's own output for that item is unchanged.
    """
    return _read_object(artifact.original) or {}


def write_reconciled(
    artifact: Artifact,
    payload: dict[str, Any],
    *,
    items_key: str,
    id_key: str | None,
    is_same: IsSame = operator.eq,
    refresh_keys: Collection[str] = (),
    rebuild: bool = False,
    lock_timeout: float | None = None,
) -> ReconcileReport:
    """Write a step's fresh output and carry the user's manual edits over to it.

    ``payload[items_key]`` is the item collection: a list of dicts whose id is under ``id_key``,
    or a dict keyed by id (``id_key=None``). Under ``file_lock`` on both files: the original on
    disk is the merge base; an existing edited file is backed up and rewritten as ``payload``
    with its items replaced by ``merge_edited(base, edited, fresh)``; then the original becomes
    ``payload``. No edited file is created if none exists. ``lock_timeout=None`` keeps
    ``file_lock``'s default. ``refresh_keys`` is passed to ``merge_edited``: the fields the step
    always owns, refreshed on manual edits that are kept (items must then be dicts).

    ``rebuild=True`` regenerates from scratch: the files on disk are not read (so a broken one
    cannot block it), the edited file is backed up and becomes ``payload`` too, and every item
    is reported as ``added``.

    Raises before writing anything: ``ValueError`` if a file is malformed or an id is missing
    or duplicated, ``TypeError`` if ``payload`` is not JSON-serialisable, ``TimeoutError`` if a
    lock cannot be taken. Every call backs up the edited file, so call it once per run; never
    call it while already holding ``file_lock`` on either file (the lock is not re-entrant).
    """
    # Compare like with like: the fresh items as JSON stores them (a tuple becomes a list).
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    fresh = _index_items(payload, items_key, id_key, "payload")
    with _locked((artifact.original, artifact.edited), lock_timeout):
        base: dict[str, Any] = {}
        edited: dict[str, Any] | None = None
        if not rebuild:
            base = _read_items(artifact.original, items_key, id_key) or {}
            edited = _read_items(artifact.edited, items_key, id_key)
        merge = merge_edited(
            base,
            base if edited is None else edited,
            fresh,
            is_same=is_same,
            refresh_keys=refresh_keys,
        )
        backup = backup_file(artifact.edited)
        if backup is not None:
            merged = merge.items if id_key is None else list(merge.items.values())
            # Edited first: if the run dies before the original is written, the next run
            # still sees the old base and repeats the same merge instead of losing new items.
            write_json_file({**payload, items_key: merged}, artifact.edited)
        write_json_file(payload, artifact.original)

    changes = merge.changes
    message = (
        f"{'rebuilt' if rebuild else 'reconciled'} {artifact.original.name}: "
        f"{changes.summary()}; edited copy: {f'backed up to {backup}' if backup else 'none'}"
    )
    if changes.lost_edits:
        message += (
            f"; manual edits now only in the backup: superseded={list(changes.superseded)}, "
            f"resurrected={list(changes.resurrected)}, "
            f"dropped_edited={list(changes.dropped_edited)}"
        )
    logger.log(logging.WARNING if changes.lost_edits else logging.INFO, message)
    return ReconcileReport(changes=changes, backup=backup)


def guarded_write(
    path: Path, payload: dict[str, Any], *, backup: bool, lock_timeout: float | None = None
) -> Path | None:
    """Lock ``path``, optionally back up its current content, then replace it atomically.

    Returns the backup path (``None`` if no backup was asked for or the file did not exist).
    Never call it while already holding ``file_lock(path)``: the lock is not re-entrant.
    """
    with _locked((path,), lock_timeout):
        backup_path = backup_file(path) if backup else None
        write_json_file(payload, path)
    return backup_path


def backup_file(path: Path) -> Path | None:
    """Copy ``path`` into ``BACKUPS_DIR`` as ``<stem>_<YYYYmmdd_HHMMSS><suffix>``.

    Never overwrites an earlier backup: a second copy within the same second gets a ``_<n>``
    suffix. Returns the copy's path, or ``None`` if ``path`` does not exist.
    """
    if not path.is_file():
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime(BACKUP_STAMP)
    target = BACKUPS_DIR / f"{path.stem}_{stamp}{path.suffix}"
    attempt = 1
    while target.exists():
        target = BACKUPS_DIR / f"{path.stem}_{stamp}_{attempt}{path.suffix}"
        attempt += 1
    shutil.copy2(path, target)
    return target


@contextmanager
def _locked(paths: tuple[Path, ...], timeout: float | None) -> Iterator[None]:
    """Hold ``file_lock`` on every path, taken in the given order."""
    options = {} if timeout is None else {"timeout": timeout}
    with ExitStack() as stack:
        for path in paths:
            stack.enter_context(file_lock(path, **options))
        yield


def _as_key_set(refresh_keys: Collection[str]) -> frozenset[str]:
    """Return ``refresh_keys`` as a set; a bare string is rejected (it would split in letters)."""
    if isinstance(refresh_keys, str):
        raise TypeError(f"refresh_keys must be a collection of names, not {refresh_keys!r}")
    return frozenset(refresh_keys)


def _require_dict_items(keys: frozenset[str], **collections: Mapping[str, Any]) -> None:
    """Raise ``ValueError`` if any item of the named collections is not a dict."""
    for name, items in collections.items():
        for item_id, item in items.items():
            if not isinstance(item, dict):
                raise ValueError(
                    f"refresh_keys={sorted(keys)} need dict items, but {name}[{item_id!r}] "
                    f"is a {type(item).__name__}"
                )


def _without(item: Any, keys: frozenset[str]) -> Any:
    """Return a dict ``item`` minus ``keys``; anything else (and an empty ``keys``) as it is."""
    if not keys or item is _ABSENT:
        return item
    return {k: v for k, v in item.items() if k not in keys}


def _refresh(user_item: dict[str, Any], fresh_item: dict[str, Any], keys: frozenset[str]) -> Any:
    """Return the user's item with its ``keys`` taken from the fresh item (keeping key order)."""
    if not keys:
        return user_item
    refreshed = {}
    for key, value in user_item.items():
        if key not in keys:
            refreshed[key] = value
        elif key in fresh_item:
            refreshed[key] = fresh_item[key]
    for key, value in fresh_item.items():
        if key in keys and key not in refreshed:
            refreshed[key] = value
    return refreshed


def _read_object(path: Path) -> dict[str, Any] | None:
    """Parse the JSON object stored at ``path``; ``None`` if the file does not exist."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must hold a JSON object, found {type(payload).__name__}")
    return payload


def _read_items(path: Path, items_key: str, id_key: str | None) -> dict[str, Any] | None:
    """Return the item collection stored at ``path`` as ``{id: item}``; ``None`` if no file."""
    payload = _read_object(path)
    return None if payload is None else _index_items(payload, items_key, id_key, path)


def _index_items(
    payload: Mapping[str, Any], items_key: str, id_key: str | None, source: Path | str
) -> dict[str, Any]:
    """Return ``payload[items_key]`` as ``{id: item}``, checking its shape and its ids."""
    items = payload.get(items_key)
    shape = dict if id_key is None else list
    if not isinstance(items, shape):
        raise ValueError(
            f"{source}: '{items_key}' must be a {shape.__name__} of items "
            f"(id_key={id_key!r}), found {type(items).__name__}"
        )
    if id_key is None:
        return items
    indexed: dict[str, Any] = {}
    for position, item in enumerate(items):
        item_id = item.get(id_key) if isinstance(item, dict) else None
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"{source}: {items_key}[{position}] has no string '{id_key}'")
        if item_id in indexed:
            raise ValueError(f"{source}: duplicate {id_key} {item_id!r} in '{items_key}'")
        indexed[item_id] = item
    return indexed
