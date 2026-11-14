import { Bidi, countText, formatCount, formatSpan, ThreadTag } from "@shell";
import { automaticText, knowledgeText } from "./threads.js";

// The rows of the list column: a conversation of the whole chat, or of a gold window. Of step
// 2's knowledge label a row shows only what stands out (most conversations carry knowledge):
// "No knowledge", and a label set by hand; the opened conversation shows the full answer.

function Knowledge({ label }) {
  if (!label || (label.value && !label.manual)) return null;
  return (
    <span className={label.value ? "text-sky-800" : "text-amber-800"} title={automaticText(label)}>
      {" · "}
      {knowledgeText(label)}
      {label.manual && ", set by hand"}
    </span>
  );
}

export function ThreadRow({ row, query }) {
  return (
    <div className="space-y-0.5">
      <div className="text-xs text-slate-500">{formatSpan(row.start_time, row.last_time)}</div>
      <Bidi as="p" className="line-clamp-2 text-sm text-slate-900">
        {query && row.match ? row.match : row.opening}
      </Bidi>
      <div className="text-xs text-slate-500">
        {countText(row.size, "message")} · {countText(row.people, "person", formatCount, "people")}
        {row.matches > 0 && ` · ${countText(row.matches, "match", formatCount, "matches")}`}
        <Knowledge label={row.knowledge} />
      </div>
    </div>
  );
}

export function WindowThreadRow({ row, info }) {
  return (
    <div className="flex gap-2">
      <ThreadTag info={info} threadId={row.thread_id} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <Bidi as="p" className="line-clamp-2 text-sm text-slate-900">
          {row.opening}
        </Bidi>
        <div className="text-xs text-slate-500">
          {formatCount(row.inside)} of {formatCount(row.size)} in the window
        </div>
        {(row.start || row.end) && (
          <div className="text-xs text-amber-800">
            {[row.start && "may begin earlier", row.end && "may go on later"].filter(Boolean).join(" · ")}
          </div>
        )}
      </div>
    </div>
  );
}
