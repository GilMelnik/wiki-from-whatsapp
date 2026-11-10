import { useState } from "react";
import { Bidi, formatCount, formatDateTime, Ltr, Panel, PanelState, useResource } from "@shell";
import { api } from "./api.js";
import { Warnings } from "./bits.jsx";
import { percent } from "./plan.js";

// Main column when the plan itself is selected: why the model built it this way, the page
// budget and the depth of each branch, every warning, the coverage check (with the sampled
// claims that fit no page: candidates for a new page) and what changed since the proposal.

function Budget({ overview }) {
  const { budget, branches } = overview;
  const full = budget.pages >= budget.max_pages;
  return (
    <section>
      <h3 className="section-title">היקף</h3>
      <p className="mb-2">
        <span className={`text-2xl font-semibold ${full ? "text-amber-700" : ""}`}>
          {formatCount(budget.pages)} מתוך {formatCount(budget.max_pages)}
        </span>{" "}
        דפים · {formatCount(budget.labels)} תוויות · דפים בעומק {budget.min_depth}–{budget.max_depth}
      </p>
      <table className="w-full text-start">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1 text-start font-medium">ענף</th>
            <th className="py-1 text-start font-medium">דפים</th>
            <th className="py-1 text-start font-medium">עומק</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {branches.map((branch) => (
            <tr key={branch.id}>
              <td className="py-1"><Bidi>{branch.title_he}</Bidi></td>
              <td className="py-1">{formatCount(branch.pages)}</td>
              <td className="py-1">{branch.depth || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function GroupList({ groups }) {
  return (
    <ul className="space-y-1">
      {groups.filter((group) => !group.missing).map((group) => (
        <li key={group.group_id} className="rounded border border-slate-200 bg-white p-2">
          <Bidi as="p">{group.claim_text}</Bidi>
          <Ltr className="text-xs text-slate-400">{group.group_id}</Ltr>
        </li>
      ))}
    </ul>
  );
}

function Unplaced({ reads }) {
  const unplaced = useResource(() => api.get("/coverage/unplaced"), [], { watch: reads });
  return (
    <PanelState loading={unplaced.loading} error={unplaced.error} hasData={Boolean(unplaced.data)} onRetry={unplaced.reload}>
      {unplaced.data && (
        <div className="space-y-3">
          <h4 className="text-xs font-medium text-slate-600">לא מתאימות לאף דף</h4>
          <GroupList groups={unplaced.data.none} />
          <h4 className="text-xs font-medium text-slate-600">לא ברור לאיזה דף</h4>
          <GroupList groups={unplaced.data.unsure} />
        </div>
      )}
    </PanelState>
  );
}

function Coverage({ coverage, reads }) {
  const [open, setOpen] = useState(false);
  if (!coverage) return <p className="text-slate-400">אין דוח כיסוי (הוא נוצר כשהתוכנית מתוכננת).</p>;
  return (
    <div className="space-y-2">
      <p>
        המסווג הזול שיבץ {formatCount(coverage.sample_size)} קבוצות מתוך {formatCount(coverage.total_groups)} לפי
        הכותרות והתחומים בלבד: <b>{percent(coverage.confident_share)}</b> בביטחון,{" "}
        {percent(coverage.unsure_share)} בלי ביטחון ({formatCount(coverage.unsure_count)}),{" "}
        {percent(coverage.none_share)} לאף דף ({formatCount(coverage.none_count)}).
      </p>
      <p className="text-xs text-slate-500">
        נבדק {formatDateTime(coverage.generated_at)} על התוכנית כפי שהמודל הציע; שינויים מאז לא נמדדו.
      </p>
      {coverage.findings?.length > 0 && (
        <ul className="list-disc space-y-1 ps-5 text-xs text-slate-600" dir="ltr">
          {coverage.findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
      )}
      <button type="button" className="btn btn-sm" onClick={() => setOpen(!open)}>
        {open ? "הסתרת" : "הצגת"} הטענות שלא שובצו בביטחון
      </button>
      {open && <Unplaced reads={reads} />}
    </div>
  );
}

function Changes({ changes, onSelect }) {
  const count = Object.keys(changes.items).length;
  if (count === 0 && changes.removed.length === 0) return <p className="text-slate-500">התוכנית עדיין כפי שהמודל הציע.</p>;
  return (
    <div className="space-y-2">
      <p>{formatCount(count)} פריטים חדשים או שנערכו (מסומנים ברשימה).</p>
      {changes.removed.length > 0 && (
        <ul className="space-y-1">
          {changes.removed.map((item) => (
            <li key={item.id}>
              <Bidi>{item.title_he}</Bidi> <Ltr className="text-xs text-slate-400">{item.id}</Ltr>{" "}
              {item.redirect ? (
                <>
                  ← מופנה אל{" "}
                  <button type="button" className="badge badge-sky" onClick={() => onSelect(item.redirect)}>
                    <Ltr>{item.redirect}</Ltr>
                  </button>
                </>
              ) : (
                <span className="badge badge-rose">נמחק</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Overview({ overview, reads, on }) {
  const { metadata } = overview;
  return (
    <Panel
      title="סקירת התוכנית"
      subtitle={
        <>
          {metadata.model && <Ltr>{metadata.model}</Ltr>} · תוכננה {formatDateTime(metadata.generated_at)}
          {metadata.edited_at && <> · נערכה {formatDateTime(metadata.edited_at)}</>} · <Ltr>{overview.file}</Ltr>
        </>
      }
    >
      <div className="space-y-6 p-4 text-sm">
        {metadata.top_level_rationale && (
          <section>
            <h3 className="section-title">למה המבנה הזה (מהמודל)</h3>
            <Bidi as="p" className="leading-relaxed">{metadata.top_level_rationale}</Bidi>
          </section>
        )}
        <Budget overview={overview} />
        <section>
          <h3 className="section-title">אזהרות ({formatCount(overview.warnings.length)})</h3>
          {overview.warnings.length === 0 ? <p className="text-slate-500">אין אזהרות.</p> : <Warnings overview={overview} warnings={overview.warnings} onSelect={on.select} />}
        </section>
        <section>
          <h3 className="section-title">בדיקת הכיסוי</h3>
          <Coverage coverage={overview.coverage} reads={reads} />
        </section>
        <section>
          <h3 className="section-title">שינויים מאז ההצעה</h3>
          <Changes changes={overview.changes} onSelect={on.select} />
        </section>
      </div>
    </Panel>
  );
}
