const REDACTION_MARK = "[הוסר]";

function renderWithHighlights(text, redactions, mode) {
  if (!text) return null;
  if (!redactions?.length) return <span>{text}</span>;

  const parts = text.split(REDACTION_MARK);
  const nodes = [];
  parts.forEach((part, index) => {
    if (part) nodes.push(<span key={`t-${index}`}>{part}</span>);
    if (index < redactions.length) {
      const redaction = redactions[index];
      if (mode === "scrubbed") {
        nodes.push(
          <mark key={`r-${index}`} className="redaction-mark" title={redaction.value}>
            {REDACTION_MARK} ({redaction.type})
          </mark>
        );
      } else {
        nodes.push(
          <mark key={`r-${index}`} className="redaction-value" title={redaction.type}>
            {redaction.value}
          </mark>
        );
      }
    }
  });
  return nodes;
}

export default function ClaimReviewPanel({
  claim,
  queue,
  saving,
  onAccept,
  onRestore,
  onPrev,
  onNext,
}) {
  if (!claim) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-400 text-sm">
        בחרו טענה מהרשימה
      </div>
    );
  }

  const isPending = claim.review_status === "pending";
  const redactions = claim.redactions || [];

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold">{claim.claim_id}</h2>
          <p className="text-xs text-slate-500 mt-1">
            שיחה: {claim.thread_id || "—"} · תאריך: {claim.date || "—"}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={!queue?.prev_in_queue}
            onClick={() => onPrev(queue?.prev_in_queue)}
            className="px-2 py-1 text-xs rounded border border-slate-300 disabled:opacity-40"
          >
            ← קודם
          </button>
          <button
            type="button"
            disabled={!queue?.next_in_queue}
            onClick={() => onNext(queue?.next_in_queue)}
            className="px-2 py-1 text-xs rounded border border-slate-300 disabled:opacity-40"
          >
            הבא →
          </button>
        </div>
      </div>

      {claim.topic_tags?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {claim.topic_tags.map((tag) => (
            <span
              key={tag}
              className="text-xs px-2 py-0.5 rounded bg-blue-50 text-blue-700"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-3">
        <h3 className="text-sm font-medium text-slate-700 mb-2">
          טקסט אחרי הסרה אוטומטית
        </h3>
        <p className="text-sm leading-relaxed">
          {renderWithHighlights(claim.claim_text, redactions, "scrubbed")}
        </p>
      </section>

      {redactions.length > 0 && (
        <section className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-3">
          <h3 className="text-sm font-medium text-emerald-900 mb-2">
            טקסט מקורי (שחזור)
          </h3>
          <p className="text-sm leading-relaxed">
            {renderWithHighlights(claim.claim_text, redactions, "original")}
          </p>
        </section>
      )}

      {redactions.length > 0 && (
        <section className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <h3 className="text-sm font-medium text-slate-700 mb-2">פריטים שהוסרו</h3>
          <ul className="text-sm space-y-1">
            {redactions.map((item, index) => (
              <li key={`${item.type}-${index}`} className="flex gap-2">
                <span className="text-xs uppercase text-slate-500 w-14 shrink-0">
                  {item.type}
                </span>
                <code className="text-rose-800 bg-rose-50 px-1 rounded break-all">
                  {item.value}
                </code>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!isPending && (
        <div className="rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-600">
          נבדק:{" "}
          {claim.pii_review === "restored"
            ? "הנתונים שוחזרו לטקסט המקורי"
            : "ההסרה אושרה"}
          {claim.pii_reviewed_at && (
            <span className="text-slate-400"> · {claim.pii_reviewed_at}</span>
          )}
        </div>
      )}
    </div>
  );
}

export function ReviewActions({ claim, saving, onAccept, onRestore }) {
  if (!claim || claim.review_status !== "pending") return null;

  return (
    <div className="border-t border-slate-200 p-4 space-y-3 bg-white">
      <p className="text-sm text-slate-600">
        האם ההסרה האוטומטית נכונה, או שהנתון צריך להישאר בטענה?
      </p>
      <button
        type="button"
        disabled={saving}
        onClick={onAccept}
        className="w-full py-2 rounded-lg bg-rose-600 text-white text-sm font-medium hover:bg-rose-700 disabled:opacity-50"
      >
        אשר הסרה — השאר [הוסר]
      </button>
      <button
        type="button"
        disabled={saving}
        onClick={onRestore}
        className="w-full py-2 rounded-lg border-2 border-emerald-600 text-emerald-800 text-sm font-medium hover:bg-emerald-50 disabled:opacity-50"
      >
        שחזר נתון — השאר בצינור
      </button>
    </div>
  );
}
