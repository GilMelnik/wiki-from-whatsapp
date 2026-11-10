import { Bidi, formatCount, Ltr } from "@shell";
import { dateRange, stanceOf } from "./groups.js";

/** People and dates of a group, one line. */
export function GroupNumbers({ group }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-500">
      <span>
        {formatCount(group.size)} טענות · תומכים {formatCount(group.supporter_count)} · מתנגדים{" "}
        {formatCount(group.opposer_count)}
      </span>
      <Ltr>{dateRange(group.first_date, group.last_date)}</Ltr>
    </div>
  );
}

/** Badges: stance, size, links, suggestions, manual edits. */
export function GroupBadges({ group }) {
  const stance = stanceOf(group.stance);
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className={`badge ${stance.badge}`}>{stance.label}</span>
      {group.size > 1 && <span className="badge badge-sky">{formatCount(group.size)} טענות</span>}
      {group.contradiction_count > 0 && <span className="badge badge-rose">סותרת {formatCount(group.contradiction_count)}</span>}
      {group.suggestion_count > 0 && <span className="badge badge-amber">הצעות {formatCount(group.suggestion_count)}</span>}
      {group.merged_count > 0 && <span className="badge badge-slate">מוזגה</span>}
    </span>
  );
}

/** One row of the groups list. */
export function GroupRow({ group }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <GroupBadges group={group} />
        <Ltr className="truncate text-xs text-slate-400">{group.group_id}</Ltr>
      </div>
      <Bidi as="p" className="line-clamp-2 text-slate-700">
        {group.claim_text}
      </Bidi>
      <GroupNumbers group={group} />
    </div>
  );
}
