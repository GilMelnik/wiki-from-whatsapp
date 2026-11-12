import { ActionButton, Bidi, Kbd, kindOf, nodeById, Panel } from "@shell";
import { DETAILS_TAB, GROUPS_TAB, isAssignmentRow } from "./assign.js";
import { reorderOp } from "./plan.js";

// Side column: what can be done with the selected item, each with its key; for a proposal of
// the assignment step, the decision on it. Every structural change opens a dialog that shows
// its consequences first; reordering only changes the navigation order.

function Reorder({ node, saving, on }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <ActionButton label="↑ Up" keyName="Alt+↑" disabled={saving || !reorderOp(node, -1)} onClick={() => on.reorder(node.id, -1)} />
      <ActionButton label="↓ Down" keyName="Alt+↓" disabled={saving || !reorderOp(node, 1)} onClick={() => on.reorder(node.id, 1)} />
    </div>
  );
}

function PageActions({ id, saving, on }) {
  return (
    <>
      <ActionButton label="Split a new page off it…" keyName="s" disabled={saving} onClick={() => on.open("split", id)} />
      <ActionButton label="Merge with other pages…" keyName="m" disabled={saving} onClick={() => on.open("merge", id)} />
      <ActionButton label="Move to another category…" keyName="t" disabled={saving} onClick={() => on.open("move", id)} />
      <ActionButton label="Change the id…" disabled={saving} onClick={() => on.open("changeId", id)} />
      <ActionButton label="Delete the page…" keyName="Del" danger disabled={saving} onClick={() => on.open("delete", id)} />
    </>
  );
}

function CategoryActions({ node, saving, on }) {
  const empty = node.children.length === 0;
  return (
    <>
      <ActionButton label="New page in the category…" keyName="n" disabled={saving} onClick={() => on.open("addPage", null, { parentId: node.id })} />
      <ActionButton label="New subcategory…" disabled={saving} onClick={() => on.open("addCategory", null, { parentId: node.id })} />
      <ActionButton label="Move…" keyName="t" disabled={saving} onClick={() => on.open("move", node.id)} />
      <ActionButton label="Change the id…" disabled={saving} onClick={() => on.open("changeId", node.id)} />
      {empty ? (
        <ActionButton label="Delete the category…" keyName="Del" danger disabled={saving} onClick={() => on.open("delete", node.id)} />
      ) : (
        <p className="text-xs text-slate-500">Only an empty category can be deleted: first move or delete what is in it.</p>
      )}
    </>
  );
}

function LabelActions({ id, overview, saving, on }) {
  return (
    <>
      <ActionButton label="Merge with other labels…" keyName="m" disabled={saving} onClick={() => on.open("merge", id)} />
      <ActionButton label="New label of the same kind…" keyName="n" disabled={saving} onClick={() => on.open("addLabel", null, { kind: overview.labels[id].kind })} />
      <ActionButton label="Change the id…" disabled={saving} onClick={() => on.open("changeId", id)} />
      <ActionButton label="Delete the label…" keyName="Del" danger disabled={saving} onClick={() => on.open("delete", id)} />
    </>
  );
}

function ProposalActions({ proposal, saving, on }) {
  const blocked = proposal.kind === "scope" && !proposal.target.page_id;
  return (
    <>
      {!blocked && <ActionButton label="Accept…" keyName="a" primary disabled={saving} onClick={() => on.openAccept(proposal)} />}
      <ActionButton label="Reject" keyName="x" danger disabled={saving} onClick={() => on.decide(proposal, "reject")} />
      <p className="text-xs text-slate-500">
        Accepting opens the change prefilled, to correct before it is saved; the next assignment run then places its
        groups. A rejected proposal is not made again.
      </p>
    </>
  );
}

function MoveHelp() {
  return (
    <div className="space-y-1 text-xs text-slate-600">
      <p>
        Pick groups with the boxes (or <Kbd>a</Kbd> for every shown one), then:
      </p>
      <p>
        <Kbd>p</Kbd> move to a page · <Kbd>2</Kbd> second page · <Kbd>x</Kbd> remove from here · <Kbd>o</Kbd> off every page
      </p>
      <p>
        <Kbd>b</Kbd> labels · <Kbd>r</Kbd> back to automatic assignment · <Kbd>f</Kbd> search · <Kbd>u</Kbd> undo
      </p>
      <p>Every move is saved at once and marks the group as assigned by hand: the assignment step will not decide it again.</p>
    </div>
  );
}

export function ActionsPanel({ overview, selectedId, proposal, labelsView, saving, lockedBy, tab, on }) {
  const kind = kindOf(overview, selectedId);
  const node = nodeById(overview, selectedId);
  const special = !labelsView && isAssignmentRow(selectedId) && !proposal;
  const itemGroups = (kind === "page" || kind === "label") && tab === GROUPS_TAB;
  return (
    <Panel
      title={proposal ? "Decide" : "Actions"}
      footer={
        <ActionButton
          label={labelsView ? "New label…" : "New top-level category…"}
          disabled={saving}
          onClick={() => on.open(labelsView ? "addLabel" : "addCategory", null, labelsView ? {} : { parentId: null })}
        />
      }
    >
      <div className="space-y-2 p-4 text-sm">
        {lockedBy && (
          <p className="note note-warning p-2 text-xs">
            <Bidi>{lockedBy.title}</Bidi> is running and rewriting a file this tool writes. You can read; changes open again
            when it finishes.
          </p>
        )}
        {proposal && <ProposalActions proposal={proposal} saving={saving} on={on} />}
        {(kind === "page" || kind === "label") && (
          <ActionButton label={itemGroups ? "Back to the details" : "Its claim groups"} keyName="g" onClick={() => on.showTab(itemGroups ? DETAILS_TAB : GROUPS_TAB)} />
        )}
        {kind === "page" && <PageActions id={selectedId} saving={saving} on={on} />}
        {kind === "category" && <CategoryActions node={node} saving={saving} on={on} />}
        {kind === "label" && <LabelActions id={selectedId} overview={overview} saving={saving} on={on} />}
        {node && <Reorder node={node} saving={saving} on={on} />}
        {(special || itemGroups) && <MoveHelp />}
        {!kind && !special && !proposal && (
          <p className="text-slate-500">
            Select a page, category or label in the list (<Kbd>j</Kbd> / <Kbd>k</Kbd>). Text changes are saved at once;
            changes to the structure or a scope open a window that first shows what will disappear, what will be redirected
            and which claims will be classified again.
          </p>
        )}
      </div>
    </Panel>
  );
}
