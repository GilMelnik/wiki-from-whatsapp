import { Bidi, Kbd, ToolLink, formatMonth, stanceOf } from "@shell";
import { canKeep, conversationParams, monthDate, peopleText, STATUS, THREADS_TOOL } from "./withheld.js";

// One claim as the owner judges it: the Hebrew text, its stance, how many people back it, its
// month, where it stands, the entities it names (one decision covers it in every entity's
// list), a link to its conversation, the "Keep withheld" switch (on: the check never releases
// it; switching it off again is the undo) and the "Release" / "Withhold again" button (the
// owner's hand release and its undo). No person is ever shown, only how many.

export function KeepSwitch({ claim, saving, onKeep }) {
  return (
    <label className="flex items-center gap-1 text-xs text-slate-700">
      <input type="checkbox" checked={claim.kept} disabled={saving} onChange={(event) => onKeep(claim.short_id, event.target.checked)} />
      Keep withheld
    </label>
  );
}

/** "Release" while the claim is withheld, "Withhold again" once it is published. */
export function ReleaseButton({ claim, published, saving, onRelease, keyName }) {
  return (
    <button type="button" className={`btn btn-sm ${published ? "" : "btn-primary"}`} disabled={saving} onClick={() => onRelease(claim.short_id, !published)}>
      {published ? "Withhold again" : "Release"}
      {keyName && <Kbd>{keyName}</Kbd>}
    </button>
  );
}

export function EntityNames({ claim }) {
  return (
    <p className="text-xs text-slate-500">
      Names{" "}
      {claim.entity_names.map((name, index) => (
        <span key={name}>
          {index > 0 && ", "}“<Bidi>{name}</Bidi>”
        </span>
      ))}
    </p>
  );
}

export function ConversationLink({ claim, id }) {
  return (
    <ToolLink
      toolId={THREADS_TOOL}
      params={conversationParams(claim)}
      newTab
      id={id}
      className="text-xs text-blue-700 hover:underline"
      title="Opens the claim's conversation in the Conversations page, in a new tab, at the first message the claim rests on; those messages are marked there"
    >
      Open the conversation
    </ToolLink>
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

/** A card of the Released view: with `onKeep`, the check's switch; with `onRelease`, the
 * "Withhold again" button of a claim the owner released by hand. */
export function ClaimCard({ claim, saving, onKeep, onRelease }) {
  return (
    <li className="space-y-2 rounded border border-slate-200 bg-white p-3">
      <Bidi as="p" className="leading-relaxed text-slate-900">
        {claim.text}
      </Bidi>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ClaimFacts claim={claim} />
        {onKeep && canKeep(claim) && <KeepSwitch claim={claim} saving={saving} onKeep={onKeep} />}
        {onRelease && <ReleaseButton claim={claim} published saving={saving} onRelease={onRelease} />}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <EntityNames claim={claim} />
        <ConversationLink claim={claim} />
      </div>
    </li>
  );
}
