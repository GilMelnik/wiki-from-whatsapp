import { Bidi, kindOf, Ltr, nodeById, Panel, titleOf } from "@shell";
import { DETAILS_TAB, GROUPS_TAB, isAssignmentRow } from "./assign.js";
import { Key } from "./bits.jsx";
import { OVERVIEW_ID, reorderOp } from "./plan.js";

// Side column: what can be done with the selected item. Every button opens a dialog that
// shows the consequences first, except reordering, which only changes the navigation order.

function Button({ onClick, disabled, children, danger, title }) {
  return (
    <button type="button" title={title} className={`btn btn-sm btn-block justify-between ${danger ? "text-rose-700" : ""}`} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  );
}

function Reorder({ node, saving, on }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <Button disabled={saving || !reorderOp(node, -1)} onClick={() => on.reorder(node.id, -1)}>
        ↑ Up <Key>Alt+↑</Key>
      </Button>
      <Button disabled={saving || !reorderOp(node, 1)} onClick={() => on.reorder(node.id, 1)}>
        ↓ Down <Key>Alt+↓</Key>
      </Button>
    </div>
  );
}

function PageActions({ id, saving, on }) {
  return (
    <>
      <Button disabled={saving} onClick={() => on.open("split", id)}>Split a new page off it… <Key>s</Key></Button>
      <Button disabled={saving} onClick={() => on.open("merge", id)}>Merge with other pages… <Key>m</Key></Button>
      <Button disabled={saving} onClick={() => on.open("move", id)}>Move to another category… <Key>t</Key></Button>
      <Button disabled={saving} onClick={() => on.open("changeId", id)}>Change the id…</Button>
      <Button danger disabled={saving} onClick={() => on.open("delete", id)}>Delete the page… <Key>Del</Key></Button>
    </>
  );
}

function CategoryActions({ node, saving, on }) {
  return (
    <>
      <Button disabled={saving} onClick={() => on.open("addPage", null, { parentId: node.id })}>
        New page in the category… <Key>n</Key>
      </Button>
      <Button disabled={saving} onClick={() => on.open("addCategory", null, { parentId: node.id })}>New subcategory…</Button>
      <Button disabled={saving} onClick={() => on.open("move", node.id)}>Move… <Key>t</Key></Button>
      <Button disabled={saving} onClick={() => on.open("changeId", node.id)}>Change the id…</Button>
      <Button
        danger
        disabled={saving || node.children.length > 0}
        title={node.children.length > 0 ? "Only an empty category can be deleted: first move or delete what is in it" : undefined}
        onClick={() => on.open("delete", node.id)}
      >
        Delete the category… <Key>Del</Key>
      </Button>
      {node.children.length > 0 && <p className="text-xs text-slate-500">Only an empty category can be deleted.</p>}
    </>
  );
}

function LabelActions({ id, overview, saving, on }) {
  return (
    <>
      <Button disabled={saving} onClick={() => on.open("merge", id)}>Merge with other labels… <Key>m</Key></Button>
      <Button disabled={saving} onClick={() => on.open("changeId", id)}>Change the id…</Button>
      <Button danger disabled={saving} onClick={() => on.open("delete", id)}>Delete the label… <Key>Del</Key></Button>
      <Button disabled={saving} onClick={() => on.open("addLabel", null, { kind: overview.labels[id].kind })}>
        New label of the same kind… <Key>n</Key>
      </Button>
    </>
  );
}

function GroupsTab({ tab, on }) {
  const groups = tab === GROUPS_TAB;
  return (
    <Button onClick={() => on.showTab(groups ? DETAILS_TAB : GROUPS_TAB)}>
      {groups ? "Back to the details" : "Assigned claim groups…"} <Key>g</Key>
    </Button>
  );
}

function MoveHelp() {
  return (
    <div className="space-y-1 text-xs text-slate-600">
      <p>Pick groups with the boxes (or <Key>a</Key> for every shown one), then:</p>
      <p><Key>p</Key> move to a page · <Key>2</Key> second page · <Key>x</Key> remove from here · <Key>o</Key> off every page</p>
      <p><Key>b</Key> labels · <Key>r</Key> back to automatic assignment · <Key>f</Key> search · <Key>u</Key> undo</p>
      <p>Every move is saved at once and marks the group as assigned by hand: the assignment step will not decide it again.</p>
    </div>
  );
}

export function ActionsPanel({ overview, selectedId, labelsView, saving, tab, on }) {
  const kind = kindOf(overview, selectedId);
  const node = nodeById(overview, selectedId);
  const special = !labelsView && isAssignmentRow(selectedId);
  return (
    <Panel
      title="Actions"
      subtitle={
        selectedId && selectedId !== OVERVIEW_ID && kind ? (
          <>
            <Bidi>{titleOf(overview, selectedId)}</Bidi> <Ltr>{selectedId}</Ltr>
          </>
        ) : (
          "Every change is checked before it is saved"
        )
      }
      footer={
        <>
          {labelsView ? (
            <Button disabled={saving} onClick={() => on.open("addLabel", null, {})}>New label…</Button>
          ) : (
            <Button disabled={saving} onClick={() => on.open("addCategory", null, { parentId: null })}>New top-level category…</Button>
          )}
          <Button disabled={saving || !overview.undo} onClick={on.undo} title={overview.undo?.label}>
            Undo the last change <Key>u</Key>
          </Button>
        </>
      }
    >
      <div className="space-y-2 p-4 text-sm">
        {(kind === "page" || kind === "label") && <GroupsTab tab={tab} on={on} />}
        {kind === "page" && <PageActions id={selectedId} saving={saving} on={on} />}
        {kind === "category" && <CategoryActions node={node} saving={saving} on={on} />}
        {kind === "label" && <LabelActions id={selectedId} overview={overview} saving={saving} on={on} />}
        {node && <Reorder node={node} saving={saving} on={on} />}
        {(special || ((kind === "page" || kind === "label") && tab === GROUPS_TAB)) && <MoveHelp />}
        {!kind && !special && (
          <p className="text-slate-500">
            Select a page, category or label in the list. Text changes are saved at once; changes to
            the structure or a scope open a window that first shows what will disappear, what will be
            redirected and which claims will be classified again.
          </p>
        )}
        {overview.undo && (
          <p className="pt-2 text-xs text-slate-500">
            Last change: {overview.undo.label}
          </p>
        )}
      </div>
    </Panel>
  );
}
