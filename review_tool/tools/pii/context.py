"""What the privacy decision rests on besides the claim itself.

For each detail step 3 removed from a claim the owner needs to see where it came from and
whose it is: the claim's chat messages (from the chat export, shown with date, time and
sender) with the ones that hold the value marked, and the entities that list the value among
their contacts (step 4's entities file, edited copy first). Both files are optional: without
the export there are no messages to show, without the entities file no owners.

A value is matched loosely, the way people write it: an email ignoring case; a phone number
by its digits, with an international prefix read as the local leading zero (``+972 3-123
4567``, ``00972 31234567`` and ``03-1234567`` are one number); a short number (a service code,
fewer than ``PHONE_MIN_DIGITS`` digits) by its exact text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from step_1_threads_split.run import load_export_messages
from utils.config import section

PRIVACY_CONFIG = section("extract")["privacy_review"]
PHONE_MIN_DIGITS = PRIVACY_CONFIG["phone_min_digits"]
COUNTRY_CODE = PRIVACY_CONFIG["country_code"]
# The entity fields that list contacts, and what each says about the value.
CONTACT_FIELDS = (("contacts", "contact"), ("contacts_uncertain", "uncertain"), ("contacts_rejected", "rejected"))
_NOT_DIGITS = re.compile(r"\D")


def contact_key(kind: str, value: str) -> str:
    """The form two writings of the same contact share (see the module docstring)."""
    if kind != "phone":
        return value.strip().casefold()
    digits = _NOT_DIGITS.sub("", value).removeprefix("00")
    if len(digits) < PHONE_MIN_DIGITS:
        return value.strip()
    return f"0{digits.removeprefix(COUNTRY_CODE)}" if digits.startswith(COUNTRY_CODE) else digits


def holds(text: str, kind: str, value: str) -> bool:
    """Whether a message ``text`` contains the contact ``value`` (in any of its writings)."""
    key = contact_key(kind, value)
    if kind != "phone":
        return key in text.casefold()
    if not key.isdigit():
        return key in text
    # The number without its leading zero is in the digits of both local and international writings.
    return key.lstrip("0") in _NOT_DIGITS.sub("", text)


class ChatMessages:
    """The chat export's messages (C1 records) by id; empty when there is no export."""

    def __init__(self, export_path: Path) -> None:
        self.available = export_path.is_file()
        records = load_export_messages(export_path) if self.available else []
        self._by_id = {record["id"]: record for record in records}

    def claim_messages(
        self, claim: dict[str, Any], redactions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """The claim's supporting and opposing messages in time order, each with ``side`` and
        ``holds``: the numbers of the removed details whose value it contains."""
        found = []
        for side, ids in (("supporting", claim["supporting_message_ids"]), ("opposing", claim["opposing_message_ids"])):
            for message_id in ids:
                message = self._by_id.get(str(message_id))
                if message is None:
                    continue
                text = message.get("content") or ""
                marks = [i for i, item in enumerate(redactions) if holds(text, item["type"], item["value"])]
                found.append({**message, "side": side, "holds": marks})
        return sorted(found, key=lambda message: message["datetime"])


class KnownContacts:
    """Who lists a contact value: ``(kind, contact_key) -> [{entity_id, canonical_name, how}]``
    (``how``: contact, uncertain or rejected)."""

    def __init__(self, original: Path, edited: Path) -> None:
        source = edited if edited.is_file() else original
        self.available = source.is_file()
        entities = json.loads(source.read_text(encoding="utf-8")).get("entities", []) if self.available else []
        self._owners: dict[tuple[str, str], list[dict[str, str]]] = {}
        for entity in entities:
            for field, how in CONTACT_FIELDS:
                for kind, values in (entity.get(field) or {}).items():
                    for value in values or []:
                        self._owners.setdefault((kind, contact_key(kind, value)), []).append(
                            {
                                "entity_id": entity["entity_id"],
                                "canonical_name": entity.get("canonical_name") or "",
                                "how": how,
                            }
                        )

    def owners(self, kind: str, value: str) -> list[dict[str, str]]:
        return self._owners.get((kind, contact_key(kind, value)), [])


def with_context(
    claim: dict[str, Any], chat: ChatMessages, contacts: KnownContacts
) -> dict[str, Any]:
    """The claim detail plus ``messages``, each redaction's ``owners`` and what was available."""
    redactions = claim["redactions"]
    return {
        **claim,
        "messages": chat.claim_messages(claim, redactions),
        "redactions": [{**item, "owners": contacts.owners(item["type"], item["value"])} for item in redactions],
        "context": {"chat": chat.available, "entities": contacts.available},
    }
