"""What the owner compares an entity with: a look-alike entity, and where each contact came from."""

from __future__ import annotations

from typing import Any

from step_4_entities.records import CONTACT_KINDS, claim_contacts
from utils.config import ENTITIES_CONFIG

LOOK_ALIKE_CLAIMS = ENTITIES_CONFIG["review"]["look_alike_claims"]
CONTACT_FIELDS = ("contacts", "contacts_uncertain", "contacts_rejected")


class ContextMixin:
    """Relies on ``EntityStore``: ``claims``, ``find`` and the views' ``_claims_page``."""

    def look_alike(self, entity_id: str) -> dict[str, Any]:
        """A look-alike entity as the comparison shows it: its names, a few claims each."""

        other = self.find(entity_id)
        return {
            "entity_id": entity_id,
            "canonical_name": other.get("canonical_name") or "",
            "status": other.get("status", "suggested"),
            "members": [
                {"name": member["name"], "claims": self._claims_page(other, member, 0, LOOK_ALIKE_CLAIMS)}
                for member in other.get("members") or []
            ],
        }

    def contact_sources(self, entity: dict[str, Any]) -> list[dict[str, Any]]:
        """For every contact the entity lists (confident, uncertain or rejected), the first of
        its members' claims it was found in: ``[{kind, value, name, claim_id, claim_text}]``.
        A contact typed by hand may have none."""

        wanted = {
            (kind, value)
            for field in CONTACT_FIELDS
            for kind, values in (entity.get(field) or {}).items()
            for value in values or []
        }
        found: dict[tuple[str, str], dict[str, Any]] = {}
        originals = self.claims.corpus.original_by_id
        for member in entity.get("members") or []:
            for claim_id in member.get("claim_ids") or []:
                claim = self.claims.claim(claim_id)
                if claim is None:
                    continue
                contacts = claim_contacts(claim, originals.get(claim_id))
                for kind, values in zip(CONTACT_KINDS, contacts):
                    for value in values:
                        if (kind, value) in wanted and (kind, value) not in found:
                            found[(kind, value)] = {
                                "kind": kind,
                                "value": value,
                                "name": member["name"],
                                "claim_id": claim_id,
                                "claim_text": claim.get("claim_text", ""),
                            }
                if len(found) == len(wanted):
                    return list(found.values())
        return list(found.values())
