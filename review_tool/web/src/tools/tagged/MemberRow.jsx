import { Bidi, Ltr } from "@shell";
import { STATUS_BADGES, STATUS_LABELS, statsText, tagText } from "./tagged.js";

/** One row of the list: the tag as the chat writes it, the decision, how often they are tagged. */
export function MemberRow({ row }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="flex flex-wrap items-center gap-2">
        <Ltr className="text-slate-800">{tagText(row)}</Ltr>
        <span className={`badge ${STATUS_BADGES[row.status]}`}>{STATUS_LABELS[row.status]}</span>
        {row.public_name && <Bidi className="font-medium text-slate-900">{row.public_name}</Bidi>}
      </span>
      <span className="text-xs text-slate-500">{statsText(row)}</span>
    </span>
  );
}
