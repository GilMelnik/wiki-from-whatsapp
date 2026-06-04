import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

# Old WhatsApp export: M/D/YY, H:MM - Sender: message
MSG_HEADER = re.compile(
    r"^[\u200e\u200f\u202a-\u202e\u2066-\u2069]*"
    r"(\d{1,2}/\d{1,2}/\d{2,4}),\s+"
    r"(\d{1,2}:\d{2})\s+-\s+"
    r"(.*)$",
)

# New-format export header used only to find the overlap cutoff in _chat.txt
NEW_FORMAT_HEADER = re.compile(
    r"^[\u200e\u200f\u202a-\u202e\u2066-\u2069]*"
    r"\[(\d{1,2}/\d{1,2}/\d{2,4}),\s+"
    r"(\d{1,2}:\d{2}:\d{2}[\u202f\s]+(?:AM|PM))\]",
    re.IGNORECASE,
)

UNICODE_MARKS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
OMITTED_MARKERS = re.compile(
    r"[\u200e\u200f]?(?:<\s*)?(?:image|video|audio|sticker|document|gif|media)\s+omitted(?:\s*>)?",
    re.IGNORECASE,
)
EDITED_MARKER = re.compile(r"[\u200e\u200f]?<\s*This message was edited\s*>?", re.IGNORECASE)

SYSTEM_PATTERNS = (
    re.compile(r"messages and calls are end-to-end encrypted", re.IGNORECASE),
    re.compile(r"joined using (?:a group link|this group's invite link)", re.IGNORECASE),
    re.compile(r"\bwas added\.?$", re.IGNORECASE),
    re.compile(r"^(.+?) removed (.+)$"),
    re.compile(r"^(.+?) left\.?$"),
    re.compile(r"changed their phone number", re.IGNORECASE),
    re.compile(r"^this message was deleted\.?$", re.IGNORECASE),
    re.compile(r"^you added .+$", re.IGNORECASE),
    re.compile(r"^(.+?) added (.+)$"),
    re.compile(r"created group", re.IGNORECASE),
)


def clean_text(text: str) -> str:
    return UNICODE_MARKS.sub("", text).replace("\u202f", " ").strip()


def normalize_content(content: str) -> str:
    content = UNICODE_MARKS.sub("", content)
    content = EDITED_MARKER.sub("", content)
    content = OMITTED_MARKERS.sub("", content)
    return content.strip()


def parse_old_datetime(date_str: str, time_str: str) -> datetime:
    normalized_time = time_str.replace("\u202f", " ").strip()
    for fmt in ("%m/%d/%y, %H:%M", "%m/%d/%Y, %H:%M"):
        try:
            return datetime.strptime(f"{date_str}, {normalized_time}", fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid date format: {date_str}, {time_str}")


def parse_new_format_datetime(date_str: str, time_str: str) -> datetime:
    normalized_time = time_str.replace("\u202f", " ").strip()
    for fmt in ("%m/%d/%y, %I:%M:%S %p", "%m/%d/%Y, %I:%M:%S %p"):
        try:
            return datetime.strptime(f"{date_str}, {normalized_time}", fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid date format: {date_str}, {time_str}")


def get_oldest_new_format_timestamp(file_path: Path) -> Optional[datetime]:
    oldest = None

    with file_path.open(encoding="utf-8") as f:
        for line in f:
            match = NEW_FORMAT_HEADER.match(line)
            if not match:
                continue
            date_str, time_str = match.groups()
            timestamp = parse_new_format_datetime(date_str, time_str)
            if oldest is None or timestamp < oldest:
                oldest = timestamp

    return oldest


def split_sender_content(rest: str) -> Tuple[Optional[str], str]:
    if ": " in rest:
        sender, content = rest.split(": ", 1)
        return sender, content
    return None, rest


def is_system_message(content: str) -> bool:
    cleaned = clean_text(content)
    if not cleaned or cleaned.lower() == "null":
        return True

    for pattern in SYSTEM_PATTERNS:
        if not pattern.search(cleaned):
            continue
        if pattern.pattern == r"^(.+?) added (.+)$" and "added by" in cleaned.lower():
            continue
        return True

    return False


def parse_messages(file_path: Path, cutoff: Optional[datetime] = None) -> List[Dict]:
    messages = []
    last_timestamp = None

    with file_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n\r")
            match = MSG_HEADER.match(line)

            if match:
                date_str, time_str, rest = match.groups()
                timestamp = parse_old_datetime(date_str, time_str)
                if cutoff is not None and timestamp >= cutoff:
                    last_timestamp = None
                    continue

                sender, content = split_sender_content(rest)
                content = normalize_content(content)
                if sender is None:
                    if is_system_message(rest):
                        continue
                    sender = ""
                elif is_system_message(content):
                    continue

                sender = clean_text(sender)

                if (
                    messages
                    and sender == messages[-1]["sender"]
                    and last_timestamp is not None
                    and (timestamp - last_timestamp) < timedelta(minutes=2)
                ):
                    messages[-1]["content"] += "\n" + content
                else:
                    messages.append({
                        "datetime": timestamp.isoformat(),
                        "sender": sender,
                        "content": content,
                    })
                last_timestamp = timestamp
            elif messages:
                if cutoff is not None and (
                    last_timestamp is None or last_timestamp >= cutoff
                ):
                    continue
                continuation = normalize_content(line)
                if continuation:
                    messages[-1]["content"] += "\n" + continuation
                else:
                    messages[-1]["content"] += "\n"

    return messages


def main():
    data_path = Path(__file__).resolve().parent.parent / "data"
    input_file = data_path / "_chat_old.txt"
    cutoff_file = data_path / "_chat.txt"
    output_file = data_path / "messages_old.json"

    cutoff = get_oldest_new_format_timestamp(cutoff_file)
    messages = parse_messages(input_file, cutoff=cutoff)

    with output_file.open("w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
