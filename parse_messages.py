import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

MSG_PATTERN = \
    re.compile(r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s+([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\s*(?:AM|PM|am|pm|A\.M\.|P\.M\.|a\.m\.|p\.m\.)?)\]\s+(.*?): (.*)")


def parse_datetime(date_str: str, time_str: str) -> datetime:
    for fmt in ("%m/%d/%y, %H:%M", "%m/%d/%Y, %H:%M"):
        try:
            return datetime.strptime(f"{date_str}, {time_str}", fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid date format: {date_str}, {time_str}")


def is_non_content(content: str) -> bool:
    content = content.strip()
    return (
        not content or
        content.lower() == "null" or
        content.lower() == "\n" or
        "joined using this group's invite link" in content or
        "added" in content and "+" in content or
        "removed" in content or
        "<media omitted>" in content.lower() or
        "Messages and calls are end-to-end encrypted" in content or
        'This message was deleted' in content
    )


def parse_messages(file_path: Path) -> List[Dict]:
    messages = []

    with file_path.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            match = MSG_PATTERN.match(line)

            if match:
                date_str, time_str, sender, content = match.groups()
                timestamp = parse_datetime(date_str, time_str)
                if is_non_content(content):
                    continue

                if (
                    messages and
                    sender == messages[-1]['sender'] and
                    (timestamp - datetime.fromisoformat(messages[-1]['datetime'])) < timedelta(minutes=2)
                ):
                    messages[-1]['content'] += "\n" + content
                else:
                    messages.append({
                        "datetime": timestamp.isoformat(),
                        "sender": sender,
                        "content": content.strip()
                    })
            elif messages and not is_non_content(line):
                messages[-1]["content"] += "\n" + line

    return messages


def create_phone_book(messages: List[Dict]) -> Tuple[Dict[str, str], Dict[str, str]]:
    num_to_contact = dict()
    contact_to_num = dict()
    for message in messages:
        contact = num_to_contact.get(message['sender'], f'contact_{len(num_to_contact)}')
        num_to_contact[message['sender']] = contact
        contact_to_num[contact] = message['sender']
    return num_to_contact, contact_to_num


def replace_num_with_contact(messages: List[Dict], num_to_contact: Dict) -> List[Dict]:
    new_messages = list()
    for message in messages:
        message['sender'] = num_to_contact[message['sender']]
        new_messages.append(message)
    return new_messages


def main():
    data_path = Path(__file__).resolve().parent / 'data'
    input_file = data_path / '_chat.txt'
    output_file = data_path / "messages.json"
    phone_book_file = data_path / "phone_book.json"

    messages = parse_messages(input_file)
    num_to_contact, contact_to_num = create_phone_book(messages)
    anonymous_messages = replace_num_with_contact(messages, num_to_contact)

    with output_file.open("w", encoding="utf-8") as f:
        json.dump(anonymous_messages, f, ensure_ascii=False, indent=2)

    with phone_book_file.open('w', encoding="utf-8") as f:
        json.dump([num_to_contact, contact_to_num], f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
