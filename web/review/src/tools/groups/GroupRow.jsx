import { Bidi, countText, formatCount, Ltr, stanceOf } from "@shell";
import { dateRange } from "./groups.js";

/** Size, people and dates of a group, one line (the size only when it holds several claims). */
export function GroupNumbers({ group }) {
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-500">
      {group.size > 1 && <span>{countText(group.size, "claim", formatCount)}</span>}
      <span>
        {countText(group.supporter_count, "supporter", formatCount)}
        {group.opposer_count > 0 && ` · ${countText(group.opposer_count, "opposer", formatCount)}`}
      </span>
      <Ltr>{dateRange(group.first_date, group.last_date)}</Ltr>
    </span>
  );
}

/** Marks: stance, kept off the wiki, disputed, a merge suggested, merged by hand. */
export function GroupBadges({ group }) {
  const stance = stanceOf(group.stance);
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className={`badge ${stance.badge}`}>{stance.label}</span>
      {group.withheld && (
        <span className="badge badge-amber" title="A negative statement that names an entity, reported by too few members: kept off every page">
          Withheld from the wiki
        </span>
      )}
      {group.contradiction_count > 0 && <span className="badge badge-rose">Disputed</span>}
      {group.suggestion_count > 0 && <span className="badge badge-sky">Merge suggested</span>}
      {group.merged_count > 0 && <span className="badge badge-slate">Merged</span>}
    </span>
  );
}

/** “Would be on page …” for a withheld group, “On page …” for any other. */
export function PageLine({ group }) {
  if (!group.page) return <span className="text-xs text-slate-500">On no page of the plan</span>;
  return (
    <span className="text-xs text-slate-500">
      {group.withheld ? "Would be on" : "On"} “<Bidi>{group.page.title_he}</Bidi>”
    </span>
  );
}

/** The entity names of a group, as badges. */
export function EntityNames({ names }) {
  if (!names?.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {names.map((name) => (
        <Bidi key={name} className="badge badge-slate">
          {name}
        </Bidi>
      ))}
    </span>
  );
}

/** One row of the groups list; in the withheld list also the names and the page. */
export function GroupRow({ group, withheldList = false }) {
  return (
    <span className="flex flex-col gap-1" title={group.group_id}>
      {!withheldList && <GroupBadges group={group} />}
      <Bidi as="span" className="line-clamp-2 block text-slate-700">
        {group.claim_text}
      </Bidi>
      {withheldList && <EntityNames names={group.entity_names} />}
      <GroupNumbers group={group} />
      {withheldList && <PageLine group={group} />}
    </span>
  );
}
