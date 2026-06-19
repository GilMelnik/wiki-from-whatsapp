"""
Map phone-number senders in messages_old.json to display names from _chat.txt.

Matches messages between _chat_old.txt and _chat.txt by normalized content in the
period where both exports overlap. Message order is preserved; timestamps may differ
because the exports come from different users/time zones.
"""

import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Tuple

from parse_messages import parse_messages as parse_new_messages
from parse_messages_old_format import (
    clean_text,
    get_oldest_new_format_timestamp,
    normalize_content,
    parse_messages as parse_old_messages,
)

UNICODE_MARKS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\u2068\u2069]")
PHONE_IN_SENDER = re.compile(r"^\+?[\d\s\-‑–—().]+$")


def normalize_phone(sender: str) -> str:
    """Digits-only key for matching phone numbers across export formats."""
    return re.sub(r"\D", "", sender)


def is_phone_sender(sender: str) -> bool:
    digits = normalize_phone(sender)
    return len(digits) >= 9 and PHONE_IN_SENDER.match(clean_text(sender).replace("\u202f", " "))


def clean_nickname(sender: str) -> str:
    name = clean_text(sender).lstrip("~").strip()
    return UNICODE_MARKS.sub("", name).strip()


def align_senders_by_content(
    old_messages: List[dict],
    new_messages: List[dict],
) -> Counter:
    """
    Align message sequences by normalized content using difflib.
    Handles duplicate texts and timing skew better than greedy window search.
    """
    old_nonempty = [
        m for m in old_messages if normalize_content(m["content"])
    ]
    new_nonempty = [
        m for m in new_messages if normalize_content(m["content"])
    ]
    old_contents = [normalize_content(m["content"]) for m in old_nonempty]
    new_contents = [normalize_content(m["content"]) for m in new_nonempty]

    matcher = SequenceMatcher(None, old_contents, new_contents, autojunk=False)
    pair_counts: Counter = Counter()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(i2 - i1):
            old_sender = old_nonempty[i1 + offset]["sender"]
            new_sender = new_nonempty[j1 + offset]["sender"]
            pair_counts[(old_sender, new_sender)] += 1

    return pair_counts


def build_sender_mapping(pair_counts: Counter) -> Dict[str, str]:
    """
    For each old phone sender, pick the most frequent new display name.
    Prefer nicknames over phone numbers when both appear for the same person.
    """
    votes: Dict[str, Counter] = defaultdict(Counter)

    for (old_sender, new_sender), count in pair_counts.items():
        votes[old_sender][new_sender] += count

    mapping: Dict[str, str] = {}
    for old_sender, counter in votes.items():
        nickname, _ = counter.most_common(1)[0]
        if not is_phone_sender(nickname):
            mapping[old_sender] = nickname

    return mapping


def apply_mapping(messages: List[dict], mapping: Dict[str, str]) -> Tuple[int, List[str]]:
    updated = 0
    unmapped_senders = set()

    for message in messages:
        sender = message["sender"]
        if sender in mapping:
            message["sender"] = mapping[sender]
            updated += 1
        elif is_phone_sender(sender):
            unmapped_senders.add(sender)

    return updated, sorted(unmapped_senders)


def main():
    data_dir = Path(__file__).resolve().parent.parent / "data"
    old_chat = data_dir / "_chat_old.txt"
    new_chat = data_dir / "_chat.txt"
    messages_file = data_dir / "messages_old.json"

    cutoff = get_oldest_new_format_timestamp(new_chat)
    if cutoff is None:
        raise RuntimeError(f"Could not find timestamps in {new_chat}")

    all_old = parse_old_messages(old_chat, cutoff=None)
    new_messages = parse_new_messages(new_chat)
    overlap_old = [m for m in all_old if m["datetime"] >= cutoff.isoformat()]

    pair_counts = align_senders_by_content(overlap_old, new_messages)
    mapping = build_sender_mapping(pair_counts)

    with messages_file.open(encoding="utf-8") as f:
        messages = json.load(f)

    updated_count, unmapped = apply_mapping(messages, mapping)

    with messages_file.open("w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

    unique_old_phones = {
        m["sender"] for m in messages if is_phone_sender(m["sender"])
    }

    print(f"Overlap period starts: {cutoff.isoformat()}")
    print(f"Matched message pairs: {sum(pair_counts.values())}")
    print(f"Phone → nickname mappings: {len(mapping)}")
    print(f"Messages updated: {updated_count} / {len(messages)}")
    print(f"Senders still showing as phone: {len(unique_old_phones)}")
    if unmapped:
        print(f"Unmapped phones ({len(unmapped)}):")
        for phone in unmapped[:20]:
            print(f"  {phone}")
        if len(unmapped) > 20:
            print(f"  ... and {len(unmapped) - 20} more")
