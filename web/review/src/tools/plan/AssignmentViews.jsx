import { Bidi, formatCount, formatDateTime, Ltr, Panel, useJobs, useStepRun } from "@shell";
import { ASSIGNMENTS_KEY, DETAILS_TAB, findingText, groupCount, GROUPS_TAB, PROPOSALS_ID, UNPLACED_ID, WAITING_ID } from "./assign.js";
import { Key, Tabs } from "./bits.jsx";
import { GroupTable } from "./GroupTable.jsx";

// The assignment views that are not about one page or label: the lists of groups on no page
// and of groups waiting for the next assignment run, the overview's numbers (with the labels
// that attach to very few groups or to a very large share), and what to show before step 6b
// has run, and the tabs that switch a page or a label between its details and its groups.

const LIST_TEXTS = {
  unplaced: {
    title: "קבוצות בלי דף",
    text: "קבוצות שהמסווג לא שיבץ בביטחון באף דף (או שהוצאתם ביד מכל הדפים). שלב השיוך הציע עבור חלקן דף, תווית או תחום רחב יותר (ברשימת ההצעות); את השאר אפשר לשבץ כאן ביד, או להשאיר בלי דף.",
  },
  waiting: {
    title: "ממתינות להרצת השיוך",
    text: "קבוצות שכל הדפים שלהן נמחקו מהתוכנית, וקבוצות חדשות שעוד לא שויכו. הרצת השיוך הבאה תשאל אותן שוב; קבוצה שכבר יש לה שיוך אפשר גם להעביר ביד.",
  },
};

/** The step-6b run button (the frame confirms a paid run first). */
export function RunAssignButton({ label = "הרצת שלב השיוך…" }) {
  const run = useStepRun();
  const { stepsByArtifact, runningByArtifact } = useJobs();
  const step = stepsByArtifact[ASSIGNMENTS_KEY];
  const running = runningByArtifact[ASSIGNMENTS_KEY];
  return (
    <button type="button" className="btn btn-sm" disabled={!step || Boolean(running)} onClick={() => run(step.key)}>
      {running ? "שלב השיוך רץ…" : label}
    </button>
  );
}

export function NotRunYet() {
  return (
    <div className="space-y-2 rounded border border-slate-200 bg-slate-50 p-3 text-slate-600">
      <p>
        שלב השיוך (6ב) עוד לא רץ: אין עדיין שיוך של קבוצות טענות לדפים ולתוויות. מריצים אותו אחרי
        שבודקים את התוכנית (<Ltr>python -m step_6_plan.assign</Ltr>), ואז כאן אפשר לראות ולתקן לאן כל
        קבוצה הגיעה.
      </p>
      <RunAssignButton />
    </div>
  );
}

export function GroupsPanel({ scope, overview, ...groups }) {
  const { title, text } = LIST_TEXTS[scope];
  const summary = overview.assignments;
  return (
    <Panel title={title} subtitle={summary?.exists ? groupCount(summary.counts[scope], formatCount) : null}>
      <div className="space-y-4 p-4 text-sm">
        <p className="text-slate-600">{text}</p>
        {summary?.exists ? <GroupTable key={scope} overview={overview} scope={scope} {...groups} /> : <NotRunYet />}
      </div>
    </Panel>
  );
}

function Count({ value, label, onClick, tone = "" }) {
  const body = (
    <>
      <span className={`block text-xl font-semibold ${tone}`}>{formatCount(value)}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </>
  );
  return onClick ? (
    <button type="button" className="rounded border border-slate-200 bg-white p-2 text-start hover:bg-slate-50" onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className="rounded border border-slate-200 bg-white p-2">{body}</div>
  );
}

export function AssignmentSummary({ overview, onSelect }) {
  const summary = overview.assignments;
  const newer = overview.newer_groups;
  if (!summary?.exists) return <NotRunYet />;
  if (summary.problem) return <p className="text-rose-700">{summary.problem}</p>;
  const { counts } = summary;
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        לפי <Ltr>{summary.classifier ?? "—"}</Ltr>, הורץ {formatDateTime(summary.generated_at)}
        {summary.edited_exists && " · כולל השינויים שעשיתם כאן"} · <Ltr>{summary.file}</Ltr>
      </p>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Count value={counts.groups} label="קבוצות ששויכו" />
        <Count value={counts.unplaced} label="בלי דף" tone={counts.unplaced ? "text-amber-700" : ""} onClick={() => onSelect(UNPLACED_ID)} />
        <Count value={counts.two_pages} label="בשני דפים" />
        <Count value={counts.manual} label="שויכו ביד" />
        {counts.waiting > 0 && <Count value={counts.waiting} label="ממתינות להרצה" tone="text-amber-700" onClick={() => onSelect(WAITING_ID)} />}
        <Count value={summary.proposals.pending} label="הצעות ממתינות" tone={summary.proposals.pending ? "text-amber-700" : ""} onClick={() => onSelect(PROPOSALS_ID)} />
      </div>
      {counts.followed > 0 && (
        <p className="text-xs text-amber-800">
          בשיוך של {groupCount(counts.followed, formatCount)} עדיין יש מזהים שמוזגו או נמחקו מהתוכנית. התצוגה
          כבר קוראת אותן לפי התוכנית של היום, והרצת השיוך הבאה תעדכן גם את הקובץ.
        </p>
      )}
      {newer && newer.count > 0 && (
        <p>
          מאז התכנון נוספו {groupCount(newer.count, formatCount)} שהמודל לא ראה; {formatCount(newer.assigned)} מהן כבר
          שובצו. את השאר משבץ שלב השיוך, בלי לתכנן מחדש.
        </p>
      )}
      {summary.label_findings.length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-medium text-slate-600">תוויות שכדאי לבדוק</h4>
          <ul className="space-y-1">
            {summary.label_findings.map((finding) => (
              <li key={finding.label_id}>
                <button type="button" className="text-start hover:underline" onClick={() => onSelect(finding.label_id)}>
                  <Bidi>{findingText(finding, overview, formatCount)}</Bidi>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** "פרטים" | "קבוצות טענות (N)" above a page or a label. */
export function ItemTabs({ count, tab, onChange }) {
  const tabs = [
    { id: DETAILS_TAB, label: "פרטים" },
    { id: GROUPS_TAB, label: count === undefined ? "קבוצות טענות" : `קבוצות טענות (${formatCount(count)})` },
  ];
  return (
    <div className="flex items-center gap-2">
      <Tabs tabs={tabs} value={tab} onChange={onChange} />
      <span className="text-xs text-slate-400">
        <Key>g</Key> מעבר
      </span>
    </div>
  );
}

/** The groups of a page or a label, or why there are none yet. */
export function ItemGroups({ scope, itemId, groups }) {
  if (!groups.overview.assignments?.exists) return <NotRunYet />;
  return <GroupTable key={`${scope}:${itemId}`} scope={scope} itemId={itemId} {...groups} />;
}
