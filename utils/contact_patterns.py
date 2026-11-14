"""Contact details in free text: e-mail addresses and phone numbers.

Step 3 redacts them from claims (``step_3_extract.scrub``); step 4 collects them as an
entity's contacts (``step_4_entities.records``).
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A run that contains 9-15 digits once separators are stripped (phone-like).
PHONE_RE = re.compile(r"(?<![\w])\+?[\d][\d\s().\-]{7,}\d(?![\w])")
# Short service hotlines: *5400, 5400*, (5400*).
SHORT_PHONE_RE = re.compile(
    r"(?<![\w])"
    r"(?:"
    r"\*\d{3,5}\b"
    r"|\(\d{3,5}\*\)"
    r"|\b\d{3,5}\*(?![\w])"
    r")"
)


def find_emails(text: str) -> list[str]:
    return EMAIL_RE.findall(text or "")


def find_phones(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for pattern in (PHONE_RE, SHORT_PHONE_RE):
        for match in pattern.finditer(text or ""):
            value = match.group(0)
            if value not in seen:
                seen.add(value)
                out.append(value)
    return out
