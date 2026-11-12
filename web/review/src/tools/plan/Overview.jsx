import { Bidi, formatCount, formatDateTime, groupCount, Ltr, Panel, PanelState, useResource } from "@shell";
import { api } from "./api.js";
import { UNPLACED_ID } from "./assign.js";
import { NotRunYet } from "./AssignmentViews.jsx";
import { percent } from "./plan.js";
import { overviewTodos } from "./todos.js";

// Main column when the plan itself is selected: what there is to do (every warning, the
// proposals waiting for a decision, the groups on no page or waiting, the labels worth
// checking; each opens what it is about), what changed since the model's proposal, and,
// folded, the model's reasons for the structure and the coverage check of the planning run
// (it describes the model's proposal on a sample, so it stays apart from the real lists).

const TODO_TONES = { warning: "note-warning", info: "note-plain" };

function Todos({ overview, onSelect }) {
  const todos = overviewTodos(overview, formatCount);
  if (todos.length === 0) return <p className="text-slate-500">Nothing to do: no warning, no proposal and no group on no page.</p>;
  return (
    <ul className="space-y-2">
      {todos.map((todo) => (
        <li key={todo.key}>
          <button
            type="button"
            disabled={!todo.target}
            className={`note ${TODO_TONES[todo.tone]} block w-full text-start enabled:hover:underline`}
            onClick={() => onSelect(todo.target)}
          >
            {todo.text}
          </button>
        </li>
      ))}
    </ul>
  );
}

function GroupList({ groups }) {
  return (
    <ul className="space-y-1">
      {groups
        .filter((group) => !group.missing)
        .map((group) => (
          <li key={group.group_id} className="card p-2" title={group.group_id}>
            <Bidi as="p">{group.claim_text}</Bidi>
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
          <h4 className="text-xs font-medium text-slate-600">Fit no page</h4>
          <GroupList groups={unplaced.data.none} />
          <h4 className="text-xs font-medium text-slate-600">Unclear which page</h4>
          <GroupList groups={unplaced.data.unsure} />
        </div>
      )}
    </PanelState>
  );
}

function Coverage({ coverage, unplaced, reads, onSelect }) {
  if (!coverage) return <p className="text-slate-500">No coverage report (it is made at planning time).</p>;
  return (
    <div className="space-y-2">
      <p>
        The cheap classifier placed a sample of {groupCount(coverage.sample_size, formatCount)} by the titles and scopes
        alone: {percent(coverage.confident_share)} with confidence, {percent(coverage.unsure_share)} without, {percent(coverage.none_share)} on
        no page. It was checked {formatDateTime(coverage.generated_at)} on the plan as the model proposed it; changes since are not measured.
      </p>
      {unplaced !== null && (
        <p className="text-xs text-slate-600">
          After the assignment step the real list is{" "}
          <button type="button" className="underline" onClick={() => onSelect(UNPLACED_ID)}>
            the groups on no page
          </button>
          .
        </p>
      )}
      {coverage.findings?.length > 0 && (
        <ul className="list-disc space-y-1 ps-5 text-xs text-slate-600">
          {coverage.findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
      )}
      <details>
        <summary className="cursor-pointer text-xs text-slate-600">The sampled claims not placed with confidence</summary>
        <div className="mt-2">
          <Unplaced reads={reads} />
        </div>
      </details>
    </div>
  );
}

function Changes({ changes, onSelect }) {
  const count = Object.keys(changes.items).length;
  if (count === 0 && changes.removed.length === 0) return <p className="text-slate-500">The plan is still as the model proposed it.</p>;
  return (
    <div className="space-y-2">
      {count > 0 && <p>{formatCount(count)} new or edited (marked “New” or “Edited” in the list).</p>}
      {changes.removed.length > 0 && (
        <ul className="space-y-1">
          {changes.removed.map((item) => (
            <li key={item.id} title={item.id}>
              <Bidi>{item.title_he}</Bidi>{" "}
              {item.redirect ? (
                <>
                  → now part of{" "}
                  <button type="button" className="underline" onClick={() => onSelect(item.redirect)}>
                    <Ltr>{item.redirect}</Ltr>
                  </button>
                </>
              ) : (
                <span className="badge badge-rose">Deleted</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function Folded({ title, children }) {
  return (
    <details className="card">
      <summary className="cursor-pointer text-xs font-semibold text-slate-500">{title}</summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

export function Overview({ overview, reads, on }) {
  const { metadata } = overview;
  const assigned = overview.assignments?.exists;
  return (
    <Panel
      title="Plan overview"
      subtitle={
        <>
          Planned {formatDateTime(metadata.generated_at)}
          {metadata.edited_at && <> · last edited by you {formatDateTime(metadata.edited_at)}</>}
        </>
      }
    >
      <div className="max-w-4xl space-y-6 p-4 text-sm">
        <Section title="To do">
          <Todos overview={overview} onSelect={on.select} />
        </Section>
        {!assigned && <NotRunYet />}
        <Section title="Changes since the model's proposal">
          <Changes changes={overview.changes} onSelect={on.select} />
        </Section>
        {metadata.top_level_rationale && (
          <Folded title="Why this structure (the model's reasons)">
            <Bidi as="p" className="leading-relaxed">
              {metadata.top_level_rationale}
            </Bidi>
          </Folded>
        )}
        <Folded title="Coverage check of the planning run (a sample)">
          <Coverage coverage={overview.coverage} unplaced={overview.assignments?.counts?.unplaced ?? null} reads={reads} onSelect={on.select} />
        </Folded>
      </div>
    </Panel>
  );
}
