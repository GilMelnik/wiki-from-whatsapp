import { Bidi, formatMonth, stanceOf } from "@shell";
import { canKeep, monthDate, peopleText, STATUS } from "./withheld.js";

// One claim as the owner judges it: the Hebrew text, its stance, how many people back it, its
// month, where it stands, and the "Keep withheld" switch (on: the check never releases it;
// switching it off again is the undo). No person is ever shown, only how many.

export function KeepSwitch({ claim, saving, onKeep }) {
  return (
    <label className="flex items-center gap-1 text-xs text-slate-700">
      <input type="checkbox" checked={claim.kept} disabled={saving} onChange={(event) => onKeep(claim.short_id, event.target.checked)} />
      Keep withheld
    </label>
  );
}

export function ClaimFacts({ claim }) {
  const stance = stanceOf(claim.stance);
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
      <span className={`badge ${stance.badge}`}>{stance.label}</span>
      {claim.status && <span className={`badge ${STATUS[claim.status].badge}`}>{STATUS[claim.status].label}</span>}
      <span>{peopleText(claim.supporter_count)}</span>
      <span>{formatMonth(monthDate(claim.month))}</span>
    </span>
  );
}

export function ClaimCard({ claim, saving, onKeep }) {
  return (
    <li className="space-y-2 rounded border border-slate-200 bg-white p-3">
      <Bidi as="p" className="leading-relaxed text-slate-900">
        {claim.text}
      </Bidi>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ClaimFacts claim={claim} />
        {canKeep(claim) && <KeepSwitch claim={claim} saving={saving} onKeep={onKeep} />}
      </div>
    </li>
  );
}
