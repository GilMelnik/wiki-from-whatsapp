import { Bidi, formatDateTime, Panel, useArtifactStatus, useJobs, useStepRun } from "@shell";
import { FollowUp } from "./Changes.jsx";
import { formatUsd, STRUCTURAL, SUGGESTIONS_KEY } from "./suggestions.js";

// "About this review": the reviewer's remark on the whole wiki, what is being applied in the
// background and what came of it, the steps a plan change needs, and, calmly, whether the
// review should run again. Every apply changes the wiki, so the frame would call the
// suggestions out of date after each one; a new review is a paid whole-wiki call, so it is
// mentioned once here, with its cost, and run only on request (the frame's banner for this
// file is quiet: index.jsx `quietStale`). The counts by status are the list's sections.

const OUTCOME_BADGES = { applied: "badge-emerald", refused: "badge-amber", failed: "badge-rose" };
const OUTCOME_LABELS = { applied: "Applied", refused: "Refused", failed: "Failed" };

function useReviewStep() {
  const { stepsByArtifact, runningByArtifact } = useJobs();
  return { step: stepsByArtifact[SUGGESTIONS_KEY], running: runningByArtifact[SUGGESTIONS_KEY] };
}

function NotRunYet({ cost }) {
  const { step, running } = useReviewStep();
  const run = useStepRun();
  return (
    <div className="max-w-2xl space-y-3 p-4 text-sm">
      <p>Step 8 (the review of the whole wiki) has not run yet, so there are no suggestions to look at.</p>
      <p className="text-slate-600">
        The review reads every page in one model call, about {formatUsd(cost)} for the wiki at its current size, and
        proposes up to a dozen fixes.
      </p>
      {step && (
        <button type="button" className="btn btn-primary" disabled={Boolean(running)} onClick={() => run(step.key)}>
          {running ? "The review is running…" : "Run the review…"}
        </button>
      )}
    </div>
  );
}

function Rerun({ data, applying }) {
  const { byKey } = useArtifactStatus();
  const { step, running } = useReviewStep();
  const run = useStepRun();
  const stale = (byKey[SUGGESTIONS_KEY]?.stale_because ?? []).length > 0;
  if (!stale || !step) return null;
  return (
    <p className="note note-plain">
      The wiki changed since this review. When you are done applying, you can run it again for suggestions on the
      updated wiki (one model call, about {formatUsd(data.rerun_cost_usd)}); the decisions you made are kept.{" "}
      <button type="button" className="btn btn-sm ms-1" disabled={applying || Boolean(running)} onClick={() => run(step.key)}>
        Run the review again…
      </button>
    </p>
  );
}

function TitleLink({ id, title, onSelect }) {
  return (
    <button type="button" className="text-start underline decoration-slate-300 underline-offset-2 hover:decoration-current" onClick={() => onSelect(id)}>
      <Bidi>{title}</Bidi>
    </button>
  );
}

function Queue({ applies, onSelect }) {
  const { running, queue, outcomes } = applies;
  if (!running && queue.length === 0 && outcomes.length === 0) return null;
  return (
    <section>
      <h3 className="section-title">Applied in the background</h3>
      <ul className="space-y-1">
        {running && (
          <li className="note note-info flex items-center gap-2 p-2">
            <span aria-hidden className="h-2 w-2 animate-pulse rounded-full bg-sky-500" />
            <span>Applying now{running.paid ? " (a model call, a minute or two)" : ""}:</span>
            <TitleLink id={running.id} title={running.title} onSelect={onSelect} />
          </li>
        )}
        {queue.map((entry) => (
          <li key={entry.id} className="card flex items-center gap-2 p-2">
            <span className="badge badge-slate">Queued</span>
            <TitleLink id={entry.id} title={entry.title} onSelect={onSelect} />
          </li>
        ))}
        {outcomes.map((outcome) => (
          <li key={`${outcome.id}@${outcome.ended_at}`} className="card space-y-1 p-2">
            <p className="flex flex-wrap items-center gap-2">
              <span className={`badge ${OUTCOME_BADGES[outcome.status]}`}>{OUTCOME_LABELS[outcome.status]}</span>
              <TitleLink id={outcome.id} title={outcome.title} onSelect={onSelect} />
              <span className="text-xs text-slate-500">{formatDateTime(outcome.ended_at)}</span>
            </p>
            <p dir="auto" className="whitespace-pre-line text-xs text-slate-700">
              {outcome.message}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Overview({ data, onSelect }) {
  const applying = Boolean(data.applies.running) || data.applies.queue.length > 0;
  const planChanged = data.items.some((item) => item.status === "applied" && STRUCTURAL.includes(item.kind));
  return (
    <Panel title="About this review" subtitle={data.exists ? `The review ran ${formatDateTime(data.generated_at)}` : null}>
      {!data.exists ? (
        <NotRunYet cost={data.rerun_cost_usd} />
      ) : (
        <div className="max-w-4xl space-y-6 p-4 text-sm">
          {data.items.length === 0 && <p>The review found nothing to suggest.</p>}
          {data.note && (
            <section>
              <h3 className="section-title">The reviewer's remark on the whole wiki</h3>
              <Bidi as="p" className="leading-relaxed">
                {data.note}
              </Bidi>
            </section>
          )}
          <Queue applies={data.applies} onSelect={onSelect} />
          {planChanged && (
            <section>
              <h3 className="section-title">After the plan changes</h3>
              <FollowUp busy={applying} />
            </section>
          )}
          <Rerun data={data} applying={applying} />
        </div>
      )}
    </Panel>
  );
}
