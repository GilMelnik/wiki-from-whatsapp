import { Bidi, consequenceLines, formatDateTime, Ltr, useArtifactStatus, useJobs, usePlanOverview, useStepRun } from "@shell";

// What an applied suggestion changed (GET /suggestions/{id} → changes): a text correction as
// the sentences before and after on each page; a plan change as what it does downstream, with
// the steps that must follow offered in order (never started by themselves); a contradiction
// also as the group pairs saved as links. A wrong apply is repaired from the backup named here.

const LINE_TONES = {
  info: "border-sky-200 bg-sky-50 text-sky-900",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  danger: "border-rose-200 bg-rose-50 text-rose-900",
};

/** The steps a plan change needs before the pages show it, offered in order. */
export function FollowUp({ busy }) {
  const { stepsByArtifact, runningByArtifact } = useJobs();
  const { byKey } = useArtifactStatus();
  const run = useStepRun();
  const rows = ["assignments", "pages"].map((key) => ({
    key,
    step: stepsByArtifact[key],
    running: runningByArtifact[key],
    stale: (byKey[key]?.stale_because ?? []).length > 0,
  }));
  return (
    <ol className="space-y-2">
      {rows.map((row, index) => {
        const waitFor = rows.slice(0, index).find((earlier) => earlier.stale || earlier.running);
        const state = row.running ? "רץ כעת" : row.stale ? "לא מעודכן" : "מעודכן";
        return (
          <li key={row.key} className="flex flex-wrap items-center gap-2">
            <span>
              {index + 1}. {row.step?.title ?? row.key}
            </span>
            <span className={`badge ${row.running ? "badge-sky" : row.stale ? "badge-amber" : "badge-emerald"}`}>{state}</span>
            {row.stale && !row.running && row.step && (
              <button
                type="button"
                className="btn btn-sm"
                disabled={Boolean(waitFor) || busy}
                title={waitFor ? `קודם ${waitFor.step?.title ?? waitFor.key}` : busy ? "מחכה שהביצועים ברקע יסתיימו" : undefined}
                onClick={() => run(row.step.key)}
              >
                {row.step.paid ? "הרצה…" : "הרצה"}
              </button>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function PlanChange({ changes, busy }) {
  const plan = usePlanOverview();
  const lines = consequenceLines(changes.plan, plan.data);
  return (
    <>
      <ul className="space-y-1">
        {lines.map((line) => (
          <li key={line.text} className={`rounded border p-2 ${LINE_TONES[line.tone]}`}>
            {line.text}
          </li>
        ))}
      </ul>
      <section>
        <h4 className="mb-1 text-xs font-medium text-slate-600">כדי שהדפים יראו את השינוי</h4>
        <FollowUp busy={busy} />
      </section>
    </>
  );
}

function Sentences({ runs }) {
  return (
    <ul className="space-y-2">
      {runs.map((run, index) => (
        <li key={index} className="grid gap-2 md:grid-cols-2">
          <div className="rounded border border-rose-200 bg-rose-50 p-2">
            <p className="mb-1 text-xs text-rose-700">לפני</p>
            {run.before.length ? run.before.map((s, i) => <Bidi as="p" key={i}>{s}</Bidi>) : <p className="text-xs text-slate-400">(לא היה)</p>}
          </div>
          <div className="rounded border border-emerald-200 bg-emerald-50 p-2">
            <p className="mb-1 text-xs text-emerald-700">אחרי</p>
            {run.after.length ? run.after.map((s, i) => <Bidi as="p" key={i}>{s}</Bidi>) : <p className="text-xs text-slate-400">(נמחק)</p>}
          </div>
        </li>
      ))}
    </ul>
  );
}

function PageChange({ page }) {
  return (
    <li className="space-y-2">
      <p className="flex flex-wrap items-baseline gap-2">
        <Bidi className="font-medium">{page.title}</Bidi>
        <Ltr className="text-xs text-slate-400">{page.page_id}</Ltr>
        {page.kept_sentence_share !== null && page.kept_sentence_share !== undefined && (
          <span className="text-xs text-slate-500">{Math.round(page.kept_sentence_share * 100)}% מהמשפטים נשארו מילה במילה</span>
        )}
      </p>
      {!page.changed ? (
        <p className="text-slate-500">הדף כבר הציג את שני הצדדים ולא שונה.</p>
      ) : page.sentences ? (
        page.sentences.length ? <Sentences runs={page.sentences} /> : <p className="text-slate-500">לא נמצא הבדל במשפטים.</p>
      ) : (
        <p className="text-slate-500">גרסת הדף שלפני או שאחרי כבר לא זמינה.</p>
      )}
      {page.rewritten_since && <p className="text-xs text-slate-500">קובץ הדפים נכתב שוב מאז; „אחרי” מוצג כפי שהביצוע השאיר אותו.</p>}
    </li>
  );
}

function Links({ links }) {
  return (
    <ul className="space-y-1">
      {links.map(({ a, b }) => (
        <li key={`${a.group_id}-${b.group_id}`} className="rounded border border-slate-200 bg-white p-2 text-xs">
          <Bidi as="p">{a.claim_text ?? a.group_id}</Bidi>
          <p className="my-1 text-slate-400">⟷ סותרת את</p>
          <Bidi as="p">{b.claim_text ?? b.group_id}</Bidi>
        </li>
      ))}
    </ul>
  );
}

export function Changes({ suggestion, changes, busy }) {
  const structural = Boolean(changes.plan);
  return (
    <section className="space-y-3 rounded border border-emerald-200 bg-emerald-50/40 p-3">
      <h3 className="section-title mb-0">מה הביצוע שינה ({formatDateTime(changes.applied_at)})</h3>
      <Bidi as="p">{changes.message}</Bidi>
      {structural ? <PlanChange changes={changes} busy={busy} /> : <ul className="space-y-4">{changes.pages.map((page) => <PageChange key={page.page_id} page={page} />)}</ul>}
      {suggestion.kind === "contradiction" && changes.links?.length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-medium text-slate-600">קישורי הסתירה שנשמרו בקבוצות הטענות</h4>
          <Links links={changes.links} />
        </section>
      )}
      <p className="text-xs text-slate-600">
        אין כאן כפתור ביטול. ביצוע שגוי מתקנים מהגיבוי של הקובץ כפי שהיה לפני הביצוע:{" "}
        {changes.backup ? <Ltr>{changes.backup}</Ltr> : <>לא היה עותק ערוך לפני הביצוע, כך שהגרסה הקודמת היא קובץ המקור; הקובץ שנכתב: <Ltr>{changes.file}</Ltr></>}
        {changes.groups_backup && (
          <>
            {" "}· קבוצות הטענות: <Ltr>{changes.groups_backup}</Ltr>
          </>
        )}
      </p>
    </section>
  );
}
