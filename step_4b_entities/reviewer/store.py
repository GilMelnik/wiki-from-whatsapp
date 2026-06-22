"""Load, edit, and persist suggested entity clusters for manual review."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from step_4b_entities.collect import _claim_contacts
from step_4b_entities.constants import DEFAULT_ENTITY_ANALYSIS_PATH
from step_4b_entities.mentions import (
    Analyzer,
    SimpleAnalyzer,
    Word,
    analyze_claims,
    build_or_load_analysis,
    find_mentions,
)
from utils.json_io import write_json_file
from utils.paths import (
    BACKUPS_DIR,
    EDITED_ENTITIES_PATH,
    ORIGINAL_ENTITIES_PATH,
    init_entities_edited,
    resolve_claims_path,
)
from step_4b_entities.run import load_claims_for_entities

SortKind = Literal["count", "size", "score"]
SortOrder = Literal["asc", "desc"]
Status = Literal["suggested", "accepted", "rejected"]


def _member_total(entity: dict[str, Any]) -> int:
    return sum(m.get("count") or 0 for m in entity.get("members") or [])


def _union_contacts(
    members: list[dict[str, Any]], key: str = "contacts"
) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {"email": set(), "phone": set(), "website": set()}
    for member in members:
        for kind, values in (member.get(key) or {}).items():
            out.setdefault(kind, set()).update(values)
    return {kind: sorted(values) for kind, values in out.items()}


def _recompute_entity(entity: dict[str, Any]) -> None:
    """Refresh aliases/topics/canonical/contacts from the member list."""

    members = entity.get("members") or []
    members.sort(key=lambda m: m.get("count") or 0, reverse=True)
    entity["aliases"] = [m["name"] for m in members]
    if not entity.get("contacts_manual"):
        entity["contacts"] = _union_contacts(members)
    # Uncertain contacts (multi-entity claims) drop anything already promoted to
    # the confident set, so an accepted value never shows up twice.
    uncertain = _union_contacts(members, "contacts_uncertain")
    confident = entity.get("contacts") or {}
    entity["contacts_uncertain"] = {
        kind: sorted(set(uncertain.get(kind, [])) - set(confident.get(kind, [])))
        for kind in ("email", "phone", "website")
    }
    topics: set[str] = set()
    for member in members:
        topics.update(member.get("topics") or [])
    entity["topics"] = sorted(topics)
    if members and entity.get("canonical_name") not in entity["aliases"]:
        entity["canonical_name"] = members[0]["name"]


def _normalize_contacts(contacts: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"email": [], "phone": [], "website": []}
    for kind in out:
        raw = contacts.get(kind) or []
        if not isinstance(raw, list):
            raise ValueError(f"contacts.{kind} must be a list")
        seen: set[str] = set()
        values: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
        out[kind] = values
    return out


def _merge_highlights(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop overlaps; ``self`` beats ``other``, then longer span wins."""

    priority = {"self": 0, "other": 1}
    ranked = sorted(
        segments,
        key=lambda s: (
            s["start"],
            priority.get(s["kind"], 2),
            -(s["end"] - s["start"]),
        ),
    )
    out: list[dict[str, Any]] = []
    cursor = -1
    for seg in ranked:
        if seg["start"] < cursor:
            continue
        out.append(seg)
        cursor = seg["end"]
    out.sort(key=lambda s: s["start"])
    return out


class EntityStore:
    def __init__(
        self,
        *,
        entities_path: Path | str | None = None,
        claims_path: Path | str | None = None,
        analyzer: Analyzer | None = None,
        analysis_cache_path: Path | str | None = None,
    ) -> None:
        self._entities_override = (
            Path(entities_path) if entities_path is not None else None
        )
        self._claims_override = Path(claims_path) if claims_path is not None else None
        # Model-free default keeps direct/test instantiation offline; the web server
        # injects a DictaAnalyzer + shared cache so highlighting matches the pipeline.
        self._analyzer: Analyzer = analyzer or SimpleAnalyzer()
        self._analysis_cache_path = analysis_cache_path
        self._analysis: dict[str, list[Word]] = {}
        self._data: dict[str, Any] | None = None
        self._claims_by_id: dict[str, dict[str, Any]] = {}
        self._original_by_id: dict[str, dict[str, Any]] = {}
        self._claim_ids_by_name: dict[str, list[str]] = {}
        self._alias_to_entity: dict[str, str] = {}
        self._entity_color_index: dict[str, int] = {}
        self._entities_source = ORIGINAL_ENTITIES_PATH
        self._backup_done = False
        self._loaded = False

    def load(self) -> None:
        init_entities_edited()
        if self._entities_override is not None:
            self._entities_source = self._entities_override
            if not self._entities_source.is_file():
                raise FileNotFoundError(
                    f"Entities file not found: {self._entities_source}"
                )
        else:
            self._entities_source = (
                EDITED_ENTITIES_PATH
                if EDITED_ENTITIES_PATH.exists()
                else ORIGINAL_ENTITIES_PATH
            )
        with self._entities_source.open(encoding="utf-8") as f:
            self._data = json.load(f)

        claims_path = (
            self._claims_override
            if self._claims_override is not None
            else resolve_claims_path()
        )
        claims, resolved_claims, original_by_id = load_claims_for_entities(claims_path)
        self._claims_by_id = {c["claim_id"]: c for c in claims}
        self._original_by_id = original_by_id or {}
        for claim in claims:
            for name in claim.get("entities") or []:
                self._claim_ids_by_name.setdefault(name, []).append(claim["claim_id"])
        if self._analysis_cache_path is not None:
            self._analysis = build_or_load_analysis(
                claims,
                self._analyzer,
                cache_path=self._analysis_cache_path,
                source_path=resolved_claims,
            )
        else:
            self._analysis = analyze_claims(claims, self._analyzer)
        self._rebuild_alias_index()
        self._loaded = True

    def _rebuild_alias_index(self) -> None:
        self._alias_to_entity = {}
        self._entity_color_index = {}
        for idx, entity in enumerate(self.entities):
            entity_id = entity["entity_id"]
            self._entity_color_index[entity_id] = idx
            for alias in entity.get("aliases") or []:
                self._alias_to_entity.setdefault(alias, entity_id)
            canonical = entity.get("canonical_name")
            if canonical:
                self._alias_to_entity.setdefault(canonical, entity_id)

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def entities(self) -> list[dict[str, Any]]:
        assert self._data is not None
        return self._data["entities"]

    def _find_entity(self, entity_id: str) -> tuple[dict[str, Any], int]:
        for idx, entity in enumerate(self.entities):
            if entity["entity_id"] == entity_id:
                return entity, idx
        raise KeyError(f"unknown entity: {entity_id}")

    def _new_entity_id(self) -> str:
        nums = [
            int(m.group(1))
            for e in self.entities
            if (m := re.fullmatch(r"e(\d+)", e["entity_id"]))
        ]
        return f"e{(max(nums) + 1) if nums else 0:04d}"

    # --- member helpers -------------------------------------------------

    def _words_for(self, claim: dict[str, Any]) -> list[Word]:
        """Analyzed words for a claim, analyzing on demand for cache misses."""

        claim_id = claim.get("claim_id")
        if claim_id and claim_id in self._analysis:
            return self._analysis[claim_id]
        words = analyze_claims([claim], self._analyzer).get(claim_id, [])
        if claim_id:
            self._analysis[claim_id] = words
        return words

    def _mention_spans(self, claim: dict[str, Any], name: str) -> list[list[int]]:
        return find_mentions(self._words_for(claim), name)

    def _claim_mentions(self, claim: dict[str, Any], name: str) -> bool:
        return bool(self._mention_spans(claim, name))

    def _raw_claim_ids_for_member(self, member: dict[str, Any]) -> set[str]:
        if member.get("claim_ids"):
            return set(member["claim_ids"])
        name = member["name"]
        ids: set[str] = set(self._claim_ids_by_name.get(name, []))
        for claim_id, claim in self._claims_by_id.items():
            if self._claim_mentions(claim, name):
                ids.add(claim_id)
        return ids

    def _claim_ids_for_member(self, member: dict[str, Any]) -> list[str]:
        excluded = set(member.get("excluded_claim_ids") or [])
        return sorted(self._raw_claim_ids_for_member(member) - excluded)

    def _member_from_claims(
        self,
        name: str,
        claim_ids: list[str],
        *,
        excluded_claim_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        emails: set[str] = set()
        phones: set[str] = set()
        websites: set[str] = set()
        topics: set[str] = set()
        for claim_id in claim_ids:
            claim = self._claims_by_id.get(claim_id, {})
            original = self._original_by_id.get(claim_id)
            topics.update(claim.get("topic_tags") or [])
            claim_emails, claim_phones, claim_sites = _claim_contacts(claim, original)
            emails.update(claim_emails)
            phones.update(claim_phones)
            websites.update(claim_sites)
        return {
            "name": name,
            "claim_ids": list(claim_ids),
            "count": len(claim_ids),
            "sample_claim_ids": list(claim_ids[:12]),
            "topics": sorted(topics),
            "contacts": {
                "email": sorted(emails),
                "phone": sorted(phones),
                "website": sorted(websites),
            },
            "excluded_claim_ids": list(excluded_claim_ids or []),
        }

    def _other_entities_in_claim(
        self,
        claim: dict[str, Any],
        *,
        current_entity_id: str,
        current_names: set[str],
    ) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        names = sorted(self._alias_to_entity.keys(), key=len, reverse=True)
        for name in names:
            if name in current_names:
                continue
            entity_id = self._alias_to_entity[name]
            if entity_id == current_entity_id:
                continue
            spans = self._mention_spans(claim, name)
            if not spans:
                continue
            existing = found.get(entity_id)
            if existing is None or len(name) > len(existing["name"]):
                entity = next(
                    e for e in self.entities if e["entity_id"] == entity_id
                )
                found[entity_id] = {
                    "entity_id": entity_id,
                    "name": name,
                    "canonical_name": entity.get("canonical_name") or name,
                    "color_index": self._entity_color_index.get(entity_id, 0),
                    "spans": spans,
                }
        return list(found.values())

    def _related_entities_in_claim(
        self,
        claim: dict[str, Any],
        *,
        current_entity_id: str,
        current_names: set[str],
        mentioned_entity_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Entity tags on the claim whose name does not appear in ``claim_text``."""

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in claim.get("entities") or []:
            if not isinstance(name, str) or not name.strip():
                continue
            if name in current_names:
                continue
            if self._claim_mentions(claim, name):
                continue
            entity_id = self._alias_to_entity.get(name)
            if entity_id == current_entity_id:
                continue
            if entity_id and entity_id in mentioned_entity_ids:
                continue
            key = entity_id or name
            if key in seen:
                continue
            seen.add(key)
            entry: dict[str, Any] = {"name": name, "tagged_only": True}
            if entity_id:
                entity = next(
                    e for e in self.entities if e["entity_id"] == entity_id
                )
                entry["entity_id"] = entity_id
                entry["canonical_name"] = entity.get("canonical_name") or name
                entry["color_index"] = self._entity_color_index.get(entity_id, 0)
            else:
                entry["entity_id"] = None
                entry["canonical_name"] = name
                entry["color_index"] = None
            out.append(entry)
        return out

    def _claim_highlights(
        self,
        claim: dict[str, Any],
        member_name: str,
        *,
        current_entity_id: str,
        current_names: set[str],
    ) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for start, end in self._mention_spans(claim, member_name):
            segments.append({"start": start, "end": end, "kind": "self"})
        for other in self._other_entities_in_claim(
            claim,
            current_entity_id=current_entity_id,
            current_names=current_names,
        ):
            for start, end in other["spans"]:
                segments.append(
                    {
                        "start": start,
                        "end": end,
                        "kind": "other",
                        "entity_id": other["entity_id"],
                        "name": other["name"],
                        "canonical_name": other["canonical_name"],
                        "color_index": other["color_index"],
                    }
                )
        return _merge_highlights(segments)

    # --- read views -----------------------------------------------------

    def _enrich(self, entity: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": entity["entity_id"],
            "canonical_name": entity.get("canonical_name", ""),
            "status": entity.get("status", "suggested"),
            "member_count": len(entity.get("members") or []),
            "total_count": _member_total(entity),
            "aliases": entity.get("aliases") or [],
            "topics": entity.get("topics") or [],
            "contacts": entity.get("contacts") or {},
            "contacts_uncertain": entity.get("contacts_uncertain") or {},
            "contacts_manual": bool(entity.get("contacts_manual")),
            "merge_signals": entity.get("merge_signals") or [],
            "conflict_with": entity.get("conflict_with") or [],
            "score": entity.get("score"),
        }

    def _sorted_entities(
        self,
        *,
        status: str | None = None,
        size_min: int | None = None,
        query: str | None = None,
        sort: SortKind = "count",
        order: SortOrder = "desc",
    ) -> list[dict[str, Any]]:
        items = [self._enrich(e) for e in self.entities]
        if status:
            items = [i for i in items if i["status"] == status]
        if size_min is not None:
            items = [i for i in items if i["member_count"] >= size_min]
        if query:
            needle = query.strip().lower()
            items = [
                i
                for i in items
                if needle in i["canonical_name"].lower()
                or any(needle in a.lower() for a in i["aliases"])
            ]
        reverse = order == "desc"
        key = {
            "count": lambda i: i["total_count"],
            "size": lambda i: i["member_count"],
            "score": lambda i: i["score"] or 0,
        }[sort]
        items.sort(key=key, reverse=reverse)
        return items

    def list_entities(
        self,
        *,
        status: str | None = None,
        size_min: int | None = None,
        query: str | None = None,
        sort: SortKind = "count",
        order: SortOrder = "desc",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        items = self._sorted_entities(
            status=status, size_min=size_min, query=query, sort=sort, order=order
        )
        return items[offset : offset + limit], len(items)

    def _member_view(
        self,
        member: dict[str, Any],
        member_index: int,
        *,
        entity_id: str,
        entity_names: set[str],
    ) -> dict[str, Any]:
        name = member["name"]
        claim_ids = self._claim_ids_for_member(member)
        sample_ids = claim_ids[:12]
        samples: list[dict[str, Any]] = []
        for claim_id in sample_ids:
            claim = self._claims_by_id.get(claim_id)
            if claim is None:
                continue
            text = claim.get("claim_text", "")
            other_entities = self._other_entities_in_claim(
                claim,
                current_entity_id=entity_id,
                current_names=entity_names,
            )
            samples.append(
                {
                    "claim_id": claim_id,
                    "claim_text": text,
                    "stance": claim.get("stance"),
                    "thread_id": claim.get("thread_id"),
                    "highlights": self._claim_highlights(
                        claim,
                        name,
                        current_entity_id=entity_id,
                        current_names=entity_names,
                    ),
                    "other_entities": other_entities,
                    "related_entities": self._related_entities_in_claim(
                        claim,
                        current_entity_id=entity_id,
                        current_names=entity_names,
                        mentioned_entity_ids={
                            o["entity_id"] for o in other_entities
                        },
                    ),
                }
            )
        return {
            "name": name,
            "member_index": member_index,
            "count": len(claim_ids),
            "claim_ids": member.get("claim_ids"),
            "excluded_claim_ids": member.get("excluded_claim_ids") or [],
            "topics": member.get("topics") or [],
            "contacts": member.get("contacts") or {},
            "sample_claims": samples,
        }

    def get_entity(
        self,
        entity_id: str,
        *,
        status: str | None = None,
        size_min: int | None = None,
        query: str | None = None,
        sort: SortKind = "count",
        order: SortOrder = "desc",
    ) -> dict[str, Any]:
        entity, _ = self._find_entity(entity_id)
        detail = self._enrich(entity)
        entity_names = set(entity.get("aliases") or [])
        canonical = entity.get("canonical_name")
        if canonical:
            entity_names.add(canonical)
        detail["members"] = [
            self._member_view(
                m, idx, entity_id=entity_id, entity_names=entity_names
            )
            for idx, m in enumerate(entity.get("members") or [])
        ]
        items = self._sorted_entities(
            status=status, size_min=size_min, query=query, sort=sort, order=order
        )
        ids = [i["entity_id"] for i in items]
        try:
            pos = ids.index(entity_id)
        except ValueError:
            pos = -1
        return {
            "entity": detail,
            "queue": {
                "prev_id": ids[pos - 1] if pos > 0 else None,
                "next_id": ids[pos + 1] if 0 <= pos < len(ids) - 1 else None,
                "position": pos + 1 if pos >= 0 else None,
                "total": len(ids),
            },
        }

    def stats(self) -> dict[str, Any]:
        statuses: Counter[str] = Counter()
        sizes: Counter[int] = Counter()
        for entity in self.entities:
            statuses[entity.get("status", "suggested")] += 1
            sizes[len(entity.get("members") or [])] += 1
        return {
            "entity_count": len(self.entities),
            "multi_member_count": sum(
                1 for e in self.entities if len(e.get("members") or []) > 1
            ),
            "by_status": dict(statuses),
            "size_histogram": [
                {"size": size, "count": sizes[size]} for size in sorted(sizes)
            ],
        }

    # --- mutations ------------------------------------------------------

    def set_status(self, entity_id: str, status: Status) -> dict[str, Any]:
        entity, _ = self._find_entity(entity_id)
        entity["status"] = status
        self.save()
        return self.get_entity(entity_id)["entity"]

    def set_canonical(self, entity_id: str, name: str) -> dict[str, Any]:
        entity, _ = self._find_entity(entity_id)
        if name not in [m["name"] for m in entity.get("members") or []]:
            raise ValueError(f"name not a member of this entity: {name}")
        entity["canonical_name"] = name
        self.save()
        return self.get_entity(entity_id)["entity"]

    def rename_entity(self, entity_id: str, canonical_name: str) -> dict[str, Any]:
        name = canonical_name.strip()
        if not name:
            raise ValueError("canonical_name must not be empty")
        entity, _ = self._find_entity(entity_id)
        entity["canonical_name"] = name
        self.save()
        return self.get_entity(entity_id)["entity"]

    def set_contacts(
        self, entity_id: str, contacts: dict[str, Any]
    ) -> dict[str, Any]:
        entity, _ = self._find_entity(entity_id)
        entity["contacts"] = _normalize_contacts(contacts)
        entity["contacts_manual"] = True
        self.save()
        return self.get_entity(entity_id)["entity"]

    def resolve_uncertain_contact(
        self,
        entity_id: str,
        *,
        kind: str,
        value: str,
        action: str,
        new_value: str | None = None,
    ) -> dict[str, Any]:
        """Accept (promote to confident, optionally edited) or reject an uncertain
        contact value seen only in multi-entity claims."""

        if kind not in ("email", "phone", "website"):
            raise ValueError(f"unknown contact kind: {kind}")
        if action not in ("accept", "reject"):
            raise ValueError(f"unknown action: {action}")

        entity, _ = self._find_entity(entity_id)
        uncertain = entity.setdefault(
            "contacts_uncertain", {"email": [], "phone": [], "website": []}
        )
        bucket = uncertain.get(kind) or []
        if value not in bucket:
            raise ValueError(f"value not an uncertain {kind}: {value}")
        uncertain[kind] = [v for v in bucket if v != value]
        # Resolve at the member level too, so a later _recompute_entity (triggered
        # by any other edit) does not resurrect this value into the uncertain set.
        for member in entity.get("members") or []:
            member_unc = member.get("contacts_uncertain")
            if member_unc and value in (member_unc.get(kind) or []):
                member_unc[kind] = [v for v in member_unc[kind] if v != value]

        if action == "accept":
            resolved = (new_value or value).strip()
            if not resolved:
                raise ValueError("accepted contact must not be empty")
            confident = entity.setdefault(
                "contacts", {"email": [], "phone": [], "website": []}
            )
            values = list(confident.get(kind) or [])
            if resolved not in values:
                values.append(resolved)
            confident[kind] = values
            entity["contacts_manual"] = True

        self.save()
        return self.get_entity(entity_id)["entity"]

    def delete_entity(self, entity_id: str) -> dict[str, Any]:
        entity, idx = self._find_entity(entity_id)
        self.entities.pop(idx)
        self.save()
        remaining = self._sorted_entities()
        next_id = None
        if remaining:
            next_id = remaining[min(idx, len(remaining) - 1)]["entity_id"]
        return {"deleted_id": entity_id, "next_id": next_id}

    def _target_entity(
        self, target_entity_id: str | None, *, status: str = "accepted"
    ) -> dict[str, Any]:
        if target_entity_id:
            target, _ = self._find_entity(target_entity_id)
            return target
        new_entity = {
            "entity_id": self._new_entity_id(),
            "canonical_name": "",
            "status": status,
            "aliases": [],
            "members": [],
            "contacts": {"email": [], "phone": [], "website": []},
            "topics": [],
            "score": 1.0,
        }
        self.entities.append(new_entity)
        return new_entity

    def _add_claims_to_member(
        self,
        target: dict[str, Any],
        name: str,
        claim_ids: list[str],
    ) -> None:
        existing = next(
            (m for m in target.get("members") or [] if m["name"] == name), None
        )
        if existing is not None:
            merged = list(
                dict.fromkeys(self._claim_ids_for_member(existing) + claim_ids)
            )
            target["members"] = [
                self._member_from_claims(
                    name,
                    merged,
                    excluded_claim_ids=existing.get("excluded_claim_ids"),
                )
                if m is existing
                else m
                for m in target["members"]
            ]
        else:
            target.setdefault("members", []).append(
                self._member_from_claims(name, claim_ids)
            )

    def move_member(
        self,
        entity_id: str,
        name: str,
        *,
        target_entity_id: str | None = None,
    ) -> dict[str, Any]:
        source, source_idx = self._find_entity(entity_id)
        members = source.get("members") or []
        moving = next((m for m in members if m["name"] == name), None)
        if moving is None:
            raise ValueError(f"name not a member of this entity: {name}")
        if target_entity_id == entity_id:
            raise ValueError("source and target entity are the same")

        target = self._target_entity(target_entity_id)
        source["members"] = [m for m in members if m["name"] != name]
        target.setdefault("members", []).append(moving)
        _recompute_entity(target)

        if not source["members"]:
            self.entities.pop(self.entities.index(source))
        else:
            _recompute_entity(source)
        self.save()
        return self.get_entity(target["entity_id"])["entity"]

    def move_claims(
        self,
        entity_id: str,
        name: str,
        claim_ids: list[str],
        *,
        target_entity_id: str | None = None,
    ) -> dict[str, Any]:
        if not claim_ids:
            raise ValueError("claim_ids must not be empty")
        source, _ = self._find_entity(entity_id)
        members = source.get("members") or []
        member = next((m for m in members if m["name"] == name), None)
        if member is None:
            raise ValueError(f"name not a member of this entity: {name}")
        owned = set(self._claim_ids_for_member(member))
        moving = [c for c in claim_ids if c in owned]
        if not moving:
            raise ValueError("none of the claim_ids belong to this member")
        if target_entity_id == entity_id:
            raise ValueError("source and target entity are the same")

        target = self._target_entity(target_entity_id)
        # An explicit claim_ids override on the target wins over the source's
        # by-name default in the resolver, so we only need to shrink the source
        # member when it already carries an explicit list.
        existing = next(
            (m for m in target.get("members") or [] if m["name"] == name), None
        )
        if existing is not None and existing.get("claim_ids"):
            merged = list(dict.fromkeys(existing["claim_ids"] + moving))
            target["members"] = [
                self._member_from_claims(name, merged) if m is existing else m
                for m in target["members"]
            ]
        else:
            target.setdefault("members", []).append(
                self._member_from_claims(name, moving)
            )
        _recompute_entity(target)

        if member.get("claim_ids"):
            remaining = [c for c in member["claim_ids"] if c not in moving]
            if remaining:
                source["members"] = [
                    self._member_from_claims(name, remaining)
                    if m is member
                    else m
                    for m in members
                ]
            else:
                source["members"] = [m for m in members if m is not member]
        # else: member keeps its by-name default; overrides peel off the moved
        # claims. ponytail: leaves the count display unchanged for null members.

        if not source["members"]:
            self.entities.pop(self.entities.index(source))
        else:
            _recompute_entity(source)
        self.save()
        return self.get_entity(target["entity_id"])["entity"]

    def copy_claims(
        self,
        entity_id: str,
        name: str,
        claim_ids: list[str],
        *,
        target_entity_id: str | None = None,
    ) -> dict[str, Any]:
        if not claim_ids:
            raise ValueError("claim_ids must not be empty")
        source, _ = self._find_entity(entity_id)
        members = source.get("members") or []
        member = next((m for m in members if m["name"] == name), None)
        if member is None:
            raise ValueError(f"name not a member of this entity: {name}")
        owned = set(self._claim_ids_for_member(member))
        copying = [c for c in claim_ids if c in owned]
        if not copying:
            raise ValueError("none of the claim_ids belong to this member")
        if target_entity_id == entity_id:
            raise ValueError("source and target entity are the same")

        target = self._target_entity(target_entity_id)
        self._add_claims_to_member(target, name, copying)
        _recompute_entity(target)
        self.save()
        return self.get_entity(target["entity_id"])["entity"]

    def exclude_claims(
        self,
        entity_id: str,
        name: str,
        claim_ids: list[str],
    ) -> dict[str, Any]:
        if not claim_ids:
            raise ValueError("claim_ids must not be empty")
        entity, _ = self._find_entity(entity_id)
        members = entity.get("members") or []
        member = next((m for m in members if m["name"] == name), None)
        if member is None:
            raise ValueError(f"name not a member of this entity: {name}")
        active = set(self._claim_ids_for_member(member))
        excluding = [c for c in claim_ids if c in active]
        if not excluding:
            raise ValueError("none of the claim_ids belong to this member")
        excluded = list(
            dict.fromkeys((member.get("excluded_claim_ids") or []) + excluding)
        )
        member["excluded_claim_ids"] = excluded
        active = self._claim_ids_for_member(member)
        preserved_claim_ids = member.get("claim_ids")
        refreshed = self._member_from_claims(
            name, active, excluded_claim_ids=excluded
        )
        member.update(refreshed)
        if preserved_claim_ids is None:
            member["claim_ids"] = None
        _recompute_entity(entity)
        self.save()
        return self.get_entity(entity_id)["entity"]

    def merge(self, entity_id: str, target_entity_id: str) -> dict[str, Any]:
        if entity_id == target_entity_id:
            raise ValueError("source and target entity are the same")
        source, _ = self._find_entity(entity_id)
        target, _ = self._find_entity(target_entity_id)
        target.setdefault("members", []).extend(source.get("members") or [])
        _recompute_entity(target)
        self.entities.pop(self.entities.index(source))
        self.save()
        return self.get_entity(target_entity_id)["entity"]

    # --- persistence ----------------------------------------------------

    def _ensure_backup(self) -> None:
        if self._backup_done:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        if self._entities_source.exists():
            dest = (
                BACKUPS_DIR
                / f"{self._entities_source.stem}_{ts}{self._entities_source.suffix}"
            )
            shutil.copy2(self._entities_source, dest)
        self._backup_done = True

    def save(self) -> None:
        assert self._data is not None
        self._ensure_backup()
        self._rebuild_alias_index()
        meta = self._data.setdefault("metadata", {})
        meta["edited_by"] = "entity_reviewer"
        meta["edited_at"] = datetime.now().isoformat(timespec="seconds")
        meta["entity_count"] = len(self.entities)
        if self._entities_override is None:
            write_json_file(self._data, EDITED_ENTITIES_PATH)
            self._entities_source = EDITED_ENTITIES_PATH
        else:
            write_json_file(self._data, self._entities_source)

    def meta(self) -> dict[str, Any]:
        s = self.stats()
        return {
            "entities_path": str(self._entities_source),
            "entity_count": s["entity_count"],
            "multi_member_count": s["multi_member_count"],
            "by_status": s["by_status"],
        }
