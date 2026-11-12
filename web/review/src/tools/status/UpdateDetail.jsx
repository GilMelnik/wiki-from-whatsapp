import { formatDateTime, Ltr, Panel, PanelState, ToolLink, useToast, useTools } from "@shell";
import { StateLabel } from "./ArtifactRow.jsx";
import { estimateText, exportText, UPDATE_STEP_STATES, updateState } from "./status.js";

// An update in progress (step_10_update), for the decision it waits on: review a step's
// output in its tool, allow a paid step after reading its estimate, or retry a failed step.
// The driver's own stop line comes first, with the tool to review in or the estimate, then the
// exact command to go on (nothing here runs it), what the export added, and the steps as a
// timeline. Read-only.

export function UpdateRow({ update }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="truncate font-medium">Update in progress</span>
      <span className="text-xs">
        <StateLabel state={updateState(update)} />
      </span>
    </span>
  );
}

function Section({ title, children }) {
  return (
    <section className="space-y-2">
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function ReviewLink({ toolId }) {
  const { tools } = useTools();
  const tool = tools.find((candidate) => candidate.name === toolId);
  return (
    <ToolLink toolId={toolId} className="btn btn-sm btn-primary" title="Ctrl+click opens it in a new tab">
      Review it in {tool?.title ?? toolId}
    </ToolLink>
  );
}

function Where({ update }) {
  if (update.running) {
    return (
      <p className="note note-info">
        {update.running.title ?? update.running.step} is running (since {formatDateTime(update.running.since)}). The tools that
        edit its file are read-only until it finishes.
      </p>
    );
  }
  if (update.failed) {
    return (
      <div className="note note-danger space-y-1">
        <p>
          {update.failed.title} failed ({formatDateTime(update.failed.at)}). The update is kept; running the command again
          retries this step.
        </p>
        <p dir="ltr" className="break-all font-mono text-xs">
          {update.failed.error}
        </p>
      </div>
    );
  }
  if (!update.stopped) return <p className="note note-plain">The update is paused between two steps.</p>;
  return (
    <div className="note note-warning space-y-2">
      <p dir="auto">{update.stopped.message}</p>
      {update.stopped.review_tool && <ReviewLink toolId={update.stopped.review_tool} />}
    </div>
  );
}

function Command({ command }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      toast.success("Command copied");
    } catch {
      toast.error("The browser refused to copy; select the command instead.");
    }
  };
  return (
    <Section title="To go on, in a terminal in the project folder">
      <div className="flex items-center gap-2">
        <code dir="ltr" className="card flex-1 select-all break-all font-mono text-xs">
          {command}
        </code>
        <button type="button" className="btn btn-sm" onClick={copy}>
          Copy
        </button>
      </div>
    </Section>
  );
}

function Timeline({ steps }) {
  return (
    <ol className="space-y-1.5">
      {steps.map((step) => {
        const state = UPDATE_STEP_STATES[step.status] ?? UPDATE_STEP_STATES.waiting;
        const quiet = step.status === "waiting";
        return (
          <li key={step.key} className={`flex flex-wrap items-center gap-x-3 gap-y-0.5 ${quiet ? "text-slate-500" : ""}`}>
            <span className="w-64 shrink-0 font-medium">{step.title}</span>
            <StateLabel state={state} />
            {step.status === "done" && step.changed === false && <span className="text-xs text-slate-500">nothing changed</span>}
            {step.at && <span className="text-xs text-slate-500">{formatDateTime(step.at)}</span>}
            {step.paid && <span className="badge badge-amber">Paid</span>}
          </li>
        );
      })}
    </ol>
  );
}

export function UpdateDetail({ update }) {
  if (!update) return <PanelState loading />;
  if (update.state === "unreadable") {
    return (
      <Panel title="Update">
        <div className="p-4 text-sm">
          <p className="note note-danger" dir="auto">
            {update.problem}
          </p>
        </div>
      </Panel>
    );
  }
  return (
    <Panel
      title="Update in progress"
      subtitle={
        <>
          Started {formatDateTime(update.started_at)} with <Ltr>{update.export}</Ltr>
        </>
      }
    >
      <div className="max-w-4xl space-y-6 p-4 text-sm">
        <Where update={update} />
        {update.estimate && (
          <Section title="What the paid step would cost">
            <p className="card">{estimateText(update.estimate)}</p>
          </Section>
        )}
        {update.command && <Command command={update.command} />}
        <Section title="What the new export added">
          <p>{exportText(update.counts)}</p>
        </Section>
        <Section title="The steps">
          <Timeline steps={update.steps} />
        </Section>
        {update.input_backup && (
          <p className="text-xs text-slate-500">
            The previous chat export is kept as <Ltr className="break-all">{update.input_backup}</Ltr>.
          </p>
        )}
      </div>
    </Panel>
  );
}
