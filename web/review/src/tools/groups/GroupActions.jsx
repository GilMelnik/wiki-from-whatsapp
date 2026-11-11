import { countText, formatCount, Ltr, Panel, PanelState } from "@shell";
import { pickedIn } from "./groups.js";
import { Key } from "./Key.jsx";

// Side column of the groups view: what can be done with the selected group. Pick member
// claims in the main column to split them off or move one; every change is saved at once.

export function GroupActions({ detail, picked, saving, on }) {
  const group = detail.data?.group;
  const chosen = group ? pickedIn(picked, group.group_id) : [];
  const canSplit = group && chosen.length > 0 && chosen.length < group.size;
  const footer = group && (
    <>
      <button type="button" className="btn btn-primary btn-block" disabled={saving} onClick={on.merge}>
        Merge with another group
        <Key>m</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving || !canSplit} onClick={on.split}>
        {canSplit ? `Split ${countText(chosen.length, "claim", formatCount)} into a new group` : "Split (pick claims)"}
        <Key>x</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving || chosen.length !== 1} onClick={on.move}>
        Move the picked claim to another group
        <Key>v</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving} onClick={on.addLink}>
        Add a contradiction link
        <Key>c</Key>
      </button>
      {group.representative_manual && (
        <button type="button" className="btn btn-sm btn-block" disabled={saving} onClick={() => on.setRepresentative(null)}>
          Choose the representative text automatically
        </button>
      )}
    </>
  );
  return (
    <Panel
      title="Actions"
      subtitle={
        <>
          Saved at once to <Ltr>claims_aggregated_edited.json</Ltr>
        </>
      }
      footer={footer}
    >
      <PanelState empty={!group} emptyText="No group selected">
        {group && (
          <div className="space-y-3 p-4 text-sm text-slate-600">
            <p>
              {formatCount(chosen.length)} of {countText(group.size, "claim", formatCount)} picked.
            </p>
            <p className="text-xs text-slate-500">
              A merge keeps the older group. A split creates a new group, and the two will not be suggested for
              a merge again. The numbers are recomputed at once, as in a step 5 run.
            </p>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
