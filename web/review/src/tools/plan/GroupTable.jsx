import { useRef, useState } from "react";
import { formatCount, groupCount, Pager, PanelState, Select, usePicking, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { api } from "./api.js";
import { DEFAULT_GROUP_VIEW, groupsQuery, ROLE_OPTIONS, SORT_OPTIONS } from "./assign.js";
import { Key } from "./bits.jsx";
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
  page: "אין קבוצות בדף הזה.",
  label: "אין קבוצות עם התווית הזו.",
  unplaced: "כל הקבוצות שובצו בדף.",
  waiting: "אין קבוצות שממתינות להרצת השיוך.",
};

function ActionBar({ scope, count, busy, locked, act, open, clear }) {
  const removeLabel = scope === "label" ? "הסרת התווית הזו" : "הסרה מהדף הזה";
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-blue-200 bg-blue-50 px-3 py-2 text-xs">
      <span className="font-medium text-blue-900">נבחרו: {groupCount(count, formatCount)}</span>
      <button type="button" className="btn btn-sm" onClick={clear}>ניקוי</button>
      <span className="flex-1" />
      <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => open("primary")}>
        העברה לדף… <Key>p</Key>
      </button>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => open("second")}>
        דף נוסף… <Key>2</Key>
      </button>
      {(scope === "page" || scope === "label") && (
        <button type="button" className="btn btn-sm" disabled={busy} onClick={act.removeHere}>
          {removeLabel} <Key>x</Key>
        </button>
      )}
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => act.run([{ action: "unplace" }])}>
        מכל הדפים <Key>o</Key>
      </button>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => open("labels")}>
        תוויות… <Key>b</Key>
      </button>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => act.run([{ action: "unpin" }])} title="שלב השיוך יחליט עליהן שוב בהרצה הבאה">
        חזרה לאוטומטי <Key>r</Key>
      </button>
      {locked && <span className="basis-full text-amber-800">שלב רץ כעת ומעדכן את הקבצים: אפשר לעיין, ההעברות ייפתחו כשיסתיים.</span>}
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
    { keys: "a", label: "בחירת כל הקבוצות המוצגות, או ביטול הבחירה", run: () => picking.setMany(visible, shown !== "all") },
    { keys: "p", label: "העברת הקבוצות שנבחרו לדף אחר", run: () => open("primary") },
    { keys: "2", label: "הוספת דף נוסף לקבוצות שנבחרו", run: () => open("second") },
    { keys: "x", label: "הסרת הקבוצות שנבחרו מהדף / מהתווית שמוצגים", run: () => can && (scope === "page" || scope === "label") && act.removeHere() },
    { keys: "o", label: "הורדת הקבוצות שנבחרו מכל הדפים", run: () => can && act.run([{ action: "unplace" }]) },
    { keys: "b", label: "תוויות לקבוצות שנבחרו", run: () => open("labels") },
    { keys: "r", label: "החזרת הקבוצות שנבחרו לשיוך אוטומטי", run: () => can && act.run([{ action: "unpin" }]) },
    { keys: "f", label: "חיפוש בקבוצות", run: () => searchRef.current?.focus() },
  ]);

  return (
    <div className="flex min-h-0 flex-col rounded border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-2 text-xs text-slate-600">
        <label className="flex items-center gap-1" title="בחירת כל הקבוצות המוצגות (a)">
          <input
            type="checkbox"
            checked={shown === "all"}
            ref={(box) => box && (box.indeterminate = shown === "some")}
            disabled={visible.length === 0}
            onChange={() => picking.setMany(visible, shown !== "all")}
          />
          כל המוצגות <Key>a</Key>
        </label>
        <input
          ref={searchRef}
          dir="auto"
          className="field min-w-40 flex-1"
          placeholder="סינון לפי טקסט, ישות או מזהה (f)"
          value={view.q}
          onChange={(event) => changeView({ q: event.target.value })}
        />
        <Select label="סדר" value={view.sort} options={SORT_OPTIONS} onChange={(sort) => changeView({ sort })} />
        {scope === "page" && (
          <Select
            label="הצג"
            value={view.role}
            options={ROLE_OPTIONS.map((option) => ({ ...option, label: `${option.label} (${formatCount(data?.counts?.[option.value] ?? 0)})` }))}
            onChange={(role) => changeView({ role })}
          />
        )}
        <span>
          {formatCount(data?.total ?? 0)} קבוצות{data?.manual ? ` · ${formatCount(data.manual)} ידני` : ""}
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
        emptyText={view.q.trim() ? "אין קבוצות שמתאימות לסינון." : EMPTY_TEXTS[scope]}
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
