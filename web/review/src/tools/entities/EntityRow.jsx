import { Bidi, countText } from "@shell";
import { statusOf } from "./entities.js";

/** One row of the entity list: name, status, size, and what step 4 flagged for review. */
export function EntityRow({ entity }) {
  const status = statusOf(entity.status);
  const multi = entity.member_count > 1;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <Bidi className="truncate font-medium">{entity.canonical_name || "(no name)"}</Bidi>
        <span className={`badge shrink-0 ${status.badge}`}>{status.label}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
        {multi && <span className="font-medium text-slate-700">{countText(entity.member_count, "name")}</span>}
        <span>{countText(entity.total_count, "claim")}</span>
        {multi && <span>Cohesion {entity.score}</span>}
        {entity.attached_count > 0 && <span className="badge badge-amber">Attached automatically</span>}
        {entity.conflict_count > 0 && <span className="badge badge-amber">Like another entity</span>}
        {entity.uncertain_count > 0 && <span className="badge badge-slate">Uncertain contacts</span>}
      </div>
      {multi && <Bidi className="truncate text-xs text-slate-400">{entity.aliases.join(" · ")}</Bidi>}
    </div>
  );
}
