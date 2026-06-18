const STANCE_LABELS = {
  positive: "חיובי",
  negative: "שלילי",
  neutral: "ניטרלי",
  factual: "עובדתי",
};

export default function ClaimsPanel({
  claims,
  total,
  loading,
  selectedClaimKey,
  onSelectClaim,
  onMoveClaim,
}) {
  if (loading) {
    return (
      <div className="p-4 text-sm text-slate-500">טוען טענות...</div>
    );
  }

  if (total === 0) {
    return (
      <div className="p-4 text-sm text-slate-500">אין טענות לעמוד זה.</div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0">
        <h3 className="text-sm font-semibold">טענות מהקהילה</h3>
        <p className="text-xs text-slate-500 mt-1">{total} טענות (לאחר מיזוג)</p>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {claims.map((claim) => {
          const selected = claim.key === selectedClaimKey;
          return (
            <article
              key={`${claim.topic_id}:${claim.key}`}
              className={`border rounded-lg p-3 bg-white cursor-pointer hover:border-teal-300 ${
                selected ? "claim-card-selected" : "border-slate-200"
              }`}
              onClick={() => onSelectClaim(claim)}
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm leading-relaxed flex-1">{claim.claim_text}</p>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onMoveClaim(claim);
                  }}
                  className="shrink-0 text-xs px-2 py-1 rounded border border-slate-300 hover:bg-teal-50 hover:border-teal-400"
                >
                  העבר...
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                <span className="bg-slate-100 px-1.5 py-0.5 rounded">{claim.topic_id}</span>
                {claim.stance && (
                  <span>{STANCE_LABELS[claim.stance] || claim.stance}</span>
                )}
                {claim.support_count != null && (
                  <span>תומכים: {claim.support_count}</span>
                )}
                {claim.entities?.length > 0 && (
                  <span>{claim.entities.slice(0, 4).join(", ")}</span>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
