"""Entity-level edits: status, reject (un-group), rename, contacts, merge, delete, un-delete.

Every edit changes memory, then ``save()``; it returns the id of the entity the owner should
see next (``None`` when the view is empty).
"""

from __future__ import annotations

from typing import Any

from step_4_entities.records import (
    CONTACT_KINDS,
    allocate_entity_id,
    empty_contacts,
    new_entity,
    refresh_entity,
)
from step_4_entities.reviewer.store.helpers import (
    ContactAction,
    ContactKind,
    EntityView,
    ReviewConflict,
    Status,
    normalize_contacts,
)


def _union(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, list[str]]:
    return {
        kind: sorted({*((a or {}).get(kind) or []), *((b or {}).get(kind) or [])})
        for kind in CONTACT_KINDS
    }


class EditsMixin:
    """Relies on ``EntityStore`` (``entities``, ``payload``, ``claims``, ``deleted_*``, ``find``,
    ``save``), ``ViewsMixin`` (``view_ids``, ``queue``, ``next_after_removal``) and
    ``MembersMixin`` (``absorb``, ``rebuild``)."""

    def _next_in_view(self, entity_id: str, view: EntityView, ids_before: list[str]) -> str | None:
        """After a decision: the entity now at its place in the view (the decided one stays if it
        is last and did not move)."""

        queue = self.queue(entity_id, view)
        before = ids_before.index(entity_id) if entity_id in ids_before else None
        if queue["position"] is not None and queue["position"] == before:
            return queue["next_id"] or entity_id
        # It left the view or moved in it (the review order puts accepted ones last).
        return self.next_after_removal(ids_before, entity_id)

    def set_status(self, entity_id: str, status: Status, view: EntityView) -> str | None:
        """Accepting confirms the whole entity: automatically attached members and the
        look-alike conflicts step 4 flagged count as reviewed."""

        entity = self.find(entity_id)
        ids_before = self.view_ids(view)
        entity["status"] = status
        if status == "accepted":
            entity.pop("conflict_with", None)
            for member in entity.get("members") or []:
                member.pop("attached_by", None)
        self.save()
        return (
            self._next_in_view(entity_id, view, ids_before) if status == "accepted" else entity_id
        )

    def reject(self, entity_id: str) -> str:
        """Undo a grouping suggestion: every member keeps only the claims that TAG its name
        (text-only mentions go) and a multi-member entity splits into one suggested entity
        per member. Returns the first of them."""

        entity = self.find(entity_id)
        members = entity.get("members") or []
        for member in members:
            member.pop("attached_by", None)
            self.rebuild(member, self.claims.tagged_claims(member["name"]))
        if len(members) <= 1:
            entity["status"] = "suggested"
            refresh_entity(entity)
            self.save()
            return entity_id
        index = self.entities.index(entity)
        split = [new_entity(allocate_entity_id(self.payload), [member]) for member in members]
        self.entities[index : index + 1] = split
        self.save()
        return split[0]["entity_id"]

    def rename(self, entity_id: str, canonical_name: str) -> str:
        name = canonical_name.strip()
        if not name:
            raise ReviewConflict("The entity name cannot be empty")
        entity = self.find(entity_id)
        old = entity.get("canonical_name")
        entity["canonical_name"] = name
        entity["canonical_manual"] = True
        for other in self.entities:
            conflicts = other.get("conflict_with")
            if other is not entity and conflicts and old in conflicts:
                other["conflict_with"] = sorted({name if c == old else c for c in conflicts})
        self.save()
        return entity_id

    def set_contacts(self, entity_id: str, contacts: dict[str, Any]) -> str:
        entity = self.find(entity_id)
        entity["contacts"] = normalize_contacts(contacts)
        entity["contacts_manual"] = True
        refresh_entity(entity)
        self.save()
        return entity_id

    def resolve_contact(
        self,
        entity_id: str,
        kind: ContactKind,
        value: str,
        action: ContactAction,
        new_value: str | None = None,
    ) -> str:
        """``accept`` an uncertain value (optionally corrected) as a confident contact,
        ``reject`` it for good, or ``restore`` a rejected value to the uncertain ones."""

        entity = self.find(entity_id)
        if action == "restore":
            self._restore_contact(entity, kind, value)
        else:
            self._resolve_uncertain(entity, kind, value, action, new_value)
        refresh_entity(entity)
        self.save()
        return entity_id

    def _resolve_uncertain(
        self,
        entity: dict[str, Any],
        kind: ContactKind,
        value: str,
        action: ContactAction,
        new_value: str | None,
    ) -> None:
        uncertain = entity.setdefault("contacts_uncertain", empty_contacts())
        if value not in (uncertain.get(kind) or []):
            raise ReviewConflict(f"The value {value} is no longer among the uncertain contacts")
        resolved = (new_value or value).strip()
        if action == "accept" and not resolved:
            raise ReviewConflict("An accepted contact cannot be empty")
        uncertain[kind] = [v for v in uncertain[kind] if v != value]
        # Resolved at the member level too, so a later refresh does not bring it back.
        for member in entity.get("members") or []:
            bucket = (member.get("contacts_uncertain") or {}).get(kind) or []
            if value in bucket:
                member["contacts_uncertain"][kind] = [v for v in bucket if v != value]
        if action == "reject":
            rejected = entity.setdefault("contacts_rejected", empty_contacts())
            rejected[kind] = sorted({*(rejected.get(kind) or []), value})
            return
        confident = entity.setdefault("contacts", empty_contacts())
        if resolved not in (confident.get(kind) or []):
            confident[kind] = [*(confident.get(kind) or []), resolved]
        entity["contacts_manual"] = True

    def _restore_contact(self, entity: dict[str, Any], kind: ContactKind, value: str) -> None:
        rejected = entity.get("contacts_rejected") or {}
        if value not in (rejected.get(kind) or []):
            raise ReviewConflict(f"The value {value} is not among the rejected contacts")
        rejected[kind] = [v for v in rejected[kind] if v != value]
        for member in entity.get("members") or []:
            fresh = self.claims.member(
                member["name"],
                member.get("claim_ids") or [],
                member.get("excluded_claim_ids") or [],
            )
            if value in fresh["contacts_uncertain"][kind]:
                bucket = member.setdefault("contacts_uncertain", empty_contacts())
                bucket[kind] = sorted({*(bucket.get(kind) or []), value})

    def delete_entity(self, entity_id: str, view: EntityView) -> str | None:
        """Remove the entity; names nobody else holds are recorded in ``deleted_names`` so a
        step-4 update never brings them back."""

        entity = self.find(entity_id)
        ids_before = self.view_ids(view)
        self.entities.remove(entity)
        held = {m["name"] for e in self.entities for m in e.get("members") or []}
        self.deleted_names |= {m["name"] for m in entity.get("members") or []} - held
        self.save()
        return self.next_after_removal(ids_before, entity_id)

    def merge(self, entity_id: str, target_id: str) -> str:
        """Fold every member of the entity into the target (same names are combined)."""

        if entity_id == target_id:
            raise ReviewConflict("An entity cannot be merged into itself")
        source, target = self.find(entity_id), self.find(target_id)
        for member in source.get("members") or []:
            member.pop("attached_by", None)
            self.absorb(target, member)
        target["merge_signals"] = sorted(
            {*(target.get("merge_signals") or []), *(source.get("merge_signals") or [])}
        )
        if source.get("contacts_rejected") or target.get("contacts_rejected"):
            target["contacts_rejected"] = _union(
                source.get("contacts_rejected"), target.get("contacts_rejected")
            )
        if source.get("contacts_manual") or target.get("contacts_manual"):
            target["contacts"] = _union(source.get("contacts"), target.get("contacts"))
            target["contacts_manual"] = True
        self.entities.remove(source)
        refresh_entity(target)
        names = {*target["aliases"], target.get("canonical_name")}
        conflicts = {*(target.get("conflict_with") or []), *(source.get("conflict_with") or [])}
        if conflicts - names:
            target["conflict_with"] = sorted(conflicts - names)
        else:
            target.pop("conflict_with", None)
        self.save()
        return target_id

    def restore_name(self, name: str) -> str:
        """Un-delete a name: a new suggested entity with every claim that names it."""

        if name not in self.deleted_names:
            raise ReviewConflict(f"The name “{name}” is not in the list of deleted names")
        claim_ids = self.claims.claims_for(name) - self.deleted_claims
        if not claim_ids:
            raise ReviewConflict(f"No claim mentions “{name}” any more; there is nothing to restore")
        self.deleted_names.discard(name)
        member = self.claims.member(name, claim_ids)
        entity = new_entity(allocate_entity_id(self.payload), [member])
        self.entities.append(entity)
        self.save()
        return entity["entity_id"]
