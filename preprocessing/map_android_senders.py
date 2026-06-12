"""
Map Android phone-export sender IDs to display nicknames from a reference message file.

Aligns messages between chat_android.json and messages_combined.json by normalized
content. Message order is preserved; timestamps may differ between exports.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from preprocessing.parse_messages import clean_text, normalize_content

UNICODE_MARKS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\u2068\u2069]")
PHONE_IN_SENDER = re.compile(r"^\+?[\d\s\-‑–—().]+$")


def normalize_phone(sender: str) -> str:
    return re.sub(r"\D", "", sender)


def is_phone_sender(sender: str) -> bool:
    digits = normalize_phone(sender)
    return len(digits) >= 9 and PHONE_IN_SENDER.match(
        clean_text(sender).replace("\u202f", " ")
    )


def is_android_id(sender: str) -> bool:
    return sender.endswith("@s.whatsapp.net") or sender.endswith("@lid")


def load_android_messages(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_reference_messages(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def align_senders_by_content(
    android_messages: list[dict[str, Any]],
    reference_messages: list[dict[str, Any]],
) -> tuple[Counter, Counter]:
    """
    Align message sequences by normalized content using difflib.
    Returns (all_pair_counts, outgoing_pair_counts) for special-casing "You".
    """
    android_nonempty = [
        m for m in android_messages if normalize_content(m.get("text", ""))
    ]
    reference_nonempty = [
        m for m in reference_messages if normalize_content(m.get("content", ""))
    ]
    android_contents = [normalize_content(m["text"]) for m in android_nonempty]
    reference_contents = [
        normalize_content(m["content"]) for m in reference_nonempty
    ]

    matcher = SequenceMatcher(
        None, android_contents, reference_contents, autojunk=False
    )
    pair_counts: Counter = Counter()
    outgoing_pair_counts: Counter = Counter()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(i2 - i1):
            android_msg = android_nonempty[i1 + offset]
            reference_msg = reference_nonempty[j1 + offset]
            android_sender = android_msg["sender"]["user_name"]
            reference_sender = reference_msg["sender"]
            pair_counts[(android_sender, reference_sender)] += 1
            if android_msg.get("is_outgoing"):
                outgoing_pair_counts[(android_sender, reference_sender)] += 1

    return pair_counts, outgoing_pair_counts


def build_sender_mapping(
    pair_counts: Counter,
    outgoing_pair_counts: Counter,
) -> dict[str, str]:
    votes: dict[str, Counter] = defaultdict(Counter)
    outgoing_votes: dict[str, Counter] = defaultdict(Counter)

    for (android_sender, reference_sender), count in pair_counts.items():
        votes[android_sender][reference_sender] += count

    for (android_sender, reference_sender), count in outgoing_pair_counts.items():
        outgoing_votes[android_sender][reference_sender] += count

    mapping: dict[str, str] = {}
    for android_sender, counter in votes.items():
        if android_sender == "You":
            if android_sender in outgoing_votes and outgoing_votes[android_sender]:
                nickname, _ = outgoing_votes[android_sender].most_common(1)[0]
            else:
                nickname, _ = counter.most_common(1)[0]
        else:
            non_phone = [
                (name, count)
                for name, count in counter.most_common()
                if not is_phone_sender(name)
            ]
            if non_phone:
                nickname = non_phone[0][0]
            else:
                nickname, _ = counter.most_common(1)[0]

        if is_android_id(android_sender) or android_sender == "You":
            mapping[android_sender] = nickname

    return mapping


def find_unmapped_senders(
    android_messages: list[dict[str, Any]],
    mapping: dict[str, str],
) -> list[str]:
    all_senders = {
        m["sender"]["user_name"] for m in android_messages
    }
    for reaction in (r for m in android_messages for r in m.get("reactions", [])):
        for sender in reaction.get("senders", []):
            all_senders.add(sender["user_name"])
    return sorted(s for s in all_senders if s not in mapping)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map Android sender IDs to reference nicknames by content alignment."
    )
    data_dir = Path(__file__).resolve().parent.parent / "data"
    parser.add_argument(
        "--android",
        type=Path,
        default=data_dir / "chats_from_phone" / "chat_android.json",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=data_dir / "messages_combined.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_dir / "sender_id_to_nickname.json",
    )
    args = parser.parse_args()

    android_messages = load_android_messages(args.android)
    reference_messages = load_reference_messages(args.reference)

    pair_counts, outgoing_pair_counts = align_senders_by_content(
        android_messages, reference_messages
    )
    mapping = build_sender_mapping(pair_counts, outgoing_pair_counts)
    unmapped = find_unmapped_senders(android_messages, mapping)

    payload: dict[str, Any] = dict(mapping)
    payload["_metadata"] = {
        "android_source": str(args.android),
        "reference_source": str(args.reference),
        "matched_pairs": sum(pair_counts.values()),
        "mapped_senders": len(mapping),
        "unmapped_senders": unmapped,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Matched message pairs: {payload['_metadata']['matched_pairs']}")
    print(f"Sender ID → nickname mappings: {len(mapping)}")
    print(f"Wrote {args.output}")
    if unmapped:
        print(f"Unmapped senders ({len(unmapped)}):")
        for sender in unmapped[:20]:
            print(f"  {sender}")
        if len(unmapped) > 20:
            print(f"  ... and {len(unmapped) - 20} more")


if __name__ == "__main__":
    main()
