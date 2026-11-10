import { Bidi, formatCount, formatDateTime, Ltr, Panel, PanelState } from "@shell";
import { ClaimText } from "./ClaimText.jsx";
import { dateRange, marksToRestore, STANCE_LABELS, STATUS } from "./review.js";

// Main column: the selected claim's text with its marks, what restoring would save, and the
// facts that help the decision (support, dates, entities).

function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function QueueNav({ queue, onSelect }) {
  if (!queue || queue.position === null) return null;
  return (
    <>
      <button
        type="button"
        className="btn btn-sm"
        disabled={!queue.prev_id}
        onClick={() => onSelect(queue.prev_id)}
      >
        ‹ הקודמת
      </button>
      <span className="text-xs text-slate-500">
        {formatCount(queue.position + 1)} / {formatCount(queue.total)}
      </span>
      <button
        type="button"
        className="btn btn-sm"
        disabled={!queue.next_id}
        onClick={() => onSelect(queue.next_id)}
      >
        הבאה ›
      </button>
    </>
  );
}

function PendingText({ claim, chosen }) {
  const restored = marksToRestore(chosen, claim.redactions.length);
  return (
    <>
      <Section title="הטקסט אחרי ההסרה האוטומטית">
        <ClaimText segments={claim.segments} redactions={claim.redactions} mode="scrubbed" />
      </Section>
      {claim.marks_match ? (
        <Section title={chosen.length > 0 ? "הטקסט אם תשחזרו את הפרטים המסומנים" : "הטקסט אם תשחזרו"}>
          <div className="rounded border border-emerald-200 bg-emerald-50/50 p-3">
            <ClaimText segments={claim.segments} redactions={claim.redactions} restored={restored} mode="preview" />
          </div>
        </Section>
      ) : (
        <p className="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
          מספר הסימונים בטקסט לא תואם את מספר הפרטים שהוסרו, ולכן אי אפשר לשחזר אוטומטית. אפשר
          לאשר את ההסרה.
        </p>
      )}
    </>
  );
}

function ReviewedText({ claim }) {
  const status = STATUS[claim.status];
  return (
    <>
      <Section title="החלטה">
        <p>
          <span className={`badge ${status.badge}`}>{status.label}</span>
          {claim.reviewed_at && <span className="ms-2 text-slate-500">{formatDateTime(claim.reviewed_at)}</span>}
        </p>
      </Section>
      <Section title="הטקסט כעת">
        <Bidi as="p" className="leading-relaxed">
          {claim.text}
        </Bidi>
      </Section>
      {claim.segments.length > 0 && (
        <Section title="מה הוסר בשלב 3">
          <ClaimText segments={claim.segments} redactions={claim.redactions} mode="scrubbed" />
        </Section>
      )}
    </>
  );
}

function Facts({ claim }) {
  return (
    <Section title="תמיכה ותאריכים">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-slate-500">תומכים</dt>
        <dd>
          {formatCount(claim.supporter_count ?? 0)} אנשים · {formatCount(claim.supporting_message_count)} הודעות
        </dd>
        <dt className="text-slate-500">מתנגדים</dt>
        <dd>
          {formatCount(claim.opposer_count ?? 0)} אנשים · {formatCount(claim.opposing_message_count)} הודעות
        </dd>
        <dt className="text-slate-500">תאריכים</dt>
        <dd>
          <Ltr>{dateRange(claim.first_date, claim.last_date)}</Ltr>
        </dd>
        <dt className="text-slate-500">עמדה</dt>
        <dd>{STANCE_LABELS[claim.stance] ?? claim.stance ?? "—"}</dd>
        <dt className="text-slate-500">ישויות</dt>
        <dd className="flex flex-wrap gap-1">
          {claim.entities.length === 0
            ? "—"
            : claim.entities.map((entity) => (
                <Bidi key={entity} className="badge badge-slate">
                  {entity}
                </Bidi>
              ))}
        </dd>
      </dl>
    </Section>
  );
}

export function ClaimDetail({ detail, selectedId, chosen, onSelect }) {
  if (!selectedId) return <PanelState empty emptyText="בחרו טענה מהרשימה" />;
  const claim = detail.data?.claim;
  return (
    <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(claim)} onRetry={detail.reload}>
      {claim && (
        <Panel
          title={<Ltr>{claim.claim_id}</Ltr>}
          subtitle={
            <>
              שיחה <Ltr>{claim.thread_id ?? "—"}</Ltr>
            </>
          }
          actions={<QueueNav queue={detail.data.queue} onSelect={onSelect} />}
        >
          <div className="space-y-6 p-4 text-sm">
            {claim.status === "pending" ? <PendingText claim={claim} chosen={chosen} /> : <ReviewedText claim={claim} />}
            <Facts claim={claim} />
          </div>
        </Panel>
      )}
    </PanelState>
  );
}
