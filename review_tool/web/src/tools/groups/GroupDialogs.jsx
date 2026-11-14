import { countText, Dialog, formatCount } from "@shell";
import { api, groupPath } from "./api.js";
import { GroupPicker } from "./GroupPicker.jsx";
import { pickedIn } from "./groups.js";
import { MergeNotes } from "./MergeNotes.jsx";

// The one dialog that is open in the groups view (`dialog.kind`) and what its confirmation
// saves. `act(request, message)` saves and throws on failure (the dialog shows the error and
// stays open). A merge shows, for the group chosen, whether the two dispute each other (the
// merge stays possible) and whether it releases a withheld group.

export function DialogHost({ dialog, group, picked, minSupporters, watch, close, act }) {
  if (!dialog || !group) return null;
  const path = groupPath(group.group_id);
  const suggested = group.partners.map((pair) => (pair.a.group_id === group.group_id ? pair.b : pair.a));
  const chosen = pickedIn(picked, group.group_id);

  switch (dialog.kind) {
    case "merge":
      return (
        <GroupPicker
          title="Merge with another group"
          description="The claims of both groups become one group; the older group's id is kept."
          excludeId={group.group_id}
          suggested={suggested}
          initial={dialog.choice}
          confirmLabel="Merge"
          confirmFor={(choice) => (group.contradicts.some((link) => link.group_id === choice) ? "Merge anyway" : null)}
          notes={(choice) => <MergeNotes ids={[group.group_id, choice]} minSupporters={minSupporters} watch={watch} />}
          onClose={close}
          onConfirm={(otherId) => act(() => api.post("/groups/merge", { group_ids: [group.group_id, otherId] }), "Groups merged")}
        />
      );
    case "move":
      return (
        <GroupPicker
          title="Move the claim to another group"
          description={group.size === 1 ? "This is the only claim in the group: the group will merge into the group you choose." : "The claim will move to the group you choose."}
          excludeId={group.group_id}
          suggested={suggested}
          confirmLabel="Move"
          onClose={close}
          onConfirm={(targetId) => act(() => api.post(`${path}/move`, { claim_id: chosen[0], target_id: targetId }), "Claim moved")}
        />
      );
    case "link":
      return (
        <GroupPicker
          title="Add a contradiction link"
          description="The two groups will be marked as contradicting each other (step 5 keeps the link)."
          excludeId={group.group_id}
          confirmLabel="Link"
          onClose={close}
          onConfirm={(otherId) => act(() => api.post(`${path}/contradictions`, { group_id: otherId, linked: true }), "Contradiction link added")}
        />
      );
    case "split":
      return (
        <Dialog
          open
          tone="danger"
          title="Split into a new group"
          confirmLabel="Split"
          onClose={close}
          onConfirm={() => act(() => api.post(`${path}/split`, { claim_ids: chosen }), "Group split")}
        >
          <p>
            {countText(chosen.length, "picked claim", formatCount)} will move to a new group, and{" "}
            {formatCount(group.size - chosen.length)} will stay here. The two will not be suggested for a merge again.
          </p>
        </Dialog>
      );
    default:
      return null;
  }
}
