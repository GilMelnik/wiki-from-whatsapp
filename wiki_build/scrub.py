"""Deterministic PII scrub safety net.

This runs independently of the LLM. Even if the extraction prompt fails to
anonymize something, this layer catches the most dangerous leaks:

- Phone numbers (Israeli / international), emails, long digit runs -> auto-redacted.
- Full (multi-word) member names and single-token Latin names from the private
  ``sender_id_to_nickname.json`` mapping -> auto-redacted.
- Single-token Hebrew names -> flagged (not auto-redacted) for human review,
  because single Hebrew tokens are frequently common/domain words.

Domain terms (provider names, countries, taxonomy keywords) are placed on an
allowlist and are never redacted, so legitimate content like "תמוז" or "ישראל"
survives.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wiki_build.taxonomy import all_pages

DEFAULT_SENDER_MAP_PATH = Path("data/sender_id_to_nickname.json")
REDACTION_MARK = "[הוסר]"

# Domain terms that must never be redacted even if they resemble a name.
EXTRA_ALLOWLIST = {
    "תמוז",
    "גאיה",
    "orm",
    "ישראל",
    "אמריקה",
    "קולומביה",
    "גאורגיה",
    "קפריסין",
    "קליפורניה",
    "אילינוי",
    "טקסס",
    "נבדה",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A run that contains 9-15 digits once separators are stripped (phone-like).
_PHONE_RE = re.compile(r"(?<![\w])\+?[\d][\d\s().\-]{7,}\d(?![\w])")
_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _is_hebrew(token: str) -> bool:
    return bool(_HEBREW_RE.search(token))


@dataclass
class ScrubResult:
    text: str
    redactions: list[dict[str, str]] = field(default_factory=list)
    flags: list[dict[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.redactions)


class Denylist:
    def __init__(
        self,
        full_names: set[str],
        latin_name_tokens: set[str],
        hebrew_name_tokens: set[str],
        allowlist: set[str],
    ):
        self.full_names = full_names
        self.latin_name_tokens = latin_name_tokens
        self.hebrew_name_tokens = hebrew_name_tokens
        self.allowlist = allowlist

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> Denylist:
        allowlist = set(EXTRA_ALLOWLIST)
        for page in all_pages():
            for kw in page.keywords:
                allowlist.add(kw.lower())

        full_names: set[str] = set()
        latin_tokens: set[str] = set()
        hebrew_tokens: set[str] = set()

        for key, value in mapping.items():
            if key == "_metadata" or not isinstance(value, str):
                continue
            name = value.strip()
            if not name:
                continue
            # Phone-number-valued entries are handled by the regex; skip here.
            digits = re.sub(r"\D", "", name)
            if digits and len(digits) >= 7 and len(re.sub(r"[\d\s+().\-]", "", name)) == 0:
                continue

            tokens = [t for t in re.split(r"\s+", name) if t]
            if len(tokens) >= 2:
                full_names.add(name.lower())
            for token in tokens:
                token_clean = token.strip(".,'\"")
                if len(token_clean) < 3:
                    continue
                if token_clean.lower() in allowlist:
                    continue
                if _is_hebrew(token_clean):
                    hebrew_tokens.add(token_clean)
                elif _LATIN_WORD_RE.fullmatch(token_clean):
                    latin_tokens.add(token_clean.lower())

        return cls(full_names, latin_tokens, hebrew_tokens, allowlist)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_SENDER_MAP_PATH) -> Denylist:
        with Path(path).open(encoding="utf-8") as f:
            return cls.from_mapping(json.load(f))


def scrub_text(text: str, denylist: Denylist) -> ScrubResult:
    if not text:
        return ScrubResult(text="")

    redactions: list[dict[str, str]] = []
    flags: list[dict[str, str]] = []
    result = text

    def _redact(pattern: re.Pattern[str], kind: str) -> None:
        nonlocal result

        def repl(match: re.Match[str]) -> str:
            value = match.group(0)
            redactions.append({"type": kind, "value": value})
            return REDACTION_MARK

        result = pattern.sub(repl, result)

    _redact(_EMAIL_RE, "email")
    _redact(_PHONE_RE, "phone")

    # Full multi-word names (longest first to avoid partial overlaps).
    for name in sorted(denylist.full_names, key=len, reverse=True):
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if pattern.search(result):
            result = pattern.sub(REDACTION_MARK, result)
            redactions.append({"type": "full_name", "value": name})

    # Single-token Latin names: auto-redact with word boundaries.
    for token in denylist.latin_name_tokens:
        pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
        if pattern.search(result):
            result = pattern.sub(REDACTION_MARK, result)
            redactions.append({"type": "latin_name", "value": token})

    # Single-token Hebrew names: flag only (high false-positive risk).
    for token in denylist.hebrew_name_tokens:
        pattern = re.compile(rf"(?<![\u0590-\u05FF]){re.escape(token)}(?![\u0590-\u05FF])")
        if pattern.search(result):
            flags.append({"type": "hebrew_name", "value": token})

    return ScrubResult(text=result, redactions=redactions, flags=flags)


def scrub_claims(claims: list[dict[str, Any]], denylist: Denylist) -> dict[str, Any]:
    """Scrub ``claim_text`` in place; return a summary of what was changed."""

    total_redactions = 0
    flagged_claims = 0
    for claim in claims:
        original = claim.get("claim_text", "")
        scrubbed = scrub_text(original, denylist)
        claim["claim_text"] = scrubbed.text
        if scrubbed.redactions:
            claim["_redactions"] = scrubbed.redactions
            total_redactions += len(scrubbed.redactions)
        if scrubbed.flags:
            claim["_pii_flags"] = scrubbed.flags
            flagged_claims += 1
    return {
        "claims_count": len(claims),
        "total_redactions": total_redactions,
        "flagged_claims": flagged_claims,
    }
