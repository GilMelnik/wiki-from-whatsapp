"""Member- and claim-level edits: move / copy / exclude claims, move or confirm a member,
hard-delete a claim.

A member never appears twice in one entity: moving or copying onto a name the target already
holds combines the two members. Edits return the id of the entity to show next.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from step_4_entities.records import allocate_entity_id, new_entity, refresh_entity
from step_4_entities.reviewer.store.helpers import EntityNotFound, ReviewConflict

# Member fields that are the owner's or step 4's knowledge, not derived from the claims.
_KEPT_FIELDS = ("attached_by",)


class MembersMixin:
    """Relies on ``EntityStore``: ``entities``, ``payload``, ``claims``, ``aggregations``,
    ``deleted_claims``, ``find``, ``member_of`` and ``save``."""

    # --- building blocks ----------------------------------------------------

    def rebuild(
        self, member: dict[str, Any], claim_ids: Iterable[str], excluded: Collection[str] = ()
    ) -> None:
        """Recompute a member (count, sample, contacts) for these claims, in place."""

        fresh = self.claims.member(member["name"], set(claim_ids) - self.deleted_claims, excluded)
        kept = {key: member[key] for key in _KEPT_FIELDS if key in member}
        member.clear()
        member.update(fresh, **kept)

    def absorb(self, target: dict[str, Any], member: dict[str, Any]) -> None:
        """Add a member to ``target``, combining it with a member of the same name."""

        members = target.setdefault("members", [])
        existing = next((m for m in members if m["name"] == member["name"]), None)
        if existing is None:
            members.append(member)
            return
        active = {*existing.get("claim_ids", []), *member.get("claim_ids", [])}
        excluded = {
            *(existing.get("excluded_claim_ids") or []),
            *(member.get("excluded_claim_ids") or []),
        } - active
        self.rebuild(existing, active, excluded)

    def _active(self, entity: dict[str, Any], name: str, claim_ids: list[str]) -> list[str]:
        """The given claims the member currently stands for (at least one, or a conflict)."""

        if not claim_ids:
            raise ReviewConflict("לא נבחרו טענות")
        member = self.member_of(entity, name)
        owned = set(member.get("claim_ids") or []) - set(member.get("excluded_claim_ids") or [])
        chosen = [c for c in dict.fromkeys(claim_ids) if c in owned]
        if not chosen:
            raise ReviewConflict(f'אף אחת מהטענות שנבחרו כבר לא שייכת לשם "{name}"')
        return chosen

    def _target(self, target_id: str | None, source_id: str) -> dict[str, Any]:
        """An existing target entity, or (``None``) a new one the owner creates by hand."""

        if target_id == source_id:
            raise ReviewConflict("ישות המקור וישות היעד זהות")
        if target_id is not None:
            return self.find(target_id)
        created = new_entity(allocate_entity_id(self.payload), [], status="accepted")
        self.entities.append(created)
        return created

    def _settle(self, *entities: dict[str, Any]) -> None:
        """Refresh the changed entities; an entity left without members is dropped."""

        for entity in entities:
            if entity.get("members"):
                refresh_entity(entity)
            elif entity in self.entities:
                self.entities.remove(entity)

    def _shown(self, source: dict[str, Any], target: dict[str, Any]) -> str:
        return source["entity_id"] if source in self.entities else target["entity_id"]

    # --- members ------------------------------------------------------------

    def move_member(self, entity_id: str, name: str, target_id: str | None) -> str:
        source = self.find(entity_id)
        member = self.member_of(source, name)
        target = self._target(target_id, entity_id)
        source["members"].remove(member)
        member.pop("attached_by", None)
        self.absorb(target, member)
        self._settle(target, source)
        self.save()
        return self._shown(source, target)

    def confirm_member(self, entity_id: str, name: str) -> str:
        """The owner checked a member step 4 attached automatically: it stays, unflagged."""

        member = self.member_of(self.find(entity_id), name)
        if member.pop("attached_by", None) is None:
            raise ReviewConflict(f'השם "{name}" לא צורף אוטומטית, אין מה לאשר')
        self.save()
        return entity_id

    # --- claims -------------------------------------------------------------

    def move_claims(
        self, entity_id: str, name: str, claim_ids: list[str], target_id: str | None
    ) -> str:
        source = self.find(entity_id)
        moving = self._active(source, name, claim_ids)
        target = self._target(target_id, entity_id)
        self.absorb(target, self.claims.member(name, moving))
        member = self.member_of(source, name)
        remaining = set(member["claim_ids"]) - set(moving)
        if remaining or member.get("excluded_claim_ids"):
            self.rebuild(member, remaining, member.get("excluded_claim_ids") or [])
        else:
            source["members"].remove(member)
        self._settle(target, source)
        self.save()
        return self._shown(source, target)

    def copy_claims(
        self, entity_id: str, name: str, claim_ids: list[str], target_id: str | None
    ) -> str:
        source = self.find(entity_id)
        copying = self._active(source, name, claim_ids)
        target = self._target(target_id, entity_id)
        self.absorb(target, self.claims.member(name, copying))
        self._settle(target)
        self.save()
        return entity_id

    def exclude_claims(self, entity_id: str, name: str, claim_ids: list[str]) -> str:
        """These claims do not belong to this name here (kept in ``excluded_claim_ids``)."""

        entity = self.find(entity_id)
        excluding = self._active(entity, name, claim_ids)
        member = self.member_of(entity, name)
        excluded = {*(member.get("excluded_claim_ids") or []), *excluding}
        self.rebuild(member, member["claim_ids"], excluded)
        refresh_entity(entity)
        self.save()
        return entity_id

    def include_claims(self, entity_id: str, name: str, claim_ids: list[str]) -> str:
        """Undo an exclusion: the claims belong to the name again."""

        entity = self.find(entity_id)
        member = self.member_of(entity, name)
        excluded = set(member.get("excluded_claim_ids") or [])
        including = [c for c in claim_ids if c in excluded]
        if not including:
            raise ReviewConflict("הטענות שנבחרו לא מסומנות כלא קשורות")
        self.rebuild(member, {*member["claim_ids"], *including}, excluded - set(including))
        refresh_entity(entity)
        self.save()
        return entity_id

    def delete_claim(self, claim_id: str) -> None:
        """Hard-delete a claim: recorded in ``deleted_claims`` (step 5 skips it), removed from
        every member and from its claim aggregation."""

        if self.claims.claim(claim_id) is None:
            raise EntityNotFound(f"הטענה {claim_id} לא קיימת")
        if claim_id in self.deleted_claims:
            raise ReviewConflict(f"הטענה {claim_id} כבר נמחקה")
        self.deleted_claims.add(claim_id)
        for entity in self.entities:
            touched = False
            for member in entity.get("members") or []:
                excluded = set(member.get("excluded_claim_ids") or [])
                if claim_id in member.get("claim_ids", []) or claim_id in excluded:
                    self.rebuild(member, member["claim_ids"], excluded - {claim_id})
                    touched = True
            if touched:
                refresh_entity(entity)
        if self.aggregations.remove_claim(claim_id):
            self.aggregations.save()
        self.save()
