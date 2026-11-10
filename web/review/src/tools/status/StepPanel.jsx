import { JobLog, Ltr, Panel, useJobs, useStepRun } from "@shell";

/** Side column, top: the step that produces the selected artifact, its run buttons and last run. */
export function StepPanel({ artifact }) {
  const { stepsByArtifact, latestByStep, runningByArtifact } = useJobs();
  const run = useStepRun();
  const step = artifact ? stepsByArtifact[artifact.key] : undefined;
  if (!step) {
    return (
      <Panel title="שלב מייצר">
        <p className="p-4 text-sm text-slate-400">הקובץ הזה לא נוצר בשלב שאפשר להריץ.</p>
      </Panel>
    );
  }
  const running = runningByArtifact[artifact.key];
  const latest = latestByStep[step.key];
  const footer = step.incremental_safe && (
    <>
      <button type="button" className="btn btn-primary btn-block" disabled={Boolean(running)} onClick={() => run(step.key)}>
        {running ? "השלב רץ…" : step.paid ? "הרצת השלב…" : "הרצת השלב"}
      </button>
      <button
        type="button"
        className="btn btn-danger btn-sm btn-block"
        disabled={Boolean(running)}
        onClick={() => run(step.key, { rebuild: true })}
      >
        בנייה מחדש מאפס…
      </button>
    </>
  );
  return (
    <Panel title={step.title} subtitle="השלב שמייצר את הקובץ" footer={footer || undefined}>
      <div className="space-y-6 p-4 text-sm">
        <section>
          <div className="flex flex-wrap gap-1">
            {step.incremental_safe ? (
              <span className="badge badge-emerald">מעבד רק מה שהשתנה</span>
            ) : (
              <span className="badge badge-slate">הרצה ידנית בלבד</span>
            )}
            {step.paid && <span className="badge badge-amber">קריאות API בתשלום</span>}
          </div>
          {step.command && (
            <p className="mt-2 text-xs text-slate-500">
              <Ltr>{step.command}</Ltr>
            </p>
          )}
          {!step.incremental_safe && (
            <p className="mt-2 text-slate-600">
              השלב עוד לא שומר על עריכות ידניות בהרצה חוזרת, ולכן אפשר להריץ אותו רק ידנית מהטרמינל.
            </p>
          )}
        </section>
        <section>
          <h3 className="section-title">הרצה אחרונה</h3>
          {latest ? (
            <JobLog jobId={latest.id} className="max-h-72" />
          ) : (
            <p className="text-slate-400">השלב לא הורץ מהשרת הזה מאז שהופעל.</p>
          )}
        </section>
      </div>
    </Panel>
  );
}
