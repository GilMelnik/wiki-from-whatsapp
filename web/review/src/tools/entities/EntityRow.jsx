import { Bidi, countText } from "@shell";
import { statusOf } from "./entities.js";

/** One row of the entity list: the name, its other names, and what step 4 flagged for review. */
export function EntityRow({ entity }) {
  const status = statusOf(entity.status);
  const multi = entity.member_count > 1;
  return (
    <span className="flex flex-col gap-0.5">
      <span className="flex items-center justify-between gap-2">
        <Bidi className="truncate font-medium">{entity.canonical_name || "(no name)"}</Bidi>
        {entity.status !== "suggested" && <span className={`badge shrink-0 ${status.badge}`}>{status.label}</span>}
      </span>
      {multi && <Bidi className="truncate text-xs text-slate-500">{entity.aliases.join(" · ")}</Bidi>}
      <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
        <span>{countText(entity.total_count, "claim")}</span>
        {entity.conflict_count > 0 && <span className="text-amber-800">Like another entity</span>}
        {entity.attached_count > 0 && <span className="text-amber-800">A name attached by an update</span>}
        {entity.uncertain_count > 0 && <span className="text-amber-800">Uncertain contacts</span>}
        {entity.low_cohesion && <span className="text-amber-800">Names far apart</span>}
      </span>
    </span>
  );
}
