import { useEffect, useState } from "react";
import { Bidi, formatCount, Ltr, Panel, PanelState, useResource } from "@shell";
import { api, pagePath } from "./api.js";
import { GROUPS_TAB } from "./assign.js";
import { ItemGroups, ItemTabs } from "./AssignmentViews.jsx";
import { ChangeBadge, MergedFrom, Warnings } from "./bits.jsx";
import { EditableText, TextArea } from "./fields.jsx";
import { aliasesToText, pathTitles, textToAliases } from "./plan.js";

// Main column for a page, in two tabs. Details: the scope is the star, a large editor, with
// the evidence to judge it right next to it, the page's example claim groups (their text,
// from the groups file) and the coverage numbers of the planning run; saving a scope goes
// through a dialog that says the page's claims will be classified again. Claim groups: what
// the assignment step put on the page, least certain first, to check and move.

function ScopeEditor({ page, saving, scopeRef, onSave }) {
  const [draft, setDraft] = useState(page.scope);
  const dirty = draft.trim() !== page.scope;
  useEffect(() => {
    if (!dirty) setDraft(page.scope);
  }, [page.scope]);
  return (
    <section className="rounded-lg border-2 border-blue-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-blue-900">תחום הדף</h3>
        <span className="text-xs text-slate-500">{formatCount(draft.length)} תווים</span>
      </div>
      <p className="mb-2 text-xs text-slate-500">
        מה שייך לדף ומה לא. המסווג קורא רק את הכותרת ואת התחום כדי לשבץ כל טענה, אז כל משפט כאן קובע
        לאן יגיעו טענות.
      </p>
      <TextArea ref={scopeRef} value={draft} onChange={setDraft} rows={9} className="field w-full text-base leading-relaxed" />
      <div className="mt-2 flex items-center gap-2">
        <button type="button" className="btn btn-primary btn-sm" disabled={saving || !dirty || !draft.trim()} onClick={() => onSave(draft.trim())}>
          שמירת התחום…
        </button>
        <button type="button" className="btn btn-sm" disabled={!dirty} onClick={() => setDraft(page.scope)}>
          ביטול השינויים
        </button>
        {dirty && <span className="text-xs text-amber-700">יש שינוי שלא נשמר</span>}
      </div>
    </section>
  );
}

function AliasesEditor({ page, saving, onSave }) {
  const [draft, setDraft] = useState(null);
  const save = async (event) => {
    event.preventDefault();
    try {
      await onSave({ he: textToAliases(draft.he), en: textToAliases(draft.en) });
      setDraft(null);
    } catch {
      // reported by the caller; keep the draft
    }
  };
  if (draft === null) {
    return (
      <section>
        <div className="mb-1 flex items-center justify-between">
          <h3 className="section-title mb-0">כינויים</h3>
          <button type="button" className="btn btn-sm" disabled={saving} onClick={() => setDraft({ he: aliasesToText(page.aliases.he), en: aliasesToText(page.aliases.en) })}>
            עריכה
          </button>
        </div>
        <p className="flex flex-wrap gap-1">
          {page.aliases.he.map((alias) => (
            <Bidi key={`he:${alias}`} className="badge badge-slate">{alias}</Bidi>
          ))}
          {page.aliases.en.map((alias) => (
            <Ltr key={`en:${alias}`} className="badge badge-slate font-sans">{alias}</Ltr>
          ))}
          {page.aliases.he.length + page.aliases.en.length === 0 && <span className="text-slate-400">אין כינויים</span>}
        </p>
      </section>
    );
  }
  return (
    <form className="space-y-2" onSubmit={save}>
      <h3 className="section-title">כינויים (אחד בשורה)</h3>
      <div className="grid gap-2 md:grid-cols-2">
        <TextArea value={draft.he} onChange={(he) => setDraft({ ...draft, he })} rows={4} placeholder="עברית" />
        <TextArea value={draft.en} onChange={(en) => setDraft({ ...draft, en })} rows={4} placeholder="English" dir="ltr" />
      </div>
      <div className="flex gap-2">
        <button type="submit" className="btn btn-sm btn-primary" disabled={saving}>שמירה</button>
        <button type="button" className="btn btn-sm" onClick={() => setDraft(null)}>ביטול</button>
      </div>
    </form>
  );
}

function CoverageCard({ coverage, report }) {
  if (!report) return <p className="text-slate-400">אין דוח כיסוי (הוא נוצר כשהתוכנית מתוכננת).</p>;
  if (!coverage?.measured) return <p className="text-slate-500">הדף נוסף אחרי בדיקת הכיסוי, אין לו מספרים.</p>;
  return (
    <div className="space-y-1">
      <p>
        <span className="text-2xl font-semibold">{formatCount(coverage.count + coverage.merged_count)}</span>
        <span className="text-slate-600"> מתוך {formatCount(report.sample_size)} קבוצות בדגימה שויכו לדף</span>
      </p>
      <p className="text-slate-600">
        בערך {formatCount(coverage.projected + coverage.merged_projected)} מתוך {formatCount(report.total_groups)} קבוצות בסך הכול
        {coverage.merged_count > 0 && ` (כולל ${formatCount(coverage.merged_count)} של דפים שמוזגו לתוכו)`}.
      </p>
      {coverage.small && <p className="badge badge-amber">מעט מאוד טענות</p>}
      {coverage.large && <p className="badge badge-amber">הרבה מאוד טענות</p>}
      {coverage.scope_changed && <p className="text-xs text-amber-800">התחום השתנה מאז הבדיקה; המספרים מתארים את התחום הקודם.</p>}
      <p className="text-xs text-slate-400">לפי הסיווג של המודל הזול בתכנון (ביטחון, לא דיוק שנמדד).</p>
    </div>
  );
}

function Examples({ examples, groupsAvailable }) {
  if (!groupsAvailable) return <p className="text-slate-400">קובץ קבוצות הטענות לא נמצא, אין טקסט להציג.</p>;
  if (examples.length === 0) return <p className="text-slate-400">אין לדף קבוצות לדוגמה.</p>;
  return (
    <ul className="space-y-2">
      {examples.map((group) =>
        group.missing ? (
          <li key={group.group_id} className="text-xs text-slate-400">
            הקבוצה <Ltr>{group.group_id}</Ltr> כבר לא בקובץ הקבוצות.
          </li>
        ) : (
          <li key={group.group_id} className="rounded border border-slate-200 bg-white p-2">
            <Bidi as="p" className="leading-relaxed">{group.claim_text}</Bidi>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span>{formatCount(group.supporter_count)} תומכים · {formatCount(group.opposer_count)} מתנגדים</span>
              {group.claim_count > 1 && <span>{formatCount(group.claim_count)} טענות דומות</span>}
              {(group.entity_names ?? []).map((name) => (
                <Bidi key={name} className="badge badge-slate">{name}</Bidi>
              ))}
              <Ltr className="text-slate-400">{group.group_id}</Ltr>
            </p>
          </li>
        )
      )}
    </ul>
  );
}

export function PageView({ overview, pageId, saving, reads, scopeRef, tab, groups, on }) {
  const page = overview.pages[pageId];
  const detail = useResource(() => api.get(pagePath(pageId)), [pageId], { watch: reads });
  const sizes = overview.assignments?.pages?.[pageId];
  return (
    <Panel
      title={<Bidi>{page.title_he}</Bidi>}
      subtitle={
        <span className="flex flex-wrap items-center gap-2">
          <span className="badge badge-emerald">דף</span>
          <Bidi>{pathTitles(overview, pageId).join(" › ")}</Bidi>
          <Ltr>{pageId}</Ltr>
          <ChangeBadge overview={overview} id={pageId} />
        </span>
      }
    >
      <div className="space-y-6 p-4 text-sm">
        <ItemTabs count={sizes?.all} tab={tab} onChange={on.showTab} />
        {tab === GROUPS_TAB ? (
          <ItemGroups scope="page" itemId={pageId} groups={groups} />
        ) : (
          <PageDetails overview={overview} page={page} pageId={pageId} detail={detail} saving={saving} scopeRef={scopeRef} on={on} />
        )}
      </div>
    </Panel>
  );
}

function PageDetails({ overview, page, pageId, detail, saving, scopeRef, on }) {
  return (
    <>
      <Warnings overview={overview} id={pageId} onSelect={on.select} />
      <div className="grid gap-6 xl:grid-cols-5">
        <div className="space-y-6 xl:col-span-3">
          <ScopeEditor key={pageId} page={page} saving={saving} scopeRef={scopeRef} onSave={(scope) => on.editScope(pageId, scope)} />
          <EditableText label="כותרת" value={page.title_he} disabled={saving} onSave={(title) => on.rename(pageId, title)} />
          <EditableText label="תיאור קצר (מוצג בניווט)" value={page.description} disabled={saving} onSave={(text) => on.edit(pageId, { description: text })} />
          <AliasesEditor key={pageId} page={page} saving={saving} onSave={(aliases) => on.edit(pageId, { aliases })} />
          <MergedFrom ids={page.merged_from} />
        </div>
        <div className="space-y-6 xl:col-span-2">
          <section>
            <h3 className="section-title">בדיקת הכיסוי</h3>
            <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(detail.data)} onRetry={detail.reload}>
              <CoverageCard coverage={detail.data?.coverage} report={overview.coverage} />
            </PanelState>
          </section>
          <section>
            <h3 className="section-title">קבוצות טענות לדוגמה</h3>
            <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(detail.data)} onRetry={detail.reload}>
              <Examples examples={detail.data?.examples ?? []} groupsAvailable={overview.groups_available} />
            </PanelState>
          </section>
        </div>
      </div>
    </>
  );
}
