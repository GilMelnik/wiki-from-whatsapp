from __future__ import annotations

from datetime import datetime
from typing import Any

from preprocessing.parse_messages import normalize_content


def parse_android_datetime(date_str: str, time_str: str) -> datetime:
    """Parse DD/MM/YYYY date and HH:MM time from Android phone export."""
    day, month, year = date_str.split("/")
    hour, minute = time_str.split(":")
    return datetime(int(year), int(month), int(day), int(hour), int(minute))


def _sender_user_name(sender: dict[str, Any] | str) -> str:
    if isinstance(sender, dict):
        return sender["user_name"]
    return sender


class Message:
    def __init__(
        self,
        date_time: datetime,
        sender: str,
        content: str,
        message_id: str | None = None,
        quote: dict[str, str] | None = None,
        reactions: list[dict[str, Any]] | None = None,
    ):
        self.id = message_id
        self.datetime = date_time
        self.sender = sender
        self.content = content
        self.quote = quote
        self.reactions = reactions or []

    @classmethod
    def from_android_dict(cls, dict_data: dict[str, Any]) -> Message:
        quote = None
        if dict_data.get("quote"):
            q = dict_data["quote"]
            quote = {
                "sender": _sender_user_name(q["sender"]),
                "text": q["text"],
            }

        reactions = []
        for reaction in dict_data.get("reactions") or []:
            reactions.append(
                {
                    "emoji": reaction["emoji"],
                    "senders": [
                        _sender_user_name(s) for s in reaction.get("senders", [])
                    ],
                }
            )

        return cls(
            date_time=parse_android_datetime(dict_data["date"], dict_data["time"]),
            sender=_sender_user_name(dict_data["sender"]),
            content=dict_data.get("text", ""),
            message_id=dict_data.get("id"),
            quote=quote,
            reactions=reactions,
        )

    def normalized_content(self) -> str:
        return normalize_content(self.content)

    def quote_lookup_key(self) -> tuple[str, str] | None:
        if not self.quote:
            return None
        return (self.quote["sender"], normalize_content(self.quote["text"]))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "datetime": self.datetime.isoformat(),
            "sender": self.sender,
            "content": self.content,
        }
        if self.quote is not None:
            result["quote"] = self.quote
        if self.reactions:
            result["reactions"] = self.reactions
        return result
