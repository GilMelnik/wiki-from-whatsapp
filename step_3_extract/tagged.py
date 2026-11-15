"""Tagged members: which group members that others tag are providers, by the owner's word.

Members recommend a provider who is in the group by tagging them. The chat writes a tag as
``@<digits>`` and the export lists it in the message's ``mentions`` (``[{tag, member}]``). The
phone holds no name for those members, so the owner says once, in the review site's Tagged
members page, who is a provider and under which public name. The table (``TAGGED_MEMBERS_PATH``,
private, never committed) is keyed by member id::

    {"members": {"<member id>": {"provider": true, "public_name": "…", "decided_at": "…"},
                 "<member id>": {"provider": false, "decided_at": "…"}}}

A member not in it is undecided, which reads like "not a provider". Step 3 shows a provider's
tag as ``@<public name>`` and any other tag as the member's alias (``utils.threads_io``).

``tagged_members`` counts, per tagged member, the tags, the different members who tag them and
the messages that do; ``waiting_members`` is how many undecided members are tagged by at least
``extract.tagged_members.min_taggers`` different members (the update's notice, the page's
default filter).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.config import section
from utils.export_dates import parse_android_datetime
from utils.threads_io import person_key

TAGGED_CONFIG = section("extract")["tagged_members"]
MIN_TAGGERS = int(TAGGED_CONFIG["min_taggers"])


def load_table(path: Path) -> dict[str, dict[str, Any]]:
    """The owner's decisions by member id; ``{}`` when the table does not exist yet."""

    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, dict):
        raise ValueError(
            f"{path} is not the tagged members table: expected an object "
            '{"members": {"<member id>": {"provider": ..., "decided_at": ...}}}'
        )
    return members


def provider_names(members: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Member id -> public name of every member the owner marked as a provider."""

    names = {}
    for member, decision in members.items():
        name = str(decision.get("public_name") or "").strip()
        if decision.get("provider") is True and name:
            names[member] = name
    return names


@dataclass
class TaggedMember:
    """How one member is tagged in the chat export (``datetime`` values are ISO-8601)."""

    member: str
    tags: list[str] = field(default_factory=list)  # the digits the chat writes, as seen
    message_ids: list[str] = field(default_factory=list)  # messages that tag them, in order
    taggers: set[str] = field(default_factory=set)  # other members who tag them
    first: str = ""
    last: str = ""

    def add(self, entry: Mapping[str, Any], tag: str) -> None:
        moment = parse_android_datetime(entry["date"], entry["time"]).isoformat()
        if tag not in self.tags:
            self.tags.append(tag)
        self.message_ids.append(str(entry["id"]))
        tagger = person_key(entry.get("sender"))
        if tagger and tagger != self.member:
            self.taggers.add(tagger)
        self.first = min(self.first or moment, moment)
        self.last = max(self.last, moment)


def tagged_members(entries: Iterable[Mapping[str, Any]]) -> dict[str, TaggedMember]:
    """Every member the export's messages tag, by member id (a message that tags the same
    member twice counts once)."""

    found: dict[str, TaggedMember] = {}
    for entry in entries:
        seen: set[str] = set()
        for mention in entry.get("mentions") or []:
            member, tag = str(mention.get("member") or ""), str(mention.get("tag") or "")
            if member and tag and member not in seen:
                seen.add(member)
                found.setdefault(member, TaggedMember(member)).add(entry, tag)
    return found


def waiting_members(export_path: Path, table_path: Path, min_taggers: int = MIN_TAGGERS) -> int:
    """How many undecided members of the export are tagged by ``min_taggers`` or more different
    members; 0 when there is no export."""

    if not export_path.is_file():
        return 0
    entries = json.loads(export_path.read_text(encoding="utf-8"))
    decided = load_table(table_path)
    return sum(
        1
        for member, tagged in tagged_members(entries).items()
        if member not in decided and len(tagged.taggers) >= min_taggers
    )
