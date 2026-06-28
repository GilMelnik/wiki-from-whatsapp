"""Collect distinct entity strings and contacts from claims."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from step_3_extract.scrub import find_emails, find_phones
from step_4_entities.constants import SAMPLE_CLAIMS_PER_MEMBER
from step_4_entities.mentions import (
    Analyzer,
    SimpleAnalyzer,
    Word,
    analyze_claims,
    mentions_name,
)
from step_4_entities.normalize import normalize_name
from utils.paths import ORIGINAL_CLAIMS_PATH, resolve_claims_path

_URL_RE = re.compile(
    r"https?://[^\s)\]<>\"']+"
    r"|\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|gov|edu|io|info|biz|travel|co\.il|co|il)\b"
)


def claim_mentions_name(
    claim: dict[str, Any], name: str, words: list[Word] | None
) -> bool:
    """True when ``name`` appears as a whole word in the claim's analyzed text."""

    if not name.strip():
        return False
    return mentions_name(words or [], name)


def _extract_websites(text: str) -> list[str]:
    return [m.group(0).rstrip(".") for m in _URL_RE.finditer(text or "")]


def _claim_contacts(
    claim: dict[str, Any],
    original: dict[str, Any] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Emails, phones, websites from a claim and its pipeline-original trail."""

    emails: set[str] = set()
    phones: set[str] = set()
    websites: set[str] = set()
    sources = [claim]
    if original is not None:
        sources.append(original)
    for src in sources:
        text = src.get("claim_text", "")
        for red in src.get("_redactions") or []:
            if red.get("type") == "email":
                emails.add(red["value"])
            elif red.get("type") == "phone":
                phones.add(red["value"])
        emails.update(find_emails(text))
        phones.update(find_phones(text))
        websites.update(_extract_websites(text))
    return sorted(emails), sorted(phones), sorted(websites)


def load_claims_for_entities(
    claims_path: Path | str | None = None,
) -> tuple[list[dict[str, Any]], Path, dict[str, dict[str, Any]] | None]:
    """Load claims for entity collection, with original contact trail when edited."""

    resolved = Path(claims_path) if claims_path is not None else resolve_claims_path()
    with resolved.open(encoding="utf-8") as f:
        claims = json.load(f)["claims"]
    original_by_id: dict[str, dict[str, Any]] | None = None
    if (
        resolved.resolve() != ORIGINAL_CLAIMS_PATH.resolve()
        and ORIGINAL_CLAIMS_PATH.is_file()
    ):
        with ORIGINAL_CLAIMS_PATH.open(encoding="utf-8") as f:
            original_by_id = {
                c["claim_id"]: c for c in json.load(f).get("claims") or []
            }
    return claims, resolved, original_by_id


def collect_entities(
    claims: list[dict[str, Any]],
    *,
    original_by_id: dict[str, dict[str, Any]] | None = None,
    analysis: dict[str, list[Word]] | None = None,
    analyzer: Analyzer | None = None,
) -> list[dict[str, Any]]:
    """Distinct entity strings with counts, sample claims, topics, contacts.

    ``analysis`` is a precomputed ``{claim_id: words}`` morphology map used for
    word-aware text-mention matching. When absent it is built with ``analyzer``
    (default: the model-free ``SimpleAnalyzer``); the real pipeline passes the
    cached dictabert analysis from ``run.py``.
    """

    if analysis is None:
        analysis = analyze_claims(claims, analyzer or SimpleAnalyzer())

    tag_counts: Counter[str] = Counter()
    claim_ids: dict[str, set[str]] = defaultdict(set)
    sample_ids: dict[str, list[str]] = defaultdict(list)
    topics: dict[str, set[str]] = defaultdict(set)
    # Confident contacts come from claims listing exactly this one entity, so the
    # contact unambiguously belongs to it. Everything else is "uncertain": a
    # contact seen in a multi-entity claim cannot be attributed to one of them.
    conf: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"email": set(), "phone": set(), "website": set()}
    )
    unc: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"email": set(), "phone": set(), "website": set()}
    )

    def _absorb_claim(name: str, claim: dict[str, Any], original: dict[str, Any] | None) -> None:
        claim_id = claim.get("claim_id")
        if not claim_id or claim_id in claim_ids[name]:
            return
        claim_ids[name].add(claim_id)
        if len(sample_ids[name]) < SAMPLE_CLAIMS_PER_MEMBER:
            sample_ids[name].append(claim_id)
        topics[name].update(claim.get("topic_tags") or [])
        claim_emails, claim_phones, claim_sites = _claim_contacts(claim, original)
        listed = {
            n for n in claim.get("entities") or [] if isinstance(n, str) and n.strip()
        }
        bucket = conf[name] if listed == {name} else unc[name]
        bucket["email"].update(claim_emails)
        bucket["phone"].update(claim_phones)
        bucket["website"].update(claim_sites)

    for claim in claims:
        claim_id = claim.get("claim_id")
        original = (original_by_id or {}).get(claim_id) if claim_id else None
        for name in claim.get("entities") or []:
            if not isinstance(name, str) or not name.strip():
                continue
            tag_counts[name] += 1
            _absorb_claim(name, claim, original)

    for name in tag_counts:
        for claim in claims:
            claim_id = claim.get("claim_id")
            original = (original_by_id or {}).get(claim_id) if claim_id else None
            if claim_mentions_name(claim, name, analysis.get(claim_id)):
                _absorb_claim(name, claim, original)

    entities: list[dict[str, Any]] = []
    for name in tag_counts:
        ids = sorted(claim_ids[name])
        # A value confident in any claim outranks the same value seen as uncertain.
        uncertain = {
            kind: sorted(unc[name][kind] - conf[name][kind])
            for kind in ("email", "phone", "website")
        }
        entities.append(
            {
                "name": name,
                "normalized": normalize_name(name),
                "count": len(ids),
                "claim_ids": ids,
                "sample_claim_ids": sample_ids[name],
                "topics": sorted(topics[name]),
                "contacts": {kind: sorted(conf[name][kind]) for kind in conf[name]},
                "contacts_uncertain": uncertain,
            }
        )
    entities.sort(key=lambda e: e["count"], reverse=True)
    return entities
