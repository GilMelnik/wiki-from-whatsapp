"""Check reaction_sentiment.json covers all emojis in chat export."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from utils.logging_setup import setup_step_logging
from utils.paths import CHAT_ANDROID_PATH, SHARED

SENTIMENT_PATH = Path(__file__).with_name("reaction_sentiment.json")


def reaction_counts(chat_path: Path) -> Counter[str]:
    with chat_path.open(encoding="utf-8") as f:
        messages = json.load(f)
    counts: Counter[str] = Counter()
    for msg in messages:
        for rx in msg.get("reactions") or []:
            emoji = rx.get("emoji") or ""
            if not emoji:
                continue
            n = rx.get("count")
            if n is None:
                n = len(rx.get("senders") or [])
            counts[emoji] += n
    return counts


def main(argv: list[str] | None = None) -> int:
    logger = setup_step_logging(SHARED)
    chat_path = Path(argv[1]) if argv and len(argv) > 1 else CHAT_ANDROID_PATH
    counts = reaction_counts(chat_path)
    with SENTIMENT_PATH.open(encoding="utf-8") as f:
        sentiment = json.load(f)

    missing = sorted(set(counts) - set(sentiment), key=lambda e: (-counts[e], e))
    logger.info(f"chat: {chat_path}")
    logger.info(f"unique emojis in chat: {len(counts)}")
    logger.info(f"mapped emojis: {len(sentiment)}")
    logger.info(f"missing: {len(missing)}")
    for emoji in missing:
        logger.info(f"  {emoji!r}\t{counts[emoji]}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
