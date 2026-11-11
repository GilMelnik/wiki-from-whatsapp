import { Bidi, textDirection } from "@shell";
import { queuePlace } from "./suggestions.js";

// Rows of the list column: "About this review" on top, then one row per suggestion (the list
// is cut into sections by status, so a row does not repeat it): its box for a
// multi-selection (click it, or Space on the selected row), title, kind, the owner's
// correction, a mark when one of its pages changed or left the wiki since the review, and its
// place in the apply queue.

export function OverviewRow() {
  return <span className="font-medium">About this review</span>;
}

function PickBox({ picked, onToggle }) {
  return (
    <span
      role="checkbox"
      aria-checked={picked}
      aria-label="Mark this suggestion"
      title="Mark it to act on several suggestions at once (Space)"
      className={`mt-0.5 flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded border text-[10px] ${
        picked ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white"
      }`}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
    >
      {picked ? "✓" : ""}
    </span>
  );
}

const MARK_LABELS = { changed: "Page changed since", gone: "Page left the wiki" };

function Marks({ item }) {
  const marks = Object.entries(item.page_marks ?? {});
  if (marks.length === 0) return null;
  const worst = marks.some(([, mark]) => mark === "gone") ? "gone" : "changed";
  const title = marks.map(([page, mark]) => `${page}: ${item.mark_names?.[page] ?? mark}`).join("\n");
  return (
    <span className={worst === "gone" ? "text-rose-700" : "text-amber-800"} title={title}>
      {MARK_LABELS[worst]}
    </span>
  );
}

const PLACE_LABELS = { running: "Applying…", queued: "In the apply queue" };

export function SuggestionRow({ item, applies, picked, onToggle }) {
  const place = queuePlace(applies, item.id);
  return (
    <span className="flex items-start gap-2">
      <PickBox picked={picked} onToggle={onToggle} />
      <span className="min-w-0 flex-1 space-y-0.5">
        <Bidi className="block leading-snug">{item.title || item.kind_name}</Bidi>
        {/* Under a Hebrew title the line starts on the right too. */}
        <span dir={textDirection(item.title)} className="flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
          <span>{item.kind_name}</span>
          {item.edited_proposal && <span className="text-sky-800">corrected by you</span>}
          <Marks item={item} />
          {place && (
            <span className="flex items-center gap-1 text-sky-800">
              {place === "running" && <span aria-hidden className="inline-block h-2 w-2 animate-pulse rounded-full bg-sky-500" />}
              {PLACE_LABELS[place]}
            </span>
          )}
        </span>
      </span>
    </span>
  );
}
