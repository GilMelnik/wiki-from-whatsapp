import { Bidi, formatCount } from "@shell";
import { KIND_BADGES, queuePlace, STATUS_BADGES } from "./suggestions.js";

// Rows of the list column: the overview on top, then one row per suggestion with its box
// for a multi-selection (click it, or Space on the selected row), kind, title, status, the
// owner's correction, a mark when one of its pages changed or left the wiki since the review,
// and its place in the apply queue.

export function OverviewRow({ data }) {
  const counts = data?.counts?.status ?? {};
  const open = (counts.pending ?? 0) + (counts.accepted ?? 0);
  return (
    <span className="flex items-center justify-between gap-2">
      <span className="font-semibold">סקירה כללית</span>
      {data?.exists && <span className="badge badge-slate" title="הצעות פתוחות">{formatCount(open)}</span>}
    </span>
  );
}

function PickBox({ picked, disabled, onToggle }) {
  return (
    <span
      role="checkbox"
      aria-checked={picked}
      aria-label="סימון ההצעה"
      title="סימון לפעולה על כמה הצעות (רווח)"
      className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${
        picked ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white"
      } ${disabled ? "opacity-40" : "cursor-pointer"}`}
      onClick={(event) => {
        event.stopPropagation();
        if (!disabled) onToggle();
      }}
    >
      {picked ? "✓" : ""}
    </span>
  );
}

const MARK_BADGES = { changed: "badge-amber", gone: "badge-rose" };
const MARK_LABELS = { changed: "דף השתנה", gone: "דף ירד" };

function Marks({ item }) {
  const marks = Object.entries(item.page_marks ?? {});
  if (marks.length === 0) return null;
  const worst = marks.some(([, mark]) => mark === "gone") ? "gone" : "changed";
  const title = marks.map(([page, mark]) => `${page}: ${item.mark_names?.[page] ?? mark}`).join("\n");
  return (
    <span className={`badge ${MARK_BADGES[worst]}`} title={title}>
      {MARK_LABELS[worst]}
    </span>
  );
}

const PLACE_LABELS = { running: "מתבצעת…", queued: "בתור לביצוע" };

export function SuggestionRow({ item, applies, picked, onToggle }) {
  const place = queuePlace(applies, item.id);
  return (
    <span className="flex items-start gap-2">
      <PickBox picked={picked} onToggle={onToggle} />
      <span className="min-w-0 flex-1 space-y-1">
        <Bidi as="span" className="block leading-snug">
          {item.title || item.kind_name}
        </Bidi>
        <span className="flex flex-wrap items-center gap-1 text-xs">
          <span className={`badge ${KIND_BADGES[item.kind] ?? "badge-slate"}`}>{item.kind_name}</span>
          <span className={`badge ${STATUS_BADGES[item.status]}`}>{item.status_name}</span>
          {item.edited_proposal && <span className="badge badge-sky" title="ההצעה תוקנה על ידיכם">תוקנה</span>}
          {item.paid && item.status === "accepted" && <span className="badge badge-slate" title="הביצוע הוא קריאה למודל">בתשלום</span>}
          <Marks item={item} />
          {place && (
            <span className="badge badge-sky">
              {place === "running" && <span aria-hidden className="me-1 inline-block h-2 w-2 animate-pulse rounded-full bg-sky-500" />}
              {PLACE_LABELS[place]}
            </span>
          )}
        </span>
      </span>
    </span>
  );
}
