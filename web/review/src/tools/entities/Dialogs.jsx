import { Bidi, countText, Dialog, Ltr } from "@shell";
import { api, entityPath } from "./api.js";
import { CreateGroupDialog, GroupDialog } from "./ClaimGroupDialogs.jsx";
import { DeletedNamesDialog } from "./DeletedNamesDialog.jsx";
import { TargetDialog } from "./TargetDialog.jsx";

// The one dialog that is open (`dialog.kind`), and what its confirmation saves. `act(request,
// message)` saves and throws on failure (the dialog shows the error and stays open);
// `fire(request, message)` saves and reports a failure as a toast.

const ENTITY_FREE = new Set(["deleteClaim", "createGroup", "group", "deletedNames"]);

function Danger({ title, confirmLabel, onClose, onConfirm, children }) {
  return (
    <Dialog open tone="danger" title={title} confirmLabel={confirmLabel} onClose={onClose} onConfirm={onConfirm}>
      {children}
    </Dialog>
  );
}

export function DialogHost({ dialog, entity, view, saving, reads, close, act, fire }) {
  if (!dialog) return null;
  if (!entity && !ENTITY_FREE.has(dialog.kind)) return null; // the entity went away meanwhile
  const path = entity ? entityPath(entity.entity_id) : null;
  const target = (targetId) => ({ target_entity_id: targetId });

  switch (dialog.kind) {
    case "merge":
      return (
        <TargetDialog
          title={<>Merge “<Bidi>{entity.canonical_name}</Bidi>” into another entity</>}
          description="Every name and claim moves to the entity you choose, and this entity disappears."
          allowNew={false}
          excludeId={entity.entity_id}
          suggested={entity.conflicts.filter((conflict) => conflict.entity_id)}
          initialTarget={dialog.targetId}
          confirmLabel="Merge"
          onClose={close}
          onConfirm={(targetId) => act(() => api.post(`${path}/merge`, target(targetId)), "Entity merged")}
        />
      );
    case "moveMember":
      return (
        <TargetDialog
          title={<>Move the name “<Bidi>{dialog.name}</Bidi>”</>}
          description="The name moves with all its claims. Choose an existing entity or a new one."
          allowNew
          excludeId={entity.entity_id}
          confirmLabel="Move"
          onClose={close}
          onConfirm={(targetId) =>
            act(() => api.post(`${path}/members/move`, { name: dialog.name, ...target(targetId) }), "Name moved")
          }
        />
      );
    case "claims":
      return (
        <TargetDialog
          title={
            <>
              {dialog.mode === "copy" ? "Copy" : "Move"} {countText(dialog.claimIds.length, "claim")} of “<Bidi>{dialog.name}</Bidi>”
            </>
          }
          description={dialog.mode === "copy" ? "The claims also stay here." : "The claims are removed from this name in this entity."}
          allowNew
          excludeId={entity.entity_id}
          confirmLabel={dialog.mode === "copy" ? "Copy" : "Move"}
          onClose={close}
          onConfirm={(targetId) =>
            act(
              () => api.post(`${path}/claims/${dialog.mode}`, { name: dialog.name, claim_ids: dialog.claimIds, ...target(targetId) }),
              dialog.mode === "copy" ? "Claims copied" : "Claims moved"
            )
          }
        />
      );
    case "reject":
      return (
        <Danger
          title="Reject the grouping"
          confirmLabel="Split the entity"
          onClose={close}
          onConfirm={() => act(() => api.post(`${path}/reject`), "Grouping rejected")}
        >
          <p>
            Each name of “<Bidi>{entity.canonical_name}</Bidi>” becomes a separate suggested entity, with
            only the claims tagged with that name (claims that only mention it in their text are removed
            from it).
          </p>
        </Danger>
      );
    case "remove":
      return (
        <Danger
          title="Delete the entity"
          confirmLabel="Delete"
          onClose={close}
          onConfirm={() => act(() => api.del(path, { params: view }), "Entity deleted")}
        >
          <p>
            “<Bidi>{entity.canonical_name}</Bidi>” will be deleted. Its names that are in no other entity
            are recorded as deleted names, and step 4 will not bring them back; you can restore them
            from “Deleted names”.
          </p>
        </Danger>
      );
    case "deleteClaim":
      return (
        <Danger
          title="Delete the claim"
          confirmLabel="Delete the claim"
          onClose={close}
          onConfirm={() => act(() => api.del(`/claims/${encodeURIComponent(dialog.claim.claim_id)}`), "Claim deleted")}
        >
          <Bidi as="p" className="mb-2 rounded bg-slate-50 p-2">
            {dialog.claim.claim_text}
          </Bidi>
          <p>
            Claim <Ltr>{dialog.claim.claim_id}</Ltr> will be removed from every entity and group, and step 5
            will skip it.
          </p>
        </Danger>
      );
    case "createGroup":
      return (
        <CreateGroupDialog
          claimIds={dialog.claimIds}
          texts={dialog.texts}
          onClose={close}
          onConfirm={(representative) =>
            act(() => api.post("/aggregations", { claim_ids: dialog.claimIds, representative }), "Claims combined")
          }
        />
      );
    case "group": {
      const groupPath = `/aggregations/${encodeURIComponent(dialog.group.id)}`;
      const then = (request, message) => fire(request, message).then(close);
      return (
        <GroupDialog
          group={dialog.group}
          saving={saving}
          onClose={close}
          onRepresentative={(claimId) => then(() => api.post(`${groupPath}/representative`, { claim_id: claimId }), "Representative claim changed")}
          onDecouple={(claimId) => then(() => api.post(`${groupPath}/decouple`, { claim_id: claimId }), "Claim removed from the group")}
          onDissolve={() => then(() => api.del(groupPath), "Group dissolved")}
        />
      );
    }
    case "deletedNames":
      return (
        <DeletedNamesDialog
          reads={reads}
          saving={saving}
          onClose={close}
          onRestore={(name) => fire(() => api.post("/deleted-names/restore", { name }), "Name restored as a new entity").then(close)}
        />
      );
    default:
      return null;
  }
}
