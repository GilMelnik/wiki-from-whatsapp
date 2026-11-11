import { useState } from "react";
import { Bidi, Dialog, formatCount, groupCount, labelItems, Ltr, pathTitles, titleOf } from "@shell";
import { labelChanges, labelShare, nextLabelTarget, pageChoices } from "./assign.js";
import { percent } from "./plan.js";

// The dialogs of a bulk move: pick a page (the primary one, or a second page) and set labels.
// `rows` are the picked groups (as the list showed them); `onConfirm(changes)` saves and
// throws on failure (the dialog shows the error and stays open).

const MODES = {
  primary: { title: "העברה לדף", confirm: "העברה", action: "set_primary" },
  second: { title: "הוספת דף נוסף", confirm: "הוספה", action: "add_page" },
};

function PageOption({ overview, id, checked, onChoose, note }) {
  return (
    <li>
      <label className={`flex cursor-pointer items-baseline gap-2 rounded px-2 py-1 ${checked ? "bg-blue-50" : "hover:bg-slate-50"}`}>
        <input type="radio" name="page" checked={checked} onChange={() => onChoose(id)} />
        <Bidi className="font-medium">{titleOf(overview, id)}</Bidi>
        <Bidi className="text-xs text-slate-400">{pathTitles(overview, id).join(" › ")}</Bidi>
        <Ltr className="text-[11px] text-slate-400">{id}</Ltr>
        {note && <span className="ms-auto text-xs text-slate-600">{note}</span>}
      </label>
    </li>
  );
}

export function PagePickerDialog({ overview, rows, mode, onClose, onConfirm }) {
  const [query, setQuery] = useState("");
  const pathOf = (id) => pathTitles(overview, id).join(" ");
  const { considered, others } = pageChoices(overview, rows, query, pathOf);
  const order = [...considered.map(({ id }) => id), ...others];
  const [chosen, setChosen] = useState(order[0] ?? null);
  const current = order.includes(chosen) ? chosen : (order[0] ?? null);
  const step = (delta) => {
    const index = order.indexOf(current);
    const next = order[Math.min(order.length - 1, Math.max(0, index + delta))];
    if (next) setChosen(next);
  };
  const { title, confirm, action } = MODES[mode];
  const single = rows.length === 1;
  return (
    <Dialog
      open
      title={`${title}: ${groupCount(rows.length, formatCount)}`}
      size="lg"
      confirmLabel={confirm}
      confirmDisabled={!current}
      onClose={onClose}
      onConfirm={() => onConfirm([{ action, page_id: current }])}
    >
      <div className="space-y-3">
        <p className="text-slate-600">
          {mode === "primary"
            ? "הדף שנבחר יהיה הדף הראשי של הקבוצות (דף נוסף, אם יש, נשאר)."
            : "הדף שנבחר יתווסף כדף נוסף שרק מזכיר את הקבוצות (במקום דף נוסף קיים)."}{" "}
          הקבוצות יסומנו כמשויכות ביד, ושלב השיוך לא ישנה אותן.
        </p>
        <input
          dir="auto"
          className="field w-full"
          placeholder="חיפוש דף לפי שם, מזהה או מקום בעץ"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              step(event.key === "ArrowDown" ? 1 : -1);
            }
          }}
        />
        {considered.length > 0 && (
          <section>
            <h3 className="section-title">דפים שהמסווג שקל</h3>
            <ul className="space-y-0.5">
              {considered.map(({ id, mean, count }) => (
                <PageOption
                  key={id}
                  overview={overview}
                  id={id}
                  checked={current === id}
                  onChoose={setChosen}
                  note={single ? percent(mean) : `ממוצע ${percent(mean)} · ${formatCount(count)} מתוך ${formatCount(rows.length)}`}
                />
              ))}
            </ul>
          </section>
        )}
        <section>
          <h3 className="section-title">{considered.length > 0 ? "שאר הדפים" : "כל הדפים"}</h3>
          {others.length === 0 ? (
            <p className="text-xs text-slate-400">אין דפים נוספים שמתאימים לחיפוש.</p>
          ) : (
            <ul className="max-h-64 space-y-0.5 overflow-y-auto">
              {others.map((id) => (
                <PageOption key={id} overview={overview} id={id} checked={current === id} onChoose={setChosen} />
              ))}
            </ul>
          )}
        </section>
      </div>
    </Dialog>
  );
}

export function LabelsDialog({ overview, rows, onClose, onConfirm }) {
  const labels = labelItems(overview);
  const initial = Object.fromEntries(labels.map(({ id }) => [id, labelShare(rows, id)]));
  const [targets, setTargets] = useState({});
  const changes = labelChanges(initial, targets);
  const toggle = (id) => setTargets((previous) => ({ ...previous, [id]: nextLabelTarget(initial[id], previous[id]) }));
  return (
    <Dialog
      open
      title={`תוויות: ${groupCount(rows.length, formatCount)}`}
      size="lg"
      confirmLabel={changes.length ? `שמירת ${formatCount(changes.length)} שינויים` : "שמירה"}
      confirmDisabled={changes.length === 0}
      onClose={onClose}
      onConfirm={() => onConfirm(changes)}
    >
      <div className="space-y-3">
        <p className="text-slate-600">
          סימון מוסיף את התווית לכל הקבוצות שנבחרו, וביטול הסימון מסיר אותה מכולן. תיבה חצי מסומנת:
          רק לחלק מהן יש התווית, והיא לא תשתנה אם לא תלחצו עליה.
        </p>
        <ul className="max-h-80 space-y-0.5 overflow-y-auto">
          {labels.map((label) => {
            const state = targets[label.id] ?? initial[label.id];
            return (
              <li key={label.id}>
                {label.first && <span className="mt-2 block text-xs font-semibold text-slate-500"><Ltr>{label.labelKind}</Ltr></span>}
                <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-slate-50">
                  <input
                    type="checkbox"
                    checked={state === "all"}
                    ref={(box) => box && (box.indeterminate = state === "some")}
                    onChange={() => toggle(label.id)}
                  />
                  <Bidi>{label.title_he}</Bidi>
                  <Ltr className="text-[11px] text-slate-400">{label.id}</Ltr>
                  {targets[label.id] && targets[label.id] !== initial[label.id] && <span className="badge badge-sky">ישתנה</span>}
                </label>
              </li>
            );
          })}
        </ul>
        {Object.keys(targets).length > 0 && (
          <button type="button" className="btn btn-sm" onClick={() => setTargets({})}>
            איפוס
          </button>
        )}
      </div>
    </Dialog>
  );
}
