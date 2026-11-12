import { useEffect, useState } from "react";
import { Bidi, countText, EditableText, formatCount, Ltr, Panel, PanelState, pathTitles, TextArea, textToAliases, useResource } from "@shell";
import { api, pagePath } from "./api.js";
import { GROUPS_TAB } from "./assign.js";
import { ItemGroups, ItemTabs } from "./AssignmentViews.jsx";
import { ChangeBadge, MergedFrom, Warnings } from "./bits.jsx";
import { aliasesToText } from "./plan.js";
import { ScopeEvidence } from "./ScopeEvidence.jsx";

// Main column for a page, in two tabs. Details: the scope is the star, a large editor, with
// the evidence to judge it right next to it (ScopeEvidence: its size, a sample of its groups,
// the closest groups it did not get, the model's examples); its warnings (a size outside the
// guide among them) above; saving a scope goes through a dialog that says the page's claims
// will be classified again. Claim groups: what the assignment step put on the page, least
// certain first, to check and move.

function ScopeEditor({ page, saving, scopeRef, onSave }) {
  const [draft, setDraft] = useState(page.scope);
  const dirty = draft.trim() !== page.scope;
  useEffect(() => {
    if (!dirty) setDraft(page.scope);
  }, [page.scope]);
  return (
    <section className="rounded-lg border-2 border-blue-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-blue-900">Page scope</h3>
        <span className="text-xs text-slate-500">{countText(draft.length, "character", formatCount)}</span>
      </div>
      <p className="mb-2 text-xs text-slate-500">
        What belongs on the page and what does not. The classifier reads only the title and the scope to
        place every claim, so every sentence here decides where claims go.
      </p>
      <TextArea ref={scopeRef} value={draft} onChange={setDraft} rows={9} className="field w-full text-base leading-relaxed" />
      <div className="mt-2 flex items-center gap-2">
        <button type="button" className="btn btn-primary btn-sm" disabled={saving || !dirty || !draft.trim()} onClick={() => onSave(draft.trim())}>
          Save the scope…
        </button>
        <button type="button" className="btn btn-sm" disabled={!dirty} onClick={() => setDraft(page.scope)}>
          Discard the changes
        </button>
        {dirty && <span className="text-xs text-amber-700">Unsaved change</span>}
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
          <h3 className="section-title mb-0">Aliases</h3>
          <button type="button" className="btn btn-sm" disabled={saving} onClick={() => setDraft({ he: aliasesToText(page.aliases.he), en: aliasesToText(page.aliases.en) })}>
            Edit
          </button>
        </div>
        <p className="flex flex-wrap gap-1">
          {page.aliases.he.map((alias) => (
            <Bidi key={`he:${alias}`} className="badge badge-slate">{alias}</Bidi>
          ))}
          {page.aliases.en.map((alias) => (
            <Ltr key={`en:${alias}`} className="badge badge-slate font-sans">{alias}</Ltr>
          ))}
          {page.aliases.he.length + page.aliases.en.length === 0 && <span className="text-slate-400">No aliases</span>}
        </p>
      </section>
    );
  }
  return (
    <form className="space-y-2" onSubmit={save}>
      <h3 className="section-title">Aliases (one per line)</h3>
      <div className="grid gap-2 md:grid-cols-2">
        <TextArea value={draft.he} onChange={(he) => setDraft({ ...draft, he })} rows={4} placeholder="Hebrew" />
        <TextArea value={draft.en} onChange={(en) => setDraft({ ...draft, en })} rows={4} placeholder="English" dir="ltr" lang="en" />
      </div>
      <div className="flex gap-2">
        <button type="submit" className="btn btn-sm btn-primary" disabled={saving}>Save</button>
        <button type="button" className="btn btn-sm" onClick={() => setDraft(null)}>Cancel</button>
      </div>
    </form>
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
          <span className="badge badge-emerald">Page</span>
          <Bidi>{pathTitles(overview, pageId).join(" › ")}</Bidi>
          <Ltr className="text-slate-400">{pageId}</Ltr>
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
          <EditableText label="Title" value={page.title_he} disabled={saving} onSave={(title) => on.rename(pageId, title)} />
          <EditableText label="Short description (shown in the navigation)" value={page.description} disabled={saving} onSave={(text) => on.edit(pageId, { description: text })} />
          <AliasesEditor key={pageId} page={page} saving={saving} onSave={(aliases) => on.edit(pageId, { aliases })} />
          <MergedFrom ids={page.merged_from} />
        </div>
        <div className="xl:col-span-2">
          <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(detail.data)} onRetry={detail.reload}>
            {detail.data && <ScopeEvidence overview={overview} detail={detail} onGroups={() => on.showTab(GROUPS_TAB)} />}
          </PanelState>
        </div>
      </div>
    </>
  );
}
