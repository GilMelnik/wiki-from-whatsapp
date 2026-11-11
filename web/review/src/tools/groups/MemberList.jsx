import { Bidi, countText, formatCount, Ltr, stanceOf } from "@shell";
import { dateRange } from "./groups.js";

// The member claims of a group, each with its own text, stance, dates and people. With `on`
// (the group detail) each claim can be picked (for a split or a move) or made the
// representative; without it (a suggestion) the list is read-only.

function Member({ member, picked, saving, on }) {
  if (member.missing) {
    return (
      <li className="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
        <Ltr>{member.claim_id}</Ltr> is no longer in the claims file (a step 5 run will take it out of the group).
      </li>
    );
  }
  const stance = stanceOf(member.stance);
  return (
    <li className={`rounded border p-3 ${member.is_representative ? "border-blue-300 bg-blue-50/40" : "border-slate-200 bg-white"}`}>
      <div className="flex items-start gap-2">
        {on && (
          <input
            type="checkbox"
            className="mt-1"
            aria-label="Pick the claim"
            checked={picked}
            disabled={saving}
            onChange={() => on.togglePick(member.claim_id)}
          />
        )}
        <div className="min-w-0 flex-1 space-y-1">
          <Bidi as="p" className="leading-relaxed">
            {member.claim_text}
          </Bidi>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
            <span className={`badge ${stance.badge}`}>{stance.label}</span>
            {member.is_representative && <span className="badge badge-sky">Representative text</span>}
            <span>
              {countText(member.supporter_count, "supporter", formatCount)} · {countText(member.opposer_count, "opposer", formatCount)}
            </span>
            <Ltr>{dateRange(member.first_date, member.last_date)}</Ltr>
            <Ltr className="text-slate-400">{member.claim_id}</Ltr>
            {member.entity_names.map((name) => (
              <Bidi key={name} className="badge badge-slate">
                {name}
              </Bidi>
            ))}
          </div>
        </div>
        {on && !member.is_representative && (
          <button type="button" className="btn btn-sm shrink-0" disabled={saving} onClick={() => on.setRepresentative(member.claim_id)}>
            Make representative
          </button>
        )}
      </div>
    </li>
  );
}

export function MemberList({ members, picked = [], saving = false, on }) {
  return (
    <ul className="space-y-2">
      {members.map((member) => (
        <Member key={member.claim_id} member={member} picked={picked.includes(member.claim_id)} saving={saving} on={on} />
      ))}
    </ul>
  );
}
