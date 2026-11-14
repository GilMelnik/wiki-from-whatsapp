import { useState } from "react";
import { Bidi, countText, EditableText, formatCount, KindField, Ltr, nodeById, Panel, pathTitles, titleOf } from "@shell";
import { GROUPS_TAB } from "./assign.js";
import { ItemGroups, ItemTabs } from "./AssignmentViews.jsx";
import { ChangeBadge, MergedFrom, Warnings } from "./bits.jsx";

// Main column for a category (its texts and what it holds) and for a label (its texts and
// kind, or in the second tab the claim groups that carry it). Titles and descriptions save at
// once; moving groups pins them (see GroupTable).

export function CategoryView({ overview, nodeId, saving, on }) {
  const node = nodeById(overview, nodeId);
  const path = pathTitles(overview, nodeId);
  return (
    <Panel
      title={<Bidi>{node.title_he}</Bidi>}
      subtitle={
        <span className="flex flex-wrap items-center gap-2">
          <span className="badge badge-slate">Category · level {node.depth}</span>
          {path.length > 0 && <Bidi>{path.join(" › ")}</Bidi>}
          <Ltr>{nodeId}</Ltr>
          <ChangeBadge overview={overview} id={nodeId} />
        </span>
      }
    >
      <div className="space-y-6 p-4 text-sm">
        <Warnings overview={overview} id={nodeId} onSelect={on.select} />
        <EditableText label="Title" value={node.title_he} disabled={saving} onSave={(title) => on.rename(nodeId, title)} />
        <EditableText
          label="Description"
          value={node.description}
          multiline
          disabled={saving}
          placeholder="No description"
          onSave={(text) => on.edit(nodeId, { description: text })}
        />
        <section>
          <h3 className="section-title">
            Inside: {countText(node.children.length, "item", formatCount)}, {countText(node.page_count, "page", formatCount)}
          </h3>
          {node.children.length === 0 ? (
            <p className="text-slate-400">The category is empty.</p>
          ) : (
            <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-white">
              {node.children.map((childId) => {
                const child = nodeById(overview, childId);
                return (
                  <li key={childId}>
                    <button type="button" className="list-row" onClick={() => on.select(childId)}>
                      <span className="flex items-center gap-2">
                        <span className={`badge ${child.kind === "page" ? "badge-emerald" : "badge-slate"}`}>
                          {child.kind === "page" ? "Page" : "Category"}
                        </span>
                        <Bidi>{child.title_he}</Bidi>
                        <Ltr className="text-xs text-slate-400">{childId}</Ltr>
                      </span>
                      <Bidi as="p" className="mt-0.5 text-xs text-slate-500">{child.description}</Bidi>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </Panel>
  );
}

function KindEditor({ overview, label, saving, onSave }) {
  const [draft, setDraft] = useState(null);
  if (draft === null) {
    return (
      <section>
        <div className="mb-1 flex items-center justify-between">
          <h3 className="section-title mb-0">Kind</h3>
          <button type="button" className="btn btn-sm" disabled={saving} onClick={() => setDraft(label.kind)}>
            Change the kind
          </button>
        </div>
        <Ltr>{label.kind}</Ltr>
      </section>
    );
  }
  const save = async (event) => {
    event.preventDefault();
    try {
      await onSave(draft.trim());
      setDraft(null);
    } catch {
      // reported by the caller; keep the draft
    }
  };
  return (
    <form className="space-y-2" onSubmit={save}>
      <KindField overview={overview} value={draft} onChange={setDraft} />
      <div className="flex gap-2">
        <button type="submit" className="btn btn-sm btn-primary" disabled={saving || !draft.trim() || draft.trim() === label.kind}>
          Save
        </button>
        <button type="button" className="btn btn-sm" onClick={() => setDraft(null)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export function LabelView({ overview, labelId, saving, tab, groups, on }) {
  const label = overview.labels[labelId];
  const count = overview.assignments?.labels?.[labelId];
  return (
    <Panel
      title={<Bidi>{label.title_he}</Bidi>}
      subtitle={
        <span className="flex flex-wrap items-center gap-2">
          <span className="badge badge-sky">Label</span>
          <Ltr>{label.kind}</Ltr>
          <Ltr>{labelId}</Ltr>
          <ChangeBadge overview={overview} id={labelId} />
        </span>
      }
    >
      <div className="space-y-6 p-4 text-sm">
        <ItemTabs count={count} tab={tab} onChange={on.showTab} />
        {tab === GROUPS_TAB ? (
          <ItemGroups scope="label" itemId={labelId} groups={groups} />
        ) : (
          <LabelDetails overview={overview} label={label} labelId={labelId} saving={saving} on={on} />
        )}
      </div>
    </Panel>
  );
}

function LabelDetails({ overview, label, labelId, saving, on }) {
  const sameKind = overview.label_kinds.find(({ kind }) => kind === label.kind)?.label_ids ?? [];
  return (
    <>
      <Warnings overview={overview} id={labelId} onSelect={on.select} />
      <EditableText label="Title" value={label.title_he} disabled={saving} onSave={(title) => on.rename(labelId, title)} />
      <EditableText
        label="Description: when a claim gets the label"
        value={label.description}
        multiline
        disabled={saving}
        onSave={(text) => on.edit(labelId, { description: text })}
      />
      <KindEditor overview={overview} label={label} saving={saving} onSave={(kind) => on.edit(labelId, { kind })} />
      <MergedFrom ids={label.merged_from} />
      <section>
        <h3 className="section-title">Labels of the same kind</h3>
        <p className="flex flex-wrap gap-1">
          {sameKind.map((id) => (
            <button key={id} type="button" className={`badge ${id === labelId ? "badge-sky" : "badge-slate hover:bg-slate-200"}`} onClick={() => on.select(id)}>
              <Bidi>{titleOf(overview, id)}</Bidi>
            </button>
          ))}
        </p>
      </section>
    </>
  );
}
