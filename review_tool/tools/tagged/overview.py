"""What the decision on a tagged member rests on, from the chat export, the conversations and the
claims.

Per member the chat export tags (``step_3_extract.tagged.tagged_members``): how many messages
tag them and how many different members, how many messages they wrote, the first and last tag,
the tagging messages (C1 records, shown with date, time and sender) with the conversation each
sits in, and the suggested names: the entities step 3's claims carry in those conversations,
those of claims the tagging messages support first, then the most frequent. Nothing here calls
a model or decides anything.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from review_tool.artifacts import ArtifactNode
from step_1_threads_split.message import message_record
from step_3_extract.tagged import TaggedMember, tagged_members
from utils.threads_io import person_key

PROVIDER = "provider"
NOT_PROVIDER = "not_provider"
UNDECIDED = "undecided"
# The list's filters: undecided members tagged by enough different members (the default view),
# every undecided one, the decided ones, everyone.
WAITING = "waiting"
SHOWS = (WAITING, UNDECIDED, "decided", "all")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def decision_status(decision: Mapping[str, Any] | None) -> str:
    if decision is None:
        return UNDECIDED
    return PROVIDER if decision.get("provider") is True else NOT_PROVIDER


class TaggedOverview:
    """Every tagged member of the chat export with the context of the decision."""

    def __init__(self, export_path: Path, threads: ArtifactNode, claims: ArtifactNode) -> None:
        entries = _read(export_path) or []
        self.export_available = export_path.is_file()
        self.members: dict[str, TaggedMember] = tagged_members(entries)
        written = Counter(person_key(entry.get("sender")) for entry in entries)
        self.written = {member: written[member] for member in self.members}
        wanted = {message_id for tagged in self.members.values() for message_id in tagged.message_ids}
        self.records = {
            str(entry["id"]): message_record(entry) for entry in entries if str(entry.get("id")) in wanted
        }
        self.thread_of: dict[str, str] = {}
        self.thread_starts: dict[str, str] = {}
        for thread in (_read(threads.effective) or {}).get("threads", []):
            self.thread_starts[thread["thread_id"]] = thread.get("start_time") or ""
            for message_id in thread.get("message_ids") or [m["id"] for m in thread.get("messages", [])]:
                if str(message_id) in wanted:
                    self.thread_of[str(message_id)] = thread["thread_id"]
        self.claims_by_thread: dict[str, list[dict[str, Any]]] = {}
        for claim in (_read(claims.effective) or {}).get("claims", []):
            self.claims_by_thread.setdefault(claim["thread_id"], []).append(claim)

    def threads_of(self, member: str) -> list[str]:
        """The conversations of the member's tagging messages, in the order of their first tag."""
        found = (self.thread_of.get(message_id) for message_id in self.members[member].message_ids)
        return list(dict.fromkeys(thread_id for thread_id in found if thread_id))

    def row(self, member: str, decision: Mapping[str, Any] | None, min_taggers: int) -> dict[str, Any]:
        tagged = self.members[member]
        status = decision_status(decision)
        return {
            "member": member,
            "tags": tagged.tags,
            "tag_count": len(tagged.message_ids),
            "tagger_count": len(tagged.taggers),
            "written": self.written[member],
            "first": tagged.first,
            "last": tagged.last,
            "thread_count": len(self.threads_of(member)),
            "status": status,
            "waiting": status == UNDECIDED and len(tagged.taggers) >= min_taggers,
            "public_name": (decision or {}).get("public_name"),
            "decided_at": (decision or {}).get("decided_at"),
        }

    def rows(
        self, decisions: Mapping[str, Mapping[str, Any]], show: str, min_taggers: int
    ) -> list[dict[str, Any]]:
        """The members ``show`` selects, the most tagged first."""
        rows = [self.row(member, decisions.get(member), min_taggers) for member in self.members]
        chosen = {
            WAITING: lambda row: row["waiting"],
            UNDECIDED: lambda row: row["status"] == UNDECIDED,
            "decided": lambda row: row["status"] != UNDECIDED,
            "all": lambda row: True,
        }[show]
        return sorted(
            (row for row in rows if chosen(row)),
            key=lambda row: (-row["tag_count"], -row["tagger_count"], row["member"]),
        )

    def counts(self, decisions: Mapping[str, Mapping[str, Any]], min_taggers: int) -> dict[str, int]:
        rows = [self.row(member, decisions.get(member), min_taggers) for member in self.members]
        statuses = Counter(row["status"] for row in rows)
        return {
            "all": len(rows),
            WAITING: sum(1 for row in rows if row["waiting"]),
            UNDECIDED: statuses[UNDECIDED],
            PROVIDER: statuses[PROVIDER],
            NOT_PROVIDER: statuses[NOT_PROVIDER],
        }

    def suggestions(self, member: str) -> list[dict[str, Any]]:
        """Entity names of the claims of the member's conversations: ``near`` counts the claims
        a tagging message supports, ``claims`` all of them; the nearest and most frequent first."""
        tagging = set(self.members[member].message_ids)
        claims: Counter[str] = Counter()
        near: Counter[str] = Counter()
        for thread_id in self.threads_of(member):
            for claim in self.claims_by_thread.get(thread_id, []):
                supported = bool(tagging & set(claim.get("supporting_message_ids") or []))
                for name in dict.fromkeys(str(entity).strip() for entity in claim.get("entities") or []):
                    if name:
                        claims[name] += 1
                        near[name] += supported
        return [
            {"name": name, "claims": claims[name], "near": near[name]}
            for name in sorted(claims, key=lambda name: (-near[name], -claims[name], name))
        ]

    def detail(self, member: str, decision: Mapping[str, Any] | None, min_taggers: int) -> dict[str, Any]:
        """The row plus the tagging messages, their conversations and the suggested names."""
        tagged = self.members[member]
        messages = [
            {**self.records[message_id], "thread_id": self.thread_of.get(message_id)}
            for message_id in tagged.message_ids
            if self.records.get(message_id)
        ]
        threads = Counter(self.thread_of.get(message_id) for message_id in tagged.message_ids)
        return {
            **self.row(member, decision, min_taggers),
            "messages": sorted(messages, key=lambda message: message["datetime"]),
            "threads": [
                {"thread_id": thread_id, "start_time": self.thread_starts.get(thread_id), "tag_count": threads[thread_id]}
                for thread_id in self.threads_of(member)
            ],
            "suggestions": self.suggestions(member),
        }
