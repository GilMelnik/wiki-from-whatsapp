import { ActionButton, countText, formatCount, Panel, PanelState } from "@shell";
import { pickedIn } from "./groups.js";

// Side column of a groups list: what can be done with the selected group, each with its key.
// Split and move appear once member claims are picked in the middle column; every change is
// saved at once and the numbers are counted again, as step 5 would.

export function GroupActions({ detail, picked, saving, on }) {
  const group = detail.data?.group;
  const chosen = group ? pickedIn(picked, group.group_id) : [];
  const canSplit = group && chosen.length > 0 && chosen.length < group.size;
  return (
    <Panel title="Actions">
      <PanelState empty={!group} emptyText="No group selected">
        {group && (
          <div className="space-y-4 p-4 text-sm">
            <div className="space-y-2">
              <ActionButton label="Merge with another group…" keyName="m" primary disabled={saving} onClick={on.merge} />
              {canSplit && (
                <ActionButton label={`Split ${countText(chosen.length, "claim", formatCount)} into a new group…`} keyName="x" disabled={saving} onClick={on.split} />
              )}
              {chosen.length === 1 && <ActionButton label="Move the picked claim to another group…" keyName="v" disabled={saving} onClick={on.move} />}
              <ActionButton label="Add a contradiction link…" keyName="c" disabled={saving} onClick={on.addLink} />
              {group.representative_manual && (
                <ActionButton label="Choose the text automatically again" disabled={saving} onClick={() => on.setRepresentative(null)} />
              )}
            </div>
            <div className="space-y-2 text-xs text-slate-500">
              {group.size > 1 && chosen.length === 0 && <p>Pick claims in the middle column to split them off, or one to move it.</p>}
              <p>A merge keeps the older group. A split makes a new group, and the two are not suggested for a merge again.</p>
            </div>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
