import { Bidi, formatCount, formatDateTime, Ltr, Panel, useArtifactStatus, useJobs, useStepRun } from "@shell";
import { FollowUp } from "./Changes.jsx";
import { formatUsd, STRUCTURAL, SUGGESTIONS_KEY } from "./suggestions.js";

// The overview: the review's own remark and counts, what is being carried out in the
// background and what came out of it, and, calmly, whether the review should run again. Every
// apply changes the wiki, so the frame would call the suggestions out of date after each one;
// a new review is a paid whole-wiki call, so it is mentioned once here, with its cost, and run
// only on request (the frame's banner for this file is quiet: index.jsx `quietStale`).

const STATUS_ORDER = ["pending", "accepted", "rejected", "applied"];
const OUTCOME_BADGES = { applied: "badge-emerald", refused: "badge-amber", failed: "badge-rose" };
const OUTCOME_LABELS = { applied: "בוצעה", refused: "סורבה", failed: "נכשלה" };

function useReviewStep() {
  const { stepsByArtifact, runningByArtifact } = useJobs();
  return { step: stepsByArtifact[SUGGESTIONS_KEY], running: runningByArtifact[SUGGESTIONS_KEY] };
}

function NotRunYet({ cost }) {
  const { step, running } = useReviewStep();
  const run = useStepRun();
  return (
    <div className="space-y-3 p-4 text-sm">
      <p>שלב 8 (סקירת הוויקי כולו) עוד לא רץ, ולכן אין הצעות לבדוק.</p>
      <p className="text-slate-600">
        הסקירה קוראת את כל הדפים בקריאה אחת למודל, בערך {formatUsd(cost)} להרצה על הוויקי בגודל הנוכחי, ומציעה עד כתריסר תיקונים.
      </p>
      {step && (
        <button type="button" className="btn btn-primary" disabled={Boolean(running)} onClick={() => run(step.key)}>
          {running ? "הסקירה רצה כעת…" : "הרצת הסקירה…"}
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
    <p className="rounded border border-slate-200 bg-white p-3 text-slate-700">
      הוויקי השתנה מאז הסקירה הזו. כשתסיימו לבצע הצעות, אפשר להריץ אותה שוב כדי לקבל הצעות על הוויקי המעודכן (קריאה אחת למודל, בערך{" "}
      {formatUsd(data.rerun_cost_usd)}); ההחלטות שכבר קיבלתם נשמרות.{" "}
      <button type="button" className="btn btn-sm ms-1" disabled={applying || Boolean(running)} onClick={() => run(step.key)}>
        הרצה חוזרת…
      </button>
    </p>
  );
}

function Queue({ applies, onSelect }) {
  const { running, queue, outcomes } = applies;
  if (!running && queue.length === 0 && outcomes.length === 0) return null;
  return (
    <section>
      <h3 className="section-title">ביצוע ברקע</h3>
      <ul className="space-y-1">
        {running && (
          <li className="flex items-center gap-2 rounded border border-sky-200 bg-sky-50 p-2">
            <span aria-hidden className="h-2 w-2 animate-pulse rounded-full bg-sky-500" />
            <span>מתבצעת עכשיו{running.paid ? " (קריאה למודל, דקה עד שתיים)" : ""}:</span>
            <button type="button" className="text-start underline" onClick={() => onSelect(running.id)}>
              <Bidi>{running.title}</Bidi>
            </button>
          </li>
        )}
        {queue.map((entry) => (
          <li key={entry.id} className="flex items-center gap-2 rounded border border-slate-200 bg-white p-2">
            <span className="badge badge-slate">בתור</span>
            <button type="button" className="text-start underline" onClick={() => onSelect(entry.id)}>
              <Bidi>{entry.title}</Bidi>
            </button>
          </li>
        ))}
        {outcomes.map((outcome) => (
          <li key={`${outcome.id}@${outcome.ended_at}`} className="space-y-1 rounded border border-slate-200 bg-white p-2">
            <p className="flex flex-wrap items-center gap-2">
              <span className={`badge ${OUTCOME_BADGES[outcome.status]}`}>{OUTCOME_LABELS[outcome.status]}</span>
              <button type="button" className="text-start underline" onClick={() => onSelect(outcome.id)}>
                <Bidi>{outcome.title}</Bidi>
              </button>
              <span className="text-xs text-slate-400">{formatDateTime(outcome.ended_at)}</span>
            </p>
            <Bidi as="p" className="whitespace-pre-line text-xs text-slate-700">
              {outcome.message}
            </Bidi>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Counts({ data, onFilter }) {
  const byStatus = data.counts.status;
  const names = Object.fromEntries(data.items.map((item) => [item.status, item.status_name]));
  return (
    <ul className="flex flex-wrap gap-2">
      {STATUS_ORDER.filter((status) => byStatus[status]).map((status) => (
        <li key={status}>
          <button type="button" className="btn btn-sm" onClick={() => onFilter(status)}>
            {names[status]}: {formatCount(byStatus[status])}
          </button>
        </li>
      ))}
    </ul>
  );
}

export function Overview({ data, onSelect, onFilter }) {
  const applying = Boolean(data.applies.running) || data.applies.queue.length > 0;
  const planChanged = data.items.some((item) => item.status === "applied" && STRUCTURAL.includes(item.kind));
  return (
    <Panel title="סקירת הוויקי כולו" subtitle={data.exists ? `הסקירה רצה ${formatDateTime(data.generated_at)}` : null}>
      {!data.exists ? (
        <NotRunYet cost={data.rerun_cost_usd} />
      ) : (
        <div className="space-y-6 p-4 text-sm">
          {data.items.length === 0 ? (
            <p>הסקירה לא מצאה מה להציע.</p>
          ) : (
            <section>
              <h3 className="section-title">ההצעות ({formatCount(data.items.length)})</h3>
              <Counts data={data} onFilter={onFilter} />
            </section>
          )}
          {data.note && (
            <section>
              <h3 className="section-title">הערת הסוקר על הוויקי כולו</h3>
              <Bidi as="p" className="leading-relaxed">
                {data.note}
              </Bidi>
            </section>
          )}
          <Queue applies={data.applies} onSelect={onSelect} />
          {planChanged && (
            <section>
              <h3 className="section-title">אחרי שינויים בתוכנית</h3>
              <FollowUp busy={applying} />
            </section>
          )}
          <Rerun data={data} applying={applying} />
          {data.run && (
            <p className="text-xs text-slate-500">
              המודל <Ltr>{data.run.model}</Ltr> קרא {formatCount(data.run.input_tokens ?? 0)} טוקנים.
              {data.run.outdated.length > 0 && ` ${formatCount(data.run.outdated.length)} דפים היו ישנים מהקבוצות שלהם כשהסקירה רצה.`}
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}
