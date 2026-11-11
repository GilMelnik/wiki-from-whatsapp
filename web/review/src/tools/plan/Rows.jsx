import { Bidi, formatCount, Ltr } from "@shell";
import { ChangeBadge } from "./bits.jsx";
import { warningsFor } from "./plan.js";

// Rows of the list column: the tree (indented by depth, with the assignment rows on top) and
// the labels (grouped by kind). Once step 6b has run, pages and labels show how many claim
// groups they hold.

const ASSIGNMENT_ROWS = {
  unplaced: { title: "קבוצות בלי דף", count: (summary) => summary.counts.unplaced },
  waiting: { title: "ממתינות להרצת השיוך", count: (summary) => summary.counts.waiting },
  proposals: { title: "הצעות לשינוי התוכנית", count: (summary) => summary.proposals.pending },
};

function GroupBadge({ count, title, warn = false }) {
  if (count === undefined || count === null) return null;
  return (
    <span className={`badge ms-auto ${warn ? "badge-amber" : "badge-slate"}`} title={title}>
      {formatCount(count)}
    </span>
  );
}

function AssignmentRow({ overview, item }) {
  const row = ASSIGNMENT_ROWS[item.kind];
  const summary = overview.assignments;
  const count = summary?.exists && !summary.problem ? row.count(summary) : null;
  return (
    <span className="flex items-center gap-2 text-slate-700">
      <span aria-hidden className="text-amber-600">◆</span>
      <span>{row.title}</span>
      {count === null ? (
        <span className="ms-auto text-xs text-slate-400">6ב לא רץ</span>
      ) : (
        <GroupBadge count={count} warn={count > 0} title={item.kind === "proposals" ? "הצעות שממתינות להחלטה" : "קבוצות טענות"} />
      )}
    </span>
  );
}

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
  if (ASSIGNMENT_ROWS[item.kind]) return <AssignmentRow overview={overview} item={item} />;
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
  const sizes = isPage ? overview.assignments?.pages?.[item.id] : null;
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
        {sizes && (
          <GroupBadge
            count={sizes.all}
            title={`${formatCount(sizes.primary)} קבוצות שהדף ראשי עבורן, ${formatCount(sizes.all - sizes.primary)} שרק מזכירות אותו, ${formatCount(sizes.manual)} שויכו ביד`}
          />
        )}
      </span>
      <Ltr className="block ps-4 text-[11px] text-slate-400">{item.id}</Ltr>
    </span>
  );
}

export function LabelRow({ overview, item }) {
  const summary = overview.assignments;
  const finding = summary?.label_findings?.find((found) => found.label_id === item.id);
  return (
    <span className="block">
      {item.first && <span className="mb-1 block text-xs font-semibold uppercase text-slate-500"><Ltr>{item.labelKind}</Ltr></span>}
      <span className="flex items-center gap-1.5 ps-2">
        <Bidi>{item.title_he}</Bidi>
        <Ltr className="text-[11px] text-slate-400">{item.id}</Ltr>
        <ChangeBadge overview={overview} id={item.id} />
        <WarningDot overview={overview} id={item.id} />
        {summary?.labels && (
          <GroupBadge
            count={summary.labels[item.id] ?? 0}
            warn={Boolean(finding)}
            title={finding ? (finding.finding === "large" ? "מודבקת לחלק גדול מאוד מהקבוצות" : "מודבקת למעט מאוד קבוצות") : "קבוצות עם התווית"}
          />
        )}
      </span>
    </span>
  );
}
