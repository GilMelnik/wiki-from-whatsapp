import { useRef, useState } from "react";
import { formatCount, groupCount, Kbd, Pager, PanelState, Select, usePicking, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { api } from "./api.js";
import { DEFAULT_GROUP_VIEW, groupsQuery, ROLE_OPTIONS, SORT_OPTIONS } from "./assign.js";
import { GroupRow } from "./GroupRow.jsx";
import { LabelsDialog, PagePickerDialog } from "./MoveDialogs.jsx";
import { TOOL_ID } from "./plan.js";

// The claim groups on a page / with a label / on no page / waiting for the assignment run,
// least certain first, with a text filter and server paging. Groups are picked (one or many,
// across pages; "a" picks or drops every visible one) and moved together: another primary
// page, a second page, off this page / label, off every page, labels, or back to automatic.
// Every move pins the groups (the assignment step never re-decides them) and can be undone.
// Render it with `key` = the list (scope + id), so a new list starts on page one, unpicked.

const EMPTY_TEXTS = {
  page: "No groups on this page.",
  label: "No groups with this label.",
  unplaced: "Every group is placed on a page.",
  waiting: "No groups are waiting for the assignment run.",
};

function ActionBar({ scope, count, busy, locked, act, open, clear }) {
  const removeLabel = scope === "label" ? "Remove this label" : "Remove from this page";
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-blue-200 bg-blue-50 px-3 py-2 text-xs">
      <span className="font-medium text-blue-900">Picked: {groupCount(count, formatCount)}</span>
      <button type="button" className="btn btn-sm" onClick={clear}>Clear</button>
      <span className="flex-1" />
      <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => open("primary")}>
        Move to a page… <Kbd>p</Kbd>
      </button>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => open("second")}>
        Second page… <Kbd>2</Kbd>
      </button>
      {(scope === "page" || scope === "label") && (
        <button type="button" className="btn btn-sm" disabled={busy} onClick={act.removeHere}>
          {removeLabel} <Kbd>x</Kbd>
        </button>
      )}
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => act.run([{ action: "unplace" }])}>
        Off every page <Kbd>o</Kbd>
      </button>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => open("labels")}>
        Labels… <Kbd>b</Kbd>
      </button>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => act.run([{ action: "unpin" }])} title="The assignment step will decide them again at its next run">
        Back to automatic <Kbd>r</Kbd>
      </button>
      {locked && <span className="basis-full text-amber-800">A step is running and updating the files: you can look around; moves open again when it finishes.</span>}
    </div>
  );
}

export function GroupTable({ overview, scope, itemId = null, watch, locked, saving, editGroups }) {
  const toast = useToast();
  const [view, setView] = useStoredState(TOOL_ID, "groupView", DEFAULT_GROUP_VIEW);
  const [offset, setOffset] = useState(0);
  const [dialog, setDialog] = useState(null);
  const searchRef = useRef(null);
  const seen = useRef(new Map());
  const picking = usePicking(`${scope}:${itemId}`);
  const list = useResource(
    () => api.get("/assignments/groups", groupsQuery(scope, itemId, view, offset)),
    [scope, itemId, view.sort, view.role, view.q, offset],
    { watch, keepPrevious: true }
  );
  const data = list.data;
  const rows = data?.items ?? [];
  for (const row of rows) seen.current.set(row.group_id, row);
  const visible = rows.filter((row) => row.record).map((row) => row.group_id);
  const shown = picking.stateOf(visible);
  const pickedRows = picking.picked.map((id) => seen.current.get(id)).filter(Boolean);
  const busy = saving || locked;
  const can = picking.count > 0 && !busy;

  const changeView = (patch) => {
    setView((previous) => ({ ...previous, ...patch }));
    setOffset(0);
  };
  const run = async (changes) => {
    const result = await editGroups(picking.picked, changes);
    picking.clear();
    list.reload();
    return result;
  };
  const act = {
    run: (changes) => run(changes).catch((error) => toast.error(error)),
    removeHere: () =>
      act.run([scope === "label" ? { action: "remove_label", label_id: itemId } : { action: "remove_page", page_id: itemId }]),
  };
  const open = (kind) => can && setDialog(kind);
  useShortcuts([
    { keys: "a", label: "Pick every shown group, or unpick them", run: () => picking.setMany(visible, shown !== "all") },
    { keys: "p", label: "Move the picked groups to another page", run: () => open("primary") },
    { keys: "2", label: "Add a second page to the picked groups", run: () => open("second") },
    { keys: "x", label: "Remove the picked groups from the shown page / label", run: () => can && (scope === "page" || scope === "label") && act.removeHere() },
    { keys: "o", label: "Take the picked groups off every page", run: () => can && act.run([{ action: "unplace" }]) },
    { keys: "b", label: "Labels of the picked groups", run: () => open("labels") },
    { keys: "r", label: "Put the picked groups back to automatic assignment", run: () => can && act.run([{ action: "unpin" }]) },
    { keys: "f", label: "Search the groups", run: () => searchRef.current?.focus() },
  ]);

  return (
    <div className="flex min-h-0 flex-col rounded border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-2 text-xs text-slate-600">
        <label className="flex items-center gap-1" title="Pick every shown group (a)">
          <input
            type="checkbox"
            checked={shown === "all"}
            ref={(box) => box && (box.indeterminate = shown === "some")}
            disabled={visible.length === 0}
            onChange={() => picking.setMany(visible, shown !== "all")}
          />
          All shown <Kbd>a</Kbd>
        </label>
        <input
          ref={searchRef}
          dir="auto"
          className="field min-w-40 flex-1"
          placeholder="Filter by text, entity or id (f)"
          value={view.q}
          onChange={(event) => changeView({ q: event.target.value })}
        />
        <Select label="Order" value={view.sort} options={SORT_OPTIONS} onChange={(sort) => changeView({ sort })} />
        {scope === "page" && (
          <Select
            label="Show"
            value={view.role}
            options={ROLE_OPTIONS.map((option) => ({ ...option, label: `${option.label} (${formatCount(data?.counts?.[option.value] ?? 0)})` }))}
            onChange={(role) => changeView({ role })}
          />
        )}
        <span>
          {groupCount(data?.total ?? 0, formatCount)}{data?.manual ? ` · ${formatCount(data.manual)} manual` : ""}
        </span>
        {data && <Pager offset={data.offset} limit={data.limit} total={data.total} onChange={setOffset} />}
      </div>
      {picking.count > 0 && (
        <ActionBar scope={scope} count={picking.count} busy={busy} locked={locked} act={act} open={open} clear={picking.clear} />
      )}
      <PanelState
        loading={list.loading}
        error={list.error}
        hasData={Boolean(data)}
        onRetry={list.reload}
        empty={Boolean(data) && rows.length === 0}
        emptyText={view.q.trim() ? "No groups match the filter." : EMPTY_TEXTS[scope]}
      >
        <ul className={`divide-y divide-slate-100 ${list.loading ? "opacity-60" : ""}`}>
          {rows.map((row) => (
            <GroupRow
              key={row.group_id}
              row={row}
              overview={overview}
              scope={scope}
              itemId={itemId}
              picked={picking.has(row.group_id)}
              onToggle={() => picking.toggle(row.group_id)}
            />
          ))}
        </ul>
      </PanelState>
      {(dialog === "primary" || dialog === "second") && (
        <PagePickerDialog overview={overview} rows={pickedRows} mode={dialog} onClose={() => setDialog(null)} onConfirm={run} />
      )}
      {dialog === "labels" && <LabelsDialog overview={overview} rows={pickedRows} onClose={() => setDialog(null)} onConfirm={run} />}
    </div>
  );
}
