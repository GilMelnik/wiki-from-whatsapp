"""Entity and claim mutations for the entity store."""

from __future__ import annotations

from typing import Any

from step_4_entities.records import empty_contacts, new_entity, refresh_entity
from step_4_entities.reviewer.store.helpers import Status, _normalize_contacts


class MutationsMixin:
    """Edits that change entities, members and claims, then persist via ``save``.

    Relies on ``EntityStore`` attributes: ``entities``, ``_data``,
    ``_claims_by_id``, ``_claim_ids_by_name``, ``_deleted_claims``,
    ``_find_entity``, ``_new_entity_id``, ``save``, ``get_entity``,
    ``_sorted_entities``, ``_claim_ids_for_member``, ``_member_from_claims``
    and ``_remove_claim_from_aggregations``.
    """

    # --- mutations ------------------------------------------------------

    def set_status(self, entity_id: str, status: Status) -> dict[str, Any]:
        entity, _ = self._find_entity(entity_id)
        entity["status"] = status
        self.save()
        return self.get_entity(entity_id)["entity"]

    def _llm_claim_ids(self, name: str) -> list[str]:
        """Only claims whose LLM ``entities`` list tagged this name (Req 3)."""

        return sorted(self._claim_ids_by_name.get(name, []))

    def reject(self, entity_id: str) -> dict[str, Any]:
        """Undo a merge suggestion: restrict every member to its LLM-tagged
        claims (drop mention-matched ones, Req 3) and split a multi-member
        cluster into one standalone ``suggested`` entity per member (Req 2)."""

        entity, _ = self._find_entity(entity_id)
        members = entity.get("members") or []
        for member in members:
            member["claim_ids"] = self._llm_claim_ids(member["name"])
            member["excluded_claim_ids"] = []

        if len(members) <= 1:
            entity["status"] = "suggested"
            if members:
                refresh_entity(entity)
            self.save()
            return self.get_entity(entity_id)["entity"]

        new_entities: list[dict[str, Any]] = []
        for member in members:
            split = new_entity(self._new_entity_id(), [member])
            self.entities.append(split)
            new_entities.append(split)
        self.entities.pop(self.entities.index(entity))
        self.save()
        return self.get_entity(new_entities[0]["entity_id"])["entity"]

    def set_canonical(self, entity_id: str, name: str) -> dict[str, Any]:
        entity, _ = self._find_entity(entity_id)
        if name not in [m["name"] for m in entity.get("members") or []]:
            raise ValueError(f"name not a member of this entity: {name}")
        entity["canonical_name"] = name
        entity["canonical_manual"] = True
        self.save()
        return self.get_entity(entity_id)["entity"]

    def rename_entity(self, entity_id: str, canonical_name: str) -> dict[str, Any]:
        name = canonical_name.strip()
        if not name:
            raise ValueError("canonical_name must not be empty")
        entity, _ = self._find_entity(entity_id)
        entity["canonical_name"] = name
        entity["canonical_manual"] = True
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
        uncertain = entity.setdefault("contacts_uncertain", empty_contacts())
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

        if action == "reject":
            # Recorded so neither a later edit nor a step-4 update brings the value back.
            rejected = entity.setdefault("contacts_rejected", empty_contacts())
            rejected[kind] = sorted({*(rejected.get(kind) or []), value})
        else:
            resolved = (new_value or value).strip()
            if not resolved:
                raise ValueError("accepted contact must not be empty")
            confident = entity.setdefault("contacts", empty_contacts())
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
        created = new_entity(self._new_entity_id(), [], status=status)
        self.entities.append(created)
        return created

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
        refresh_entity(target)

        if not source["members"]:
            self.entities.pop(self.entities.index(source))
        else:
            refresh_entity(source)
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
        refresh_entity(target)

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
            refresh_entity(source)
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
        refresh_entity(target)
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
        refresh_entity(entity)
        self.save()
        return self.get_entity(entity_id)["entity"]

    def delete_claim(self, claim_id: str) -> dict[str, Any]:
        """Hard-delete a claim: record it on the entities payload and scrub it
        from every member/aggregation so it is gone from the reviewer and is
        filtered out of step 5 (the deleted list lives in entities_edited.json)."""

        if claim_id not in self._claims_by_id:
            raise KeyError(f"unknown claim: {claim_id}")
        self._deleted_claims.add(claim_id)
        self._data["deleted_claims"] = sorted(self._deleted_claims)

        for entity in self.entities:
            members = entity.get("members") or []
            touched = False
            for i, member in enumerate(members):
                cids = member.get("claim_ids")
                excluded = member.get("excluded_claim_ids") or []
                if cids is not None and claim_id in cids:
                    members[i] = self._member_from_claims(
                        member["name"],
                        [c for c in cids if c != claim_id],
                        excluded_claim_ids=[c for c in excluded if c != claim_id],
                    )
                    touched = True
                elif claim_id in excluded:
                    member["excluded_claim_ids"] = [
                        c for c in excluded if c != claim_id
                    ]
                    touched = True
            if touched:
                refresh_entity(entity)

        self._remove_claim_from_aggregations(claim_id)
        self.save()
        return {"deleted_id": claim_id, "deleted_count": len(self._deleted_claims)}

    def merge(self, entity_id: str, target_entity_id: str) -> dict[str, Any]:
        if entity_id == target_entity_id:
            raise ValueError("source and target entity are the same")
        source, _ = self._find_entity(entity_id)
        target, _ = self._find_entity(target_entity_id)
        target.setdefault("members", []).extend(source.get("members") or [])
        refresh_entity(target)
        self.entities.pop(self.entities.index(source))
        self.save()
        return self.get_entity(target_entity_id)["entity"]
