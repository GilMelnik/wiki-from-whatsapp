import { countText, JobLog, Panel, PanelState, ToolLink, useTools } from "@shell";
import { Files } from "./Files.jsx";
import { NextAction } from "./NextAction.jsx";

// The main column: the selected artifact's state and what to do about it, the tools to
// review it in, what it is made from and what uses it (inputs that changed are marked), its
// step's last run, and folded away its files and metadata. For the written pages: how many
// claim groups the withholding rule keeps off every page.

function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function Chips({ keys, byKey, changed = [], onSelect, emptyText }) {
  if (keys.length === 0) return <p className="text-slate-500">{emptyText}</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {keys.map((key) => (
        <button key={key} type="button" className={`btn btn-sm ${changed.includes(key) ? "border-amber-400" : ""}`} onClick={() => onSelect(key)}>
          {byKey[key]?.title ?? key}
          {changed.includes(key) && <span className="badge badge-amber">changed</span>}
        </button>
      ))}
    </div>
  );
}

function ReviewTools({ artifactKey }) {
  const { tools } = useTools();
  const related = tools.filter((tool) => tool.reads.includes(artifactKey) || tool.writes.includes(artifactKey));
  if (related.length === 0) return <p className="text-slate-500">No review tool works on this file.</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {related.map((tool) => (
        <ToolLink key={tool.name} toolId={tool.name} className="btn btn-sm" title="Ctrl+click opens it in a new tab">
          {tool.title}
          <span className="text-slate-500">{tool.writes.includes(artifactKey) ? "edits it" : "shows it"}</span>
        </ToolLink>
      ))}
    </div>
  );
}

const PAGES_KEY = "pages";

function Withheld({ artifactKey, metadata }) {
  const count = artifactKey === PAGES_KEY ? metadata?.withheld?.count : 0;
  if (!count) return null;
  return (
    <p className="note note-plain">
      {countText(count, "claim group")} withheld from every page: each is a negative report about a named entity from fewer
      than two people. A second person reporting it releases it.
    </p>
  );
}

export function ArtifactDetail({ artifact, state, step, running, latest, detail, byKey, artifacts, now, onSelect }) {
  if (!artifact) return <PanelState empty emptyText="Pick a file in the list" />;
  const usedBy = artifacts.filter((other) => other.upstream.includes(artifact.key)).map((other) => other.key);
  const showLastRun = latest && !running && state.kind !== "failed";
  return (
    <Panel title={artifact.title} subtitle={step ? `Made by ${step.title}` : "Not made by a step"}>
      <div className="max-w-4xl space-y-6 p-4 text-sm">
        <NextAction artifact={artifact} state={state} step={step} running={running} latest={latest} byKey={byKey} />
        <Withheld artifactKey={artifact.key} metadata={detail.data?.metadata} />
        <Section title="Review it in">
          <ReviewTools artifactKey={artifact.key} />
        </Section>
        <div className="grid gap-6 md:grid-cols-2">
          <Section title="Made from">
            <Chips keys={artifact.upstream} byKey={byKey} changed={artifact.stale_because} onSelect={onSelect} emptyText="Nothing: it comes from outside." />
          </Section>
          <Section title="Used by">
            <Chips keys={usedBy} byKey={byKey} onSelect={onSelect} emptyText="Nothing yet." />
          </Section>
        </div>
        {showLastRun && (
          <Section title="Last run">
            <JobLog jobId={latest.id} className="max-h-72" />
          </Section>
        )}
        <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(detail.data)} onRetry={detail.reload}>
          {detail.data && <Files artifact={artifact} data={detail.data} now={now} />}
        </PanelState>
      </div>
    </Panel>
  );
}
