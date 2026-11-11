import { Bidi, formatCount, Panel } from "@shell";
import { actionTargets, queuePlace, STRUCTURAL } from "./suggestions.js";

// The side column: what can be done with the selected suggestion, or with the marked ones
// (each button says how many of them it moves), and "apply every accepted suggestion".
// Applying runs in the background (a text correction is a model call of a minute or two);
// while a step rewrites a file the tool writes, every button is off and the panel says why.

function Key({ children }) {
  return <kbd className="rounded border border-slate-300 bg-white px-1 font-mono text-[10px] text-slate-500">{children}</kbd>;
}

function ActionButton({ label, keyName, count, primary = false, disabled, onClick }) {
  return (
    <button type="button" className={`btn btn-block justify-between ${primary ? "btn-primary" : ""}`} disabled={disabled || count === 0} onClick={onClick}>
      <span>
        {label}
        {count !== undefined && count !== null && ` (${formatCount(count)})`}
      </span>
      <Key>{keyName}</Key>
    </button>
  );
}

function Locked({ lockedBy }) {
  return (
    <p className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
      „<Bidi>{lockedBy.title}</Bidi>” רץ כעת ומעדכן קובץ שהכלי כותב. אפשר לעיין; ההחלטות והביצוע ייפתחו כשההרצה תסתיים.
    </p>
  );
}

function ForMarked({ targets, act, disabled, clear }) {
  const count = (action) => actionTargets(targets, action).length;
  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-600">
        {formatCount(targets.length)} הצעות מסומנות.{" "}
        <button type="button" className="underline" onClick={clear}>
          ניקוי הסימון
        </button>
      </p>
      <ActionButton label="קבלה" keyName="a" count={count("accept")} disabled={disabled} onClick={() => act.decide("accept")} />
      <ActionButton label="דחייה" keyName="x" count={count("reject")} disabled={disabled} onClick={() => act.decide("reject")} />
      <ActionButton label="החזרה להמתנה" keyName="o" count={count("reopen")} disabled={disabled} onClick={() => act.decide("reopen")} />
      <ActionButton label="ביצוע" keyName="y" count={count("apply")} primary disabled={disabled} onClick={() => act.apply()} />
    </div>
  );
}

function ForOne({ item, applies, act, disabled }) {
  const place = queuePlace(applies, item.id);
  const can = (action) => actionTargets([item], action).length > 0;
  const editable = (item.status === "pending" || item.status === "accepted") && !item.busy;
  return (
    <div className="space-y-2">
      {place && (
        <div className="space-y-1 rounded border border-sky-200 bg-sky-50 p-2 text-xs text-sky-900">
          <p>{place === "running" ? "ההצעה מתבצעת עכשיו ברקע. אפשר להמשיך לעיין בהצעות אחרות; התוצאה תופיע כאן." : "ההצעה בתור לביצוע."}</p>
          {place === "queued" && (
            <button type="button" className="btn btn-sm" onClick={() => act.withdraw(item.id)}>
              הוצאה מהתור
            </button>
          )}
        </div>
      )}
      <ActionButton label="קבלה" keyName="a" primary={item.status === "pending"} disabled={disabled || !can("accept")} onClick={() => act.decide("accept")} />
      <ActionButton label="דחייה" keyName="x" disabled={disabled || !can("reject")} onClick={() => act.decide("reject")} />
      <ActionButton label="החזרה להמתנה" keyName="o" disabled={disabled || !can("reopen")} onClick={() => act.decide("reopen")} />
      <ActionButton label="תיקון ההצעה…" keyName="e" disabled={disabled || !editable} onClick={() => act.edit()} />
      <ActionButton
        label={item.paid ? "ביצוע… (בתשלום)" : "ביצוע"}
        keyName="y"
        primary={item.status === "accepted"}
        disabled={disabled || !can("apply")}
        onClick={() => act.apply()}
      />
      {item.status === "applied" && <p className="text-xs text-slate-500">ההצעה בוצעה; זה סופי. מה השתנה מוצג באמצע.</p>}
      {item.status === "pending" && (
        <p className="text-xs text-slate-500">
          {STRUCTURAL.includes(item.kind)
            ? "שינוי בתוכנית: הביצוע מיידי, ואחריו מריצים את שלב השיוך ואת שלב 7."
            : "תיקון טקסט: הביצוע הוא קריאה אחת למודל (דקה עד שתיים), ברקע."}
        </p>
      )}
    </div>
  );
}

export function ActionsPanel({ item, targets, marked, applies, accepted, act, lockedBy, saving, clear }) {
  const disabled = Boolean(lockedBy) || saving;
  return (
    <Panel
      title="פעולות"
      footer={
        <ActionButton label="ביצוע כל ההצעות שהתקבלו" keyName="shift+y" count={accepted.length} disabled={disabled} onClick={() => act.applyAll()} />
      }
    >
      <div className="space-y-4 p-4 text-sm">
        {lockedBy && <Locked lockedBy={lockedBy} />}
        {marked ? (
          <ForMarked targets={targets} act={act} disabled={disabled} clear={clear} />
        ) : item ? (
          <ForOne item={item} applies={applies} act={act} disabled={disabled} />
        ) : (
          <p className="text-slate-500">בחרו הצעה ברשימה (j / k), או סמנו כמה (רווח) כדי להחליט על כולן יחד.</p>
        )}
      </div>
    </Panel>
  );
}
