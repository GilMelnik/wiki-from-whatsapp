import { Bidi, countText, formatCount, Ltr, stanceOf } from "@shell";
import { dateRange } from "./groups.js";
import { EntityNames } from "./GroupRow.jsx";

// The member claims of a group, each with its own text, stance, dates and people. With `on`
// (the group detail) each claim can be picked (for a split or a move) or made the text of the
// group; without it (a suggestion) the list is read-only.

function Member({ member, picked, saving, on }) {
  if (member.missing) {
    return (
      <li className="note note-warning">
        <Ltr>{member.claim_id}</Ltr> is no longer in the claims file (a step 5 run will take it out of the group).
      </li>
    );
  }
  const stance = stanceOf(member.stance);
  return (
    <li className={`card ${member.is_representative ? "border-blue-300" : ""}`} title={member.claim_id}>
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
            <Ltr>{dateRange(member.first_date, member.last_date)}</Ltr>
            <span>
              {countText(member.supporter_count, "supporter", formatCount)}
              {member.opposer_count > 0 && ` · ${countText(member.opposer_count, "opposer", formatCount)}`}
            </span>
            {member.is_representative && <span className="badge badge-sky">The group's text</span>}
            <EntityNames names={member.entity_names} />
          </div>
        </div>
        {on && !member.is_representative && (
          <button type="button" className="btn btn-sm shrink-0" disabled={saving} onClick={() => on.setRepresentative(member.claim_id)}>
            Use as the group's text
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
