"""Deterministic PII scrub safety net.

Auto-redacts only high-confidence contact PII that should never appear in wiki
content: phone numbers and email addresses. Member and provider names are left
intact; anonymity is enforced by the extraction prompt and human draft review.
Step 4 finds an entity's contacts with the same patterns (``step_4_entities.records``).

A run of digits is phone-like by its shape alone, so two things that share the shape are
left out (``phone_matches``): calendar dates (22.6.2023, 2023-06-22, 10-12.6.2024, 2023-2024)
and prices (an amount or a range of amounts next to a currency mark: 1500-2000$). Both rules
are narrow on purpose: when in doubt a run is still redacted and a person decides.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from utils.config import section

PRIVACY_CONFIG = section("extract")["privacy_review"]
REDACTION_MARK = "[הוסר]"
# While scrubbing, each match is replaced by one supplementary private-use character (never
# a word character, digit or separator, so later patterns see it like the mark) numbered
# from here; chat text does not contain these code points.
_PLACEHOLDER_BASE = 0xF0000
_PLACEHOLDER_RE = re.compile("[\U000F0000-\U000FFFFD]")

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

# A calendar date inside a phone-like run: day and month (either order) and a year, with
# one separator; or year-month-day.
_DATE_RE = re.compile(r"(\d{1,2})([.\-])(\d{1,2})\2(\d{4}|\d{2})")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_FIRST_YEAR, _LAST_YEAR = PRIVACY_CONFIG["date_years"]
_MAX_DAY, _MAX_MONTH = 31, 12
# An amount (1500, 1.500.000, 3.45) or a range of two, as the whole of a phone-like run.
_AMOUNT = r"(?:[1-9]\d{0,2}(?:[. ]\d{3})+|[1-9]\d*(?:\.\d{1,2})?)"
_PRICE_RE = re.compile(rf"{_AMOUNT}(?:\s?-\s?{_AMOUNT})?")
_CURRENCY = "|".join(
    re.escape(mark) for mark in sorted(PRIVACY_CONFIG["currency_marks"], key=len, reverse=True)
)
_CURRENCY_AFTER_RE = re.compile(rf"\s?(?:{_CURRENCY})(?!\w)")
_CURRENCY_BEFORE_RE = re.compile(rf"(?:{_CURRENCY})\s?$")

# Forbidden, offensive framing of the surrogate as the child's "mother". The
# surrogate is never the mother of the child; collapse the phrase to the correct
# term, keeping the definite article / plural of the surrogate word itself.
#
# Two patterns, deliberately conservative to avoid corrupting the very common
# Hebrew word "אם" meaning *if* (e.g. "אם הפונדקאית חתמה" = "if the surrogate
# signed"):
#   1. Unambiguous mother nouns (אמא / אימא / אמהות / אימהות), with an optional
#      proclitic and definite article: e.g. "אמא פונדקאית", "האמא הפונדקאית",
#      "לאמא הפונדקאית", "אמהות פונדקאיות".
#   2. "אם" ONLY when it carries a preposition (ל/מ/כ) that the conjunction
#      "if" can never take: e.g. "לאם הפונדקאית". Bare "אם", "האם", "ואם",
#      "באם" are left untouched (handled by the prompt + human review).
_MOTHER_NOUN_RE = re.compile(
    r"(?P<pre>[למכבוש]?)ה?(?:אמא|אימא|אמהות|אימהות)[\s\u05be\-]+"
    r"(?P<sur>ה?פונדקאי(?:ות|ת))"
)
_MOTHER_EM_RE = re.compile(
    r"(?P<pre>[למכ])אם[\s\u05be\-]+(?P<sur>ה?פונדקאי(?:ות|ת))"
)

# Shared prompt directive so every LLM stage avoids producing the term at all.
FORBIDDEN_TERM_INSTRUCTION = (
    'אסור בתכלית האיסור להשתמש בצירוף "אמא פונדקאית" / "אם פונדקאית" / '
    '"האמא הפונדקאית" / "אמהות פונדקאיות" או כל הטיה אחרת המתארת את הפונדקאית '
    'כ"אם" או כ"אמא" — זהו מונח שגוי ופוגעני. השתמש אך ורק ב"פונדקאית" '
    '(או "נושאת").'
)


def _collapse_to_surrogate(match: re.Match[str]) -> str:
    """Drop the "mother" word, keeping the proclitic and the surrogate word.

    Re-attaches a leading preposition, contracting ל/ב/כ + ה (e.g.
    "לאם הפונדקאית" -> "לפונדקאית", "מהאמא הפונדקאיות" -> "מהפונדקאיות").
    """

    pre = match.group("pre")
    sur = match.group("sur")
    if pre in ("ל", "ב", "כ") and sur.startswith("ה"):
        sur = sur[1:]
    return f"{pre}{sur}"


def correct_surrogate_terminology(text: str) -> str:
    """Strip the offensive "surrogate mother" phrasing from text.

    Replaces variants such as "אמא פונדקאית", "האמא הפונדקאית",
    "אמהות פונדקאיות" and "לאם הפונדקאית" with the correct term, preserving the
    surrogate word's definite article and number. Acts as a deterministic safety
    net behind the prompt instruction above. Intentionally does not touch bare
    "אם"/"האם" to avoid mangling the conjunction "if".
    """

    if not text:
        return text
    text = _MOTHER_NOUN_RE.sub(_collapse_to_surrogate, text)
    text = _MOTHER_EM_RE.sub(_collapse_to_surrogate, text)
    return text


@dataclass
class ScrubResult:
    text: str
    redactions: list[dict[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.redactions)


def _placeholder(index: int) -> str:
    return chr(_PLACEHOLDER_BASE + index)


def _is_year(number: str) -> bool:
    return _FIRST_YEAR <= int(number) <= _LAST_YEAR


def _is_date(token: str) -> bool:
    """A calendar date: 22.6.2023, 22-6-23, 2023-06-22."""
    if match := _DATE_RE.fullmatch(token):
        first, second, year = int(match[1]), int(match[3]), match[4]
        day_month = 1 <= first <= _MAX_DAY and 1 <= second <= _MAX_MONTH
        month_day = 1 <= first <= _MAX_MONTH and 1 <= second <= _MAX_DAY
        return (day_month or month_day) and (len(year) == 2 or _is_year(year))
    if match := _ISO_DATE_RE.fullmatch(token):
        return _is_year(match[1]) and 1 <= int(match[2]) <= _MAX_MONTH and 1 <= int(match[3]) <= _MAX_DAY
    return False


def _is_date_token(token: str) -> bool:
    """A date, or a range: of dates (1.10.2023-5.10.2023), of days (10-12.6.2024), of years
    (2023-2024)."""
    if _is_date(token):
        return True
    start, hyphen, end = token.partition("-")
    if not hyphen or not start.isdigit():
        return "." in end and _is_date(start) and _is_date(end)
    if "." in end:
        return 1 <= int(start) <= _MAX_DAY and _is_date(end)
    return end.isdigit() and len(start) == len(end) == 4 and _is_year(start) and _is_year(end)


def _only_dates(run: str) -> bool:
    """Whether a phone-like run is made of dates, with too few other digits (a time, the
    start of an amount) to hold a phone number."""
    tokens = [token.strip("()") for token in run.split()]
    other_digits = sum(
        sum(char.isdigit() for char in token) for token in tokens if not _is_date_token(token)
    )
    return any(map(_is_date_token, tokens)) and other_digits < PRIVACY_CONFIG["phone_min_digits"]


def _is_price(text: str, match: re.Match[str]) -> bool:
    """Whether a phone-like run is an amount, or a range of amounts, with a currency mark
    right after or right before it."""
    if not _PRICE_RE.fullmatch(match.group(0)):
        return False
    return bool(
        _CURRENCY_AFTER_RE.match(text, match.end())
        or _CURRENCY_BEFORE_RE.search(text[: match.start()])
    )


def phone_matches(text: str) -> list[re.Match[str]]:
    """The phone-like runs of ``text`` that are neither calendar dates nor a price."""
    return [
        match
        for match in PHONE_RE.finditer(text)
        if not _only_dates(match.group(0)) and not _is_price(text, match)
    ]


def scrub_text(text: str) -> ScrubResult:
    """Replace emails and phone numbers with ``REDACTION_MARK``.

    ``redactions`` lists one entry per mark, in the order the marks appear in the text, so
    ``restore_scrubbed_text`` puts every value back in its own place. (The patterns run one
    after the other; each match is first replaced by a placeholder that the later patterns
    treat exactly like the mark, then the placeholders are numbered in text order.)
    """
    if not text:
        return ScrubResult(text="")

    found: list[dict[str, str]] = []
    result = text

    def _redact(find: Callable[[str], Iterable[re.Match[str]]], kind: str) -> None:
        nonlocal result
        pieces: list[str] = []
        last = 0
        for match in find(result):
            found.append({"type": kind, "value": match.group(0)})
            pieces += [result[last : match.start()], _placeholder(len(found) - 1)]
            last = match.end()
        result = "".join(pieces) + result[last:]

    _redact(EMAIL_RE.finditer, "email")
    _redact(phone_matches, "phone")
    _redact(SHORT_PHONE_RE.finditer, "phone")

    in_text_order = [
        found[ord(match.group(0)) - _PLACEHOLDER_BASE]
        for match in _PLACEHOLDER_RE.finditer(result)
    ]
    return ScrubResult(
        text=_PLACEHOLDER_RE.sub(REDACTION_MARK, result), redactions=in_text_order
    )


def scrub_claims(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Scrub ``claim_text`` in place; return a summary of what was changed."""

    total_redactions = 0
    pii_review_claims = 0
    for claim in claims:
        original = claim.get("claim_text", "")
        scrubbed = scrub_text(original)
        claim["claim_text"] = correct_surrogate_terminology(scrubbed.text)
        if scrubbed.redactions:
            claim["_redactions"] = scrubbed.redactions
            total_redactions += len(scrubbed.redactions)
            pii_review_claims += 1
    return {
        "claims_count": len(claims),
        "total_redactions": total_redactions,
        "pii_review_claims": pii_review_claims,
    }


def restore_scrubbed_text(
    text: str, redactions: list[dict[str, str]], only: Collection[int] | None = None
) -> str:
    """Put redacted values back: mark number ``i`` (in text order) gets ``redactions[i]``.

    With ``only``, just the marks with those numbers are restored; the others stay. Marks
    without a matching redaction are left as they are.
    """

    parts = text.split(REDACTION_MARK)
    pieces = [parts[0]]
    for index, part in enumerate(parts[1:]):
        restore = index < len(redactions) and (only is None or index in only)
        pieces.append(redactions[index]["value"] if restore else REDACTION_MARK)
        pieces.append(part)
    return "".join(pieces)
