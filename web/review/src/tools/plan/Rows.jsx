import { Bidi, formatCount, Ltr } from "@shell";
import { ChangeBadge } from "./bits.jsx";
import { warningsFor } from "./plan.js";

// Rows of the list column: the tree (indented by depth) and the labels (grouped by kind).

const INDENT_REM = 1;

function WarningDot({ overview, id }) {
  const count = warningsFor(overview, id).length;
  if (count === 0) return null;
  return (
    <span className="badge badge-amber" title={`${count} אזהרות`}>
      !
    </span>
  );
}

export function TreeRow({ overview, item }) {
  if (item.kind === "overview") {
    const { budget } = overview;
    return (
      <span className="flex items-center justify-between gap-2">
        <span className="font-semibold">סקירת התוכנית</span>
        <span className={`badge ${budget.pages >= budget.max_pages ? "badge-amber" : "badge-slate"}`}>
          {formatCount(budget.pages)}/{formatCount(budget.max_pages)} דפים
        </span>
      </span>
    );
  }
  const isPage = item.kind === "page";
  return (
    <span className="block" style={{ paddingInlineStart: `${(item.depth - 1) * INDENT_REM}rem` }}>
      <span className="flex items-center gap-1.5">
        <span aria-hidden className={isPage ? "text-emerald-600" : "text-slate-400"}>
          {isPage ? "•" : "▾"}
        </span>
        <Bidi className={isPage ? "" : "font-semibold text-slate-700"}>{item.title_he}</Bidi>
        {!isPage && <span className="text-xs text-slate-400">{formatCount(item.page_count)}</span>}
        <ChangeBadge overview={overview} id={item.id} />
        <WarningDot overview={overview} id={item.id} />
      </span>
      <Ltr className="block ps-4 text-[11px] text-slate-400">{item.id}</Ltr>
    </span>
  );
}

export function LabelRow({ overview, item }) {
  return (
    <span className="block">
      {item.first && <span className="mb-1 block text-xs font-semibold uppercase text-slate-500"><Ltr>{item.labelKind}</Ltr></span>}
      <span className="flex items-center gap-1.5 ps-2">
        <Bidi>{item.title_he}</Bidi>
        <Ltr className="text-[11px] text-slate-400">{item.id}</Ltr>
        <ChangeBadge overview={overview} id={item.id} />
        <WarningDot overview={overview} id={item.id} />
      </span>
    </span>
  );
}
