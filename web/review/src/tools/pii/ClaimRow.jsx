import { Bidi, formatCount, Ltr } from "@shell";
import { dateRange, STATUS, typeCounts } from "./review.js";

/** One row of the queue: status, id, text, what was removed, dates and support. */
export function ClaimRow({ claim }) {
  const status = STATUS[claim.status] ?? STATUS.pending;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span className={`badge ${status.badge}`}>{status.label}</span>
        <Ltr className="truncate text-xs text-slate-400">{claim.claim_id}</Ltr>
      </div>
      <Bidi as="p" className="line-clamp-2 text-slate-700">
        {claim.text}
      </Bidi>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-500">
        <span>{typeCounts(claim.redaction_counts)}</span>
        <Ltr>{dateRange(claim.first_date, claim.last_date)}</Ltr>
        <span>
          Supporters {formatCount(claim.supporter_count ?? 0)} · Opposers {formatCount(claim.opposer_count ?? 0)}
        </span>
      </div>
    </div>
  );
}
